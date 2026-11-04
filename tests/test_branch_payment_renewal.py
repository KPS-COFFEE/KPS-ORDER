"""Renew only provably unused sessions. No real merchant traffic."""
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from fastapi import HTTPException
from app import db, payment_intents, payment_gateways, branch_payment_renewal
from test_branch_checkout import checkout_case, branch_payment, ops
from test_branch_payments import provider
from test_backup import independent_restore_database


@pytest.mark.parametrize('current_status',['superseded','verified','review_required'])
def test_stale_pending_snapshot_cannot_release_checkout(monkeypatch,current_status):
    from app import branch_checkout, branch_payments, orders
    stale={'id':'pay_stale','order_id':'order','status':'pending',
           'expires_at':datetime.now(timezone.utc)+timedelta(minutes=10),
           'amount_halalas':1000,'order_revision':0}
    locked=[]
    def scoped(c,oid,user,lock=False):
        assert lock
        locked.append(True)
        return {'preparation_status':'accepted','payment_status':'unpaid',
                'total_halalas':1000,'amendment_revision':0}
    class Connection:
        def execute(self,query,args):
            assert locked and args==('pay_stale',)
            return self
        def fetchone(self):return {**stale,'status':current_status}
    monkeypatch.setattr(orders,'scoped_order',scoped)
    monkeypatch.setattr(branch_payments,'require_sandbox',lambda order:None)
    with pytest.raises(HTTPException) as error:
        branch_checkout.eligible(Connection(),stale,{'id':'customer'})
    assert error.value.detail['code']=='PAYMENT_CHECKOUT_UNAVAILABLE'


@pytest.mark.postgres
def test_checkout_rereads_attempt_after_renewal_wins_order_lock(checkout_case,monkeypatch):
    from app import branch_checkout, orders
    actor,post,oid,a,calls=checkout_case
    post('customer','/api/branch-payments/attempts/'+a['id']+'/checkout')
    with db.tx() as c:
        customer=c.execute('SELECT customer_id FROM orders WHERE id=%s',(oid,)).fetchone()['customer_id']
        user=c.execute('SELECT * FROM users WHERE id=%s',(customer,)).fetchone()
    monkeypatch.setattr(payment_intents,'safe_unused_intent',lambda *args,**kw:'provider_canceled')
    original=orders.scoped_order
    renewed=[]
    def competing_renewal(c,order_id,actor,lock=False):
        if lock and not renewed:
            # acquire() has already read pending. Another transaction completes
            # renewal before this request obtains the canonical order lock.
            renewed.append(branch_payment_renewal.renew(a['id'],user))
        return original(c,order_id,actor,lock=lock)
    monkeypatch.setattr(orders,'scoped_order',competing_renewal)
    with pytest.raises(HTTPException) as error:
        branch_checkout.acquire(a['id'],user)
    assert error.value.detail['code']=='PAYMENT_CHECKOUT_UNAVAILABLE'
    assert len(renewed)==1 and len(calls)==1
    assert renewed[0]['attempt']['id']!=a['id']


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
        suffix='/cancel' if request.method=='POST' else ''
        assert str(request.url) == 'https://api.moyasar.com/v1/payment_intents/'+session['provider_intent_id']+suffix
        if request.method=='POST':
            data['status']=data.get('_cancel_state','canceled')
            if data['status']=='fulfilled':data['fulfilled_at']=datetime.now(timezone.utc).isoformat()
            if data.get('_cancel_timeout'):raise httpx.ReadTimeout('synthetic private provider data',request=request)
        return httpx.Response(200, json=data, headers={'Date': data.get('_response_date',format_datetime(datetime.now(timezone.utc),usegmt=True))})
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
    {'_response_date':''}, {'_response_date':'invalid'},
    {'_response_date':format_datetime(datetime.now(timezone.utc)-timedelta(hours=1),usegmt=True)},
    {'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()},
])
def test_unproven_or_consumed_session_never_permits_renewal(intent_evidence,change):
    context, session, data, calls = intent_evidence
    data.update(change)
    with pytest.raises(HTTPException) as error:
        payment_intents.safe_unused_intent(context,session)
    assert error.value.detail['code']=='PAYMENT_RECONCILIATION_REQUIRED'
    assert len(calls)==1


@pytest.mark.parametrize('outcome',['canceled','fulfilled','created'])
def test_local_expiry_cancellation_requires_independent_provider_proof(intent_evidence,outcome):
    context,session,data,calls=intent_evidence
    data['expires_at']=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    data['_cancel_state']=outcome
    if outcome=='canceled':
        assert payment_intents.safe_unused_intent(context,session,cancel_unused=True)=='provider_canceled'
    else:
        with pytest.raises(HTTPException):payment_intents.safe_unused_intent(context,session,cancel_unused=True)
    assert [r.method for r in calls]==['GET','POST','GET']


def test_lost_cancellation_response_recovers_by_get_not_another_post(intent_evidence):
    context,session,data,calls=intent_evidence
    data['expires_at']=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    data['_cancel_timeout']=True
    with pytest.raises(HTTPException) as error:
        payment_intents.safe_unused_intent(context,session,cancel_unused=True)
    assert 'private provider' not in str(error.value)
    assert payment_intents.safe_unused_intent(context,session,cancel_unused=True)=='provider_canceled'
    assert [r.method for r in calls]==['GET','POST','GET']


@pytest.mark.postgres
def test_renewal_preserves_history_and_old_attempt_cannot_checkout(checkout_case,monkeypatch):
    actor,post,oid,a,calls=checkout_case
    url='/api/branch-payments/attempts/'+a['id']
    post('customer',url+'/checkout')
    monkeypatch.setattr(payment_intents,'safe_unused_intent',lambda context,session,**kw:'provider_expired')
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
    def fail(*args,**kw): raise db.error(409,'PAYMENT_RECONCILIATION_REQUIRED','Unconfirmed')
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
    def proof(context,session,**kw):
        with monkeypatch.context() as stub:
            provider(stub,a['id'])
            post('customer',url+'/reconcile')
        return 'provider_expired'
    monkeypatch.setattr(payment_intents,'safe_unused_intent',proof)
    post('customer',url+'/renew',status=409)
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM branch_payment_renewals').fetchone()['n']==0
        assert c.execute('SELECT payment_status FROM orders WHERE id=%s',(oid,)).fetchone()['payment_status']=='paid'


@pytest.mark.postgres
def test_renewal_history_is_immutable_and_restorable(checkout_case,independent_restore_database,tmp_path):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from app.exports import build_archive,restore_archive
    actor,post,oid,a,calls=checkout_case
    post('customer','/api/branch-payments/attempts/'+a['id']+'/renew')
    with db.tx() as c:
        before=c.execute('SELECT * FROM branch_payment_renewals').fetchall()
        attempts=c.execute('SELECT * FROM branch_payment_attempts ORDER BY id').fetchall()
        archive,manifest=build_archive(c,full=True)
    assert manifest['counts']['branch_payment_renewals']==1
    for statement in ('DELETE FROM branch_payment_renewals',"UPDATE branch_payment_renewals SET reason='provider_expired'"):
        with pytest.raises(psycopg.Error):
            with db.tx() as c:c.execute(statement)
    schema='kps_restore_'+uuid4().hex
    assert restore_archive(archive,target_url=independent_restore_database,target_schema=schema,asset_directory=tmp_path/'assets')['verified']
    with psycopg.connect(independent_restore_database,row_factory=dict_row) as c:
        c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        assert c.execute('SELECT * FROM branch_payment_renewals').fetchall()==before
        assert c.execute('SELECT * FROM branch_payment_attempts ORDER BY id').fetchall()==attempts
    with db.tx() as c:
        assert c.execute('SELECT * FROM branch_payment_renewals').fetchall()==before
