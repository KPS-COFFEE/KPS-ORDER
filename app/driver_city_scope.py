"""City-bound physical driver duty, shared by assignment and custody transfer."""
from . import catalog, db


def guard(conn, shift, order, pickup_branch_id, *, physical_shift=None):
    anchors = set(shift.get('branch_ids') or [])
    if physical_shift:
        anchors.update(physical_shift.get('branch_ids') or [])
    branches = conn.execute('SELECT id,city FROM branches WHERE id=ANY(%s)',
                            (sorted(anchors | {pickup_branch_id}),)).fetchall()
    cities = {b['id']: catalog.normalize_city(b.get('city') or '') for b in branches}
    target = cities.get(pickup_branch_id)
    # A multi-city duty has no unambiguous physical anchor. A broad employee
    # permission cannot authorize a cross-city journey from that duty.
    if (not target or not anchors or any(not cities.get(branch) for branch in anchors)
            or {cities[branch] for branch in anchors} != {target}):
        raise db.error(409, 'DRIVER_CITY_SCOPE', 'شفت السائق مخصص لمدينة واحدة؛ اختر سائقا تابعا لمدينة الطلب.')
    active = conn.execute("""SELECT b.city FROM effective_driver_tasks t
        JOIN orders o ON o.id=t.order_id
        LEFT JOIN order_preparation_routes r ON r.id=o.preparation_route_id
        JOIN branches b ON b.id=coalesce(r.target_branch_id,o.branch_id)
        WHERE t.driver_id=%s AND t.order_id<>%s
          AND t.status NOT IN ('delivered','cancelled','transferred','reassigned')""",
        (shift['user_id'], order['id'])).fetchall()
    if any(catalog.normalize_city(row.get('city') or '') != target for row in active):
        raise db.error(409, 'DRIVER_CITY_ACTIVE_TASK', 'السائق مسؤول عن طلب نشط في مدينة أخرى؛ أكمل العهدة قبل الإسناد.')
