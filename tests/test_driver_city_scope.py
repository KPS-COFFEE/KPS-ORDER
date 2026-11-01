"""Explicit city scope cannot be widened by employee branch permissions."""
import pytest

from app import db
from test_operations import ops

pytestmark = pytest.mark.postgres


def branch(city):
    with db.tx() as c:
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) "
                  "VALUES('city-extra',2,'city-extra','ops-brand','فرع آخر',%s,'{}')", (city,))
        c.execute("UPDATE users SET branch_ids='[\"ops-branch\",\"city-extra\"]' "
                  "WHERE id IN ('ops-manager','ops-driver','ops-driver2')")


@pytest.mark.parametrize('city,expected', [('حفرالباطن', 200), ('الخفجي', 409)])
def test_assignment_keeps_same_city_branches_and_rejects_ambiguous_multi_city_duty(ops, city, expected):
    _, post, make_order, shifts = ops
    _, driver = shifts()
    branch(city)
    with db.tx() as c:
        c.execute("UPDATE shifts SET branch_ids='[\"ops-branch\",\"city-extra\"]' WHERE id=%s", (driver,))
    oid = make_order()
    result = post('manager', f'/api/orders/{oid}/assign', {'driver_shift_id': driver}, status=expected)
    if expected == 409:
        assert result['detail']['code'] == 'DRIVER_CITY_SCOPE'
        with db.tx() as c:
            assert c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s', (oid,)).fetchone()['n'] == 0


def test_active_foreign_city_task_cannot_be_hidden_by_single_city_anchor(ops):
    _, post, make_order, shifts = ops
    _, driver = shifts()
    branch('الخفجي')
    other = make_order(branch='city-extra')
    with db.tx() as c:
        # A retained legacy assignment must block another city even when the
        # current branch anchor itself is correctly configured.
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,assigned_by) "
                  "VALUES('retained-city-task',%s,'ops-driver',%s,'ops-manager')", (other, driver))
    oid = make_order()
    response = post('manager', f'/api/orders/{oid}/assign', {'driver_shift_id': driver}, status=409)
    assert response['detail']['code'] == 'DRIVER_CITY_ACTIVE_TASK'


@pytest.mark.parametrize('reassign', [False, True])
def test_handover_and_reassignment_repeat_city_check_before_custody_moves(ops, reassign):
    _, post, make_order, shifts = ops
    manager, driver = shifts()
    target = post('driver2', '/api/shifts', {'kind': 'driver', 'branch_ids': ['ops-branch'],
                  'manager_shift_id': manager})['shift']['id']
    oid = make_order()
    tid = post('manager', f'/api/orders/{oid}/assign', {'driver_shift_id': driver})['task']['id']
    branch('الخفجي')
    with db.tx() as c:
        c.execute("UPDATE shifts SET branch_ids='[\"ops-branch\",\"city-extra\"]' WHERE id=%s", (target,))
    response = post('manager', f'/api/tasks/{tid}/handover', {'driver_shift_id': target,
                    'reassign_before_pickup': reassign, 'reason': 'نقل المهمة للسائق البديل'}, status=409)
    assert response['detail']['code'] == 'DRIVER_CITY_SCOPE'
    with db.tx() as c:
        row = c.execute('SELECT driver_id,transfer_shift_id,status FROM driver_tasks WHERE id=%s', (tid,)).fetchone()
        assert row == {'driver_id': 'ops-driver', 'transfer_shift_id': None, 'status': 'assigned'}
