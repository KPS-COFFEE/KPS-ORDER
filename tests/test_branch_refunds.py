"""Sandbox refunds: synthetic provider and isolated PostgreSQL, no real charges."""
import json
from uuid import uuid4
import httpx
import pytest
from fastapi import HTTPException
from app import db, payment_refunds, payment_gateways, order_amendments
from test_branch_payments import branch_payment, accept, attempt, provider
from test_operations import ops


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
    assert post('owner', url, body, key='stable-refund') == first
    post('owner', url, body | {'version': version+1}, status=409)
    post('owner', '/api/orders/'+oid+'/preparation', {'version': version+1, 'status': 'preparing'}, status=409)
    assert not calls
