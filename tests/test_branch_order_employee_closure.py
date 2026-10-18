"""Existing branch-order workflow, explicit manager-free intake, and audit scope."""
import pytest
from app import db
from tests.test_v5_operations import v5

pytestmark = pytest.mark.postgres


def transition(call, order, status, who='branch', **extra):
    return call(who, 'POST', f"/api/orders/{order['id']}/preparation",
                {'status': status, 'version': order['version'], **extra})


def test_manager_free_branch_accept_prepare_ready_and_audited_history(v5):
    actor, call, body, _ = v5
    assert call('branch', 'PATCH', '/api/admin/settings', {'delivery_workflow': 'branch_without_manager'}).status_code == 403
    assert call('owner', 'PATCH', '/api/admin/settings', {'delivery_workflow': 'branch_without_manager'}).status_code == 200
    created = call('customer', 'POST', '/api/orders', body('delivery'))
    assert created.status_code == 200, created.text
    order = created.json()['order']
    assert order['snapshot']['delivery_workflow'] == 'branch_without_manager'
    for status in ('accepted', 'preparing', 'ready'):
        result = transition(call, order, status)
        assert result.status_code == 200, result.text
        order = result.json()['order']
    # No assignment powers or fictitious driver pickup follows branch preparation.
    assert call('branch', 'POST', f"/api/orders/{order['id']}/assign", {'driver_shift_id': 'invented'}).status_code == 403
    assert transition(call, order, 'handed_over').status_code == 409
    detail = actor('owner').get(f"/api/orders/{order['id']}").json()
    events = [e for e in detail['events'] if e['event_type'].startswith('preparation.')]
    assert [e['details']['status'] for e in events] == ['accepted', 'preparing', 'ready']
    assert [e['details']['previous'] for e in events] == ['new', 'accepted', 'preparing']
    assert all(e['actor_id'] == 'v5-branch' and e['actor_name'] == 'TEST branch' and
               e['details']['branch_id'] == 'v5-a' and e['created_at'] for e in events)
    public = actor('customer').get(f"/api/orders/{order['id']}").json()['events']
    assert all('actor_id' not in e and 'actor_name' not in e['details'] for e in public)


def test_manager_duty_modes_and_existing_orders_unchanged(v5):
    _, call, body, _ = v5
    for mode in ('manager_dispatch', 'branch_fulfillment'):
        assert call('owner', 'PATCH', '/api/admin/settings', {'delivery_workflow': mode}).status_code == 200
        result = call('customer', 'POST', '/api/orders', body('delivery'))
        assert result.status_code == 409, result.text
        assert result.json()['detail']['code'] == 'DELIVERY_DUTY_UNAVAILABLE'


def test_pickup_branch_employee_full_existing_flow_and_scope(v5):
    actor, call, body, _ = v5
    created = call('customer', 'POST', '/api/orders', body('pickup'))
    assert created.status_code == 200, created.text
    order = created.json()['order']
    for status in ('accepted', 'preparing', 'ready'):
        result = transition(call, order, status)
        assert result.status_code == 200, result.text
        order = result.json()['order']
    assert transition(call, order, 'completed').status_code == 409  # paid handover remains mandatory
    with db.tx() as c:
        c.execute("UPDATE orders SET payment_status='paid' WHERE id=%s", (order['id'],))
    result = transition(call, order, 'completed')
    assert result.status_code == 200, result.text
    assert result.json()['order']['completed_at']
    outside = call('customer', 'POST', '/api/orders', body('pickup', branch='v5-b')).json()['order']
    assert transition(call, outside, 'accepted').status_code == 404
    with db.tx() as c:
        c.execute("UPDATE orders SET brand_id='kps' WHERE id=%s", (outside['id'],))
    assert transition(call, outside, 'accepted').status_code == 404


def test_branch_preparation_then_owner_dispatch_without_delivery_manager(v5):
    """Owner supervision is an existing approved path; branch receives no dispatch grant."""
    actor, call, body, _ = v5
    from tests.test_v5_operations import synthetic_vehicle_inspection
    from tests.test_order_support import image_body
    assert call('owner', 'PATCH', '/api/admin/settings', {'delivery_workflow': 'branch_without_manager'}).status_code == 200
    order = call('customer', 'POST', '/api/orders', body('delivery')).json()['order']
    for status in ('accepted', 'preparing', 'ready'):
        result = transition(call, order, status)
        assert result.status_code == 200, result.text
        order = result.json()['order']
    # The owner carries existing supervision/custody responsibility, rather than
    # allowing an unowned driver shift or silently elevating branch employees.
    supervisor = call('owner', 'POST', '/api/shifts', {'kind': 'manager', 'branch_ids': ['v5-a']})
    assert supervisor.status_code == 200, supervisor.text
    supervisor_id = supervisor.json()['shift']['id']
    shift = call('owner', 'POST', '/api/shifts', {'kind': 'driver', 'user_id': 'v5-driver',
        'branch_ids': ['v5-a'], 'manager_shift_id': supervisor_id,
        'vehicle_inspection': synthetic_vehicle_inspection()})
    assert shift.status_code == 200, shift.text
    sid = shift.json()['shift']['id']
    assignment = call('owner', 'POST', f"/api/orders/{order['id']}/assign", {'driver_shift_id': sid})
    assert assignment.status_code == 200, assignment.text
    tid = assignment.json()['task']['id']
    assert call('driver', 'POST', f'/api/tasks/{tid}/transition', {'status': 'accepted'}).status_code == 200
    order = actor('owner').get(f"/api/orders/{order['id']}").json()['order']
    handover = transition(call, order, 'handed_over')
    assert handover.status_code == 200, handover.text
    photo = call('driver', 'POST', f'/api/tasks/{tid}/photos', image_body('picked_up'))
    assert photo.status_code == 200, photo.text
    assert call('driver', 'POST', f'/api/tasks/{tid}/transition', {'status': 'picked_up', 'photo_id': photo.json()['id']}).status_code == 200
    payment = call('driver', 'POST', f"/api/orders/{order['id']}/collect", {'method': 'cash', 'amount_halalas': 1200, 'tendered_halalas': 1200})
    assert payment.status_code == 200, payment.text
    photo = call('driver', 'POST', f'/api/tasks/{tid}/photos', image_body('delivered'))
    assert photo.status_code == 200, photo.text
    delivered = call('driver', 'POST', f'/api/tasks/{tid}/transition', {'status': 'delivered', 'photo_id': photo.json()['id'], 'proof': 'TEST owner-supervised delivery'})
    assert delivered.status_code == 200, delivered.text
    with db.tx() as c:
        parent = c.execute('SELECT user_id FROM shifts WHERE id=%s', (supervisor_id,)).fetchone()
        assert parent['user_id'] == 'v5-owner'
        assert c.execute("SELECT count(*) n FROM shifts WHERE user_id='v5-manager'").fetchone()['n'] == 0
