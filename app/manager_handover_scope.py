"""Explicit handover grants and immutable custody observations, never money moves."""
from . import db


def live_principal(c, shift):
    from . import multibrand
    user = c.execute('SELECT * FROM users WHERE id=%s FOR SHARE', (shift['user_id'],)).fetchone()
    if user and multibrand.enabled():
        user = multibrand.project(c, user, shift['brand_id'])
    return user


def authorized(c, shift, branches):
    user = live_principal(c, shift)
    if (shift.get('delivery_window_snapshot') or {}).get('period') == 'morning':
        from . import morning_delivery
        from fastapi import HTTPException
        for branch_id in branches:
            try:
                morning_delivery.authorize_order(c, user, {'brand_id': shift['brand_id'],
                    'branch_id': branch_id, 'snapshot': {'delivery_period': 'morning'}})
            except HTTPException as exc:
                if exc.status_code != 403:
                    raise
                return False
    return bool(user and user['active'] and user['brand_id'] == shift['brand_id']
                and user['role'] in ('owner', 'delivery_manager')
                and set(branches) <= set(shift['branch_ids'])
                and (user['role'] == 'owner' and not user.get('home_brand_id')
                     or set(branches) <= set(user.get('branch_ids', []))))


def accepted_order(c, user, order, *, shift_id=None):
    """Only orders explicitly handed to this live recipient gain an exception."""
    if not order.get('id') or not user or not user.get('active'):
        return False
    rows = c.execute("SELECT s.*,h.branch_ids AS handed_branches FROM manager_handovers h "
        "JOIN shifts s ON s.id=h.to_shift_id WHERE h.status='accepted' "
        "AND s.user_id=%s AND s.brand_id=%s AND s.status='open' "
        "AND h.operational_snapshot->'order_ids' ? %s FOR SHARE OF s",
        (user['id'], order['brand_id'], order['id'])).fetchall()
    rows += c.execute("SELECT s.*,h.branch_ids AS handed_branches FROM driver_support_requests h "
        "JOIN shifts s ON s.id=h.to_shift_id JOIN shifts d ON d.id=h.driver_shift_id "
        "WHERE h.status='accepted' AND d.manager_shift_id=s.id "
        "AND s.user_id=%s AND s.brand_id=%s AND s.status='open' "
        "AND h.operational_snapshot->'order_ids' ? %s FOR SHARE OF s",
        (user['id'], order['brand_id'], order['id'])).fetchall()
    rows = [s for s in rows if not c.execute(
        "SELECT 1 FROM effective_driver_tasks t JOIN shifts d ON d.id=t.shift_id WHERE t.order_id=%s "
        "AND t.status NOT IN ('cancelled','transferred','reassigned') AND d.manager_shift_id IS DISTINCT FROM %s LIMIT 1",
        (order['id'], s['id'])).fetchone()]
    return any((shift_id is None or s['id'] == shift_id)
               and order['branch_id'] in s['handed_branches']
               and authorized(c, s, [order['branch_id']]) for s in rows)


def handed_off(c, source_id, order):
    rows = c.execute("SELECT s.*,h.branch_ids AS handed_branches FROM manager_handovers h "
        "JOIN shifts s ON s.id=h.to_shift_id WHERE h.from_shift_id=%s AND h.status='accepted' "
        "AND s.status='open' AND s.brand_id=%s AND h.operational_snapshot->'order_ids' ? %s "
        "FOR SHARE OF s", (source_id, order['brand_id'], order['id'])).fetchall()
    return any(order['branch_id'] in s['handed_branches']
               and authorized(c, s, [order['branch_id']]) for s in rows)


def snapshot(c, source, children):
    from .operations import balance
    from .blind_closure import card_reconciliation
    child_ids = [s['id'] for s in children]
    orders = c.execute("SELECT DISTINCT o.id FROM orders o WHERE o.brand_id=%s "
        "AND o.branch_id=ANY(%s) AND o.service='delivery' AND o.completed_at IS NULL "
        "AND o.preparation_status NOT IN ('cancelled','rejected','completed') "
        "AND o.delivery_status NOT IN ('cancelled','delivered') AND ("
        "EXISTS(SELECT 1 FROM effective_driver_tasks t WHERE t.order_id=o.id AND t.shift_id=ANY(%s) "
        "AND t.status NOT IN ('cancelled','transferred','reassigned')) OR "
        "EXISTS(SELECT 1 FROM order_events e WHERE e.order_id=o.id AND e.event_type='preparation.accepted' "
        "AND e.details->>'manager_shift_id'=%s) OR "
        "EXISTS(SELECT 1 FROM manager_handovers h WHERE h.to_shift_id=%s AND h.status='accepted' "
        "AND h.operational_snapshot->'order_ids' ? o.id) OR "
        "(%s AND o.snapshot->>'delivery_period'='morning' "
        "AND (o.snapshot->>'manager_shift_id' IS NULL OR o.snapshot->>'manager_shift_id'=%s) "
        "AND NOT EXISTS(SELECT 1 FROM effective_driver_tasks t WHERE t.order_id=o.id "
        "AND t.status NOT IN ('cancelled','transferred','reassigned')))) ORDER BY o.id",
        (source['brand_id'], source['branch_ids'], child_ids, source['id'], source['id'],
         (source.get('delivery_window_snapshot') or {}).get('period') == 'morning', source['id'])).fetchall()
    return {'order_ids': [o['id'] for o in orders], 'cash_transferred': False,
            'source_manager_cash_halalas': balance(c, source),
            'drivers': [{'shift_id': s['id'], 'driver_id': s['user_id'],
                         'cash_halalas': balance(c, s),
                         'expected_card_halalas': card_reconciliation(c, s['id'], 0,
                             (s.get('external_enbishan') or {}).get('card_halalas', 0))['expected_card_halalas']}
                        for s in children]}


def guard_received_work(c, shift):
    orders = c.execute("SELECT DISTINCT o.* FROM manager_handovers h JOIN orders o "
        "ON h.operational_snapshot->'order_ids' ? o.id WHERE h.to_shift_id=%s "
        "AND h.status='accepted' AND o.completed_at IS NULL "
        "AND o.preparation_status NOT IN ('cancelled','rejected','completed') "
        "AND o.delivery_status NOT IN ('cancelled','delivered') "
        "AND NOT EXISTS(SELECT 1 FROM effective_driver_tasks t WHERE t.order_id=o.id "
        "AND t.status NOT IN ('cancelled','transferred','reassigned'))", (shift['id'],)).fetchall()
    if any(not handed_off(c, shift['id'], o) for o in orders):
        raise db.error(409, 'MORNING_ORDERS_AWAITING_DISPATCH',
                       'توجد طلبات مستلمة بالتسليم بانتظار المعالجة أو الإسناد.')
