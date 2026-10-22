"""Real-session dispatch grants remain limited to one morning manager duty."""
from uuid import uuid4

import pytest
from psycopg.errors import RaiseException

from app import db, employee_permissions, service_hours
from test_morning_delivery import at
from test_core import _authenticate, _branch
from test_morning_manager_context_postgres import B2, B3, morning_manager, post
from test_operations import ops

pytestmark = pytest.mark.postgres


def own_second_driver():
    with db.tx() as c:
        c.execute("UPDATE shifts SET manager_shift_id='ctx-duty' WHERE id='ctx-other-driver'")


def assigned(client, ids):
    result = post(client, '/api/orders/'+ids['morning']+'/assign', {'driver_shift_id': 'ctx-driver'})
    assert result.status_code == 200, result.text
    return result.json()['task']['id']


def pin_order(c, oid, mode, duty):
    if mode == 'snapshot':
        pinned = 'ctx-pinned-'+uuid4().hex
        # Confirmed snapshots are immutable: construct the pinned fixture at
        # insertion, just as the WhatsApp order creation path does.
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) "
                  "SELECT %s,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,jsonb_set(snapshot,'{manager_shift_id}',%s),customer FROM orders WHERE id=%s",
                  (pinned, db.json(duty), oid))
        return pinned
    else:
        db.event(c, oid, 'ops-manager', 'preparation.accepted', {'manager_shift_id': duty})
        return oid


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('mode', ['snapshot', 'event'])
def test_explicit_foreign_morning_parent_hides_unassigned_order_and_all_writes(morning_manager, mode):
    client, ids, _ = morning_manager; oid = ids['morning']
    with db.tx() as c:
        oid = pin_order(c, oid, mode, 'ctx-other-duty')
    response = client.get('/api/orders')
    assert response.status_code == 200, response.text
    assert oid not in {o['id'] for o in response.json()['orders']}
    assert client.get('/api/orders/'+oid).status_code == 404
    assert post(client, f'/api/orders/{oid}/manager-accept', {'version': 1}).status_code == 404
    assert post(client, f'/api/orders/{oid}/assign', {'driver_shift_id': 'ctx-driver'}).status_code == 404
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM driver_tasks WHERE order_id=%s', (oid,)).fetchone()


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('mode', ['snapshot', 'event'])
def test_saved_assignment_response_never_replays_after_explicit_parent_changes(morning_manager, mode):
    client, ids, _ = morning_manager; oid = ids['morning']; key = uuid4().hex
    body = {'driver_shift_id': 'ctx-driver'}; path = f'/api/orders/{oid}/assign'
    assert post(client, path, body, key).status_code == 200
    if mode == 'snapshot':
        # Rebinding a confirmed order directly is itself prohibited. Keep that
        # protection intact, then exercise actual dynamic revocation via the
        # accepted-event pin below (the original snapshot remains unbound).
        with pytest.raises(RaiseException, match='approved immutable revision'):
            with db.tx() as c:
                c.execute("UPDATE orders SET snapshot=jsonb_set(snapshot,'{manager_shift_id}',%s) WHERE id=%s", (db.json('ctx-other-duty'), oid))
        with db.tx() as c:
            assert not c.execute('SELECT snapshot FROM orders WHERE id=%s', (oid,)).fetchone()['snapshot'].get('manager_shift_id')
    with db.tx() as c:
        pin_order(c, oid, 'event', 'ctx-other-duty')
    assert post(client, path, body, key).status_code == 404
    response = client.get('/api/orders')
    assert oid not in {o['id'] for o in response.json()['orders']}
    assert client.get('/api/tasks', params={'order_id': oid}).status_code == 404


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('mode', ['snapshot', 'event'])
def test_own_explicit_parent_retains_read_and_assignment(morning_manager, mode):
    client, ids, _ = morning_manager; oid = ids['morning']
    with db.tx() as c:
        oid = pin_order(c, oid, mode, 'ctx-duty')
    assert client.get('/api/orders/'+oid).status_code == 200
    assert oid in {o['id'] for o in client.get('/api/orders').json()['orders']}
    assigned(client, ids | {'morning': oid})


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_manager_preparation_intervention_requires_actual_driver_acceptance_and_reason(morning_manager):
    client, ids, manager = morning_manager
    tid = assigned(client, ids); oid = ids['morning']; path = f'/api/orders/{oid}/preparation'
    with db.tx() as c:
        c.execute("UPDATE orders SET preparation_status='accepted' WHERE id=%s", (oid,))
        version = c.execute('SELECT version FROM orders WHERE id=%s', (oid,)).fetchone()['version']
        driver = c.execute("SELECT * FROM users WHERE id='ops-driver'").fetchone()
    denied = post(client, path, {'status': 'preparing', 'version': version, 'reason': 'Branch delayed; manager intervention'})
    assert denied.status_code in (403, 409), denied.text
    del client.headers['X-Delivery-Period']; _authenticate(client, driver)
    accepted = post(client, f'/api/tasks/{tid}/transition', {'status': 'accepted'})
    assert accepted.status_code == 200, accepted.text
    _authenticate(client, manager); client.headers['X-Delivery-Period'] = 'morning'
    for state in ('preparing', 'ready'):
        with db.tx() as c:
            version = c.execute('SELECT version FROM orders WHERE id=%s', (oid,)).fetchone()['version']
        for invalid in ('cancelled', 'completed', 'handed_over', 'accepted'):
            denied = post(client, path, {'status': invalid, 'version': version, 'reason': 'Forbidden intervention'})
            assert denied.status_code == 403, denied.text
        missing = post(client, path, {'status': state, 'version': version})
        assert missing.status_code in (403, 422), missing.text
        for reason in ('x', 'xx', '   ', 'x'*501, 'reason\x00invalid'):
            invalid_reason = post(client, path, {'status': state, 'version': version, 'reason': reason})
            assert invalid_reason.status_code in (403, 422), invalid_reason.text
        key = uuid4().hex
        body = {'status': state, 'version': version, 'reason': 'Branch delayed; manager intervention'}
        result = post(client, path, body, key)
        assert result.status_code == 200, result.text
        replay = post(client, path, body, key)
        assert replay.status_code == 200 and replay.json() == result.json(), replay.text
        forbidden = post(client, path, body | {'status': 'cancelled'}, key)
        assert forbidden.status_code == 409, forbidden.text
    with db.tx() as c:
        task = c.execute('SELECT * FROM driver_tasks WHERE id=%s', (tid,)).fetchone()
        assert task['status'] == 'accepted' and task['picked_up_at'] is None
        assert not c.execute('SELECT 1 FROM collections WHERE order_id=%s', (oid,)).fetchone()


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_manager_requests_handover_without_impersonating_target_driver(morning_manager):
    client, ids, before = morning_manager
    own_second_driver()
    tid = assigned(client, ids)
    response = post(client, f'/api/tasks/{tid}/handover', {
        'driver_shift_id': 'ctx-other-driver', 'reason': 'Driver intervention test'})
    assert response.status_code == 200, response.text
    assert response.json()['acceptance_required'] is True
    assert post(client, f'/api/tasks/{tid}/accept-handover').status_code == 403
    assert post(client, f'/api/tasks/{tid}/transition', {'status': 'delivered'}).status_code in (403, 404, 405)
    with db.tx() as c:
        task = c.execute('SELECT * FROM driver_tasks WHERE id=%s', (tid,)).fetchone()
        assert task['driver_id'] == 'ops-driver' and task['shift_id'] == 'ctx-driver'
        assert task['status'] == 'assigned' and task['transfer_shift_id'] == 'ctx-other-driver'
        after = c.execute("SELECT * FROM users WHERE id='ops-manager'").fetchone()
        assert after['permission_denials'] == before['permission_denials']
        assert after['permissions'] == before['permissions']
        assert not c.execute('SELECT 1 FROM collections WHERE order_id=%s', (ids['morning'],)).fetchone()


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('revocation', ['target_parent', 'target_branch', 'target_period', 'target_inactive',
    'target_role', 'target_user_branch', 'source_parent', 'source_period', 'policy'])
def test_handover_saved_response_cannot_escape_fresh_scope(morning_manager, revocation):
    client, ids, _ = morning_manager
    own_second_driver(); tid = assigned(client, ids); key = uuid4().hex
    body = {'driver_shift_id': 'ctx-other-driver', 'reason': 'Fresh scope replay test'}
    path = f'/api/tasks/{tid}/handover'
    response = post(client, path, body, key)
    assert response.status_code == 200, response.text
    with db.tx() as c:
        if revocation == 'target_parent':
            c.execute("UPDATE shifts SET manager_shift_id='ctx-other-duty' WHERE id='ctx-other-driver'")
        elif revocation == 'target_branch':
            c.execute("UPDATE shifts SET branch_ids=%s WHERE id='ctx-other-driver'", (db.json([B3]),))
        elif revocation == 'target_period':
            c.execute("UPDATE shifts SET delivery_window_snapshot=%s WHERE id='ctx-other-driver'", (db.json({'period': 'evening'}),))
        elif revocation == 'target_inactive':
            c.execute("UPDATE users SET active=false WHERE id='ops-driver2'")
        elif revocation == 'target_role':
            c.execute("UPDATE users SET role='branch_manager' WHERE id='ops-driver2'")
        elif revocation == 'target_user_branch':
            c.execute("UPDATE users SET branch_ids=%s WHERE id='ops-driver2'", (db.json([B3]),))
        elif revocation == 'source_parent':
            c.execute("UPDATE shifts SET manager_shift_id='ctx-other-duty' WHERE id='ctx-driver'")
        elif revocation == 'source_period':
            c.execute("UPDATE shifts SET delivery_window_snapshot=%s WHERE id='ctx-driver'", (db.json({'period': 'evening'}),))
        else:
            c.execute("UPDATE brands SET settings=jsonb_set(settings,'{morning_delivery,manager_user_ids}','[]') WHERE id='kps'")
    response = post(client, path, body, key)
    assert response.status_code in (403, 404, 409), response.text


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('change', ['inactive', 'branch', 'role'])
def test_assignment_replay_rechecks_target_employee(morning_manager, change):
    client, ids, _ = morning_manager
    path = '/api/orders/'+ids['morning']+'/assign'; key = uuid4().hex
    body = {'driver_shift_id': 'ctx-driver'}
    result = post(client, path, body, key)
    assert result.status_code == 200, result.text
    with db.tx() as c:
        if change == 'inactive':
            c.execute("UPDATE users SET active=false WHERE id='ops-driver'")
        elif change == 'branch':
            c.execute("UPDATE users SET branch_ids=%s WHERE id='ops-driver'", (db.json([B3]),))
        else:
            c.execute("UPDATE users SET role='branch_manager' WHERE id='ops-driver'")
    response = post(client, path, body, key)
    assert response.status_code in (403, 404, 409), response.text


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_task_path_and_query_cannot_reveal_other_manager_even_on_same_branch(morning_manager):
    client, _, _ = morning_manager
    assert client.get('/api/tasks', params={'order_id': 'ctx-missing'}).status_code == 404
    assert client.get('/api/shifts', params={'task_id': 'ctx-bound-task'}).status_code == 404
    result = post(client, '/api/tasks/ctx-bound-task/handover', {
        'driver_shift_id': 'ctx-driver', 'reason': 'Cross manager attempt'})
    assert result.status_code == 404, result.text


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('change', ['period', 'branch', 'brand'])
def test_task_listing_excludes_children_whose_real_shift_scope_changed(morning_manager, change):
    client, ids, _ = morning_manager
    tid = assigned(client, ids)
    with db.tx() as c:
        if change == 'period':
            c.execute("UPDATE shifts SET delivery_window_snapshot=%s WHERE id='ctx-driver'", (db.json({'period': 'evening'}),))
        elif change == 'branch':
            c.execute("UPDATE shifts SET branch_ids=%s WHERE id='ctx-driver'", (db.json([B3]),))
        else:
            c.execute("UPDATE shifts SET brand_id='ops-other' WHERE id='ctx-driver'")
    response = client.get('/api/tasks')
    assert response.status_code in (200, 403), response.text
    if response.status_code == 200:
        assert tid not in {t['id'] for t in response.json()['tasks']}


def remove_duty():
    with db.tx() as c:
        c.execute("UPDATE shifts SET status='closed' WHERE id='ctx-duty'")
        c.execute('UPDATE branches SET settings=settings||%s WHERE id=%s', (db.json(_branch(B2)['settings']), B2))


def opening():
    return {'kind': 'manager', 'branch_ids': [B2], 'delivery_period': 'morning', 'opening_halalas': 0}


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_pending_context_only_self_opens_own_real_morning_duty(morning_manager):
    client, ids, before = morning_manager; remove_duty()
    session = client.get('/api/session')
    assert session.status_code == 200, session.text
    user = session.json()['user']
    assert user['delivery_context']['bootstrap'] is True
    assert user['delivery_context']['shift_id'] is None
    assert set(user['effective_permissions']) == {'shifts_view', 'shifts_open'}
    assert client.get('/api/orders').status_code == 403
    assert client.get('/api/tasks').status_code == 403
    assert client.get('/api/shifts', params={'order_id': ids['morning']}).status_code == 403
    assert post(client, '/api/orders/'+ids['morning']+'/assign', {'driver_shift_id': 'ctx-driver'}).status_code == 403
    response = post(client, '/api/shifts', opening())
    assert response.status_code == 200, response.text
    with db.tx() as c:
        shifts = c.execute("SELECT * FROM shifts WHERE user_id='ops-manager' AND status='open'").fetchall()
        assert len(shifts) == 1
        assert shifts[0]['kind'] == 'manager' and shifts[0]['branch_ids'] == [B2]
        assert shifts[0]['opening_halalas'] == 0
        assert shifts[0]['delivery_window_snapshot']['period'] == 'morning'
        after = c.execute("SELECT * FROM users WHERE id='ops-manager'").fetchone()
        assert employee_permissions.effective(after) == employee_permissions.effective(before)
    assert not client.get('/api/session').json()['user']['delivery_context'].get('bootstrap')


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_bootstrap_does_not_consume_existing_financial_carryover(morning_manager):
    client, _, _ = morning_manager; remove_duty()
    with db.tx() as c:
        c.execute("INSERT INTO retained_carryovers(id,from_shift_id,user_id,amount_halalas,approved_by,reason) "
                  "VALUES('bootstrap-existing-cash','ctx-duty','ops-manager',500,'ops-owner','Existing approved retained money')")
        before = c.execute("SELECT * FROM retained_carryovers WHERE id='bootstrap-existing-cash'").fetchone()
    response = post(client, '/api/shifts', opening())
    assert response.status_code in (403, 409), response.text
    with db.tx() as c:
        assert c.execute("SELECT * FROM retained_carryovers WHERE id='bootstrap-existing-cash'").fetchone() == before
        assert not c.execute("SELECT 1 FROM shifts WHERE user_id='ops-manager' AND status='open'").fetchone()


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('tamper', [
    {'kind': 'driver', 'user_id': 'ops-driver'}, {'user_id': 'context-other-manager'},
    {'delivery_period': 'evening'}, {'delivery_period': None}, {'branch_ids': [B3]},
    {'branch_ids': [B2, B3]}, {'opening_halalas': 100, 'reason': 'Not authorized cash'},
    {'manager_shift_id': 'ctx-other-duty'}, {'receipt_trial': True}])
def test_bootstrap_body_cannot_grant_other_shift_or_financial_authority(morning_manager, tamper):
    client, _, _ = morning_manager; remove_duty()
    response = post(client, '/api/shifts', opening() | tamper)
    assert response.status_code in (403, 409, 422), response.text
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM shifts WHERE user_id='ops-manager' AND status='open'").fetchone()


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('block', ['night', 'disabled', 'revoked'])
def test_self_open_never_bypasses_clock_or_owner_approval(morning_manager, monkeypatch, block):
    client, _, _ = morning_manager; remove_duty()
    if block == 'night':
        monkeypatch.setattr(service_hours, 'now_riyadh', lambda: at('20:00:00'))
    else:
        with db.tx() as c:
            path, value = ('enabled', False) if block == 'disabled' else ('manager_user_ids', [])
            c.execute("UPDATE brands SET settings=jsonb_set(settings,%s,%s) WHERE id='kps'",
                      (['morning_delivery', path], db.json(value)))
    response = post(client, '/api/shifts', opening())
    assert response.status_code in (403, 409), response.text

