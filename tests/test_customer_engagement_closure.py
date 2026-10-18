"""Source contracts + explicitly PostgreSQL-only financial/identity acceptance."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request
from app import activation, auth, catalog, customer_benefits as benefits, db, gift_orders, storefront_scope


def program():
    return {'terms_version':'SYNTHETIC-ONLY-v1','cashback_earn_bps':1000,'cashback_redemption_max_bps':5000,
            'redemption_treatment':'promotional_discount','expiration_policy':'no_expiration',
            'refund_policy':'proportional_confirmed_refund','minimum_items_halalas':0}


def rule():
    now=datetime.now(timezone.utc)
    return {'code':'SYNTHETIC','label':'TEST ONLY','discount_bps':5000,'branch_ids':[],
            'services':['pickup','car','delivery'],'product_ids':['coffee'],'segment':'all','customer_ids':[],
            'customer_limit':1,'total_limit':10,'minimum_items_halalas':0,'allow_cashback_redemption':True,
            'automatic':False,'starts_at':now-timedelta(hours=1),'ends_at':now+timedelta(hours=1),'terms_version':'test-v1'}


def enable(monkeypatch,feature):
    sha='a'*40
    monkeypatch.setenv('RUNTIME_REVISION',sha)
    for suffix,value in [('ENABLED','true'),('ACCEPTANCE_SHA',sha),('OWNER_APPROVAL_SHA',sha)]:
        monkeypatch.setenv('KPS_'+feature.upper()+'_'+suffix,value)
        if feature!='multibrand':
            monkeypatch.setenv('KPS_'+feature.upper()+'_KPS_'+suffix,value)


def test_activating_kps_does_not_activate_enbishan(monkeypatch):
    enable(monkeypatch,'gift_orders')
    assert activation.enabled('gift_orders','kps')
    assert not activation.enabled('gift_orders','enbishan')


@pytest.mark.parametrize('feature',['customer_benefits','gift_orders','multibrand'])
def test_activation_defaults_off_and_exact_sha_is_required(monkeypatch,feature):
    prefix='KPS_'+feature.upper()
    monkeypatch.delenv(prefix+'_ENABLED',raising=False)
    assert activation.enabled(feature) is False
    monkeypatch.setenv(prefix+'_ENABLED','true')
    with pytest.raises(HTTPException):activation.enabled(feature)
    enable(monkeypatch,feature)
    assert activation.enabled(feature)
    monkeypatch.setenv(prefix+'_OWNER_APPROVAL_SHA','b'*40)
    with pytest.raises(HTTPException):activation.enabled(feature)


def test_discount_does_not_discount_modifiers_delivery_or_foreign_items():
    items=[{'product_id':'coffee','unit_price_halalas':1000,'quantity':2,'line_total_halalas':2500},
           {'product_id':'other','unit_price_halalas':900,'quantity':1,'line_total_halalas':900}]
    assert benefits.calculate(items,rule(),0,program())==(1000,0)
    assert benefits.calculate(items,rule(),500,program())==(1000,500)
    with pytest.raises(HTTPException):benefits.calculate(items,rule(),1201,program())
    r=rule();r['allow_cashback_redemption']=False
    with pytest.raises(HTTPException):benefits.calculate(items,r,1,program())


@pytest.mark.parametrize('bad',[True,-1,1.5,'10',10000001])
def test_redemption_requires_bounded_integer_halas(bad):
    from tests.test_core import _body
    with pytest.raises(ValidationError):catalog.QuoteBody(**{**_body(),'cashback_halalas':bad})


def test_policy_requires_explicit_accounting_terms_rates_and_timezones():
    with pytest.raises(ValidationError):benefits.Program(terms_version='test')
    r=rule();r['starts_at']=datetime.now()
    with pytest.raises(ValidationError):benefits.Rule(**r)
    r=rule();r['segment']='selected'
    with pytest.raises(ValidationError):benefits.Rule(**r)
    r=rule();r['discount_bps']=10000
    with pytest.raises(ValidationError):benefits.Rule(**r)


def test_disabled_benefits_do_not_read_wallet_or_change_ordinary_quotes(monkeypatch):
    from tests.test_core import _body
    monkeypatch.delenv('KPS_CUSTOMER_BENEFITS_ENABLED',raising=False)
    class NoDatabase:
        def execute(self,*args):raise AssertionError('Disabled feature accessed the database')
    q={'total_halalas':1000,'discount_halalas':0}
    assert benefits.apply(NoDatabase(),catalog.QuoteBody(**_body()),q) is q
    with pytest.raises(HTTPException):benefits.apply(NoDatabase(),catalog.QuoteBody(**{**_body(),'benefit_code':'SYNTHETIC'}),q)


def test_additive_fields_preserve_existing_order_replay_fingerprints():
    from tests.test_core import _body
    ordinary=catalog.QuoteBody(**_body())
    legacy={k:v for k,v in ordinary.model_dump().items() if k not in {'gift','benefit_code','cashback_halalas'}}
    assert db.digest(catalog.order_request_payload(ordinary))==db.digest(legacy)
    explicit=catalog.QuoteBody(**{**_body(),'cashback_halalas':0,'benefit_code':''})
    assert catalog.order_request_payload(explicit)==legacy
    changed=catalog.QuoteBody(**{**_body(),'benefit_code':'SYNTHETIC'})
    assert db.digest(catalog.order_request_payload(changed))!=db.digest(legacy)


def test_unsafe_text_is_rejected_before_postgresql():
    from app.customer_engagement import Followup,MarketingConsent
    with pytest.raises(ValidationError):Followup(note='bad\x00text',status='open')
    with pytest.raises(ValidationError):MarketingConsent(channel='sms',granted=True,notice_version='bad\x00version')
    invalid=rule();invalid['product_ids']=['bad\x00id']
    with pytest.raises(ValidationError):benefits.Rule(**invalid)


def test_payment_sandbox_cannot_consume_real_wallet_or_eligibility(monkeypatch):
    from tests.test_core import _body
    enable(monkeypatch,'customer_benefits')
    class NoDatabase:
        def execute(self,*args):raise AssertionError('Sandbox touched a live benefit ledger')
    user={'id':'synthetic','role':'customer','brand_id':'kps','display_name':'TEST',
          'phone_sms_verified_at':datetime.now(timezone.utc),'customer_verified_at':datetime.now(timezone.utc)}
    quote={'branch_payment_mode':'test','total_halalas':1000}
    assert benefits.apply(NoDatabase(),catalog.QuoteBody(**_body()),quote,user) is quote
    with pytest.raises(HTTPException) as rejected:
        benefits.apply(NoDatabase(),catalog.QuoteBody(**{**_body(),'cashback_halalas':1}),quote,user)
    assert rejected.value.detail['code']=='BENEFIT_SANDBOX_ISOLATION'


def test_approved_runtime_uses_real_host_and_ignores_forwarded_brand(monkeypatch):
    import json
    enable(monkeypatch,'multibrand')
    monkeypatch.delenv('ENBISHAN_ISOLATED_ENABLED',raising=False)
    monkeypatch.setenv('DATABASE_URL','postgresql://synthetic@127.0.0.1/test_runtime')
    monkeypatch.setenv('KPS_MULTIBRAND_DATABASE_HOST','127.0.0.1')
    monkeypatch.setenv('KPS_MULTIBRAND_DATABASE_NAME','test_runtime')
    monkeypatch.setenv('PUBLIC_BASE_URL','https://order.kps.sa')
    monkeypatch.setenv('KPS_STOREFRONT_HOST_BRANDS',json.dumps({'order.kps.sa':'kps','order.enbishan.com':'enbishan'}))
    monkeypatch.setenv('APP_ALLOWED_HOSTS','order.kps.sa,order.enbishan.com')
    def request(host):
        return Request({'type':'http','method':'GET','path':'/api/catalog','scheme':'https',
                        'headers':[(b'host',host.encode()),(b'x-forwarded-host',b'order.kps.sa')]})
    assert storefront_scope.bound_brand(request('order.enbishan.com'))=='enbishan'
    with pytest.raises(HTTPException):storefront_scope.bound_brand(request('unknown.invalid'))


def test_gift_keeps_purchaser_ownership_and_uses_recipient_for_operations():
    from tests.test_core import _body
    body=catalog.QuoteBody(**{**_body(),'gift':{'recipient_name':'TEST recipient','recipient_phone':'0500001002','message':'TEST'}})
    user={'id':'purchaser','phone':'+966500001001'}
    customer={'name':'TEST buyer','phone':user['phone'],'phone_sms_verified':True}
    snapshot={}
    gift_orders.capture(body,user,customer,snapshot)
    assert snapshot['gift']['purchaser']['customer_id']=='purchaser'
    assert snapshot['gift']['purchaser']['phone']==user['phone']
    assert customer['phone']=='+966500001002' and not customer['phone_sms_verified']
    assert customer['name']=='TEST recipient'


def test_gift_invoice_buyer_is_purchaser_not_recipient():
    from app import fiscal
    from tests.test_fiscal import sample
    order=sample();order['snapshot']['gift']={'purchaser':{'customer_id':'buyer','name':'TEST BUYER','phone':'+966500001001'}}
    order['customer']={'name':'TEST RECIPIENT','phone':'+966500001002'}
    invoice=fiscal.build(order,'TEST-ONLY',datetime.now(timezone.utc))
    assert invoice['buyer']['customer_id']=='buyer'
    assert invoice['buyer']['phone']!=order['customer']['phone']


def test_runtime_database_binding_cannot_disable_original_isolation(monkeypatch):
    from app import multibrand
    monkeypatch.setenv('ENBISHAN_ISOLATED_ENABLED','true')
    enable(monkeypatch,'multibrand')
    with pytest.raises(RuntimeError):multibrand.isolation_guard()
    monkeypatch.delenv('ENBISHAN_ISOLATED_ENABLED')
    monkeypatch.setenv('DATABASE_URL','postgresql://synthetic@127.0.0.1/test_engagement')
    with pytest.raises(RuntimeError):multibrand.isolation_guard()
    monkeypatch.setenv('KPS_MULTIBRAND_DATABASE_HOST','127.0.0.1')
    monkeypatch.setenv('KPS_MULTIBRAND_DATABASE_NAME','test_engagement')
    multibrand.isolation_guard()
    monkeypatch.setenv('KPS_MULTIBRAND_DATABASE_NAME','wrong')
    with pytest.raises(RuntimeError):multibrand.isolation_guard()


def test_work_header_cannot_reclassify_customer_endpoints():
    for path in ('/api/quote','/api/orders','/api/customer/benefits','/api/customer/marketing-consent'):
        request=Request({'type':'http','method':'POST','path':path,'headers':[(b'x-kps-context',b'work')]})
        assert auth.session_context(request)=='customer'


@pytest.mark.parametrize('role',['customer','driver','delivery_manager','branch_manager','admin'])
def test_engagement_management_rejects_unauthorized_roles_before_reading(role,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import customer_engagement
    app=FastAPI();app.include_router(customer_engagement.router);app.include_router(benefits.router)
    app.dependency_overrides[auth.current_user]=lambda:{'role':role}
    def forbidden(*args,**kwargs):raise AssertionError('Unauthorized access reached the database')
    monkeypatch.setattr(db,'tx',forbidden)
    with TestClient(app) as client:
        assert client.get('/api/crm/benefits/setup').status_code==403
        assert client.get('/api/crm/customers/foreign/engagement').status_code==403


@pytest.fixture
def verified_customer(client):
    # Preverified synthetic state; this does NOT certify real SMS delivery.
    from tests.test_core import _authenticate,_make_user,_prepare_catalog
    user=_make_user();_prepare_catalog()
    with db.tx() as c:
        user=c.execute("UPDATE users SET phone='+966500001001',phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s RETURNING *",(user['id'],)).fetchone()
    _authenticate(client,user)
    with db.tx() as c:
        c.execute('UPDATE sessions SET customer_verified_at=now() WHERE user_id=%s',(user['id'],))
    return client,user


@pytest.mark.postgres
def test_explicit_consent_replay_revocation_and_brand_isolation(verified_customer):
    client,user=verified_customer
    assert client.get('/api/customer/marketing-consent').json()['marketing']=={'sms':False,'whatsapp':False}
    payload={'channel':'sms','granted':True,'notice_version':'TEST-v1'}
    assert client.post('/api/customer/marketing-consent',json=payload,headers={'Idempotency-Key':'test-no-notice-0001'}).status_code==409
    with db.tx() as c:
        c.execute("INSERT INTO customer_marketing_notices(id,brand_id,version,text_ar,text_en,actor_id) VALUES('test-notice','kps','TEST-v1','إشعار تجريبي فقط','TEST ONLY NOTICE',%s)",(user['id'],))
        c.execute("UPDATE app_settings SET settings=settings||'{\"customer_marketing_notice_id\":\"test-notice\"}'::jsonb WHERE brand_id='kps'")
    first=client.post('/api/customer/marketing-consent',json=payload,headers={'Idempotency-Key':'test-consent-0001'})
    assert first.status_code==200,first.text
    assert client.post('/api/customer/marketing-consent',json=payload,headers={'Idempotency-Key':'test-consent-0001'}).json()==first.json()
    revoked=client.post('/api/customer/marketing-consent',json={**payload,'granted':False},headers={'Idempotency-Key':'test-consent-0002'})
    assert revoked.status_code==200,revoked.text
    assert client.get('/api/customer/marketing-consent').json()['marketing']['sms'] is False
    with db.tx() as c:
        assert c.execute('SELECT count(*) AS n FROM customer_marketing_consents WHERE customer_id=%s',(user['id'],)).fetchone()['n']==2
    selected=client.get('/api/customer/marketing-consent?brand_id=enbishan')
    assert selected.status_code==200 and selected.json()['brand_id']=='kps'
    # An ignored selector must never change the authenticated scope.
    response=client.get('/api/customer/benefits?brand_id=enbishan')
    assert response.status_code==200 and response.json()['brand_id']=='kps'


@pytest.mark.postgres
def test_redemption_once_refund_return_and_earn_clawback(verified_customer,monkeypatch):
    from tests.test_core import _body
    client,user=verified_customer
    enable(monkeypatch,'customer_benefits')
    with db.tx() as c:
        c.execute("INSERT INTO benefit_programs(id,brand_id,actor_id,definition) VALUES('test-program','kps',%s,%s)",(user['id'],db.json(program())))
        c.execute("UPDATE app_settings SET settings=settings||'{\"customer_benefit_program_id\":\"test-program\"}'::jsonb WHERE brand_id='kps'")
        # A real source earn, with collection and completed state in one txn.
        oid='earned-'+uuid4().hex
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps','kps-branch-1',%s,'pickup','completed','not_applicable','cash',1000,0,1000,'{}','{}')",(oid,user['id']))
        c.execute("INSERT INTO customer_wallets(brand_id,customer_id) VALUES('kps',%s)",(user['id'],))
        c.execute("INSERT INTO benefit_order_states(order_id,brand_id,customer_id,discount_halalas,redeemed_halalas,earn_bps,earn_base_halalas) VALUES(%s,'kps',%s,0,0,1000,1000)",(oid,user['id']))
        collection='col-'+uuid4().hex
        c.execute("INSERT INTO collections(id,order_id,collector_id,method,amount_halalas) VALUES(%s,%s,%s,'cash',1000)",(collection,oid,user['id']))
    assert client.get('/api/customer/benefits').json()['balance_halalas']==100
    payload={**_body(),'cashback_halalas':50};payload['customer']['phone']=user['phone']
    response=client.post('/api/orders',json=payload,headers={'Idempotency-Key':'test-wallet-order-0001'})
    assert response.status_code==200,response.text
    replay=client.post('/api/orders',json=payload,headers={'Idempotency-Key':'test-wallet-order-0001'})
    assert replay.json()==response.json()
    assert client.get('/api/customer/benefits').json()['balance_halalas']==50
    with db.tx() as c:
        c.execute("INSERT INTO refunds(id,collection_id,order_id,method,amount_halalas,reason,authorized_by) VALUES(%s,%s,%s,'cash',1000,'TEST confirmed refund',%s)",('refund-'+uuid4().hex,collection,oid,user['id']))
    assert client.get('/api/customer/benefits').json()['balance_halalas']==-50
    with db.tx() as c:
        new_id=response.json()['order']['id']
        c.execute("UPDATE orders SET preparation_status='cancelled' WHERE id=%s",(new_id,))
    assert client.get('/api/customer/benefits').json()['balance_halalas']==0
    with db.tx() as c:
        c.execute("UPDATE orders SET version=version+1 WHERE id=%s",(new_id,))
    assert client.get('/api/customer/benefits').json()['balance_halalas']==0


@pytest.mark.postgres
def test_coupon_customer_limit_serializes_concurrent_orders(verified_customer,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from tests.test_core import _body
    from app.orders import _create_order
    _,user=verified_customer;enable(monkeypatch,'customer_benefits')
    definition=rule();definition['product_ids']=['kps-01'];definition['discount_bps']=1000
    with db.tx() as c:
        c.execute("INSERT INTO benefit_rules(id,brand_id,code,definition,active,actor_id) VALUES('test-once','kps','SYNTHETIC',%s,true,%s)",(db.json(benefits.Rule(**definition).model_dump(mode='json')),user['id']))
    principal=dict(user,customer_verified_at=datetime.now(timezone.utc),customer_phone_required=False)
    payload=catalog.QuoteBody(**{**_body(),'benefit_code':'SYNTHETIC'})
    def order(index):
        try:return _create_order(payload,None,principal,'test-concurrent-offer-'+str(index))
        except HTTPException as e:return e.detail['code']
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(order,[1,2]))
    assert sum(isinstance(r,dict) for r in results)==1
    assert results.count('BENEFIT_NOT_ELIGIBLE')==1
    with db.tx() as c:
        assert c.execute("SELECT count(*) AS n FROM benefit_order_states WHERE rule_id='test-once'").fetchone()['n']==1


@pytest.mark.postgres
def test_wallet_and_consent_never_follow_phone_to_another_brand(verified_customer):
    from tests.test_core import _make_user
    client,user=verified_customer
    with db.tx() as c:c.execute("INSERT INTO brands(id,name) VALUES('enbishan','TEST ONLY') ON CONFLICT DO NOTHING")
    foreign=_make_user(brand='enbishan')
    with db.tx() as c:
        c.execute('UPDATE users SET phone=%s,phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s',(user['phone'],foreign['id']))
        c.execute("INSERT INTO customer_wallets(brand_id,customer_id,balance_halalas) VALUES('enbishan',%s,500)",(foreign['id'],))
    result=client.get('/api/customer/benefits?brand_id=enbishan&customer_id='+foreign['id'])
    assert result.status_code==200,result.text
    assert result.json()['brand_id']=='kps' and result.json()['balance_halalas']==0 and not result.json()['entries']
    payload={'channel':'sms','granted':False,'notice_version':'withdrawal','customer_id':foreign['id']}
    assert client.post('/api/customer/marketing-consent',json=payload,headers={'Idempotency-Key':'test-foreign-consent'}).status_code==422
