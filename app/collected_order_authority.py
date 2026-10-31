"""Explicit authority for cancelling an order that has ever recorded a receipt.

The permission is granted to a verified staff ID by the owner. A name, job title,
after-print grant, refunded balance, or an older browser session is not authority.
No receipt, refund, invoice, or custody entry is created or rewritten here.
"""
from . import auth, db, multibrand

PERMISSION = 'orders_cancel_collected'
PAID_STATES = {'paid', 'partially_refunded', 'refunded', 'refund_pending'}


def has_recorded_collection(c, order):
    # Raw immutable receipts remain evidence after full refund or attribution /
    # tender corrections. A net balance of zero must not reopen cancellation.
    recorded = c.execute("""SELECT
        EXISTS(SELECT 1 FROM collections WHERE order_id=%s AND amount_halalas>0)
        OR EXISTS(SELECT 1 FROM branch_online_receipts WHERE order_id=%s AND amount_halalas>0)
        AS recorded""", (order['id'], order['id'])).fetchone()
    return bool(recorded['recorded'] or order.get('payment_status') in PAID_STATES)


def authorized(user):
    # The morning view may add bounded operating capabilities; it must neither
    # invent this grant nor hide an owner's explicit grant to the same staff ID.
    principal=dict(user or {})
    principal.pop('_morning_manager_context',None)
    return bool(user and user.get('active', True) and (
        user.get('role') == 'owner' or
        user.get('role') in {'admin', 'delivery_manager'}
        and PERMISSION in (principal.get('extra_permissions') or [])
        and auth.has_permission(principal, PERMISSION)))


def current_actor(c, user, brand_id, *, lock=False):
    """Reload current grants; callers lock the order before a locking read here.

    A share lock serializes permission/active changes with a sensitive decision.
    Membership is locked in the same user-then-membership order. Reads of the
    interface use ordinary SELECTs and never acquire these locks.
    """
    actor = c.execute('SELECT * FROM users WHERE id=%s' + (' FOR SHARE' if lock else ''),
                      (user['id'],)).fetchone()
    if not actor or not actor.get('active'):
        raise db.error(403, 'INTERVENTION_ACTOR_UNAVAILABLE', 'الحساب غير مفعل؛ لا يمكن تنفيذ المعالجة.')
    if lock and actor['brand_id'] != brand_id and multibrand.enabled():
        c.execute('SELECT user_id FROM staff_brand_memberships WHERE user_id=%s AND brand_id=%s FOR SHARE',
                  (actor['id'], brand_id)).fetchone()
    actor = multibrand.project(c, actor, brand_id)
    if not actor:
        raise db.error(404, 'ORDER_NOT_FOUND', 'الطلب غير متاح ضمن الصلاحيات الحالية.')
    # Re-resolve the same duty against current policy. Reloading the wider
    # employee row must not widen a restricted morning session's branches.
    if user.get('_morning_manager_context'):
        from .morning_manager_context import resolve
        context = user['_morning_manager_context']
        actor = resolve(c, actor, shift_id=context.get('shift_id'), allow_bootstrap=bool(context.get('bootstrap')))
        for key in ('_morning_history_read','_morning_cancellation_replay','_morning_cancelled_incentive_review'):
            if user.get(key):actor[key]=user[key]
    return actor


def require(c, order, user, *, lock=False):
    if not has_recorded_collection(c, order):
        return None
    actor = current_actor(c, user, order['brand_id'], lock=lock)
    in_scope = (actor['role'] == 'owner' and not actor.get('home_brand_id')
                or order['branch_id'] in actor.get('branch_ids', []))
    if not authorized(actor) or not in_scope:
        raise db.error(403, 'COLLECTED_CANCELLATION_AUTHORITY',
                       'الطلب مسجل له تحصيل؛ إلغاؤه للمالك أو المفوض المحدد بإلغاء الطلبات المحصلة فقط.')
    return {'actor_id': actor['id'], 'permission': 'owner' if actor['role'] == 'owner' else PERMISSION,
            'recorded_collection': True, 'financial_approval': False}
