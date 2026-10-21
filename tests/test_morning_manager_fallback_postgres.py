"""Scoped manager fallback and manual contact orders on actual PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import auth, db, orders, service_hours
from test_core import _authenticate, _branch
from test_morning_delivery import at
from test_morning_manager_context_postgres import B2, B3, morning_manager, post
from test_operations import ops

pytestmark = pytest.mark.postgres


def printer_and_new_order(ids, *, devices=1, approved=True):
    with db.tx() as c:
        # These tests isolate acceptance/print atomicity, not automatic dispatch.
        c.execute("UPDATE shifts SET available=false WHERE id='ctx-driver'")
        c.execute("UPDATE orders SET preparation_status='new' WHERE id=%s", (ids['morning'],))
        for _ in range(devices):
            c.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,token_hash,created_by,approved_at) "
                      "VALUES(%s,'kps',%s,'isolated printer',%s,'ops-owner',CASE WHEN %s THEN now() END)",
                      ('fallback-printer-'+uuid4().hex, B2, uuid4().hex, approved))


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_manager_fallback_queues_once_and_preparer_can_mark_ready(morning_manager):
    client, ids, _ = morning_manager
    printer_and_new_order(ids)
    oid = ids['morning']; path = f'/api/orders/{oid}/manager-accept'; key = uuid4().hex
    first = post(client, path, {'version': 1}, key)
    assert first.status_code == 200, first.text
    assert first.json()['printing']['status'] == 'queued'
    assert first.json()['order']['preparation_status'] == 'accepted'
    assert first.json()['order']['delivery_status'] == 'pending'
    assert post(client, path, {'version': 1}, key).json() == first.json()
    assert post(client, path, {'version': 1}).status_code == 409
    # Intervention never bypasses a driver's actual acceptance or the reason gate.
    denied = post(client, f'/api/orders/{oid}/preparation', {'status': 'ready', 'version': 2})
    assert denied.status_code in (403, 409, 422), denied.text
    with db.tx() as c:
        jobs = c.execute('SELECT * FROM branch_print_jobs WHERE order_id=%s', (oid,)).fetchall()
        assert len(jobs) == 1 and jobs[0]['branch_id'] == B2
        assert jobs[0]['requested_by'] == 'ops-manager' and jobs[0]['status'] == 'queued'
        event = c.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='preparation.accepted'", (oid,)).fetchall()
        assert len(event) == 1 and event[0]['details']['manager_shift_id'] == 'ctx-duty'
        assert not c.execute('SELECT 1 FROM driver_tasks WHERE order_id=%s', (oid,)).fetchone()
        preparer = c.execute("SELECT * FROM users WHERE id='ops-branch'").fetchone()
        driver = c.execute("SELECT * FROM users WHERE id='ops-driver'").fetchone()
        c.execute("UPDATE shifts SET available=true WHERE id='ctx-driver'")
    assignment = post(client, f'/api/orders/{oid}/assign', {'driver_shift_id': 'ctx-driver'})
    assert assignment.status_code == 200, assignment.text
    del client.headers['X-Delivery-Period']
    _authenticate(client, driver)
    accepted = post(client, '/api/tasks/'+assignment.json()['task']['id']+'/transition', {'status': 'accepted'})
    assert accepted.status_code == 200, accepted.text
    _authenticate(client, preparer)
    for state in ('preparing', 'ready'):
        with db.tx() as c:
            version = c.execute('SELECT version FROM orders WHERE id=%s', (oid,)).fetchone()['version']
        prepared = post(client, f'/api/orders/{oid}/preparation', {'status': state, 'version': version})
        assert prepared.status_code == 200, prepared.text
    assert prepared.json()['order']['preparation_status'] == 'ready'
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM branch_print_jobs WHERE order_id=%s', (oid,)).fetchone()['n'] == 1


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('devices,approved', [(0, True), (1, False), (2, True)])
def test_missing_unapproved_or_ambiguous_printer_rolls_back_manager_acceptance(morning_manager, devices, approved):
    client, ids, _ = morning_manager
    printer_and_new_order(ids, devices=devices, approved=approved)
    oid = ids['morning']
    result = post(client, f'/api/orders/{oid}/manager-accept', {'version': 1})
    assert result.status_code == 409 and result.json()['detail']['code'] == 'MORNING_PRINTER_NOT_READY', result.text
    with db.tx() as c:
        assert c.execute('SELECT preparation_status,version FROM orders WHERE id=%s', (oid,)).fetchone() == {'preparation_status': 'new', 'version': 1}
        assert not c.execute('SELECT 1 FROM branch_print_jobs WHERE order_id=%s', (oid,)).fetchone()
        assert not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='preparation.accepted'", (oid,)).fetchone()


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('label', ['evening', 'branch3', 'foreign', 'bound_other'])
def test_fallback_rejects_other_period_branch_brand_and_duty(morning_manager, label):
    client, ids, _ = morning_manager
    result = post(client, f'/api/orders/{ids[label]}/manager-accept', {'version': 1})
    assert result.status_code == 404, result.text


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_manager_and_preparer_concurrent_acceptance_queue_one_receipt(morning_manager):
    _, ids, principal = morning_manager
    printer_and_new_order(ids)
    oid = ids['morning']
    manager = {**principal, 'branch_ids': [B2], '_morning_manager_context': {'period': 'morning', 'shift_id': 'ctx-duty', 'branch_ids': [B2]}}
    with db.tx() as c:
        preparer = c.execute("SELECT * FROM users WHERE id='ops-branch'").fetchone()
    def accept(index):
        try:
            if index:
                orders.manager_accept(oid, orders.ManagerAcceptanceBody(version=1), user=manager, idempotency_key=uuid4().hex)
            else:
                orders.preparation(oid, orders.PreparationBody(status='accepted', version=1), user=preparer, idempotency_key=uuid4().hex)
            return 200
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(accept, range(2))) == [200, 409]
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM branch_print_jobs WHERE order_id=%s', (oid,)).fetchone()['n'] == 1
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='preparation.accepted'", (oid,)).fetchone()['n'] == 1


def manual_order_body():
    with db.tx() as c:
        c.execute('UPDATE branches SET settings=settings||%s WHERE id=%s', (db.json(_branch()['settings'] | {'location_verified': True}), B2))
        c.execute("INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES('fallback-coffee','kps','قهوة تجربة','تجربة',3000,%s)",
                  (db.json({'source_verified': True, 'enabled': True, 'option_groups': []}),))
    return {'brand_id': 'kps', 'branch_id': B2, 'service': 'delivery', 'payment_method': 'cash',
            'items': [{'product_id': 'fallback-coffee', 'quantity': 1}],
            'customer': {'name': 'عميل واتساب تجريبي', 'phone': '+966512345678', 'neighborhood': 'حي اختبار',
                         'address': 'عنوان اختبار', 'location': {'lat': 28.4, 'lng': 45.9}}}


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_manual_customer_order_has_morning_scope_guest_identity_and_safe_replay(morning_manager):
    client, _, _ = morning_manager
    body = manual_order_body(); key = uuid4().hex
    first = post(client, '/api/orders/delivery-center', body, key)
    assert first.status_code == 200, first.text
    assert post(client, '/api/orders/delivery-center', body, key).json() == first.json()
    oid = first.json()['order']['id']
    with db.tx() as c:
        saved = c.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
        assert saved['branch_id'] == B2 and saved['snapshot']['delivery_period'] == 'morning'
        assert saved['snapshot']['manager_shift_id'] == 'ctx-duty'
        assert saved['snapshot']['order_source'] == 'delivery_center'
        assert saved['customer_id'].startswith('customer_contact_')
        assert not saved['customer']['phone_sms_verified'] and not saved['customer']['phone_whatsapp_verified']
        c.execute("UPDATE brands SET settings=jsonb_set(settings,'{morning_delivery,manager_user_ids}','[]') WHERE id='kps'")
    assert post(client, '/api/orders/delivery-center', body, key).status_code == 403


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_manual_customer_creation_cannot_escape_to_evening_or_another_branch(morning_manager, monkeypatch):
    client, _, _ = morning_manager
    body = manual_order_body()
    assert post(client, '/api/orders/delivery-center', body | {'branch_id': B3}).status_code == 403
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: at('18:00:00'))
    result = post(client, '/api/orders/delivery-center', body)
    assert result.status_code == 403 and result.json()['detail']['code'] == 'MORNING_CUSTOMER_ORDER_SCOPE', result.text
    # No added customer-history search or financial concessions in this context.
    assert client.get('/api/orders/customer-lookup', params={'branch_id': B2, 'phone': '+966512345678'}).status_code == 403
    assert post(client, '/api/orders/complimentary', body | {'kind': 'hospitality', 'reason': 'test'}).status_code == 403


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_existing_evening_response_cannot_replay_through_morning_context(morning_manager, monkeypatch):
    client, _, _ = morning_manager
    body = manual_order_body(); key = uuid4().hex
    del client.headers['X-Delivery-Period']
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: at('18:00:00'))
    first = post(client, '/api/orders/delivery-center', body, key)
    assert first.status_code == 200, first.text
    oid = first.json()['order']['id']
    client.headers['X-Delivery-Period'] = 'morning'
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: at('08:00:00'))
    replay = post(client, '/api/orders/delivery-center', body, key)
    assert replay.status_code == 404, replay.text
    assert oid not in replay.text
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM orders WHERE snapshot->>'created_by_staff_id'='ops-manager'").fetchone()['n'] == 1


@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_morning_creation_replay_after_window_closes_is_rejected_before_response(morning_manager, monkeypatch):
    client, _, _ = morning_manager
    body = manual_order_body(); key = uuid4().hex
    first = post(client, '/api/orders/delivery-center', body, key)
    assert first.status_code == 200, first.text
    oid = first.json()['order']['id']
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: at('18:00:00'))
    replay = post(client, '/api/orders/delivery-center', body, key)
    assert replay.status_code == 403 and replay.json()['detail']['code'] == 'MORNING_CUSTOMER_ORDER_SCOPE', replay.text
    assert oid not in replay.text
