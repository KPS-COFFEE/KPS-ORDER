"""Brand-local duplicate review; immutable creation evidence and append-only decisions.

The contact lock is shared by every checkout source. Gifts, prices, translated
labels and item ordering are not identity; paid selections and quantities are.
"""
from collections import Counter
import json
from typing import Literal

from fastapi import Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from . import auth, db


def selections(items):
    result = Counter()
    for item in items:
        if item.get('is_gift'):
            continue
        product = item.get('product_id')
        units = item.get('units') or []
        if units:
            for unit in units:
                options = {k: sorted(v) if isinstance(v, list) else v
                           for k, v in (unit.get('options') or {}).items()}
                result[json.dumps([product, options], sort_keys=True, ensure_ascii=False)] += 1
        else:
            result[json.dumps([product, {}], sort_keys=True, ensure_ascii=False)] += item.get('quantity', 1)
    return sorted(result.items())


def contact_lock(c, brand_id, phone):
    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
              ('duplicate-contact:'+brand_id+':'+auth.normalize_phone(phone),))


def matches(c, brand_id, phone, snapshot, *, before=None):
    """Read committed candidates after taking the contact lock, without row locks.

    No branch restriction: routing or staff entry cannot evade a brand-local hold.
    A stable older-first ordering prevents circular dependency between candidates.
    """
    params = [brand_id, auth.normalize_phone(phone)]
    bound = ''
    if before:
        bound = ' AND (created_at,id)<(%s,%s)'
        params.extend(before)
    rows = c.execute("SELECT id,snapshot,created_at,branch_id FROM orders WHERE brand_id=%s "
        "AND normalized_identity_phone(customer->>'phone')=%s "
        "AND created_at>now()-interval '30 minutes' "
        "AND preparation_status NOT IN ('cancelled','rejected') AND delivery_status<>'cancelled' AND test_void IS NULL"
        + bound + ' ORDER BY created_at,id', params).fetchall()
    wanted = selections(snapshot.get('items') or [])
    return [r for r in rows if wanted and selections(r['snapshot'].get('items') or []) == wanted]


def capture(c, brand_id, phone, quote):
    contact_lock(c, brand_id, phone)
    related = matches(c, brand_id, phone, quote)
    quote['duplicate_review'] = {'status':'pending' if related else 'clear',
        'related_order_ids':[r['id'] for r in related],
        'reason':'same_contact_and_purchased_selections', 'window_minutes':30}


def review(c, order):
    decision = c.execute("SELECT details,actor_id,created_at FROM order_events WHERE order_id=%s "
        "AND event_type='order.duplicate_decided' ORDER BY created_at DESC,id DESC LIMIT 1",
        (order['id'],)).fetchone()
    if decision:
        return {**decision['details'], 'decided_by':decision['actor_id'], 'decided_at':decision['created_at']}
    return undecided_review(c,order)


def undecided_review(c,order):
    recorded = order['snapshot'].get('duplicate_review')
    if recorded:
        return recorded
    # Legacy / already accepted orders are checked at the execution boundary too.
    if not order.get('customer',{}).get('phone'):
        return {'status':'clear','related_order_ids':[]}
    related = matches(c, order['brand_id'], order['customer']['phone'], order['snapshot'],
                      before=(order['created_at'],order['id']))
    return {'status':'pending' if related else 'clear', 'related_order_ids':[r['id'] for r in related]}


def enrich(c, orders):
    if not orders:return
    decisions=c.execute("SELECT DISTINCT ON (order_id) order_id,details,actor_id,created_at FROM order_events "
        "WHERE order_id=ANY(%s) AND event_type='order.duplicate_decided' ORDER BY order_id,created_at DESC,id DESC",
        ([o['id'] for o in orders],)).fetchall()
    by_order={r['order_id']:r for r in decisions}
    for order in orders:
        decision=by_order.get(order['id'])
        state=({**decision['details'],'decided_by':decision['actor_id'],'decided_at':decision['created_at']}
               if decision else undecided_review(c,order))
        # List/detail projections contain no cross-order IDs. Related references
        # require independent scope checks through the dedicated manager API.
        public={k:v for k,v in state.items() if k!='related_order_ids'}
        order['duplicate_review']=public
        if 'duplicate_review' in order['snapshot']:
            order['snapshot']={**order['snapshot'],'duplicate_review':public}


def guard(c, order):
    if order['preparation_status'] in ('cancelled','rejected') or order.get('test_void'):
        return
    if order.get('customer',{}).get('phone'):
        contact_lock(c, order['brand_id'], order['customer']['phone'])
    state = review(c, order)
    if state['status'] == 'pending':
        raise db.error(409, 'DUPLICATE_REVIEW_REQUIRED',
            'طلب مشابه بانتظار مدير التوصيل: تأكيد طلب إضافي أو إلغاء كمكرر قبل التنفيذ.')


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    action: Literal['confirm_additional','cancel_duplicate']
    version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=3,max_length=1000,pattern=r'^[^\x00]+$')
    physical_resolution: Literal['returned_to_branch','customer_kept'] | None = None


def manager_scope(c, oid, user, *, lock=False):
    from .orders import scoped_order
    order = scoped_order(c, oid, user, lock=lock)
    from . import morning_delivery
    morning_delivery.authorize_order(c,user,order)
    from .order_amendments import reviewer
    if not reviewer(order,user):
        raise db.error(403,'DUPLICATE_REVIEW_FORBIDDEN','حسم التشابه من مسؤول الخدمة المختص فقط.')
    permission='delivery_assign' if order['service']=='delivery' else 'orders_prepare'
    if not auth.has_permission(user,permission):
        raise db.error(403,'DUPLICATE_REVIEW_FORBIDDEN','صلاحية قبول الطلبات متوقفة.')
    return order


def detail(oid: str, user=Depends(auth.require_roles('owner','delivery_manager','branch_manager'))):
    with db.tx() as c:
        order = manager_scope(c,oid,user)
        state = review(c,order)
        # A similar order may belong to another branch. Expose references only
        # when that order is independently accessible to this manager.
        related=[]
        for rid in state.get('related_order_ids',[]):
            r=c.execute('SELECT * FROM orders WHERE id=%s',(rid,)).fetchone()
            if r and auth.can_access_order(user,r,c):
                related.append({'id':rid,'reference':r['snapshot'].get('order_reference',rid),
                                'preparation_status':r['preparation_status'],'payment_status':r['payment_status']})
        return {'review':{k:v for k,v in state.items() if k!='related_order_ids'},
                'related_orders':related,'version':order['version']}


def decision_response(state, order):
    return {'review':{k:v for k,v in state.items() if k!='related_order_ids'},
            'order':{'id':order['id'],'version':order['version'],
                     'preparation_status':order['preparation_status'],'payment_status':order['payment_status']}}


def decide(oid: str, body:Decision, user=Depends(auth.require_roles('owner','delivery_manager','branch_manager')),
           idempotency_key: str | None=Header(default=None)):
    with db.tx() as c:
        manager_scope(c,oid,user)
        if body.action=='cancel_duplicate':
            from .order_financial_cases import cancellation_scope
            cancellation_scope(c,oid,user)
        order=manager_scope(c,oid,user,lock=True)
        def perform():
            current=review(c,order)
            if current['status']!='pending':
                raise db.error(409,'DUPLICATE_ALREADY_DECIDED','حُسم الطلب من جلسة أخرى.')
            if order['version']!=body.version:
                raise db.error(409,'ORDER_VERSION_CONFLICT','تغير الطلب؛ حدّث الصفحة.')
            if body.action=='confirm_additional':
                if order['preparation_status'] in ('cancelled','rejected'):
                    raise db.error(409,'DUPLICATE_ORDER_CANCELLED','الطلب ملغى ولا يمكن تأكيده.')
            elif order['preparation_status'] not in ('cancelled','rejected'):
                # Cancellation, custody evidence, accounting referral and the
                # duplicate decision commit or roll back together under one lock.
                from .order_financial_cases import cancel_locked,Cancellation
                cancel_locked(c,oid,Cancellation(version=body.version,reason=body.reason,
                              physical_resolution=body.physical_resolution),user)
            details={'status':'confirmed_additional' if body.action=='confirm_additional' else 'cancelled_duplicate',
                     'reason':body.reason,'related_order_ids':current.get('related_order_ids',[])}
            db.event(c,oid,user['id'],'order.duplicate_decided',details)
            db.audit(c,user['id'],'order.duplicate_decided','order',oid,details)
            c.execute('UPDATE orders SET version=version+1 WHERE id=%s',(oid,))
            return decision_response(details,c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone())
        return db.idempotent(c,user['id'],idempotency_key,'order.duplicate_decision:'+oid,body.model_dump(),perform)
