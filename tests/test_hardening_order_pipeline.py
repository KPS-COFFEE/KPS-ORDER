"""Real PostgreSQL rollback and replay safety for pipelined checkout writes."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import json

import pytest
from psycopg import sql

from app import db, orders

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


@pytest.mark.parametrize('last_number,expected_suffix', [(0, '-0001'), (9999, '-10000')])
def test_checkout_atomic_numbering_preserves_padding_and_committed_snapshot(pipeline_checkout, last_number, expected_suffix):
    client, user_id, body = pipeline_checkout
    if last_number:
        with db.tx() as conn:
            conn.execute('INSERT INTO order_branch_counters(branch_id,last_number) VALUES(%s,%s)', (BRANCH, last_number))
    key = uuid4().hex
    response = client.post('/api/orders', json=body, headers={'Idempotency-Key': key})
    assert response.status_code == 200, response.text
    order = response.json()['order']
    reference = order['snapshot']['order_reference']
    assert reference.startswith('HFR1-') and reference.endswith(expected_suffix)
    with db.tx() as conn:
        stored = conn.execute('SELECT snapshot FROM orders WHERE id=%s', (order['id'],)).fetchone()
        assert stored['snapshot']['order_reference'] == reference
    assert client.post('/api/orders', json=body, headers={'Idempotency-Key': key}).json() == response.json()
    assert effects(user_id) == {'counter': last_number + 1, 'orders': 1, 'events': 1, 'audits': 1, 'keys': 1}


@pytest.mark.parametrize('locale', ['ar', 'en'])
def test_atomic_checkout_response_matches_existing_projection_and_single_write(pipeline_checkout, monkeypatch, locale):
    import psycopg
    client, user_id, body = pipeline_checkout
    writes = []
    execute = psycopg.Connection.execute
    def observed(conn, query, *args, **kwargs):
        if isinstance(query, str) and 'INSERT INTO idempotency_keys' in query:
            writes.append(query)
        return execute(conn, query, *args, **kwargs)
    monkeypatch.setattr(psycopg.Connection, 'execute', observed)
    key = uuid4().hex
    first = client.post('/api/orders', json=body, headers={'Idempotency-Key': key, 'Accept-Language': locale})
    assert first.status_code == 200, first.text
    assert len(writes) == 1
    assert all('INSERT INTO ' + table in writes[0] for table in ('orders', 'order_events', 'audit_events', 'idempotency_keys'))
    with db.tx() as conn:
        row = conn.execute('SELECT * FROM orders WHERE id=%s', (first.json()['order']['id'],)).fetchone()
        expected = json.loads(json.dumps({'order': orders.customer_order_view(row)}, ensure_ascii=False, default=str))
        saved = conn.execute('SELECT response FROM idempotency_keys WHERE user_id=%s AND key=%s', (user_id, key)).fetchone()
    assert expected == first.json() == saved['response']
    assert not {'performance_snapshot', 'preparation_route_id', 'preparation_branch'} & first.json()['order'].keys()
    assert client.post('/api/orders', json=body, headers={'Idempotency-Key': key}).json() == expected
    assert len(writes) == 1
    assert effects(user_id) == {'counter': 1, 'orders': 1, 'events': 1, 'audits': 1, 'keys': 1}

@pytest.mark.parametrize('name',['اسم اختبار "خاص" \\ جديد','TEST "name" \\ <script>unsafe</script>'])
def test_database_encoded_checkout_preserves_text_scope_and_identical_replay(pipeline_checkout,name):
    client,user_id,body=pipeline_checkout
    body['customer']['name']=name
    key=uuid4().hex
    first=client.post('/api/orders',json=body,headers={'Idempotency-Key':key})
    replay=client.post('/api/orders',json=body,headers={'Idempotency-Key':key})
    assert first.status_code==replay.status_code==200
    assert first.headers['content-type']=='application/json'
    assert first.headers['cache-control']=='no-store'
    assert first.content==replay.content
    assert first.json()['order']['customer']['name']==name
    assert 'performance_snapshot' not in first.json()['order']
    with db.tx() as conn:
        stored=conn.execute('SELECT response FROM idempotency_keys WHERE user_id=%s AND key=%s',(user_id,key)).fetchone()['response']
    assert first.json()==stored
    from tests.test_core import _authenticate,_make_user
    _authenticate(client,_make_user(brand=BRAND))
    assert client.get('/api/orders/'+first.json()['order']['id']).status_code==404


@pytest.mark.parametrize('timezone', ['UTC', 'Asia/Riyadh', 'Asia/Kolkata', 'America/St_Johns'])
@pytest.mark.parametrize('fraction', ['', '.120000', '.000001'])
def test_atomic_response_timestamp_matches_python_contract(database, timezone, fraction):
    with db.tx() as conn:
        conn.execute("SELECT set_config('TimeZone',%s,true)", (timezone,))
        row = conn.execute('SELECT ts,' + orders._response_timestamp_sql('ts') +
                           ' AS encoded FROM (SELECT %s::timestamptz ts) v',
                           ('2026-10-13 12:34:56' + fraction + '+00:00',)).fetchone()
        assert row['encoded'] == str(row['ts'])
        null = conn.execute('SELECT ' + orders._response_timestamp_sql('NULL::timestamptz') + ' AS encoded', ()).fetchone()
        assert null['encoded'] is None


def test_atomic_statement_without_response_rolls_back_business_effect(pipeline_checkout):
    _, user_id, _ = pipeline_checkout
    with pytest.raises(RuntimeError, match='did not persist'):
        with db.tx(pipeline=True) as conn:
            db.idempotent(conn, user_id, uuid4().hex, 'TEST-empty-atomic', {}, lambda: db.AtomicIdempotentWrite(
                'created AS (INSERT INTO brands(id,name) VALUES(%s,%s) RETURNING id), '
                "operation_response AS (SELECT '{}'::jsonb response WHERE false)",
                ('TEST-empty-atomic-brand', 'TEST rollback')))
    with db.tx() as conn:
        assert conn.execute("SELECT id FROM brands WHERE id='TEST-empty-atomic-brand'").fetchone() is None
        assert conn.execute("SELECT key FROM idempotency_keys WHERE operation='TEST-empty-atomic'").fetchone() is None


def test_deferred_atomic_empty_response_fails_before_commit(pipeline_checkout):
    import psycopg
    _, user_id, _ = pipeline_checkout
    with pytest.raises(psycopg.errors.DivisionByZero):
        with db.tx(pipeline=True) as conn:
            db.idempotent(conn, user_id, uuid4().hex, 'TEST-empty-deferred', {}, lambda: db.AtomicIdempotentWrite(
                'created AS (INSERT INTO brands(id,name) VALUES(%s,%s) RETURNING id), '
                "operation_response AS (SELECT '{}'::jsonb response WHERE false)",
                ('TEST-empty-deferred-brand', 'TEST rollback')), defer_atomic_response=True)
    with db.tx() as conn:
        assert conn.execute("SELECT id FROM brands WHERE id='TEST-empty-deferred-brand'").fetchone() is None
        assert conn.execute("SELECT key FROM idempotency_keys WHERE operation='TEST-empty-deferred'").fetchone() is None


@pytest.mark.parametrize('response_lost', [False, True])
def test_checkout_commits_all_effects_before_decoding_and_lost_response_replays(pipeline_checkout, monkeypatch, response_lost):
    client, user_id, body = pipeline_checkout
    resolve = db.DeferredAtomicResponse.resolve
    observed = []
    def after_commit(result):
        # This separate transaction must see every effect before the original
        # response is decoded. A slow/lost HTTP response cannot hold the counter.
        observed.append(effects(user_id))
        assert observed[-1] == {'counter': 1, 'orders': 1, 'events': 1, 'audits': 1, 'keys': 1}
        assert result.cursor.pgresult is not None
        if response_lost:
            result.cursor.close()
            raise RuntimeError('TEST connection lost after durable COMMIT')
        return resolve(result)
    monkeypatch.setattr(db.DeferredAtomicResponse, 'resolve', after_commit)
    key = uuid4().hex
    first = client.post('/api/orders', json=body, headers={'Idempotency-Key': key})
    assert first.status_code == (503 if response_lost else 200), first.text
    replay = client.post('/api/orders', json=body, headers={'Idempotency-Key': key})
    assert replay.status_code == 200, replay.text
    assert len(observed) == 1  # Replays never queue another write/response.
    if not response_lost:
        assert first.json() == replay.json()
    with db.tx() as conn:
        saved = conn.execute('SELECT response FROM idempotency_keys WHERE user_id=%s AND key=%s', (user_id, key)).fetchone()
    assert saved['response'] == replay.json()
    assert effects(user_id) == observed[0]
