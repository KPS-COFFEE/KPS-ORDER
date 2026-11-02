"""Read-only manager workspace over canonical shifts, tasks and order events."""

ACTIVE = "o.preparation_status NOT IN ('completed','cancelled','rejected') AND o.delivery_status NOT IN ('delivered','cancelled') AND o.completed_at IS NULL"
UNASSIGNED = "NOT EXISTS(SELECT 1 FROM effective_driver_tasks wt WHERE wt.order_id=o.id AND wt.status NOT IN ('cancelled','transferred','reassigned'))"


def _physical_staffing_clause():
    # The completion marker is installed only by the multi-brand migration.
    # Old submitted financial records remain history, never fresh staffing.
    from . import multibrand
    return ' AND staffing_ended_at IS NULL' if multibrand.enabled() else ''


def current_shift(conn, user):
    return conn.execute("SELECT * FROM shifts WHERE user_id=%s AND brand_id=%s AND kind='manager' AND status<>'closed'"+_physical_staffing_clause()+" ORDER BY opened_at DESC LIMIT 1",(user['id'],user['brand_id'])).fetchone()


def current_orders(shift, user):
    if not shift:
        return 'FALSE', []
    # Task membership, not the wall clock, keeps overnight/overdue work visible.
    sql = f"""(EXISTS(SELECT 1 FROM effective_driver_tasks wt JOIN shifts ws ON ws.id=wt.shift_id
        WHERE wt.order_id=o.id AND ws.manager_shift_id=%s AND wt.status NOT IN ('transferred','reassigned'))
        OR ({ACTIVE} AND {UNASSIGNED} AND o.branch_id=ANY(%s))
        OR EXISTS(SELECT 1 FROM order_events we WHERE we.order_id=o.id
            AND we.actor_id=%s AND we.event_type='preparation.cancelled' AND we.created_at>=%s))"""
    return sql, [shift['id'],shift['branch_ids'],user['id'],shift['opened_at']]


STAGE_SQL = """CASE
 WHEN o.preparation_status IN ('cancelled','rejected') OR o.delivery_status='cancelled' THEN 'cancelled'
 WHEN o.delivery_status='delivered' OR o.completed_at IS NOT NULL THEN 'delivered'
 WHEN o.delivery_status='problem' THEN 'attention'
 WHEN o.delivery_status='picked_up' THEN 'road'
 WHEN o.delivery_status='assigned' THEN 'assigned'
 WHEN o.delivery_status='accepted' THEN 'driver_accepted'
 WHEN o.preparation_status='new' THEN 'new' ELSE 'preparing' END"""


def workspace_orders(conn, user):
    """Owner sees the union of real active manager shifts; no invented day window."""
    if user['role'] == 'delivery_manager':
        return current_orders(current_shift(conn, user), user)
    shifts = conn.execute("SELECT * FROM shifts WHERE brand_id=%s AND kind='manager' AND status<>'closed'"+_physical_staffing_clause(), (user['brand_id'],)).fetchall()
    predicates, values = [], []
    for shift in shifts:
        predicate, params = current_orders(shift, {'id': shift['user_id']})
        predicates.append(predicate)
        values.extend(params)
    return ('('+' OR '.join(predicates)+')', values) if predicates else ('FALSE', [])


def workspace_counts(conn, conditions, params, predicate, current_params):
    # Count the full authorized result, before the paginated page of 100 records.
    # The immutable creation snapshot determines origin; later staff actions do
    # not turn a website order into a delivery-center order. Website orders
    # historically omit order_source. Keep future, unknown sources separate.
    source = """CASE WHEN o.snapshot->>'order_source'='delivery_center' THEN 'manager'
        WHEN coalesce(o.snapshot->>'order_source','') IN ('','website','customer') THEN 'website'
        ELSE 'other' END"""
    rows = conn.execute('SELECT '+STAGE_SQL+' AS stage,'+source+' AS source,count(*) AS all_count,'
                        'count(*) FILTER (WHERE '+predicate+') AS current_count '
                        'FROM orders o JOIN branches b ON b.id=o.branch_id WHERE '+
                        ' AND '.join(conditions)+' GROUP BY stage,source',
                        (*current_params, *params)).fetchall()
    result = {scope: {'total': 0, 'sources': {'manager': 0, 'website': 0}}
              for scope in ('all', 'current')}
    for row in rows:
        for scope in result:
            count = int(row[scope+'_count'])
            result[scope][row['stage']] = result[scope].get(row['stage'], 0) + count
            result[scope]['total'] += count
            if row['source'] in result[scope]['sources']:
                result[scope]['sources'][row['source']] += count
    return result
