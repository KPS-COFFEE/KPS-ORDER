"""Synthetic notifications + independent mocked provider GET on real PostgreSQL."""
from uuid import uuid4
import pytest
from app import db, branch_payment_webhooks as hooks, payment_gateways
from test_branch_payments import branch_payment, accept, attempt, provider
from test_operations import ops
from test_backup import independent_restore_database


@pytest.mark.parametrize('change', [
    {'live': True}, {'live': 'false'}, {'live': 0}, {'id': ''},
    {'secret_token': ''}, {'data': {}}, {'data': {'id': 'invalid'}},
    {'type': None}, {'id': 'x' * 81}, {'secret_token': 'x' * 513},
])
def test_notification_rejects_invalid_envelope(change):
    body = {'id': 'evt_fixture', 'type': 'payment_paid', 'live': False,
            'secret_token': 'x' * 32, 'data': {'id': str(uuid4())}}
    body.update(change)
    with pytest.raises((ValueError, TypeError, KeyError)):
        hooks.notification(body)


@pytest.fixture
def webhook(branch_payment, monkeypatch):
    actor, post, oid = branch_payment
    a = attempt(post, oid, accept(post, oid))
    monkeypatch.setenv('KPS_BRANCH_PAYMENT_WEBHOOKS_ENABLED', 'true')
    secret = 'synthetic-test-secret-never-real-' + uuid4().hex
    monkeypatch.setenv(payment_gateways.credential_prefix('moyasar', 'ops-brand', 'fixture-seller') + '_TEST_WEBHOOK_SECRET', secret)
    body = {'id': str(uuid4()), 'type': 'payment_paid', 'live': False,
            'secret_token': secret, 'data': {'id': a['provider_payment_id'], 'status': 'paid'}}
    return actor('customer'), oid, a, body


@pytest.mark.postgres
def test_durable_notification_duplicate_and_worker(webhook, monkeypatch):
    client, oid, a, body = webhook
    calls = provider(monkeypatch, a['id'])
    for _ in range(3):
        response = client.post('/paymenthooks/moyasar', json=body)
        assert response.status_code == 202, response.text
    assert not calls  # HTTP acknowledgement never performs provider I/O.
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM branch_payment_webhooks').fetchone()['n'] == 1
        assert c.execute('SELECT payment_status FROM orders WHERE id=%s', (oid,)).fetchone()['payment_status'] == 'unpaid'
        assert body['secret_token'] not in str(c.execute('SELECT * FROM audit_events').fetchall())
    assert hooks.dispatch_once() is True
    assert hooks.dispatch_once() is False
    assert len(calls) == 1
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM branch_online_receipts WHERE order_id=%s', (oid,)).fetchone()['n'] == 1
        assert c.execute('SELECT processed_at FROM branch_payment_webhooks').fetchone()['processed_at']
        assert c.execute("SELECT actor_id FROM order_events WHERE order_id=%s AND event_type='payment.verified'", (oid,)).fetchone()['actor_id'] is None


@pytest.mark.postgres
@pytest.mark.parametrize('invalid', ['token', 'live', 'unknown', 'disabled'])
def test_untrusted_webhook_cannot_enqueue(webhook, monkeypatch, invalid):
    client, oid, a, body = webhook
    expected = 401
    if invalid == 'token': body['secret_token'] = 'wrong-' * 10
    if invalid == 'live': body['live'] = True; expected = 400
    if invalid == 'unknown': body['data']['id'] = str(uuid4())
    if invalid == 'disabled': monkeypatch.delenv('KPS_BRANCH_PAYMENT_WEBHOOKS_ENABLED'); expected = 404
    assert client.post('/paymenthooks/moyasar', json=body).status_code == expected
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM branch_payment_webhooks').fetchone()['n'] == 0
        assert c.execute('SELECT count(*) n FROM branch_online_receipts').fetchone()['n'] == 0


@pytest.mark.postgres
def test_claimed_paid_event_requires_independent_proof_and_retries(webhook, monkeypatch):
    client, oid, a, body = webhook
    assert client.post('/paymenthooks/moyasar', json=body).status_code == 202
    with monkeypatch.context() as pending:
        provider(pending, a['id'], {'status': 'initiated'})
        assert hooks.dispatch_once() is False
    with db.tx() as c:
        row = c.execute('SELECT * FROM branch_payment_webhooks').fetchone()
        assert row['processed_at'] is None and row['lease_id'] is None and row['attempts'] == 1
        assert c.execute('SELECT count(*) n FROM branch_online_receipts').fetchone()['n'] == 0
        c.execute("UPDATE branch_payment_webhooks SET next_attempt_at=now()-interval '1 second'")
    provider(monkeypatch, a['id'])
    assert hooks.dispatch_once() is True


@pytest.mark.postgres
def test_expired_worker_lease_recovers_and_late_success_stays_review(webhook, monkeypatch):
    client, oid, a, body = webhook
    provider(monkeypatch, a['id'])
    assert client.post('/paymenthooks/moyasar', json=body).status_code == 202
    with db.tx() as c:
        c.execute("UPDATE branch_payment_webhooks SET lease_id='interrupted',lease_until=now()-interval '1 second'")
        c.execute("UPDATE orders SET preparation_status='cancelled' WHERE id=%s", (oid,))
    assert hooks.dispatch_once() is True
    with db.tx() as c:
        assert c.execute('SELECT status FROM branch_payment_attempts WHERE id=%s', (a['id'],)).fetchone()['status'] == 'review_required'
        assert c.execute('SELECT payment_status FROM orders WHERE id=%s', (oid,)).fetchone()['payment_status'] == 'unpaid'


@pytest.mark.postgres
def test_anonymous_provider_callback_and_nonpayment_event(webhook):
    from app import auth
    client, oid, a, body = webhook
    saved = client.app.dependency_overrides.pop(auth.current_user)
    try:
        body['type'] = 'payment_verified'  # Card verification is NOT collection.
        response = client.post('/paymenthooks/moyasar', json=body)
        assert response.status_code == 202 and response.json()['queued'] is False
        body['type'] = 'payment_paid'
        assert client.post('/paymenthooks/moyasar', json=body).status_code == 202
        assert client.post('/api/branch-payments/attempts/'+a['id']+'/reconcile').status_code == 401
    finally:
        client.app.dependency_overrides[auth.current_user] = saved


@pytest.mark.postgres
def test_parallel_workers_and_two_events_have_one_financial_effect(webhook, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    client, oid, a, body = webhook
    provider(monkeypatch, a['id'])
    for _ in range(2):
        body['id'] = str(uuid4())
        assert client.post('/paymenthooks/moyasar', json=body).status_code == 202
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: hooks.dispatch_once(), range(4)))
    assert outcomes.count(True) == 2
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM branch_online_receipts WHERE order_id=%s', (oid,)).fetchone()['n'] == 1
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='payment.verified'", (oid,)).fetchone()['n'] == 1
        assert c.execute('SELECT count(*) n FROM branch_payment_webhooks WHERE processed_at IS NOT NULL').fetchone()['n'] == 2


@pytest.mark.postgres
def test_webhook_queue_survives_independent_backup_restore(webhook, independent_restore_database, tmp_path):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from app.exports import build_archive, restore_archive
    client, oid, a, body = webhook
    assert client.post('/paymenthooks/moyasar', json=body).status_code == 202
    with db.tx() as c:
        before = c.execute('SELECT * FROM branch_payment_webhooks').fetchall()
        contents, manifest = build_archive(c, full=True)
        _, business = build_archive(c, brand_id='ops-brand')
    assert manifest['counts']['branch_payment_webhooks'] == 1
    assert 'branch_payment_webhooks' in business['excluded_tables']
    schema = 'kps_restore_'+uuid4().hex
    result = restore_archive(contents, target_url=independent_restore_database,
                             target_schema=schema, asset_directory=tmp_path/'assets')
    assert result['verified']
    with psycopg.connect(independent_restore_database, row_factory=dict_row) as c:
        c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        assert c.execute('SELECT * FROM branch_payment_webhooks').fetchall() == before
    with db.tx() as c:
        assert c.execute('SELECT * FROM branch_payment_webhooks').fetchall() == before


@pytest.mark.postgres
def test_sweep_recovers_payment_without_callback_and_without_new_charge(webhook, monkeypatch):
    client, oid, a, body = webhook
    calls = provider(monkeypatch, a['id'])
    monkeypatch.delenv('KPS_BRANCH_PAYMENT_WEBHOOKS_ENABLED')
    with db.tx() as c:
        c.execute("INSERT INTO branch_checkout_sessions(attempt_id,brand_id,state,created_at) VALUES(%s,'ops-brand','creating',now()-interval '2 minutes')", (a['id'],))
    assert hooks.enqueue_pending() == 0  # Sweeping is separately OFF by default.
    monkeypatch.setenv('KPS_BRANCH_PAYMENT_RECONCILIATION_ENABLED', 'true')
    assert hooks.enqueue_pending() == 1
    assert hooks.enqueue_pending() == 0
    assert client.post('/paymenthooks/moyasar', json=body).status_code == 404
    assert hooks.dispatch_once() is True
    assert len(calls) == 1 and calls[0].method == 'GET'
    with db.tx() as c:
        assert c.execute('SELECT payment_status FROM orders WHERE id=%s', (oid,)).fetchone()['payment_status'] == 'paid'
        assert c.execute('SELECT event_type FROM branch_payment_webhooks').fetchone()['event_type'] == 'reconciliation_requested'
