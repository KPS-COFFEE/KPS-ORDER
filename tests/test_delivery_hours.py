"""Approved Riyadh boundaries and real order/custody flows after cutoff."""
from copy import deepcopy
from datetime import datetime, timezone
import os
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import catalog, db, service_hours
from app.exports import build_archive, restore_archive
from test_operations import ops, close_driver


def local(value):
    return datetime.fromisoformat(value).replace(tzinfo=service_hours.RIYADH)


def configured_branch():
    return {'id': 'ops-branch', 'brand_id': 'ops-brand', 'city': 'حفر الباطن',
            'settings': {'active': True, 'pickup_enabled': True, 'car_enabled': True,
                         'delivery_enabled': True, 'operational_data_verified': True,
                         'opening_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
                         'delivery_hours': deepcopy(service_hours.DEFAULT_DELIVERY_HOURS),
                         'payment_methods': ['cash', 'card'], 'delivery_fee_halalas': 0,
                         'location': {'lat': 28, 'lng': 45}, 'location_verified': True,
                         'coverage': {'type': 'radius', 'center': {'lat': 28, 'lng': 45}, 'radius_meters': 10000}}}


@pytest.mark.parametrize(('stamp', 'opened', 'day'), [
    ('2026-10-10T03:59:00', True, '2026-10-09'),
    ('2026-10-10T03:59:59', True, '2026-10-09'),
    ('2026-10-10T04:00:00', False, '2026-10-09'),
    ('2026-10-10T15:59:00', False, '2026-10-09'),
    ('2026-10-10T15:59:59', False, '2026-10-09'),
    ('2026-10-10T16:00:00', True, '2026-10-10'),
    ('2026-10-10T23:59:59', True, '2026-10-10'),
    ('2026-10-11T00:00:00', True, '2026-10-10'),
    ('2026-10-11T03:59:00', True, '2026-10-10'),
])
def test_approved_boundaries_and_midnight_are_riyadh(stamp, opened, day):
    now = local(stamp)
    status = service_hours.hours_status(service_hours.DEFAULT_DELIVERY_HOURS, now)
    assert status['open'] is opened
    assert status['operational_date'] == day
    assert status['timezone'] == 'Asia/Riyadh'
    branch = configured_branch()
    allowed, code, reason = catalog.branch_service(branch, 'delivery', now)
    assert allowed is opened
    if not opened:
        assert code == 'DELIVERY_CLOSED'
        assert 'يبدأ استقبال الطلبات الساعة 4 مساءً' in reason
        assert status['next_open_at'] == '2026-10-10T16:00:00+03:00'
    # Delivery admission never imposes its clock on pickup or car pickup.
    assert catalog.branch_service(branch, 'pickup', now)[0]
    assert catalog.branch_service(branch, 'car', now)[0]


def test_utc_input_and_owner_custom_weekday_hours():
    assert service_hours.hours_open(service_hours.DEFAULT_DELIVERY_HOURS,
                                    datetime(2026, 10, 10, 0, 59, tzinfo=timezone.utc))
    assert not service_hours.hours_open(service_hours.DEFAULT_DELIVERY_HOURS,
                                        datetime(2026, 10, 10, 1, tzinfo=timezone.utc))
    friday_only = [{'days': [4], 'start': '17:00', 'end': '02:00'}]
    assert service_hours.hours_open(friday_only, local('2026-10-10T01:59:00'))
    state = service_hours.hours_status(friday_only, local('2026-10-10T02:00:00'))
    assert not state['open']
    assert state['next_open_at'] == '2026-10-16T17:00:00+03:00'
    assert '5:00 مساءً' in service_hours.closed_delivery_message(friday_only, local('2026-10-10T02:00:00'))


def test_invalid_hours_fail_closed_and_equal_endpoints_preserve_calendar_contract():
    for hours in (None, [], [{'days': [True], 'start': '16:00', 'end': '04:00'}],
                  [{'days': [0], 'start': '25:00', 'end': '04:00'}]):
        assert not service_hours.hours_status(hours, local('2026-10-10T16:00:00'))['configured']
        assert not service_hours.hours_open(hours, local('2026-10-10T16:00:00'))
    full_day = [{'days': [5], 'start': '16:00', 'end': '16:00'}]
    assert service_hours.hours_open(full_day, local('2026-10-10T01:00:00'))
    assert not service_hours.hours_open(full_day, local('2026-10-11T01:00:00'))
    monday_full_day = [{'days': [0], 'start': '09:00', 'end': '09:00'}]
    assert service_hours.hours_open(monday_full_day, local('2026-10-12T02:00:00'))
    assert service_hours.hours_open(monday_full_day, local('2026-10-12T23:00:00'))
    assert not service_hours.hours_open(monday_full_day, local('2026-10-13T02:00:00'))


@pytest.fixture
def delivery_ops(ops, monkeypatch):
    actor, post, order, shifts = ops
    clock = [local('2026-10-10T16:00:00')]
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: clock[0])
    with db.tx() as c:
        c.execute("UPDATE branches SET settings=%s WHERE id='ops-branch'", (db.json(configured_branch()['settings']),))
        c.execute("INSERT INTO products(id,brand_id,name,category,price_halalas,settings) "
                  "VALUES('ops-product','ops-brand','TEST delivery hours coffee','TEST',1000,%s)",
                  (db.json({'enabled': True, 'source_verified': True}),))
    body = {'brand_id': 'ops-brand', 'branch_id': 'ops-branch', 'service': 'delivery', 'payment_method': 'cash',
            'items': [{'product_id': 'ops-product', 'quantity': 1}],
            'customer': {'name': 'TEST ONLY', 'phone': '+966500000000', 'neighborhood': 'TEST حي',
                         'address': 'TEST address', 'location': {'lat': 28, 'lng': 45}}}
    return actor, post, order, shifts, clock, body


@pytest.mark.postgres
@pytest.mark.parametrize(('stamp', 'allowed'), [('2026-10-10T03:59:00', True),
                                             ('2026-10-10T04:00:00', False),
                                             ('2026-10-10T15:59:00', False),
                                             ('2026-10-10T16:00:00', True),
                                             ('2026-10-11T00:00:00', True)])
def test_manager_fresh_shift_and_customer_quote_boundaries(delivery_ops, stamp, allowed):
    actor, post, _, _, clock, body = delivery_ops
    clock[0] = local(stamp)
    response = actor('customer').post('/api/quote', json=body)
    assert response.status_code == (200 if allowed else 409), response.text
    started = post('manager', '/api/shifts', {'kind': 'manager', 'branch_ids': ['ops-branch']},
                   status=200 if allowed else 409)
    if allowed:
        assert started['shift']['delivery_operational_date'] == service_hours.delivery_operational_date(clock[0]).isoformat()
    else:
        assert '4 مساءً' in started['detail']


@pytest.mark.postgres
@pytest.mark.parametrize('stamp', ['2026-10-10T04:00:00', '2026-10-10T15:59:00'])
def test_driver_fresh_shift_cannot_start_outside_window(delivery_ops, stamp):
    _, post, _, _, clock, _ = delivery_ops
    manager = post('manager', '/api/shifts', {'kind': 'manager', 'branch_ids': ['ops-branch']})['shift']['id']
    clock[0] = local(stamp)
    post('driver', '/api/shifts', {'kind': 'driver', 'branch_ids': ['ops-branch'], 'manager_shift_id': manager}, status=409)
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM shifts WHERE user_id='ops-driver'").fetchone()['n'] == 0


@pytest.mark.postgres
def test_pre_cutoff_order_finishes_and_custody_closes_after_cutoff(delivery_ops):
    actor, post, _, shifts, clock, body = delivery_ops
    clock[0] = local('2026-10-10T03:59:00')
    manager, driver = shifts()
    created = post('customer', '/api/orders', body)['order']
    oid = created['id']
    assert created['delivery_operational_date'] == '2026-10-09'
    assert created['snapshot']['delivery_operating_day']['date'] == '2026-10-09'
    task = post('manager', '/api/orders/' + oid + '/assign', {'driver_shift_id': driver})['task']['id']
    post('driver', '/api/tasks/' + task + '/transition', {'status': 'accepted'})
    clock[0] = local('2026-10-10T04:00:00')
    blocked = post('customer', '/api/orders', body, status=409)
    assert blocked['detail']['code'] == 'DELIVERY_CLOSED'
    # A new-admission cutoff cannot cancel or freeze a previously accepted order.
    for state in ('accepted', 'preparing', 'ready', 'handed_over'):
        current = actor('branch').get('/api/orders/' + oid).json()['order']
        post('branch', '/api/orders/' + oid + '/preparation', {'status': state, 'version': current['version']})
    post('driver', '/api/tasks/' + task + '/transition', {'status': 'picked_up'})
    post('driver', '/api/orders/' + oid + '/collect', {'method': 'cash', 'amount_halalas': 1000, 'tendered_halalas': 1000})
    post('driver', '/api/tasks/' + task + '/transition', {'status': 'delivered', 'proof': 'TEST after cutoff receipt'})
    post('manager', '/api/shifts/' + manager + '/closing')
    post('manager', '/api/shifts/' + manager + '/submit', {'counted_halalas': 1000, 'handover_halalas': 1000}, status=409)
    driver_report = close_driver(post, driver, manager)
    post('manager', '/api/shifts/' + manager + '/submit', {'counted_halalas': 1000, 'handover_halalas': 1000})
    transfer = post('manager', '/api/custody', {'from_shift_id': manager, 'recipient_user_id': 'ops-owner',
                                             'amount_halalas': 1000, 'reference': 'TEST-HOURS-FINAL', 'closure': True})['transfer']['id']
    post('owner', '/api/shifts/' + manager + '/approve', status=409)
    post('owner', '/api/custody/' + transfer + '/confirm')
    final = post('owner', '/api/shifts/' + manager + '/approve')
    for report_id in (driver_report['report_id'], final['report_id']):
        report = actor('owner').get('/api/reports/' + report_id).json()['report']
        assert report['snapshot']['operational_date'] == '2026-10-09'
        assert report['snapshot']['summary']['cash_collected_halalas'] == 1000
    with db.tx() as c:
        before = c.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
        assert before['delivery_status'] == 'delivered' and before['payment_status'] == 'paid'
        assert c.execute('SELECT status FROM shifts WHERE id=%s', (manager,)).fetchone()['status'] == 'closed'
    db.migrate()
    with db.tx() as c:
        assert c.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone() == before


@pytest.mark.postgres
def test_owner_branch_hours_override_pause_and_independent_pickup(delivery_ops):
    actor, post, _, _, clock, body = delivery_ops
    # Exercise the real owner PATCH route; overrides are not overwritten by boot.
    custom = [{'days': list(range(7)), 'start': '18:00', 'end': '02:00'}]
    response = actor('owner').patch('/api/admin/branches/ops-branch',
                                    json={'settings': {'delivery_hours': custom}},
                                    headers={'Idempotency-Key': uuid4().hex})
    assert response.status_code == 200, response.text
    assert actor('customer').post('/api/quote', json=body).json()['detail']['code'] == 'DELIVERY_CLOSED'
    clock[0] = local('2026-10-10T18:00:00')
    assert actor('customer').post('/api/quote', json=body).status_code == 200
    response = actor('owner').patch('/api/admin/branches/ops-branch',
                                    json={'settings': {'delivery_enabled': False}},
                                    headers={'Idempotency-Key': uuid4().hex})
    assert response.status_code == 200, response.text
    assert actor('customer').post('/api/quote', json=body).json()['detail']['code'] == 'SERVICE_DISABLED'
    post('manager', '/api/shifts', {'kind': 'manager', 'branch_ids': ['ops-branch']}, status=409)
    for service in ('pickup', 'car'):
        independent = deepcopy(body)
        independent['service'] = service
        independent['customer']['car'] = {'plate': 'TEST', 'color': 'black'}
        assert actor('customer').post('/api/quote', json=independent).status_code == 200
    db.migrate()
    with db.tx() as c:
        settings = c.execute("SELECT settings FROM branches WHERE id='ops-branch'").fetchone()['settings']
        assert settings['delivery_hours'] == custom and settings['delivery_enabled'] is False


@pytest.mark.postgres
def test_delivery_dates_windows_and_cleared_owner_hours_survive_export_restore(delivery_ops):
    _, post, _, shifts, clock, body = delivery_ops
    manager, driver = shifts()
    clock[0] = local('2026-10-11T01:00:00')
    created = post('customer', '/api/orders', body)['order']
    with db.tx() as c:
        c.execute("UPDATE branches SET settings=jsonb_set(settings,'{delivery_hours}','[]'::jsonb) WHERE id='kps-branch-1'")
    db.migrate()
    with db.tx() as c:
        before_order = c.execute('SELECT * FROM orders WHERE id=%s', (created['id'],)).fetchone()
        before_shifts = c.execute('SELECT * FROM shifts WHERE id=ANY(%s) ORDER BY id', ([manager, driver],)).fetchall()
        assert before_order['delivery_operational_date'].isoformat() == '2026-10-10'
        assert all(s['delivery_operational_date'].isoformat() == '2026-10-10' for s in before_shifts)
        assert c.execute("SELECT settings FROM branches WHERE id='kps-branch-1'").fetchone()['settings']['delivery_hours'] == []
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        contents, manifest = build_archive(c, full=True)
    assert manifest['counts']['delivery_hours_decisions'] > 0
    target = 'kps_restore_' + uuid4().hex
    base_url = os.environ['TEST_DATABASE_URL']
    try:
        restored = restore_archive(contents, target_url=base_url, target_schema=target)
        assert restored['verified']
        with psycopg.connect(base_url, row_factory=dict_row) as c:
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
            # A subsequent normal boot must preserve the restored owner clear.
            db.apply_migrations(c, seed=False)
            assert c.execute('SELECT * FROM orders WHERE id=%s', (created['id'],)).fetchone() == before_order
            assert c.execute('SELECT * FROM shifts WHERE id=ANY(%s) ORDER BY id', ([manager, driver],)).fetchall() == before_shifts
            assert c.execute("SELECT settings FROM branches WHERE id='kps-branch-1'").fetchone()['settings']['delivery_hours'] == []
    finally:
        assert target.startswith('kps_restore_') and len(target) == len('kps_restore_') + 32
        with psycopg.connect(base_url, autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(target)))


@pytest.mark.postgres
def test_default_only_eligible_branches_once_and_never_enables_service(client):
    with db.tx() as c:
        branches = c.execute("SELECT * FROM branches WHERE brand_id='kps'").fetchall()
        eligible = [b for b in branches if catalog.normalize_city(b['city']) in catalog.DELIVERY_CITIES]
        excluded = [b for b in branches if b not in eligible]
        assert len(branches) == 20 and eligible and excluded
        for branch in eligible:
            assert branch['settings']['delivery_hours'] == service_hours.DEFAULT_DELIVERY_HOURS
            assert branch['settings']['delivery_enabled'] is False
        for branch in excluded:
            assert not branch['settings'].get('delivery_hours')
        bid = eligible[0]['id']
        c.execute("UPDATE branches SET settings=jsonb_set(settings,'{delivery_hours}','[]'::jsonb) WHERE id=%s", (bid,))
    db.migrate()
    with db.tx() as c:
        assert c.execute('SELECT settings FROM branches WHERE id=%s', (bid,)).fetchone()['settings']['delivery_hours'] == []
        assert c.execute('SELECT count(*) n FROM delivery_hours_decisions').fetchone()['n'] == len(eligible)
