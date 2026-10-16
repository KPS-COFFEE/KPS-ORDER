"""Live-mode contracts with synthetic HTTP only; never merchant acceptance."""
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from uuid import uuid4
import json
import httpx
import pytest
from fastapi import HTTPException
from cryptography.fernet import Fernet
from app import db,payment_gateways as gateways,payment_runtime as runtime,payment_intents,payment_refunds
from test_operations import ops
from test_branch_payments import accept,attempt


def approve(monkeypatch,brand='ops-brand',seller='fixture-seller'):
    prefix=gateways.credential_prefix('moyasar',brand,seller)
    values={
        'RUNTIME_REVISION':'a'*40,
        'KPS_BRANCH_PAYMENT_LIVE_ENABLED':'true',
        'KPS_BRANCH_PAYMENT_LIVE_ACCEPTANCE_SHA':'a'*40,
        'KPS_BRANCH_PAYMENT_LIVE_OWNER_APPROVAL_SHA':'a'*40,
        prefix+'_LIVE_ACCOUNT_VERIFIED':'true',
        prefix+'_LIVE_ACCOUNT_ID':'synthetic-account',
        prefix+'_LIVE_OWNERSHIP_EVIDENCE':'synthetic-evidence-not-merchant-approval',
        prefix+'_LIVE_SECRET_KEY':'sk_live_synthetic_not_real',
        prefix+'_LIVE_PUBLIC_KEY':'pk_live_synthetic_not_real',
        'PUBLIC_BASE_URL':'https://fixture.invalid',
        'KPS_BRANCH_PAYMENT_CHECKOUT_ENABLED':'true',
        'KPS_BRANCH_PAYMENT_REFUNDS_ENABLED':'true',
        'KPS_PAYMENT_CHECKOUT_ENCRYPTION_KEY':Fernet.generate_key().decode(),
    }
    for key,value in values.items():monkeypatch.setenv(key,value)
    return prefix,values


@pytest.mark.parametrize('missing',[
    'KPS_BRANCH_PAYMENT_LIVE_ENABLED','KPS_BRANCH_PAYMENT_LIVE_ACCEPTANCE_SHA',
    'KPS_BRANCH_PAYMENT_LIVE_OWNER_APPROVAL_SHA','RUNTIME_REVISION',
    '_LIVE_ACCOUNT_VERIFIED','_LIVE_ACCOUNT_ID','_LIVE_OWNERSHIP_EVIDENCE','_LIVE_SECRET_KEY'])
def test_live_requests_blocked_before_network_without_each_gate(monkeypatch,missing):
    prefix,values=approve(monkeypatch)
    monkeypatch.delenv(prefix+missing if missing.startswith('_') else missing)
    monkeypatch.delenv('RENDER_GIT_COMMIT',raising=False)
    monkeypatch.setattr(httpx,'Client',lambda **kw:pytest.fail('Unapproved provider traffic'))
    ctx=gateways.PaymentContext('ops-brand','fixture-seller','order','attempt',1000,mode='live')
    for call in (lambda:gateways.adapter_for('moyasar').verify_live_payment(ctx,str(uuid4())),
                 lambda:payment_intents.create_moyasar_live_intent(ctx),
                 lambda:payment_refunds.request(ctx,str(uuid4()),100)):
        with pytest.raises(HTTPException):call()


def test_live_approval_is_exact_release_and_seller_scoped(monkeypatch):
    approve(monkeypatch)
    assert runtime.live_blockers('ops-brand','fixture-seller')==[]
    assert runtime.live_blockers('other-brand','fixture-seller')
    assert runtime.live_blockers('ops-brand','other-seller')
    monkeypatch.setenv('RUNTIME_REVISION','b'*40)
    assert len(runtime.live_blockers('ops-brand','fixture-seller'))==2


@pytest.mark.parametrize('mode',['','LIVE','production','unknown'])
def test_invalid_runtime_mode_fails_closed(monkeypatch,mode):
    monkeypatch.setenv('KPS_BRANCH_PAYMENT_MODE',mode)
    with pytest.raises(HTTPException):runtime.mode()


def test_cross_mode_proof_and_credentials_are_rejected(monkeypatch):
    prefix,values=approve(monkeypatch)
    ctx=gateways.PaymentContext('ops-brand','fixture-seller','order','attempt',1000,mode='live')
    pid=str(uuid4())
    payload={'id':pid,'amount':1000,'currency':'SAR','status':'paid','refunded':0,'metadata':ctx.metadata,'live':True}
    adapter=gateways.adapter_for('moyasar')
    assert adapter.validate(payload,pid,ctx).mode=='live'
    for flag in (False,None,'true'):
        with pytest.raises(HTTPException):adapter.validate(payload|{'live':flag},pid,ctx)
    with pytest.raises(HTTPException):adapter.validate(payload,pid,replace(ctx,mode='test'))
    assert ctx.idempotency_reference!=replace(ctx,mode='test').idempotency_reference
    monkeypatch.setenv(prefix+'_LIVE_SECRET_KEY','sk_test_synthetic_not_real')
    with pytest.raises(HTTPException):runtime.secret(ctx)


@pytest.mark.postgres
def test_live_contract_checkout_verify_refund_and_separate_report(ops,monkeypatch):
    actor,post,create,shifts=ops
    prefix,_=approve(monkeypatch)
    oid='live-contract-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'ops-brand','ops-branch','ops-customer','car','new','not_applicable','card',1000,0,1000,%s,'{}')",
            (oid,db.json({'items':[],'branch_payment_mode':'live','branch_payment_seller':'fixture-seller'})))
    version=accept(post,oid)
    a=attempt(post,oid,version)
    assert a['mode']=='live'
    calls=[];state={'refunded':0,'paid':False}; original=httpx.Client
    def handle(request):
        calls.append(request)
        if request.url.path=='/v1/payment_intents':
            payload=json.loads(request.content)
            return httpx.Response(201,json=payload|{'id':str(uuid4()),'status':'created',
                'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),
                'client_secret':'pi_secret_synthetic_live_contract'})
        assert request.url.path in {'/v1/payments/'+a['provider_payment_id'],'/v1/payments/'+a['provider_payment_id']+'/refund'}
        if request.method=='POST':state['refunded']+=json.loads(request.content)['amount']
        return httpx.Response(200,json={'id':a['provider_payment_id'],'amount':1000,'currency':'SAR',
            'live':True,'status':'refunded' if state['refunded']==1000 else ('paid' if state['paid'] else 'initiated'),
            'refunded':state['refunded'],'metadata':{'brand_id':'ops-brand','entity_id':'fixture-seller','order_id':oid,'attempt_id':a['id']}})
    monkeypatch.setattr(httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    base='/api/branch-payments/attempts/'+a['id']
    config=post('customer',base+'/checkout')
    assert config['mode']=='live' and config['publishable_api_key'].startswith('pk_live_')
    post('customer',base+'/reconcile',status=409)
    post('owner','/api/orders/'+oid+'/preparation',{'status':'preparing','version':version},status=409)
    state['paid']=True
    from app import branch_payment_webhooks as webhooks
    monkeypatch.setenv('KPS_BRANCH_PAYMENT_WEBHOOKS_ENABLED','true')
    monkeypatch.setenv(prefix+'_LIVE_WEBHOOK_SECRET','synthetic-live-webhook-secret-0123456789')
    body={'id':'event_live_fixture','type':'payment_paid','live':True,
          'secret_token':'synthetic-live-webhook-secret-0123456789','data':{'id':a['provider_payment_id']}}
    assert actor('customer').post('/paymenthooks/moyasar',json=body|{'live':False}).status_code==401
    assert actor('customer').post('/paymenthooks/moyasar',json=body).status_code==202
    assert webhooks.dispatch_once() is True
    assert post('customer',base+'/reconcile')['attempt']['status']=='verified'
    assert post('customer',base+'/reconcile')['attempt']['status']=='verified'
    with db.tx() as c:
        order=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        assert c.execute('SELECT mode FROM branch_online_receipts WHERE order_id=%s',(oid,)).fetchone()['mode']=='live'
    row=post('owner','/api/branch-payments/orders/'+oid+'/refunds',{'version':order['version'],'amount_halalas':1000,'reason':'Synthetic live-contract refund'})['refund']
    assert post('owner','/api/branch-payments/refunds/'+row['id']+'/execute')['refund']['status']=='verified'
    assert post('owner','/api/branch-payments/refunds/'+row['id']+'/execute')['refund']['status']=='verified'
    assert sum(r.method=='POST' and r.url.path.endswith('/refund') for r in calls)==1
    for mode,count in [('test',0),('live',1)]:
        report=actor('owner').get('/api/branch-payments/report?branch_id=ops-branch&mode='+mode)
        assert report.status_code==200,report.text
        assert report.json()['mode']==mode
        assert report.json()['totals']['payment_count']==count
        assert report.json()['totals']['refund_count']==count
    assert actor('owner').get('/api/branch-payments/report?branch_id=ops-branch&mode=invalid').status_code==422
