"""Real quotation/order/claim atomicity; no SMS or runtime database access."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import io
import json
import zipfile
import pytest
from fastapi import HTTPException
from app import auth, catalog, db, exports, first_order_gift as gift, orders
from test_operations import ops
from test_core import _branch

pytestmark=pytest.mark.postgres


@pytest.fixture
def gift_setup(ops,monkeypatch):
    actor,_,_,_=ops
    settings=_branch()['settings']|{'minimum_order_halalas':1000}
    p={'enabled':True,'starts_at':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(),
       'items':[{'product_id':'gift-coffee','options':{}},{'product_id':'gift-cookie','options':{}}],'version':1}
    with db.tx() as c:
        c.execute("UPDATE branches SET settings=%s WHERE id='ops-branch'",(db.json(settings),))
        c.execute("INSERT INTO app_settings(brand_id,settings) VALUES('ops-brand',%s)",
            (db.json({'delivery_workflow':'branch_without_manager','first_order_gift':p}),))
        for pid,name in [('gift-paid','مدفوع'),('gift-coffee','قهوة اليوم'),('gift-cookie','كوكيز')]:
            c.execute("INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,'ops-brand',%s,'اختبار',1000,%s)",
                (pid,name,db.json({'source_verified':True,'enabled':True,'option_groups':[]})))
        c.execute("UPDATE users SET phone='+966512345678',phone_verified_at=now(),phone_sms_verified_at=now() WHERE id='ops-customer'")
        user=c.execute("SELECT * FROM users WHERE id='ops-customer'").fetchone()
    user['customer_verified_at']=datetime.now(timezone.utc)
    user['customer_phone_required']=True
    def login():
        # Existing identity suites cover actual SMS proof; this fixture isolates
        # business logic and real PostgreSQL transactions after verification.
        actor('customer').app.dependency_overrides[auth.current_user]=lambda:user
        return actor('customer')
    def customer_client():
        client=actor('customer');client.app.dependency_overrides[auth.current_user]=lambda:user
        return client
    body={'brand_id':'ops-brand','branch_id':'ops-branch','service':'delivery','payment_method':'cash',
          'items':[{'product_id':'gift-paid','quantity':1}],
          'customer':{'name':'اختبار','phone':'512345678','neighborhood':'حي اختبار','address':'عنوان اختبار',
                      'location':{'lat':28.4,'lng':45.9}}}
    return customer_client,user,body,p


def test_quote_does_not_claim_create_is_atomic_and_replay_identical(gift_setup):
    client,user,body,p=gift_setup
    response=client().post('/api/gifts/quote',json=body)
    assert response.status_code==200,response.text
    quote=response.json()['quote'];assert quote['first_order_gift']['status']=='eligible'
    assert len(quote['items'])==3 and quote['subtotal_halalas']==1000 and quote['total_halalas']==1500
    with db.tx() as c:assert c.execute('SELECT count(*) n FROM first_order_gift_claims').fetchone()['n']==0
    body.update(expected_total_halalas=1500,expected_gift=True,expected_gift_version=1)
    key=uuid4().hex
    first=client().post('/api/orders',json=body,headers={'Idempotency-Key':key})
    assert first.status_code==200,first.text
    assert client().post('/api/orders',json=body,headers={'Idempotency-Key':key}).json()==first.json()
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM orders').fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM first_order_gift_claims').fetchone()['n']==1
        c.execute("UPDATE app_settings SET settings=jsonb_set(settings,'{first_order_gift,version}','2') WHERE brand_id='ops-brand'")
    assert client().post('/api/gifts/quote',json=body).json()['quote']['first_order_gift']['status']=='claimed'
    duplicate=client().post('/api/orders',json=body,headers={'Idempotency-Key':uuid4().hex})
    assert duplicate.status_code==409 and duplicate.json()['detail']['code']=='GIFT_CHANGED'
    with db.tx() as c:
        archive,manifest=exports.build_archive(c,brand_id='ops-brand')
        assert manifest['counts']['first_order_gift_claims']==1


def test_two_simultaneous_orders_receive_one_gift(gift_setup):
    _,user,body,_=gift_setup
    def create(_):
        result=orders._create_order(catalog.QuoteBody.model_validate(body),None,user,uuid4().hex)
        if isinstance(result,str):result=json.loads(result)
        return result['order']['snapshot'].get('first_order_gift',{}).get('status')
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(create,range(2)))
    assert sorted(statuses)==['claimed','eligible']
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM orders').fetchone()['n']==2
        assert c.execute('SELECT count(*) n FROM first_order_gift_claims').fetchone()['n']==1


def test_failed_order_rolls_back_claim_and_delivery_fee_never_meets_minimum(gift_setup):
    client,user,body,_=gift_setup
    body.update(expected_gift=True,expected_gift_version=1,expected_total_halalas=1)
    assert client().post('/api/orders',json=body,headers={'Idempotency-Key':uuid4().hex}).status_code==409
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM first_order_gift_claims').fetchone()['n']==0
        c.execute("UPDATE branches SET settings=jsonb_set(settings,'{minimum_order_halalas}','1200') WHERE id='ops-branch'")
    body.pop('expected_total_halalas')
    response=client().post('/api/gifts/quote',json=body)
    assert response.status_code==409 and response.json()['detail']['code']=='MINIMUM_ORDER_NOT_MET'


def test_unverified_contact_and_other_brand_cannot_get_a_gift(gift_setup):
    client,user,body,_=gift_setup
    user['customer_verified_at']=None
    assert client().post('/api/gifts/quote',json=body).status_code==403
    body['brand_id']='ops-other'
    assert client().post('/api/gifts/quote',json=body).status_code==403
