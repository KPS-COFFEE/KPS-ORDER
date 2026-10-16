"""Renew only provably unused sessions. No real merchant traffic."""
from datetime import datetime, timezone, timedelta
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from fastapi import HTTPException
from app import db, payment_intents, payment_gateways, branch_payment_renewal
from test_branch_checkout import checkout_case, branch_payment, ops
from test_branch_payments import provider


@pytest.fixture
def intent_evidence(monkeypatch):
    context = payment_gateways.PaymentContext('brand','seller','order','attempt',1000)
    session = {'provider_intent_id': str(uuid4()), 'callback_url': 'https://fixture.invalid/payment-return?attempt=attempt'}
    data = {'id': session['provider_intent_id'], 'amount': 1000, 'currency': 'SAR',
            'description': 'KPS order order / attempt', 'callback_url': session['callback_url'],
            'status': 'created', 'fulfilled_at': None,
            'expires_at': (datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()}
    for key in ('KPS_BRANCH_PAYMENT_SANDBOX_ENABLED','KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED'):
        monkeypatch.setenv(key, 'true')
    monkeypatch.setenv(payment_gateways.credential_prefix('moyasar','brand','seller')+'_TEST_SECRET_KEY','sk_test_synthetic_renewal')
    calls = []
    original = httpx.Client
    def handle(request):
        calls.append(request)
        assert request.method == 'GET'
        assert str(request.url) == 'https://api.moyasar.com/v1/payment_intents/'+session['provider_intent_id']
        return httpx.Response(200, json=data)
    monkeypatch.setattr(payment_intents.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    return context, session, data, calls


def test_expired_unused_and_canceled_intents_have_normalized_proof(intent_evidence):
    context, session, data, calls = intent_evidence
    assert payment_intents.safe_unused_intent(context,session) == 'provider_expired'
    data['status'] = 'canceled'
    assert payment_intents.safe_unused_intent(context,session) == 'provider_canceled'
    assert len(calls) == 2


@pytest.mark.parametrize('change', [
    {'status':'fulfilled'}, {'status':'paid'}, {'fulfilled_at':'2026-01-01T00:00:00Z'},
    {'amount':True}, {'amount':999}, {'currency':'USD'}, {'description':'other order'},
    {'callback_url':'https://other.invalid'}, {'id':str(uuid4())},
    {'expires_at':'invalid'}, {'expires_at':'2026-01-01T00:00:00'},
    {'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()},
])
def test_unproven_or_consumed_session_never_permits_renewal(intent_evidence,change):
    context, session, data, calls = intent_evidence
    data.update(change)
    with pytest.raises(HTTPException) as error:
        payment_intents.safe_unused_intent(context,session)
    assert error.value.detail['code']=='PAYMENT_RECONCILIATION_REQUIRED'
    assert len(calls)==1


@pytest.mark.postgres
def test_renewal_preserves_history_and_old_attempt_cannot_checkout(checkout_case,monkeypatch):
    actor,post,oid,a,calls=checkout_case
    url='/api/branch-payments/attempts/'+a['id']
    post('customer',url+'/checkout')
    monkeypatch.setattr(payment_intents,'safe_unused_intent',lambda context,session:'provider_expired')
    result=post('customer',url+'/renew')['attempt']
    assert result['id'] != a['id'] and result['provider_payment_id'] != a['provider_payment_id']
    assert post('customer',url+'/renew')['attempt']==result
    post('customer',url+'/checkout',status=409)
    post('customer','/api/branch-payments/attempts/'+result['id']+'/checkout')
    with db.tx() as c:
        assert c.execute('SELECT status FROM branch_payment_attempts WHERE id=%s',(a['id'],)).fetchone()['status']=='superseded'
        assert c.execute('SELECT count(*) n FROM branch_checkout_sessions').fetchone()['n']==2
        assert c.execute('SELECT count(*) n FROM branch_payment_renewals').fetchone()['n']==1
    provider(monkeypatch,result['id'])
    assert post('customer','/api/branch-payments/attempts/'+result['id']+'/reconcile')['attempt']['status']=='verified'
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM branch_online_receipts WHERE order_id=%s',(oid,)).fetchone()['n']==1
    post('customer',url+'/renew',status=409)


@pytest.mark.postgres
def test_parallel_renewal_creates_one_successor_without_provider_charge(checkout_case):
    actor,post,oid,a,calls=checkout_case
    # Resolve the synthetic customer from the order rather than depending on a name.
    with db.tx() as c:
        order=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        user=c.execute('SELECT * FROM users WHERE id=%s',(order['customer_id'],)).fetchone()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:branch_payment_renewal.renew(a['id'],user),range(4)))
    assert len({r['attempt']['id'] for r in results})==1
    assert not calls


@pytest.mark.postgres
def test_creating_session_must_age_and_cannot_release_old_secret(checkout_case,monkeypatch):
    actor,post,oid,a,calls=checkout_case
    with db.tx() as c:
        c.execute("INSERT INTO branch_checkout_sessions(attempt_id,brand_id,created_at) VALUES(%s,'ops-brand',now()-interval '2 minutes')",(a['id'],))
    result=post('customer','/api/branch-payments/attempts/'+a['id']+'/renew')['attempt']
    assert result['id'] != a['id']
    post('customer','/api/branch-payments/attempts/'+a['id']+'/checkout',status=409)
    with db.tx() as c:
        c.execute("INSERT INTO branch_checkout_sessions(attempt_id,brand_id) VALUES(%s,'ops-brand')",(result['id'],))
    post('customer','/api/branch-payments/attempts/'+result['id']+'/renew',status=409)
    assert not calls


@pytest.mark.postgres
def test_unconfirmed_proof_and_foreign_actor_do_not_retire_attempt(checkout_case,monkeypatch):
    actor,post,oid,a,calls=checkout_case
    url='/api/branch-payments/attempts/'+a['id']
    post('customer',url+'/checkout')
    def fail(*args): raise db.error(409,'PAYMENT_RECONCILIATION_REQUIRED','Unconfirmed')
    monkeypatch.setattr(payment_intents,'safe_unused_intent',fail)
    post('customer',url+'/renew',status=409)
    post('owner',url+'/renew',status=403)
    with db.tx() as c:
        assert c.execute('SELECT status FROM branch_payment_attempts WHERE id=%s',(a['id'],)).fetchone()['status']=='pending'
        assert c.execute('SELECT count(*) n FROM branch_payment_renewals').fetchone()['n']==0


@pytest.mark.postgres
def test_verified_payment_winning_race_prevents_renewal(checkout_case,monkeypatch):
    actor,post,oid,a,calls=checkout_case
    url='/api/branch-payments/attempts/'+a['id']
    post('customer',url+'/checkout')
    def proof(context,session):
        with monkeypatch.context() as stub:
            provider(stub,a['id'])
            post('customer',url+'/reconcile')
        return 'provider_expired'
    monkeypatch.setattr(payment_intents,'safe_unused_intent',proof)
    post('customer',url+'/renew',status=409)
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM branch_payment_renewals').fetchone()['n']==0
        assert c.execute('SELECT payment_status FROM orders WHERE id=%s',(oid,)).fetchone()['payment_status']=='paid'
