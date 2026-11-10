"""Automatic dispatch for actual KPS Branch 2 morning orders only.

The morning policy is the sole enablement gate. Evening dispatch settings and
simulated rehearsals do not participate. Driver acceptance remains explicit.
"""
import asyncio
import logging
import time

from fastapi import HTTPException

from . import catalog, db, morning_delivery

CAPACITY = 3
TICK_SECONDS = 10
BATCH_SIZE = 20
TICK_BUDGET_SECONDS = 2
log = logging.getLogger(__name__)


def lock_operations(c, *, wait=True):
    function = 'pg_advisory_xact_lock' if wait else 'pg_try_advisory_xact_lock'
    row = c.execute('SELECT '+function+'(hashtextextended(%s,0)) AS acquired', ('operations:kps',)).fetchone()
    return True if wait else row['acquired']


def actual_morning(order):
    snapshot = (order or {}).get('snapshot') or {}
    return bool(order and order.get('brand_id') == 'kps' and order.get('service') == 'delivery'
                and snapshot.get('delivery_period') == 'morning'
                and not snapshot.get('simulated') and not snapshot.get('morning_trial_id')
                and not str(order.get('id', '')).startswith('TRIAL-'))


def launch_order(c, order):
    if not actual_morning(order):return False
    branch=c.execute('SELECT branch_no,city FROM branches WHERE id=%s AND brand_id=%s', (order['branch_id'],'kps')).fetchone()
    return bool(branch and branch['branch_no']==2 and catalog.normalize_city(branch['city'])=='حفر الباطن')


def enrich(c, rows):
    selected={row['id']:row for row in rows if actual_morning(row)}
    if not selected:return
    brand=c.execute("SELECT settings FROM brands WHERE id='kps'").fetchone()
    approved=morning_delivery.policy((brand or {}).get('settings'))['manager_user_ids']
    for row in selected.values():
        row.update(morning_driver_accepted=False,morning_task_status=None,morning_driver_name=None)
    tasks=c.execute("SELECT t.order_id,t.status,t.accepted_at,d.display_name,EXISTS(SELECT 1 FROM order_events e "
                    "WHERE e.order_id=t.order_id AND e.actor_id=t.driver_id AND e.event_type IN ('task.accepted','task.handover.accepted') "
                    "AND e.details->>'task_id'=t.id) AS driver_confirmed FROM driver_tasks t "
                    "JOIN orders o ON o.id=t.order_id JOIN branches b ON b.id=o.branch_id AND b.brand_id=o.brand_id "
                    "JOIN shifts s ON s.id=t.shift_id JOIN shifts m ON m.id=s.manager_shift_id "
                    "JOIN users d ON d.id=t.driver_id AND d.id=s.user_id JOIN users u ON u.id=m.user_id "
                    "WHERE t.order_id=ANY(%s) AND t.status NOT IN ('delivered','cancelled','transferred','reassigned') "
                    "AND b.branch_no=2 AND replace(b.city,' ','')='حفرالباطن' "
                    "AND s.brand_id='kps' AND s.kind='driver' AND s.status='open' AND s.branch_ids=jsonb_build_array(o.branch_id) "
                    "AND s.delivery_window_snapshot->>'period'='morning' AND m.brand_id='kps' AND m.kind='manager' "
                    "AND m.status='open' AND m.branch_ids=jsonb_build_array(o.branch_id) AND m.delivery_window_snapshot->>'period'='morning' "
                    "AND d.active AND d.invitation_status='active' AND d.brand_id='kps' AND d.role='driver' AND d.branch_ids ? o.branch_id "
                    "AND u.active AND u.invitation_status='active' AND u.brand_id='kps' AND u.role='delivery_manager' "
                    "AND u.branch_ids ? o.branch_id AND u.id=ANY(%s) ORDER BY t.created_at,t.id",
                    (list(selected),approved)).fetchall()
    for task in tasks:
        row=selected[task['order_id']]
        row.update(morning_driver_accepted=bool(task['status'] in {'accepted','picked_up'} and task['accepted_at'] and task['driver_confirmed']),
                   morning_task_status=task['status'],morning_driver_name=task['display_name'])


def require_driver_acceptance(c, order):
    if not launch_order(c, order):return
    enrich(c,[order])
    if not order['morning_driver_accepted']:
        raise db.error(409,'MORNING_DRIVER_ACCEPTANCE_REQUIRED','يلزم قبول السائق الفعلي للمهمة قبل بدء التجهيز.')


def _policy(c):
    row = c.execute("SELECT settings FROM brands WHERE id='kps' AND active FOR SHARE").fetchone()
    policy = morning_delivery.policy((row or {}).get('settings'))
    return policy if row and policy['enabled'] else None


def _pending(c, order, reason):
    # A retry never floods the customer's event history with the same fallback.
    previous = c.execute("SELECT details->>'reason' AS reason FROM order_events WHERE order_id=%s "
                         "AND event_type='dispatch.morning_pending' ORDER BY created_at DESC,id DESC LIMIT 1", (order['id'],)).fetchone()
    if not previous or previous['reason'] != reason:
        db.event(c, order['id'], None, 'dispatch.morning_pending',
                 {'reason': reason, 'branch_id': order['branch_id'], 'automatic': True})
    return {'status': 'manager_required', 'reason': reason}


def try_assign(c, oid):
    """Caller must enter the canonical brand lane before taking any order lock."""
    from . import operations
    lock_operations(c)
    policy = _policy(c)
    if not policy:
        return {'status': 'not_applicable'}
    order = c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE', (oid,)).fetchone()
    if (not actual_morning(order) or order['preparation_status'] not in {'accepted','preparing','ready'}
            or order['delivery_status'] != 'pending' or order.get('completed_at')):
        return {'status': 'not_applicable'}
    branch = c.execute('SELECT branch_no,city FROM branches WHERE id=%s AND brand_id=%s',
                       (order['branch_id'], 'kps')).fetchone()
    if (not branch or branch['branch_no'] != 2 or catalog.normalize_city(branch['city']) != 'حفر الباطن'
            or order['branch_id'] not in policy['execution_branch_ids']):
        return {'status': 'not_applicable'}
    if c.execute("SELECT 1 FROM driver_tasks WHERE order_id=%s AND status NOT IN ('delivered','cancelled','transferred')", (oid,)).fetchone():
        return {'status': 'already_assigned'}
    preferred = (order.get('snapshot') or {}).get('manager_shift_id')
    if not preferred:
        accepted = c.execute("SELECT details->>'manager_shift_id' AS manager_shift_id FROM order_events "
                             "WHERE order_id=%s AND event_type='preparation.accepted' AND details ? 'manager_shift_id' "
                             "ORDER BY created_at DESC,id DESC LIMIT 1", (oid,)).fetchone()
        preferred = (accepted or {}).get('manager_shift_id')
    rows = c.execute("SELECT s.id,s.manager_shift_id,count(t.id) FILTER (WHERE t.status NOT IN "
                     "('delivered','cancelled','transferred','reassigned')) AS active_tasks,max(t.created_at) AS last_assignment "
                     "FROM shifts s JOIN users d ON d.id=s.user_id JOIN shifts m ON m.id=s.manager_shift_id "
                     "JOIN users u ON u.id=m.user_id LEFT JOIN driver_tasks t ON t.shift_id=s.id "
                     "WHERE s.brand_id='kps' AND s.kind='driver' AND s.status='open' AND s.available "
                     "AND s.delivery_window_snapshot->>'period'='morning' AND s.branch_ids=%s::jsonb "
                     "AND d.active AND d.invitation_status='active' AND d.role='driver' AND d.brand_id='kps' AND d.branch_ids ? %s "
                     "AND m.brand_id='kps' AND m.kind='manager' AND m.status='open' "
                     "AND m.delivery_window_snapshot->>'period'='morning' AND m.branch_ids=%s::jsonb "
                     "AND u.active AND u.invitation_status='active' AND u.role='delivery_manager' "
                     "AND u.brand_id='kps' AND u.branch_ids ? %s AND u.id=ANY(%s) "
                     "AND (%s::text IS NULL OR m.id=%s) GROUP BY s.id "
                     "ORDER BY active_tasks,last_assignment NULLS FIRST,s.opened_at,s.id LIMIT 100",
                     (db.json([order['branch_id']]), order['branch_id'], db.json([order['branch_id']]), order['branch_id'],
                      policy['manager_user_ids'], preferred, preferred)).fetchall()
    saturated = False
    for row in rows:
        if row['active_tasks'] >= CAPACITY:
            saturated = True
            continue
        try:
            # Keep rollback local if an eligibility or amendment guard rejects a
            # candidate. No partial task/assignment can survive that rejection.
            with c.transaction():
                shift = operations.lock_shift(c, row['id'])
                manager = operations.eligible_driver_shift(c, shift, order)
                principal = c.execute('SELECT * FROM users WHERE id=%s', (manager['user_id'],)).fetchone()
                morning_delivery.authorize_order(c, principal, order)
                result = operations.assign_in_transaction(c, principal, oid,
                    operations.Assignment(driver_shift_id=shift['id'], accept_order=False))
                db.audit(c, principal['id'], 'dispatch.morning_auto_assigned', 'order', oid,
                         {'task_id': result['task']['id'], 'driver_shift_id': shift['id'],
                          'manager_shift_id': manager['id'], 'capacity': CAPACITY, 'automatic': True})
                return {'status': 'assigned', 'task': result['task']}
        except HTTPException as error:
            if error.status_code not in (403, 404, 409):
                raise
    return _pending(c, order, 'capacity' if saturated else 'no_eligible_driver')


def tick():
    """Bounded retry covers driver opening, resumed availability and freed space."""
    with db.tx() as c:
        if not lock_operations(c, wait=False) or not _policy(c):
            return 0
        rows = c.execute("SELECT o.id FROM orders o JOIN branches b ON b.id=o.branch_id AND b.brand_id=o.brand_id "
                         "WHERE o.brand_id='kps' AND b.branch_no=2 AND o.service='delivery' "
                         "AND o.preparation_status IN ('accepted','preparing','ready') AND o.delivery_status='pending' AND o.completed_at IS NULL "
                         "AND o.snapshot->>'delivery_period'='morning' "
                         "AND NOT EXISTS(SELECT 1 FROM driver_tasks t WHERE t.order_id=o.id "
                         "AND t.status NOT IN ('delivered','cancelled','transferred')) "
                         "ORDER BY o.created_at,o.id LIMIT %s", (BATCH_SIZE,)).fetchall()
        assigned = 0
        started = time.monotonic()
        for row in rows:
            if time.monotonic()-started >= TICK_BUDGET_SECONDS:break
            with c.transaction():
                assigned += try_assign(c, row['id'])['status'] == 'assigned'
        return assigned


async def poll_loop():
    from starlette.concurrency import run_in_threadpool
    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            await run_in_threadpool(tick)
        except Exception:
            # Database/implementation errors remain visible to operations, and
            # cannot convert a failed assignment into success or lose an order.
            log.exception('Morning Branch 2 dispatch retry failed')
