"""Focused executable tests; PostgreSQL tests require the isolated fixture DSN."""
from __future__ import annotations

import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import psycopg
import pytest
from fastapi import HTTPException

from app import auth, catalog, db, orders


def _branch(branch_id='kps-branch-1', city='حفر الباطن', **settings):
    return {'id': branch_id, 'brand_id': 'kps', 'city': city,
            'settings': {'active': True, 'pickup_enabled': True, 'car_enabled': True,
                         'delivery_enabled': True, 'ordering_verified': True,
                         'opening_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
                         'delivery_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
                         'payment_methods': ['cash', 'card'], 'delivery_fee_halalas': 500,
                         'location': {'lat': 28.4, 'lng': 45.9},
                         'coverage': {'type': 'radius', 'center': {'lat': 28.4, 'lng': 45.9}, 'radius_meters': 2000},
                         **settings}}


def _product(pid='kps-33', **settings):
    return {'id': pid, 'brand_id': 'kps', 'name': 'اختبار', 'price_halalas': 1950,
            'settings': {'enabled': True, 'source_verified': True, **settings}}


@pytest.mark.parametrize(('value', 'normalized'), [('حفرالباطن', 'حفر الباطن'), ('حفر الباطن', 'حفر الباطن'),
                                                 ('رفحا', 'رفحاء'), ('رفحاء', 'رفحاء'), ('الدمام', 'الدمام')])
def test_safe_city_aliases(value, normalized):
    assert catalog.normalize_city(value) == normalized


@pytest.mark.parametrize('pid', ['kps-33', 'kps-34'])
@pytest.mark.parametrize('city', ['الذيبية', 'القيصومة', 'الخفجي', 'رفحاء', 'قبه', 'الدمام'])
def test_initial_khaliya_city_rule_both_original_products(pid, city):
    assert catalog.product_availability(_product(pid), _branch(city=city))[0] is False
    assert catalog.product_availability(_product(pid), _branch())[0] is True
    assert catalog.product_availability(_product(pid), None)[0] is False


def test_city_policy_admin_revision_and_branch_stop_are_independent():
    product = _product('other-product', available_cities=['رفحاء'], availability_policy_version=1)
    assert catalog.product_availability(product, _branch(city='رفحا'))[0] is True
    assert catalog.product_availability(product, _branch())[0] is False
    stopped = _product(unavailable_branch_ids=['kps-branch-1'])
    assert catalog.product_availability(stopped, _branch())[0] is False
    # The final owner instruction keeps both Khaliya source IDs Hafar-only;
    # other products retain the existing explicit configurable city policy.
    khaliya = _product(available_cities=['رفحاء'], availability_policy_version=1)
    assert catalog.product_availability(khaliya, _branch(city='رفحا'))[0] is False
    assert catalog.product_availability(khaliya, _branch())[0] is True


def test_delivery_city_guard_does_not_disable_independent_pickup():
    branch = _branch(city='الدمام')
    assert catalog.branch_service(branch, 'delivery')[1] == 'DELIVERY_CITY_NOT_ELIGIBLE'
    assert catalog.branch_service(branch, 'pickup')[0] is True
    assert catalog.branch_service(_branch(ordering_verified=False), 'pickup')[1] == 'BRANCH_CONFIGURATION_UNVERIFIED'


def test_overnight_hours_use_prior_riyadh_day_and_separate_delivery_hours():
    hours = [{'days': [4], 'start': '16:00', 'end': '04:00'}]  # Friday into Saturday.
    assert catalog._hours_open(hours, datetime(2026, 10, 10, 2, tzinfo=catalog.RIYADH))
    assert not catalog._hours_open(hours, datetime(2026, 10, 10, 4, tzinfo=catalog.RIYADH))
    assert not catalog._hours_open(hours, datetime(2026, 10, 11, 2, tzinfo=catalog.RIYADH))
    branch = _branch(delivery_hours=[])
    assert catalog.branch_service(branch, 'pickup')[0]
    assert catalog.branch_service(branch, 'delivery')[1] == 'DELIVERY_HOURS_UNCONFIGURED'


def test_coverage_never_uses_city_or_guessed_coordinates():
    polygon = {'type': 'polygon', 'coordinates': [[45.8, 28.3], [46.0, 28.3], [46.0, 28.5], [45.8, 28.5]]}
    assert catalog.covered(polygon, {'lat': 28.4, 'lng': 45.9})
    assert not catalog.covered(polygon, {'lat': 28.8, 'lng': 45.9})
    assert catalog.covered(polygon, {'lat': 28.3, 'lng': 45.9})
    assert not catalog.covered({}, {'lat': 28.4, 'lng': 45.9})
    assert not catalog.valid_location({'lat': True, 'lng': 45.9})
    assert not catalog.valid_location({'lat': float('nan'), 'lng': 45.9})
    radius = _branch()['settings']['coverage']
    assert catalog.covered(radius, {'lat': 28.4, 'lng': 45.9})
    assert not catalog.covered(radius, {'lat': 28.8, 'lng': 45.9})


def test_per_unit_color_options_are_preserved_and_never_trusted_prices():
    product = _product('cup', option_groups=[{'code': 'COLOR', 'name': 'اللون', 'type': 'color', 'required': True,
                                            'values': [{'code': 'BLK', 'name': 'أسود', 'price_delta_halalas': 0},
                                                       {'code': 'PNK', 'name': 'وردي', 'price_delta_halalas': 100}]}])
    item = catalog.OrderItem(product_id='cup', quantity=2, units=[{'options': {'COLOR': 'BLK'}}, {'options': {'COLOR': 'PNK'}}])
    total, snapshots = catalog._price_units(product, item)
    assert total == 4000
    assert [unit['options']['COLOR'] for unit in snapshots] == ['BLK', 'PNK']
    assert snapshots[1]['option_snapshot']['COLOR']['choices'][0]['name'] == 'وردي'
    with pytest.raises(HTTPException) as exc:
        catalog._price_units(product, catalog.OrderItem(product_id='cup', quantity=2))
    assert exc.value.detail['code'] == 'UNIT_OPTIONS_REQUIRED'
    with pytest.raises(HTTPException):
        catalog._price_units(product, catalog.OrderItem(product_id='cup', quantity=1, units=[{'options': {'COLOR': 'UNKNOWN'}}]))


def test_password_hashes_are_salted_and_validated_without_default_credentials():
    encoded = auth.hash_password('TestOnly!LongPassword1')
    assert encoded != auth.hash_password('TestOnly!LongPassword1')
    assert auth.verify_password('TestOnly!LongPassword1', encoded)
    assert not auth.verify_password('incorrect', encoded)
    with pytest.raises(HTTPException):
        auth.hash_password('short')


def _prepare_catalog():
    with db.tx() as conn:
        for bid in ('kps-branch-1', 'kps-branch-5', 'kps-branch-7'):
            conn.execute('UPDATE branches SET settings=%s WHERE id=%s', (db.json(_branch(bid)['settings']), bid))
        conn.execute("UPDATE products SET settings=settings||'{\"source_verified\":true}'::jsonb WHERE id IN ('kps-01','kps-30','kps-33','kps-34')")


def _make_user(role='customer', brand='kps', branches=None, username=None, password=None):
    identifier = db.new_id('test_')
    with db.tx() as conn:
        conn.execute('INSERT INTO users(id,role,brand_id,branch_ids,username,phone,password_hash,display_name) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                     (identifier, role, brand, db.json(branches or []), username,
                      '+9665' + f'{secrets.randbelow(100_000_000):08d}' if role == 'customer' else None,
                      auth.hash_password(password) if password else None, 'بيانات اختبار'))
        return conn.execute('SELECT * FROM users WHERE id=%s', (identifier,)).fetchone()


def _authenticate(client, user):
    # Match the UI context on shared API routes while preserving separate jars.
    client.headers['X-KPS-Context'] = 'customer' if user['role'] == 'customer' else 'work'
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with db.tx() as conn:
        conn.execute('INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES(%s,%s,%s,%s,now()+interval \'1 hour\')',
                     (db.new_id('s_'), auth.token_hash(token), auth.token_hash(csrf), user['id']))
    client.cookies.clear()
    client.cookies.set(auth.COOKIE, token)
    client.cookies.set(auth.CSRF_COOKIE, csrf)
    client.headers['X-CSRF-Token'] = csrf
    return token, csrf


def _body(service='pickup', branch='kps-branch-1', product='kps-01'):
    return {'brand_id': 'kps', 'branch_id': branch, 'service': service, 'payment_method': 'cash',
            'items': [{'product_id': product, 'quantity': 2}],
            'customer': {'name': 'اختبار فقط', 'neighborhood': 'حي اختبار', 'address': 'عنوان اختبار', 'location': {'lat': 28.4, 'lng': 45.9},
                         'car': {'plate': 'TEST-ONLY', 'color': 'أبيض', 'model': 'اختبار'}}}


def _create(client, body=None, key='test-order-0001'):
    return client.post('/api/orders', json=body or _body(), headers={'Idempotency-Key': key})


@pytest.mark.postgres
class TestPostgreSQLCore:
    def test_original_twenty_branches_owner_approved_menu_and_unconfigured_operations(self, client):
        branches = client.get('/api/branches').json()['branches']
        assert [branch['branch_no'] for branch in branches] == list(range(1, 21))
        assert len({branch['id'] for branch in branches}) == 20
        assert all(not branch['services']['delivery']['available'] for branch in branches)
        catalog_response = client.get('/api/catalog').json()
        assert len(catalog_response['products']) == 31
        assert catalog_response['source_verified'] is True
        assert client.get('/ready').json()['ordering_configured'] is False

    def test_anonymous_cannot_create_or_read_orders_even_with_typed_phone(self, client):
        response = _create(client)
        assert response.status_code == 401
        assert client.get('/api/orders').status_code == 401
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) AS n FROM orders').fetchone()['n'] == 0

    def test_quote_source_city_coverage_and_price_guard(self, client):
        _prepare_catalog()
        with db.tx() as conn:
            conn.execute("UPDATE products SET settings=settings||'{\"source_verified\":false}'::jsonb WHERE id='kps-01'")
        response = client.post('/api/quote', json=_body())
        assert response.status_code == 409 and response.json()['detail']['code'] == 'CATALOG_SOURCE_UNVERIFIED'
        response = client.post('/api/quote', json=_body('pickup', 'kps-branch-5', 'kps-33'))
        assert response.status_code == 409 and response.json()['detail']['code'] == 'PRODUCT_UNAVAILABLE'
        response = client.post('/api/quote', json=_body('delivery', 'kps-branch-7'))
        assert response.status_code == 409 and response.json()['detail']['code'] == 'DELIVERY_CITY_NOT_ELIGIBLE'
        outside = _body('delivery')
        outside['customer']['location']['lat'] = 28.8
        response = client.post('/api/quote', json=outside)
        assert response.status_code == 409 and response.json()['detail']['code'] == 'OUTSIDE_COVERAGE'
        _authenticate(client, _make_user())
        with db.tx() as conn:
            conn.execute("UPDATE products SET settings=settings||'{\"source_verified\":true}'::jsonb WHERE id='kps-01'")
        changed = _body()
        changed['expected_total_halalas'] = 1
        response = _create(client, changed)
        assert response.status_code == 409 and response.json()['detail']['code'] == 'PRICE_CHANGED'

    def test_order_snapshot_replay_conflict_and_restart_keep_history(self, client):
        _prepare_catalog()
        user = _make_user()
        _authenticate(client, user)
        response = _create(client)
        assert response.status_code == 200
        order = response.json()['order']
        replay = _create(client).json()['order']
        assert replay == order
        different = _body()
        different['items'][0]['quantity'] = 3
        assert _create(client, different).status_code == 409
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) AS n FROM orders').fetchone()['n'] == 1
            assert conn.execute("SELECT count(*) AS n FROM order_events WHERE event_type='order.created'").fetchone()['n'] == 1
            conn.execute("UPDATE products SET price_halalas=9999,settings=settings||'{\"enabled\":false}'::jsonb WHERE id='kps-01'")
        db.migrate()
        restored = client.get('/api/orders/' + order['id']).json()['order']
        assert restored['snapshot'] == order['snapshot']
        assert restored['total_halalas'] == 1900
        with db.tx() as conn:
            product = conn.execute("SELECT price_halalas,settings FROM products WHERE id='kps-01'").fetchone()
            assert product['price_halalas'] == 9999 and product['settings']['enabled'] is False

    def test_concurrent_same_key_commits_one_order(self, client):
        _prepare_catalog()
        user = _make_user()
        parsed = catalog.QuoteBody.model_validate(_body())
        def create():
            return orders.create_order(parsed, user=user, idempotency_key='parallel-order-001')['order']['id']
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: create(), range(4)))
        assert len(set(ids)) == 1
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) AS n FROM orders').fetchone()['n'] == 1

    def test_session_csrf_origin_revocation_and_password_login(self, client, monkeypatch):
        user = _make_user('branch_manager', branches=['kps-branch-1'], username='staff.test', password='LongTestPassword!1')
        login = client.post('/api/auth/login', json={'username': 'STAFF.TEST', 'password': 'LongTestPassword!1'})
        assert login.status_code == 403 and login.json()['detail']['code'] == 'WORK_SMS_REQUIRED'
        assert client.get('/api/session').json()['user'] is None
        from tests.sms_policy_helpers import sms_login
        login = sms_login(client, user, monkeypatch)
        csrf = login['csrf_token']
        client.headers.pop('X-CSRF-Token')
        assert client.get('/api/session').json()['user']['id'] == user['id']
        assert client.post('/api/auth/logout').status_code == 403
        assert client.post('/api/auth/logout', headers={'X-CSRF-Token': csrf, 'Origin': 'https://other.invalid'}).status_code == 403
        client.headers.pop('Origin')
        assert client.post('/api/auth/logout', headers={'X-CSRF-Token': csrf}).status_code == 403
        client.headers['Origin'] = 'http://testserver'
        with db.tx() as conn:
            conn.execute('UPDATE users SET active=false WHERE id=%s', (user['id'],))
        assert client.get('/api/orders').status_code == 401
        assert client.get('/api/session').json()['user'] is None

    def test_customer_branch_brand_delivery_and_driver_identifier_isolation(self, client):
        _prepare_catalog()
        owner_customer = _make_user()
        _authenticate(client, owner_customer)
        order = _create(client).json()['order']
        for user in [_make_user(), _make_user('branch_manager', branches=['kps-branch-5']), _make_user('delivery_manager'),
                     _make_user('driver', branches=['kps-branch-1'])]:
            _authenticate(client, user)
            assert client.get('/api/orders/' + order['id']).status_code == 404
            assert client.get('/api/orders').json()['orders'] == []
        with db.tx() as conn:
            conn.execute("INSERT INTO brands(id,name) VALUES('test-other','اختبار')")
        other_owner = _make_user('owner', brand='test-other')
        _authenticate(client, other_owner)
        assert client.get('/api/orders/' + order['id']).status_code == 404
        allowed_branch = _make_user('branch_manager', branches=['kps-branch-1'])
        _authenticate(client, allowed_branch)
        assert client.get('/api/orders/' + order['id']).status_code == 200

    def test_branch_stock_stop_is_durable_and_cannot_expand_city_policy(self, client):
        _prepare_catalog()
        _authenticate(client, _make_user('branch_manager', branches=['kps-branch-1', 'kps-branch-5']))
        path = '/api/branches/kps-branch-1/products/kps-33/availability'
        response = client.post(path, json={'available': False, 'reason': 'اختبار نفاد'}, headers={'Idempotency-Key': 'stock-stop-test001'})
        assert response.status_code == 200 and not response.json()['available']
        assert client.post('/api/quote', json=_body(product='kps-33')).status_code == 409
        db.migrate()
        assert not client.get('/api/products/kps-33/availability?branch_id=kps-branch-1').json()['available']
        response = client.post('/api/branches/kps-branch-5/products/kps-33/availability', json={'available': True}, headers={'Idempotency-Key': 'stock-enable-test001'})
        assert response.status_code == 200 and not response.json()['available']
        denied = client.post('/api/branches/kps-branch-7/products/kps-33/availability', json={'available': False}, headers={'Idempotency-Key': 'stock-other-test001'})
        assert denied.status_code == 404

    def test_preparation_replay_reauthorizes_branch_after_employee_scope_revocation(self, client):
        _prepare_catalog()
        customer = _make_user()
        _authenticate(client, customer)
        order = _create(client).json()['order']
        branch = _make_user('branch_manager', branches=['kps-branch-1'])
        _authenticate(client, branch)
        path = '/api/orders/' + order['id'] + '/preparation'
        body = {'status': 'accepted', 'version': 1}
        headers = {'Idempotency-Key': 'scope-replay-test001'}
        assert client.post(path, json=body, headers=headers).status_code == 200
        with db.tx() as conn:
            conn.execute("UPDATE users SET branch_ids='[]'::jsonb WHERE id=%s", (branch['id'],))
            current = conn.execute('SELECT * FROM users WHERE id=%s', (branch['id'],)).fetchone()
        _authenticate(client, current)  # Valid new session must not grant access through an old replay response.
        assert client.post(path, json=body, headers=headers).status_code == 404

    def test_actual_driver_assignment_is_revoked_by_current_branch_and_terminal_task(self, client):
        _prepare_catalog()
        manager = _make_user('delivery_manager', branches=['kps-branch-1'])
        driver = _make_user('driver', branches=['kps-branch-1'])
        manager_shift, driver_shift, task_id = db.new_id('ms_'), db.new_id('ds_'), db.new_id('t_')
        with db.tx() as conn:
            conn.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps',%s)",
                         (manager_shift, manager['id'], db.json(['kps-branch-1'])))
            conn.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id) VALUES(%s,'driver',%s,'kps',%s,%s)",
                         (driver_shift, driver['id'], db.json(['kps-branch-1']), manager_shift))
        _authenticate(client, _make_user())
        order = _create(client, _body('delivery')).json()['order']
        with db.tx() as conn:
            conn.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by) VALUES(%s,%s,%s,%s,'accepted',%s)",
                         (task_id, order['id'], driver['id'], driver_shift, manager['id']))
        _authenticate(client, driver)
        assert client.get('/api/orders/' + order['id']).status_code == 200
        with db.tx() as conn:
            conn.execute("UPDATE users SET branch_ids='[]'::jsonb WHERE id=%s", (driver['id'],))
        assert client.get('/api/orders/' + order['id']).status_code == 404
        assert client.get('/api/orders').json()['orders'] == []
        with db.tx() as conn:
            conn.execute('UPDATE users SET branch_ids=%s WHERE id=%s', (db.json(['kps-branch-1']), driver['id']))
            conn.execute("UPDATE driver_tasks SET status='transferred' WHERE id=%s", (task_id,))
        assert client.get('/api/orders/' + order['id']).status_code == 404
        with pytest.raises(psycopg.Error):
            with db.tx() as conn:
                conn.execute("UPDATE driver_tasks SET status='delivered' WHERE id=%s", (task_id,))
        with db.tx() as conn:
            conn.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by) VALUES(%s,%s,%s,%s,'delivered',%s)",
                         (db.new_id('delivered_'), order['id'], driver['id'], driver_shift, manager['id']))
        assert client.get('/api/orders/' + order['id']).status_code == 404
        with db.tx() as conn:
            conn.execute("UPDATE users SET branch_ids='[]'::jsonb WHERE id=%s", (manager['id'],))
        _authenticate(client, _make_user())
        closed_duty = _create(client, _body('delivery'), key='revoked-duty-order001')
        assert closed_duty.status_code == 409 and closed_duty.json()['detail']['code'] == 'DELIVERY_DUTY_UNAVAILABLE'
        for role in ['delivery_manager', 'delivery_staff']:
            _authenticate(client, _make_user(role))
            assert client.get('/api/orders/' + order['id']).status_code == 404

    def test_order_dates_use_riyadh_operational_day_and_explicit_custom_range(self, client):
        _prepare_catalog()
        _authenticate(client, _make_user())
        order = _create(client).json()['order']
        with db.tx() as conn:
            conn.execute("UPDATE app_settings SET settings='{\"operational_day_start\":\"05:30\"}' WHERE brand_id='kps'")
            conn.execute("UPDATE orders SET created_at='2026-10-09T06:00:00+03:00' WHERE id=%s", (order['id'],))
        period = client.get('/api/orders', params={'period': 'custom', 'start': '2026-10-09T05:30:00', 'end': '2026-10-10T05:30:00'})
        assert period.status_code == 200 and len(period.json()['orders']) == 1
        assert period.json()['period']['operational_day_configured'] is True
        empty = client.get('/api/orders', params={'period': 'custom', 'start': '2026-10-10T05:30:00', 'end': '2026-10-11T05:30:00'})
        assert empty.json()['orders'] == []
        assert client.get('/api/orders?period=custom').status_code == 422

    def test_preparation_payment_and_arrival_are_separate(self, client):
        _prepare_catalog()
        customer = _make_user()
        _authenticate(client, customer)
        order = _create(client, _body('car')).json()['order']
        reached = client.post('/api/orders/' + order['id'] + '/arrival', json={'version': 1}, headers={'Idempotency-Key': 'car-arrival-test001'})
        assert reached.status_code == 200
        assert reached.json()['order']['arrival_at']
        assert client.post('/api/orders/' + order['id'] + '/arrival', json={'version': 1}, headers={'Idempotency-Key': 'car-arrival-test001'}).json() == reached.json()
        branch = _make_user('branch_manager', branches=['kps-branch-1'])
        _authenticate(client, branch)
        version = reached.json()['order']['version']
        for status in ['accepted', 'preparing', 'ready']:
            result = client.post('/api/orders/' + order['id'] + '/preparation', json={'status': status, 'version': version},
                                 headers={'Idempotency-Key': 'prepare-test-' + status})
            assert result.status_code == 200
            version = result.json()['order']['version']
        denied = client.post('/api/orders/' + order['id'] + '/preparation', json={'status': 'completed', 'version': version},
                             headers={'Idempotency-Key': 'complete-unpaid-test'})
        assert denied.status_code == 409 and denied.json()['detail']['code'] == 'PAYMENT_NOT_COLLECTED'
        assert client.get('/api/orders/' + order['id']).json()['order']['payment_status'] == 'unpaid'

    def test_database_itself_protects_confirmed_snapshot(self, client):
        _prepare_catalog()
        _authenticate(client, _make_user())
        order = _create(client).json()['order']
        with pytest.raises(psycopg.Error):
            with db.tx() as conn:
                conn.execute('UPDATE orders SET snapshot=%s WHERE id=%s', (db.json({'tampered': True}), order['id']))
        assert client.get('/api/orders/' + order['id']).json()['order']['snapshot'] == order['snapshot']

    def test_missing_otp_provider_fails_closed_and_typed_phone_is_not_identity(self, client):
        response = client.post('/api/auth/otp/request', json={'brand_id': 'kps', 'phone': '0500000000'})
        assert response.status_code == 503 and response.json()['detail']['code'] == 'OTP_PROVIDER_UNAVAILABLE'
        assert client.post('/api/auth/otp/verify', json={'challenge_id': 'invented', 'code': '1234'}).status_code == 400
        with db.tx() as conn:
            assert conn.execute("SELECT count(*) AS n FROM users WHERE role='customer'").fetchone()['n'] == 0
            assert conn.execute('SELECT count(*) AS n FROM sessions').fetchone()['n'] == 0

    def test_real_provider_contract_success_and_wrong_phone_rejected(self, client, monkeypatch):
        # Network is mocked exclusively in this test; no development OTP branch exists in application code.
        monkeypatch.setenv('OTP_PROVIDER','twilio_verify')
        monkeypatch.setenv('TWILIO_ACCOUNT_SID','AC'+'1'*32)
        monkeypatch.setenv('TWILIO_AUTH_TOKEN','test-only')
        monkeypatch.setenv('TWILIO_VERIFY_SERVICE_SID','VA'+'1'*32)
        with db.tx() as conn:
            conn.execute("UPDATE identity_channels SET state='active',enabled=true,delivery_proven_at=now() WHERE brand_id='kps' AND channel='sms'")
        outputs = {'Verifications': {'status': 'pending', 'sid': 'VE' + '2' * 32},
                   'VerificationCheck': {'status': 'approved', 'to': '+966500000000'}}
        monkeypatch.setattr(auth, '_provider_post', lambda path, data: outputs[path])
        sent = client.post('/api/auth/otp/request', json={'phone': '0500000000'}).json()
        outputs['VerificationCheck']['to'] = '+966500000001'
        wrong = client.post('/api/auth/otp/verify', json={'challenge_id': sent['challenge_id'], 'code': '1234'})
        assert wrong.status_code == 400
        with db.tx() as conn:
            assert conn.execute("SELECT count(*) AS n FROM users WHERE role='customer'").fetchone()['n'] == 0
        outputs['VerificationCheck']['to'] = '+966500000000'
        correct = client.post('/api/auth/otp/verify', json={'challenge_id': sent['challenge_id'], 'code': '1234'})
        assert correct.status_code == 200 and correct.json()['user']['phone'] == '+966500000000'
        assert client.post('/api/auth/otp/verify', json={'challenge_id': sent['challenge_id'], 'code': '1234'}).status_code == 400

    def test_opaque_static_allowlist_security_headers(self, client):
        assert client.get('/').status_code == 200
        for path in ['/.env', '/data/catalog.json', '/requirements.txt', '/index.html', '/migrations/001_core.sql', '/assets/../../requirements.txt']:
            assert client.get(path).status_code == 404
        health = client.get('/health')
        assert health.status_code == 200
        assert health.headers['x-content-type-options'] == 'nosniff'
        assert "script-src 'self'" in health.headers['content-security-policy']
