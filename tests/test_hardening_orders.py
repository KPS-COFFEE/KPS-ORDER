"""Guest HTTP authorization and persisted exactly-once order effects.

Database tests use the suite's explicitly named local test database and UUID
schema. No dependency overrides, live provider calls or production data are used.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import auth, catalog, db


def payload():
    return {'brand_id': 'hardening-orders', 'branch_id': 'hardening-orders-branch',
            'service': 'pickup', 'payment_method': 'cash',
            'items': [{'product_id': 'hardening-orders-product', 'quantity': 1}],
            'customer': {'name': 'عميل اختبار', 'phone': '0501234567'}}


@pytest.mark.parametrize('path,value', [
    (('customer', 'name'), '   '),
    (('customer', 'name'), 'name\x00'),
    (('customer', 'name'), '\ud800'),
    (('customer', 'address'), 'address\x00'),
    (('customer', 'neighborhood'), '\udfff'),
    (('notes',), 'note\x00'),
    (('notes',), '\udc00'),
    (('items', 0, 'note'), 'item\x00'),
    (('items', 0, 'product_id'), 'product\x00'),
    (('branch_id',), 'branch\x00'),
    (('brand_id',), 'brand\ud800'),
])
def test_checkout_rejects_unpersistable_or_missing_customer_text(path, value):
    body = payload()
    target = body
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        catalog.QuoteBody.model_validate(body)


@pytest.mark.parametrize('extra', ['price_halalas', 'total_halalas', 'tax', 'customer_id', 'discount_halalas'])
def test_checkout_rejects_client_authoritative_financial_or_ownership_fields(extra):
    with pytest.raises(ValidationError):
        catalog.QuoteBody.model_validate(payload() | {extra: 1})


def test_legitimate_arabic_english_and_multiline_notes_are_preserved():
    body = payload() | {'notes': 'ملاحظة عربية\nEnglish note\t2'}
    result = catalog.QuoteBody.model_validate(body)
    assert result.notes == body['notes']
    assert result.customer.name == body['customer']['name']


@pytest.mark.parametrize('field', ['brand_id', 'display_name', 'phone', 'email'])
def test_guest_identity_rejects_unpersistable_text_before_database_access(field):
    body = {'brand_id': 'hardening-orders', 'display_name': 'TEST guest',
            'phone': '0501234567', 'email': 'guest@fixture.invalid'}
    body[field] += '\x00'
    with pytest.raises(ValidationError):
        auth.GuestBody.model_validate(body)


@pytest.mark.parametrize('field', ['lat', 'lng'])
@pytest.mark.parametrize('value', [True, False, '28.4'])
def test_location_requires_a_real_json_number(field, value):
    location = {'lat': 28.4, 'lng': 45.9} | {field: value}
    with pytest.raises(ValidationError):
        catalog.Location.model_validate(location)


def test_car_pickup_requires_nonblank_color():
    car = {'plate': '', 'color': '  '}
    with pytest.raises(ValidationError):
        catalog.Car.model_validate(car)


@pytest.fixture
def checkout(client):
    brand = 'hardening-orders'
    hours = [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}]
    with db.tx() as conn:
        conn.execute('INSERT INTO brands(id,name) VALUES(%s,%s)', (brand, 'TEST ONLY'))
        conn.execute('INSERT INTO app_settings(brand_id,settings) VALUES(%s,%s)', (brand, db.json({})))
        conn.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled) VALUES(%s,'guest','active',true)", (brand,))
        conn.execute('INSERT INTO branches(id,brand_id,branch_no,source_id,name,city,settings) VALUES(%s,%s,1,%s,%s,%s,%s)',
                     ('hardening-orders-branch', brand, 'TEST ONLY', 'TEST branch', 'حفر الباطن',
                      db.json({'pickup_enabled': True, 'car_enabled': True, 'ordering_verified': True,
                               'opening_hours': hours, 'payment_methods': ['cash', 'card']})))
        conn.execute('INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,%s,%s,%s,1250,%s)',
                     ('hardening-orders-product', brand, 'TEST coffee', 'TEST',
                      db.json({'enabled': True, 'source_verified': True})))
    guest = client.post('/api/identity/guest', json={
        'brand_id': brand, 'display_name': 'عميل اختبار', 'phone': '0501234567'})
    assert guest.status_code == 200, guest.text
    client.headers['X-CSRF-Token'] = guest.json()['csrf_token']
    client.headers['X-KPS-Context'] = 'customer'
    return client, guest.json()['user']


def create(client, body=None, key=None):
    return client.post('/api/orders', json=body or payload(),
                       headers={'Idempotency-Key': key or uuid4().hex})


def counts(user_id):
    with db.tx() as conn:
        return {
            'orders': conn.execute('SELECT count(*) n FROM orders WHERE customer_id=%s', (user_id,)).fetchone()['n'],
            'created_events': conn.execute("SELECT count(*) n FROM order_events e JOIN orders o ON o.id=e.order_id WHERE o.customer_id=%s AND e.event_type='order.created'", (user_id,)).fetchone()['n'],
            'created_audits': conn.execute("SELECT count(*) n FROM audit_events WHERE actor_id=%s AND action='order.created'", (user_id,)).fetchone()['n'],
            'keys': conn.execute("SELECT count(*) n FROM idempotency_keys WHERE user_id=%s AND operation='orders.create'", (user_id,)).fetchone()['n'],
        }


@contextmanager
def staff_client(client, branch_ids):
    user_id, token, csrf = db.new_id('test_staff_'), auth.secrets.token_urlsafe(32), auth.secrets.token_urlsafe(32)
    with db.tx() as conn:
        conn.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES(%s,'TEST staff','branch_manager','hardening-orders',%s)",
                     (user_id, db.json(branch_ids)))
        conn.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES(%s,%s,%s,%s,now()+interval '1 hour')",
                     (db.new_id('test_session_'), auth.token_hash(token), auth.token_hash(csrf), user_id))
    with TestClient(client.app, base_url='http://testserver', headers={'Origin': 'http://testserver', 'X-CSRF-Token': csrf, 'X-KPS-Context': 'work'}) as staff:
        staff.cookies.set(auth.COOKIE, token)
        staff.cookies.set(auth.CSRF_COOKIE, csrf)
        yield staff, user_id


@pytest.mark.postgres
def test_real_guest_checkout_no_otp_retry_after_lost_response_and_price_update(checkout):
    client, user = checkout
    assert user['identity_kind'] == 'guest'
    assert user['phone_verified_at'] is None and user['email_verified_at'] is None
    key = uuid4().hex
    original = create(client, key=key)
    assert original.status_code == 200, original.text
    order = original.json()['order']
    assert order['customer']['phone'] == '+966501234567'
    assert order['total_halalas'] == 1250
    with db.tx() as conn:
        conn.execute("UPDATE products SET price_halalas=2000 WHERE id='hardening-orders-product'")
    # The original HTTP response may have been lost after COMMIT. Replay must
    # return the committed order, not quote again or allocate another number.
    replay = create(client, key=key)
    assert replay.status_code == 200 and replay.json() == original.json()
    assert counts(user['id']) == {'orders': 1, 'created_events': 1, 'created_audits': 1, 'keys': 1}
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM otp_challenges').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) n FROM email_outbox').fetchone()['n'] == 0


@pytest.mark.postgres
def test_twelve_concurrent_http_replays_have_one_persisted_effect(checkout):
    client, user = checkout
    key, barrier = uuid4().hex, Barrier(12)
    def submit(_):
        barrier.wait(timeout=20)
        response = create(client, key=key)
        assert response.status_code == 200, response.text
        return response.json()
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(submit, range(12)))
    assert all(result == results[0] for result in results)
    assert counts(user['id']) == {'orders': 1, 'created_events': 1, 'created_audits': 1, 'keys': 1}
    with db.tx() as conn:
        row = conn.execute('SELECT * FROM order_branch_counters WHERE branch_id=%s', ('hardening-orders-branch',)).fetchone()
        assert row['last_number'] == 1


@pytest.mark.postgres
def test_same_customer_same_contents_different_keys_are_two_real_orders(checkout):
    client, user = checkout
    first, second = create(client), create(client)
    assert first.status_code == second.status_code == 200
    assert first.json()['order']['id'] != second.json()['order']['id']
    assert first.json()['order']['snapshot']['order_reference'] != second.json()['order']['snapshot']['order_reference']
    assert counts(user['id']) == {'orders': 2, 'created_events': 2, 'created_audits': 2, 'keys': 2}
    assert second.json()['order']['customer'].get('risk_alert') is None


@pytest.mark.postgres
def test_recent_contact_lookup_uses_selective_index_and_exact_basket(checkout):
    client, user = checkout
    response = create(client)
    assert response.status_code == 200
    order = response.json()['order']
    with db.tx() as conn:
        conn.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,delivery_status,payment_method,"
                     "subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) "
                     "SELECT 'TEST index '||n,brand_id,branch_id,customer_id,service,delivery_status,payment_method,"
                     "subtotal_halalas,delivery_fee_halalas,total_halalas,jsonb_set(snapshot,'{order_reference}',to_jsonb('TEST ref '||n)),"
                     "jsonb_set(customer,'{phone}',to_jsonb('TEST phone '||n)) "
                     "FROM orders CROSS JOIN generate_series(1,2000) n WHERE id=%s", (order['id'],))
        conn.execute('ANALYZE orders')
        query = ("SELECT id FROM orders WHERE brand_id=%s AND branch_id=%s AND customer->>'phone'=%s "
                 "AND snapshot->'items'=%s AND created_at>now()-interval '15 minutes' LIMIT 1")
        args = (user['brand_id'], order['branch_id'], order['customer']['phone'], db.json(order['snapshot']['items']))
        plan = conn.execute('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+query,args).fetchone()['QUERY PLAN'][0]
        assert 'orders_recent_contact' in str(plan)
        assert conn.execute(query,args).fetchone()['id'] == order['id']
        assert conn.execute(query,(*args[:3],db.json([]))).fetchone() is None
        # Index is an optimization only: time, brand and contact boundaries stay exact.
        assert conn.execute(query,(args[0],args[1],'unknown',args[3])).fetchone() is None
        conn.execute("UPDATE orders SET created_at=now()-interval '16 minutes' WHERE id=%s",(order['id'],))
        assert conn.execute(query,args).fetchone() is None


@pytest.mark.postgres
@pytest.mark.parametrize('change', ['user', 'brand', 'session'])
def test_warm_authorization_plans_do_not_cache_revoked_access(checkout, monkeypatch, change):
    client, user = checkout
    db.close_pools()
    monkeypatch.setattr(db, 'POOL_MIN_SIZE', 1)
    monkeypatch.setattr(db, 'POOL_MAX_SIZE', 1)
    for _ in range(16):
        assert client.get('/api/orders').status_code == 200
    with db.tx() as conn:
        if change == 'user':
            conn.execute('UPDATE users SET active=false WHERE id=%s',(user['id'],))
        elif change == 'brand':
            conn.execute('UPDATE brands SET active=false WHERE id=%s',(user['brand_id'],))
        else:
            conn.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s',(user['id'],))
    assert client.get('/api/orders').status_code == 401


@pytest.mark.postgres
def test_failed_order_event_rolls_back_order_number_audit_and_key_then_same_key_retries(checkout):
    client, user = checkout
    key = uuid4().hex
    # Inject after the actual database write, independent of whether the
    # application uses a helper or a combined CTE. All rollback assertions stay.
    with db.tx() as conn:
        conn.execute("CREATE FUNCTION test_reject_created_event() RETURNS trigger LANGUAGE plpgsql AS $$ "
                     "BEGIN IF NEW.event_type='order.created' THEN "
                     "RAISE EXCEPTION 'TEST ONLY failure after event insert' USING ERRCODE='23514'; "
                     "END IF; RETURN NEW; END $$")
        conn.execute('CREATE TRIGGER test_reject_created_event AFTER INSERT ON order_events '
                     'FOR EACH ROW EXECUTE FUNCTION test_reject_created_event()')
    assert create(client, key=key).status_code == 503
    assert counts(user['id']) == {'orders': 0, 'created_events': 0, 'created_audits': 0, 'keys': 0}
    with db.tx() as conn:
        assert conn.execute('SELECT * FROM order_branch_counters WHERE branch_id=%s', ('hardening-orders-branch',)).fetchone() is None
        conn.execute('DROP TRIGGER test_reject_created_event ON order_events')
    result = create(client, key=key)
    assert result.status_code == 200, result.text
    assert counts(user['id']) == {'orders': 1, 'created_events': 1, 'created_audits': 1, 'keys': 1}
    assert create(client, key=key).json() == result.json()
    assert counts(user['id']) == {'orders': 1, 'created_events': 1, 'created_audits': 1, 'keys': 1}
    with db.tx() as conn:
        assert conn.execute('SELECT last_number FROM order_branch_counters WHERE branch_id=%s',
                            ('hardening-orders-branch',)).fetchone()['last_number'] == 1


@pytest.mark.postgres
def test_replayed_key_changed_body_conflicts_without_second_effect(checkout):
    client, user = checkout
    key = uuid4().hex
    assert create(client, key=key).status_code == 200
    changed = payload()
    changed['items'][0]['quantity'] = 2
    conflict = create(client, body=changed, key=key)
    assert conflict.status_code == 409 and conflict.json()['detail']['code'] == 'IDEMPOTENCY_CONFLICT'
    assert counts(user['id'])['orders'] == 1


@pytest.mark.postgres
def test_price_and_product_tampering_write_nothing(checkout):
    client, user = checkout
    for body in [payload() | {'expected_total_halalas': 1},
                 payload() | {'total_halalas': 1},
                 payload() | {'items': [{'product_id': 'kps-01', 'quantity': 1}]},
                 payload() | {'items': [{'product_id': 'hardening-orders-product', 'quantity': True}]},
                 payload() | {'items': [{'product_id': 'hardening-orders-product', 'quantity': 1, 'price_halalas': 1}]}]:
        response = create(client, body=body)
        assert response.status_code in (409, 422), response.text
    assert counts(user['id']) == {'orders': 0, 'created_events': 0, 'created_audits': 0, 'keys': 0}


@pytest.mark.postgres
def test_matching_unverified_phone_never_grants_other_guests_orders(checkout):
    client, user = checkout
    order = create(client).json()['order']
    with TestClient(client.app, base_url='http://testserver', headers={'Origin': 'http://testserver'}) as other:
        guest = other.post('/api/identity/guest', json={'brand_id': 'hardening-orders', 'display_name': 'TEST other', 'phone': '0501234567'})
        assert guest.status_code == 200 and guest.json()['user']['id'] != user['id']
        other.headers['X-CSRF-Token'] = guest.json()['csrf_token']
        other.headers['X-KPS-Context'] = 'customer'
        assert other.get('/api/orders/' + order['id']).status_code == 404
        assert other.get('/api/orders').json()['orders'] == []
        assert other.post('/api/orders/' + order['id'] + '/arrival', json={'version': 1},
                          headers={'Idempotency-Key': uuid4().hex}).status_code == 404


@pytest.mark.postgres
def test_guest_expired_session_csrf_and_pause_cannot_create_or_replay(checkout):
    client, user = checkout
    key = uuid4().hex
    assert create(client, key=key).status_code == 200
    saved_csrf = client.headers.pop('X-CSRF-Token')
    assert create(client).status_code == 403
    client.headers['X-CSRF-Token'] = saved_csrf
    with db.tx() as conn:
        conn.execute("UPDATE identity_channels SET state='paused' WHERE brand_id='hardening-orders' AND channel='guest'")
    assert create(client, key=key).status_code == 403
    with db.tx() as conn:
        conn.execute('UPDATE sessions SET expires_at=now()-interval \'1 second\' WHERE user_id=%s', (user['id'],))
    assert create(client, key=key).status_code == 401
    assert client.get('/api/orders').status_code == 401
    assert counts(user['id'])['orders'] == 1


@pytest.mark.postgres
@pytest.mark.parametrize('same_key', [True, False])
def test_concurrent_payment_confirmations_create_one_collection_and_audit(checkout, same_key):
    client, _ = checkout
    order = create(client).json()['order']
    with staff_client(client, ['hardening-orders-branch']) as (staff, staff_id):
        barrier, shared = Barrier(8), uuid4().hex
        body = {'method': 'cash', 'amount_halalas': 1250, 'tendered_halalas': 1300}
        def collect(_):
            barrier.wait(timeout=20)
            return staff.post(f"/api/orders/{order['id']}/collect", json=body,
                              headers={'Idempotency-Key': shared if same_key else uuid4().hex})
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(collect, range(8)))
        assert sorted(r.status_code for r in responses) == ([200] * 8 if same_key else [200] + [409] * 7)
        if same_key:
            assert all(r.json() == responses[0].json() for r in responses)
        with db.tx() as conn:
            rows = conn.execute('SELECT * FROM collections WHERE order_id=%s', (order['id'],)).fetchall()
            assert len(rows) == 1 and rows[0]['amount_halalas'] == 1250 and rows[0]['change_halalas'] == 50
            assert conn.execute('SELECT payment_status FROM orders WHERE id=%s', (order['id'],)).fetchone()['payment_status'] == 'paid'
            assert conn.execute("SELECT count(*) n FROM audit_events WHERE actor_id=%s AND action='order.collect'", (staff_id,)).fetchone()['n'] == 1
            conn.execute("UPDATE users SET branch_ids='[]' WHERE id=%s", (staff_id,))
        assert staff.post(f"/api/orders/{order['id']}/collect", json=body,
                          headers={'Idempotency-Key': shared}).status_code == 404


@pytest.mark.postgres
def test_quote_and_order_malicious_text_returns_422_without_database_effect(checkout):
    client, user = checkout
    for field, value in [('name', '  '), ('name', '\x00'), ('name', '\ud800'), ('address', 'address\x00')]:
        body = payload()
        body['customer'][field] = value
        # httpx rejects lone-surrogate json serialization before sending. Use an
        # escaped JSON body to exercise the server boundary for these inputs.
        import json
        encoded = json.dumps(body)
        response = client.post('/api/orders', content=encoded,
                               headers={'Content-Type': 'application/json', 'Idempotency-Key': uuid4().hex})
        assert response.status_code == 422, response.text
        assert client.post('/api/quote', content=encoded,
                           headers={'Content-Type': 'application/json'}).status_code == 422
    assert counts(user['id']) == {'orders': 0, 'created_events': 0, 'created_audits': 0, 'keys': 0}


@pytest.mark.postgres
def test_guest_gate_and_bulk_readiness_are_fresh_single_reads_with_matching_policy(checkout):
    _, guest = checkout

    class CountIdentityReads:
        """Count real SQL round trips; every query executes on PostgreSQL."""
        def __init__(self, conn):
            self.conn, self.reads = conn, 0

        def execute(self, statement, params=None):
            if 'FROM identity_channels' in statement:
                self.reads += 1
            return self.conn.execute(statement, params)

    with db.tx() as conn:
        user = conn.execute('SELECT * FROM users WHERE id=%s', (guest['id'],)).fetchone()
        reader = CountIdentityReads(conn)
        for state, enabled, allowed in [('active', True, True), ('paused', True, False),
                                        ('active', False, False), ('active', True, True)]:
            conn.execute("UPDATE identity_channels SET state=%s,enabled=%s WHERE brand_id=%s AND channel='guest'",
                         (state, enabled, user['brand_id']))
            before = reader.reads
            readiness = auth.channel_readiness(reader, user['brand_id'])
            assert reader.reads == before + 1
            assert readiness['guest']['available'] is allowed
            assert readiness['email']['state'] == 'unconfigured'
            assert readiness['email']['available'] is False
            before = reader.reads
            if allowed:
                auth.assert_customer_order_eligible(reader, user)
            else:
                with pytest.raises(HTTPException) as failure:
                    auth.assert_customer_order_eligible(reader, user)
                assert failure.value.status_code == 403
                assert failure.value.detail['code'] == 'GUEST_ORDERING_PAUSED'
            assert reader.reads == before + 1
