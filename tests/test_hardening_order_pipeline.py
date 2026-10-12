"""Real PostgreSQL rollback and replay safety for pipelined checkout writes."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from psycopg import sql

from app import db

pytestmark = pytest.mark.postgres
BRAND = 'hardening-pipeline'
BRANCH = 'hardening-pipeline-branch'


@pytest.fixture
def pipeline_checkout(client):
    hours = [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}]
    with db.tx() as conn:
        conn.execute('INSERT INTO brands(id,name) VALUES(%s,%s)', (BRAND, 'TEST pipeline brand'))
        conn.execute('INSERT INTO app_settings(brand_id,settings) VALUES(%s,%s)', (BRAND, db.json({})))
        conn.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled) VALUES(%s,'guest','active',true)", (BRAND,))
        conn.execute('INSERT INTO branches(id,brand_id,branch_no,source_id,name,city,settings) VALUES(%s,%s,1,%s,%s,%s,%s)',
            (BRANCH, BRAND, 'TEST pipeline source', 'TEST branch', 'حفر الباطن', db.json({
                'pickup_enabled': True, 'ordering_verified': True, 'opening_hours': hours,
                'payment_methods': ['cash', 'card']})))
        conn.execute('INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,%s,%s,%s,1250,%s)',
            ('hardening-pipeline-product', BRAND, 'TEST product', 'TEST', db.json({'enabled': True, 'source_verified': True})))
    guest = client.post('/api/identity/guest', json={'brand_id': BRAND, 'display_name': 'TEST pipeline guest', 'phone': '0501234567'})
    assert guest.status_code == 200, guest.text
    client.headers['X-CSRF-Token'] = guest.json()['csrf_token']
    body = {'brand_id': BRAND, 'branch_id': BRANCH, 'service': 'pickup', 'payment_method': 'cash',
            'items': [{'product_id': 'hardening-pipeline-product', 'quantity': 1}],
            'customer': {'name': 'TEST pipeline customer', 'phone': '0501234567'},
            'expected_total_halalas': 1250}
    return client, guest.json()['user']['id'], body


def effects(user_id):
    with db.tx() as conn:
        counter = conn.execute('SELECT last_number FROM order_branch_counters WHERE branch_id=%s', (BRANCH,)).fetchone()
        return {
            'counter': counter['last_number'] if counter else 0,
            'orders': conn.execute('SELECT count(*) n FROM orders WHERE customer_id=%s', (user_id,)).fetchone()['n'],
            'events': conn.execute("SELECT count(*) n FROM order_events WHERE actor_id=%s AND event_type='order.created'", (user_id,)).fetchone()['n'],
            'audits': conn.execute("SELECT count(*) n FROM audit_events WHERE actor_id=%s AND action='order.created'", (user_id,)).fetchone()['n'],
            'keys': conn.execute("SELECT count(*) n FROM idempotency_keys WHERE user_id=%s AND operation='orders.create'", (user_id,)).fetchone()['n'],
        }


@pytest.mark.parametrize('table,condition', [
    ('order_events', "event_type <> 'order.created'"),
    ('audit_events', "action <> 'order.created'"),
    ('idempotency_keys', "operation <> 'orders.create'"),
])
def test_queued_constraint_failure_rolls_back_all_business_writes_and_allows_retry(pipeline_checkout, table, condition):
    client, user_id, body = pipeline_checkout
    key = uuid4().hex
    with db.tx() as conn:
        conn.execute(sql.SQL('ALTER TABLE {} ADD CONSTRAINT hardening_pipeline_reject CHECK ({})').format(
            sql.Identifier(table), sql.SQL(condition)))
    failed = client.post('/api/orders', json=body, headers={'Idempotency-Key': key})
    assert failed.status_code == 503, failed.text
    assert failed.json()['detail']['code'] == 'SERVICE_UNAVAILABLE'
    assert effects(user_id) == {'counter': 0, 'orders': 0, 'events': 0, 'audits': 0, 'keys': 0}
    with db.tx() as conn:
        conn.execute(sql.SQL('ALTER TABLE {} DROP CONSTRAINT hardening_pipeline_reject').format(sql.Identifier(table)))
    retried = client.post('/api/orders', json=body, headers={'Idempotency-Key': key})
    assert retried.status_code == 200, retried.text
    assert retried.json()['order']['total_halalas'] == 1250
    replay = client.post('/api/orders', json=body, headers={'Idempotency-Key': key})
    assert replay.status_code == 200 and replay.json() == retried.json()
    assert effects(user_id) == {'counter': 1, 'orders': 1, 'events': 1, 'audits': 1, 'keys': 1}


def test_concurrent_same_key_and_distinct_true_intent_keep_exactly_one_effect_each(pipeline_checkout):
    client, user_id, body = pipeline_checkout
    barrier, shared_key = Barrier(8), uuid4().hex
    def submit(_):
        barrier.wait(timeout=20)
        return client.post('/api/orders', json=body, headers={'Idempotency-Key': shared_key})
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(submit, range(8)))
    assert all(response.status_code == 200 for response in responses), [response.text for response in responses]
    assert all(response.json() == responses[0].json() for response in responses)
    assert effects(user_id) == {'counter': 1, 'orders': 1, 'events': 1, 'audits': 1, 'keys': 1}
    separate = client.post('/api/orders', json=body, headers={'Idempotency-Key': uuid4().hex})
    assert separate.status_code == 200, separate.text
    assert separate.json()['order']['id'] != responses[0].json()['order']['id']
    assert effects(user_id) == {'counter': 2, 'orders': 2, 'events': 2, 'audits': 2, 'keys': 2}
    conflict = client.post('/api/orders', json=body | {'notes': 'different request'}, headers={'Idempotency-Key': shared_key})
    assert conflict.status_code == 409 and conflict.json()['detail']['code'] == 'IDEMPOTENCY_CONFLICT'
    assert effects(user_id) == {'counter': 2, 'orders': 2, 'events': 2, 'audits': 2, 'keys': 2}
