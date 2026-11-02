"""Driver order review is a read witness, never a new financial authority."""
from datetime import date

import pytest
from fastapi import HTTPException

from app import db
from app.driver_order_review import history_bounds
from test_central_workdays import central, open_days
from test_enbishan_dual_brand import dual


def test_history_dates_are_bounded_and_use_riyadh_midnight():
    start, end = history_bounds(date(2026, 10, 1), date(2026, 10, 2))
    assert start.isoformat() == '2026-10-01T00:00:00+03:00'
    assert end.isoformat() == '2026-10-03T00:00:00+03:00'
    for dates in ((None, None), (date(2026, 10, 3), date(2026, 10, 2)),
                  (date(2026, 1, 1), date(2026, 2, 1))):
        with pytest.raises(HTTPException) as error:
            history_bounds(*dates)
        assert error.value.status_code == 422


def assigned_orders(central):
    _, actor, post, _, order = central
    _, driver = open_days(central)
    ids = []
    for brand in ('kps', 'kps', 'enbishan'):
        shift = next(s for s in driver['shifts'] if s['brand_id'] == brand)
        oid = order(brand)
        post('manager', '/api/orders/'+oid+'/assign', {'driver_shift_id': shift['id']}, brand=brand)
        ids.append(oid)
    return actor, driver, ids


@pytest.mark.postgres
def test_current_review_separates_brand_totals_before_pagination(central):
    actor, driver, ids = assigned_orders(central)
    path = '/api/driver-order-review?driver_id=dual-driver&limit=1'
    response = actor('manager').get(path)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['view'] == 'current'
    assert data['shift']['id'] == driver['shift']['id']
    assert data['total_count'] == 3
    assert data['next_offset'] == 1
    assert len(data['orders']) == 1
    assert {r['id']: (r['count'], r['total_halalas']) for r in data['brands']} == {
        'kps': (2, 2000), 'enbishan': (1, 1000)}
    selected = actor('manager').get(path+'&brand_id=enbishan').json()
    assert selected['brands'] == data['brands']
    assert selected['total_count'] == 1
    assert selected['next_offset'] is None
    row = selected['orders'][0]
    assert row['id'] == ids[-1]
    assert row['brand_id'] == 'enbishan'
    assert row['total_halalas'] == 1000
    assert row['payment_method'] == 'cash'
    assert row['task_status'] == 'assigned'
    assert 'customer' not in row


@pytest.mark.postgres
def test_closure_review_uses_exact_legs_and_rejects_date_override(central):
    actor, driver, ids = assigned_orders(central)
    # An older creation date remains part of this exact closure, not a date search.
    with db.tx() as c:
        c.execute("UPDATE orders SET created_at=now()-interval '3 days' WHERE id=%s", (ids[0],))
    for shift in driver['shifts']:
        response = actor('manager').get('/api/driver-order-review?shift_id='+shift['id'])
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['view'] == 'closure'
        assert {o['id'] for o in data['orders']} == set(ids)
    path = '/api/driver-order-review?shift_id='+driver['shift']['id']
    assert actor('manager').get(path+'&start=2026-10-01&end=2026-10-02').status_code == 422
    assert actor('manager').get(path+'&driver_id=somebody-else').status_code == 404
    assert actor('driver').get(path).status_code == 403


@pytest.mark.postgres
def test_shared_closure_rechecks_brand_grants_and_denied_permissions(central):
    actor, driver, _ = assigned_orders(central)
    path = '/api/driver-order-review?shift_id='+driver['shift']['id']
    with db.tx() as c:
        c.execute("UPDATE users SET permission_denials=%s WHERE id='dual-manager'", (db.json(['orders_view']),))
    assert actor('manager').get(path).status_code == 403
    with db.tx() as c:
        c.execute("UPDATE users SET permission_denials='[]' WHERE id='dual-manager'")
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-manager' AND brand_id='enbishan'")
    assert actor('manager').get(path).status_code == 404


@pytest.mark.postgres
def test_same_branch_manager_cannot_read_another_managers_driver_closure(central):
    actor, driver, _ = assigned_orders(central)
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) "
                  "VALUES('dual-other-manager','TEST OTHER MANAGER','delivery_manager','kps',%s)",
                  (db.json(['kps-branch-1']),))
        c.execute("INSERT INTO staff_brand_memberships(user_id,brand_id,branch_ids,granted_by) "
                  "VALUES('dual-other-manager','enbishan',%s,'dual-owner')",
                  (db.json(['enbishan-test-1', 'enbishan-test-2']),))
    response = actor('other-manager').get('/api/driver-order-review?shift_id='+driver['shift']['id'])
    assert response.status_code == 403, response.text
