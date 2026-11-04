"""Sandbox refunds: synthetic provider and isolated PostgreSQL, no real charges."""
import json
from uuid import uuid4
import httpx
import pytest
from fastapi import HTTPException
from app import db, payment_refunds, payment_gateways, order_amendments
from test_branch_payments import branch_payment, accept, attempt, provider
from test_operations import ops
from test_backup import independent_restore_database
from app import branch_refund_reconciliation as recovery


def test_optional_payment_selection_preserves_legacy_refund_digest():
    from app.branch_refunds import RefundBody
    legacy={'version':1,'amount_halalas':1000,'reason':'Preserved reason'}
    assert RefundBody(**legacy).model_dump(exclude_none=True)==legacy
    assert RefundBody(**legacy,attempt_id='pay_old').model_dump(exclude_none=True)==dict(legacy,attempt_id='pay_old')


@pytest.mark.parametrize('missing',[
    'KPS_BRANCH_REFUND_RECONCILIATION_ENABLED','KPS_BRANCH_PAYMENT_SANDBOX_ENABLED',
    'KPS_BRANCH_PAYMENT_REFUNDS_ENABLED','KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED'])
def test_refund_recovery_is_off_without_every_explicit_gate(monkeypatch,missing):
    for flag in ('KPS_BRANCH_REFUND_RECONCILIATION_ENABLED','KPS_BRANCH_PAYMENT_SANDBOX_ENABLED',
                 'KPS_BRANCH_PAYMENT_REFUNDS_ENABLED','KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED'):
        monkeypatch.setenv(flag,'true')
    monkeypatch.delenv(missing)
    def forbidden():raise AssertionError('Disabled worker must not access the database')
    monkeypatch.setattr(db,'tx',forbidden)
    assert recovery.enqueue_pending()==0
    assert recovery.dispatch_once() is False


@pytest.fixture
def refund_provider(monkeypatch):
    context = payment_gateways.PaymentContext('ops-brand', 'fixture-seller', 'order', 'attempt', 1000)
    for flag in ('KPS_BRANCH_PAYMENT_REFUNDS_ENABLED', 'KPS_BRANCH_PAYMENT_SANDBOX_ENABLED', 'KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED'):
        monkeypatch.setenv(flag, 'true')
    monkeypatch.setenv(payment_gateways.credential_prefix('moyasar', context.brand_id, context.entity_id)+'_TEST_SECRET_KEY', 'sk_test_synthetic_refund_only')
    pid = str(uuid4())
    state = {'refunded': 0, 'timeout': False, 'change': {}, 'context': context, 'pid': pid}
    calls = []
    original = httpx.Client
    def handle(request):
        calls.append(request)
        assert str(request.url).startswith('https://api.moyasar.com/v1/payments/'+state['pid'])
        if request.method == 'GET' and state.get('read_timeout'):
            raise httpx.ReadTimeout('sensitive_provider_response', request=request)
        if request.method == 'POST':
            state['refunded'] += json.loads(request.content)['amount']
            if state['timeout']:
                raise httpx.ReadTimeout('sensitive_provider_response', request=request)
        payload = {'id': state['pid'], 'amount': 1000, 'currency': 'SAR', 'live': False,
                   'status': 'refunded' if state['refunded'] == 1000 else 'paid',
                   'refunded': state['refunded'], 'metadata': state['context'].metadata}
        payload.update(state['change'])
        return httpx.Response(200, json=payload)
    monkeypatch.setattr(payment_refunds.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    return context, pid, state, calls


@pytest.mark.parametrize('change', [
    {'amount': 999}, {'currency': 'USD'}, {'live': True}, {'live': None},
    {'metadata': {}}, {'status': 'authorized'}, {'refunded': True},
    {'refunded': -1}, {'refunded': 1001}, {'id': str(uuid4())},
])
def test_refund_provider_evidence_is_strict(refund_provider, change):
    context, pid, state, calls = refund_provider
    state['change'] = change
    with pytest.raises(HTTPException):
        payment_refunds.request(context, pid)
    assert len(calls) == 1


def test_refund_disabled_by_default(refund_provider, monkeypatch):
    context, pid, state, calls = refund_provider
    monkeypatch.delenv('KPS_BRANCH_PAYMENT_REFUNDS_ENABLED')
    with pytest.raises(HTTPException):
        payment_refunds.request(context, pid, 100)
    assert not calls


def test_refund_timeout_is_not_reposted_or_leaked(refund_provider):
    context, pid, state, calls = refund_provider
    state['timeout'] = True
    with pytest.raises(HTTPException) as error:
        payment_refunds.request(context, pid, 100)
    assert len(calls) == 1 and 'sensitive' not in str(error.value)


@pytest.fixture
def paid_order(branch_payment, monkeypatch):
    actor, post, oid = branch_payment
    a = attempt(post, oid, accept(post, oid))
    with monkeypatch.context() as stub:
        provider(stub, a['id'])
        post('customer', '/api/branch-payments/attempts/'+a['id']+'/reconcile')
    return actor, post, oid, a


def configure_paid(paid_order, refund_provider):
    actor, post, oid, a = paid_order
    context, pid, state, calls = refund_provider
    state['context'] = payment_gateways.PaymentContext('ops-brand', 'fixture-seller', oid, a['id'], 1000)
    state['pid'] = a['provider_payment_id']
    return actor, post, oid, a, state, calls


def authorize(post, oid, amount=1000, key=None):
    with db.tx() as c:
        version = c.execute('SELECT version FROM orders WHERE id=%s', (oid,)).fetchone()['version']
    return post('owner', '/api/branch-payments/orders/'+oid+'/refunds',
                {'version': version, 'amount_halalas': amount, 'reason': 'Synthetic authorized refund'}, key=key)['refund']


@pytest.mark.postgres
def test_partial_then_full_refund_preserves_single_collection(paid_order, refund_provider):
    actor, post, oid, a, state, calls = configure_paid(paid_order, refund_provider)
    first = authorize(post, oid, 300)
    assert not calls
    for rid, amount in [(first['id'], 300), (None, 700)]:
        if rid is None: rid = authorize(post, oid, amount)['id']
        url = '/api/branch-payments/refunds/'+rid+'/execute'
        assert post('owner', url)['refund']['status'] == 'verified'
        assert post('owner', url)['refund']['status'] == 'verified'
    assert [r.method for r in calls].count('POST') == 2
    with db.tx() as c:
        order = c.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
        assert order['payment_status'] == 'refunded' and order_amendments.balance(c, order) == 0
        assert c.execute('SELECT count(*) n FROM branch_online_receipts WHERE order_id=%s', (oid,)).fetchone()['n'] == 1
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s', (oid,)).fetchone()['n'] == 0


@pytest.mark.postgres
def test_timeout_recovery_reads_provider_without_second_post(paid_order, refund_provider):
    actor, post, oid, a, state, calls = configure_paid(paid_order, refund_provider)
    row = authorize(post, oid)
    state['timeout'] = True
    base = '/api/branch-payments/refunds/'+row['id']
    post('owner', base+'/execute', status=503)
    post('owner', base+'/execute', status=409)
    assert post('owner', base+'/reconcile')['refund']['status'] == 'verified'
    assert [r.method for r in calls].count('POST') == 1


@pytest.mark.postgres
def test_worker_recovers_lost_response_once_without_owner_impersonation(paid_order,refund_provider,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    actor,post,oid,a,state,calls=configure_paid(paid_order,refund_provider)
    row=authorize(post,oid)
    state['timeout']=True
    post('owner','/api/branch-payments/refunds/'+row['id']+'/execute',status=503)
    monkeypatch.setenv('KPS_BRANCH_REFUND_RECONCILIATION_ENABLED','true')
    assert recovery.enqueue_pending()==1
    assert recovery.enqueue_pending()==0
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:recovery.dispatch_once(),range(4)))
    assert results.count(True)==1
    assert [r.method for r in calls].count('POST')==1
    assert [r.method for r in calls].count('GET')==2
    with db.tx() as c:
        assert c.execute('SELECT status FROM branch_online_refunds WHERE id=%s',(row['id'],)).fetchone()['status']=='verified'
        events=c.execute("SELECT * FROM order_events WHERE order_id=%s AND event_type='payment.refund.verified'",(oid,)).fetchall()
        assert len(events)==1 and events[0]['actor_id'] is None
        assert c.execute('SELECT processed_at FROM branch_refund_reconciliation WHERE refund_id=%s',(row['id'],)).fetchone()['processed_at']


@pytest.mark.postgres
def test_unapplied_refund_remains_unresolved_and_later_get_recovers(paid_order,refund_provider,monkeypatch):
    actor,post,oid,a,state,calls=configure_paid(paid_order,refund_provider)
    row=authorize(post,oid)
    # A crash after the durable claim but before provider POST must never cause
    # this worker to submit money. A delayed matching result may later recover.
    with db.tx() as c:
        c.execute("UPDATE branch_online_refunds SET status='submitting' WHERE id=%s",(row['id'],))
    monkeypatch.setenv('KPS_BRANCH_REFUND_RECONCILIATION_ENABLED','true')
    assert recovery.enqueue_pending()==1
    assert recovery.dispatch_once() is False
    with db.tx() as c:
        job=c.execute('SELECT * FROM branch_refund_reconciliation WHERE refund_id=%s',(row['id'],)).fetchone()
        assert job['attempts']==1 and job['processed_at'] is None and job['lease_id'] is None
        assert c.execute('SELECT status FROM branch_online_refunds WHERE id=%s',(row['id'],)).fetchone()['status']=='submitting'
        c.execute("UPDATE branch_refund_reconciliation SET next_attempt_at=now()-interval '1 second' WHERE refund_id=%s",(row['id'],))
    state['refunded']=1000
    assert recovery.dispatch_once() is True
    assert [r.method for r in calls]==['GET','GET']


@pytest.mark.postgres
def test_recovery_never_executes_unsent_authorization(paid_order,refund_provider,monkeypatch):
    actor,post,oid,a,state,calls=configure_paid(paid_order,refund_provider)
    row=authorize(post,oid)
    monkeypatch.setenv('KPS_BRANCH_REFUND_RECONCILIATION_ENABLED','true')
    assert recovery.enqueue_pending()==0
    # Even an incorrectly queued authorization is not permission to pay/refund.
    with db.tx() as c:
        c.execute("INSERT INTO branch_refund_reconciliation(refund_id,brand_id) VALUES(%s,'ops-brand')",(row['id'],))
    assert recovery.dispatch_once() is False
    assert not calls


@pytest.mark.postgres
def test_crashed_refund_worker_lease_expires_safely(paid_order,refund_provider,monkeypatch):
    actor,post,oid,a,state,calls=configure_paid(paid_order,refund_provider)
    row=authorize(post,oid)
    state['timeout']=True
    post('owner','/api/branch-payments/refunds/'+row['id']+'/execute',status=503)
    monkeypatch.setenv('KPS_BRANCH_REFUND_RECONCILIATION_ENABLED','true')
    assert recovery.enqueue_pending()==1
    with db.tx() as c:
        c.execute("UPDATE branch_refund_reconciliation SET lease_id='crashed',lease_until=now()+interval '60 seconds' WHERE refund_id=%s",(row['id'],))
    assert recovery.dispatch_once() is False
    with db.tx() as c:
        c.execute("UPDATE branch_refund_reconciliation SET lease_until=now()-interval '1 second' WHERE refund_id=%s",(row['id'],))
    assert recovery.dispatch_once() is True
    assert [r.method for r in calls].count('POST')==1


@pytest.mark.postgres
def test_preflight_outage_leaves_unsent_refund_safely_retryable(paid_order, refund_provider):
    actor, post, oid, a, state, calls = configure_paid(paid_order, refund_provider)
    row = authorize(post, oid)
    base = '/api/branch-payments/refunds/'+row['id']
    state['read_timeout'] = True
    post('owner', base+'/execute', status=503)
    assert [r.method for r in calls] == ['GET']
    with db.tx() as c:
        assert c.execute('SELECT status FROM branch_online_refunds WHERE id=%s', (row['id'],)).fetchone()['status'] == 'requested'
        assert c.execute('SELECT payment_status FROM orders WHERE id=%s', (oid,)).fetchone()['payment_status'] == 'refund_pending'
    state['read_timeout'] = False
    assert post('owner', base+'/execute')['refund']['status'] == 'verified'
    assert post('owner', base+'/execute')['refund']['status'] == 'verified'
    assert [r.method for r in calls].count('POST') == 1


@pytest.mark.postgres
def test_refund_permissions_scope_and_fulfillment_hold(paid_order, refund_provider):
    actor, post, oid, a, state, calls = configure_paid(paid_order, refund_provider)
    with db.tx() as c:
        version = c.execute('SELECT version FROM orders WHERE id=%s', (oid,)).fetchone()['version']
    body = {'version': version, 'amount_halalas': 1000, 'reason': 'Synthetic refund'}
    url = '/api/branch-payments/orders/'+oid+'/refunds'
    for who in ('customer', 'driver', 'branch', 'manager'):
        post(who, url, body, status=403)
    post('other', url, body, status=404)
    post('owner', url, body | {'amount_halalas': 1001}, status=422)
    post('owner', url, body | {'reason': '   '}, status=422)
    first = post('owner', url, body, key='stable-refund')
    history = actor('owner').get(url)
    assert history.status_code == 200 and history.json()['enabled'] is True
    assert history.json()['refunds'] == [first['refund']]
    assert actor('customer').get(url).status_code == 403
    assert actor('other').get(url).status_code == 404
    assert post('owner', url, body, key='stable-refund') == first
    post('owner', url, body | {'version': version+1}, status=409)
    post('owner', '/api/orders/'+oid+'/preparation', {'version': version+1, 'status': 'preparing'}, status=409)
    assert not calls


@pytest.mark.postgres
def test_parallel_refund_execute_sends_once(paid_order, refund_provider):
    from concurrent.futures import ThreadPoolExecutor
    from app import branch_refunds
    actor, post, oid, a, state, calls = configure_paid(paid_order, refund_provider)
    row = authorize(post, oid)
    with db.tx() as c:
        user = c.execute("SELECT * FROM users WHERE id='ops-owner'").fetchone()
    def run(_):
        try: return branch_refunds.execute(row['id'], user)['refund']['status']
        except HTTPException as exc:
            assert exc.status_code == 409
            return 'pending'
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(4)))
    assert 'verified' in results and [r.method for r in calls].count('POST') == 1


@pytest.mark.postgres
def test_completed_order_unchanged_and_refund_survives_restore(paid_order, refund_provider, independent_restore_database, tmp_path, monkeypatch):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from app.exports import build_archive, restore_archive
    from app.branch_payments import enrich_orders
    actor, post, oid, a, state, calls = configure_paid(paid_order, refund_provider)
    with db.tx() as c:
        c.execute("UPDATE orders SET preparation_status='completed',completed_at=now() WHERE id=%s", (oid,))
        original = c.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
    row = authorize(post, oid)
    state['timeout'] = True
    post('owner', '/api/branch-payments/refunds/'+row['id']+'/execute', status=503)
    monkeypatch.setenv('KPS_BRANCH_REFUND_RECONCILIATION_ENABLED','true')
    assert recovery.enqueue_pending()==1
    assert recovery.dispatch_once() is True
    with db.tx() as c:
        assert c.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone() == original
        before = c.execute('SELECT * FROM branch_online_refunds').fetchall()
        jobs_before = c.execute('SELECT * FROM branch_refund_reconciliation').fetchall()
        enriched = dict(original)
        enrich_orders(c, [enriched])
        assert enriched['online_refunded_halalas'] == 1000 and enriched['online_payment_verified'] is False
        archive, manifest = build_archive(c, full=True)
        _, business_manifest = build_archive(c, brand_id='ops-brand')
    assert manifest['counts']['branch_online_refunds'] == 1
    assert manifest['counts']['branch_refund_reconciliation'] == 1
    assert 'branch_refund_reconciliation' in business_manifest['excluded_tables']
    schema = 'kps_restore_'+uuid4().hex
    assert restore_archive(archive, target_url=independent_restore_database, target_schema=schema,
                           asset_directory=tmp_path/'assets')['verified']
    with psycopg.connect(independent_restore_database, row_factory=dict_row) as c:
        c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        assert c.execute('SELECT * FROM branch_online_refunds').fetchall() == before
        assert c.execute('SELECT * FROM branch_refund_reconciliation').fetchall() == jobs_before
    with db.tx() as c:
        assert c.execute('SELECT * FROM branch_online_refunds').fetchall() == before
        assert c.execute('SELECT * FROM branch_refund_reconciliation').fetchall() == jobs_before
