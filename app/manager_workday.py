"""Read-only manager workspace over canonical shifts, tasks and order events."""

ACTIVE = "o.preparation_status NOT IN ('completed','cancelled','rejected') AND o.delivery_status NOT IN ('delivered','cancelled') AND o.completed_at IS NULL"
UNASSIGNED = "NOT EXISTS(SELECT 1 FROM driver_tasks wt WHERE wt.order_id=o.id AND wt.status NOT IN ('cancelled','transferred','reassigned'))"


def current_shift(conn, user):
    return conn.execute("SELECT * FROM shifts WHERE user_id=%s AND brand_id=%s AND kind='manager' AND status<>'closed' ORDER BY opened_at DESC LIMIT 1",(user['id'],user['brand_id'])).fetchone()


def current_orders(shift, user):
    if not shift:
        return 'FALSE', []
    # Task membership, not the wall clock, keeps overnight/overdue work visible.
    sql = f"""(EXISTS(SELECT 1 FROM driver_tasks wt JOIN shifts ws ON ws.id=wt.shift_id
        WHERE wt.order_id=o.id AND ws.manager_shift_id=%s AND wt.status NOT IN ('transferred','reassigned'))
        OR ({ACTIVE} AND {UNASSIGNED} AND o.branch_id=ANY(%s))
        OR EXISTS(SELECT 1 FROM order_events we WHERE we.order_id=o.id
            AND we.actor_id=%s AND we.event_type='preparation.cancelled' AND we.created_at>=%s))"""
    return sql, [shift['id'],shift['branch_ids'],user['id'],shift['opened_at']]
