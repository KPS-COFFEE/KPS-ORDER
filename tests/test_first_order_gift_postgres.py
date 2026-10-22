"""Real quotation/order/claim atomicity; no SMS or runtime database access."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import io
import json
import zipfile
import os
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
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


@pytest.mark.parametrize('phone', ['0512345678', '512345678', '966512345678',
    '00966512345678', '+966 51 234 5678', '٠٥١٢٣٤٥٦٧٨', '۰۵۱۲۳۴۵۶۷۸'])
def test_phone_variants_new_session_and_recreated_identity_cannot_reclaim(gift_setup, phone):
    client, user, body, _ = gift_setup
    first = client().post('/api/orders', json=body, headers={'Idempotency-Key': uuid4().hex})
    assert first.status_code == 200, first.text
    # A new identity/session with the same verified phone must still hit the
    # permanent brand+phone claim, not a user-id, cookie, or policy-version flag.
    replacement = 'replacement-' + uuid4().hex
    with db.tx() as conn:
        # Simulate the old account relinquishing its phone, without deleting
        # its order or permanent redemption ledger. Normal identity uniqueness
        # remains enabled throughout this test.
        conn.execute("UPDATE users SET phone=NULL,phone_verified_at=NULL,phone_sms_verified_at=NULL,active=false WHERE id=%s",
                     (user['id'],))
        conn.execute("INSERT INTO users(id,brand_id,role,display_name,phone,phone_verified_at,phone_sms_verified_at) "
                     "VALUES(%s,'ops-brand','customer','اختبار',%s,now(),now())",
                     (replacement, auth.normalize_phone(phone)))
    user.update(id=replacement, phone=auth.normalize_phone(phone))
    body['customer']['phone'] = phone
    response = client().post('/api/gifts/quote', json=body)
    assert response.status_code == 200, response.text
    assert response.json()['quote']['first_order_gift']['status'] == 'claimed'
    assert not any(item.get('is_gift') for item in response.json()['quote']['items'])
    second = client().post('/api/orders', json=body, headers={'Idempotency-Key': uuid4().hex})
    assert second.status_code == 200, second.text
    assert not any(item.get('is_gift') for item in second.json()['order']['snapshot']['items'])
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM first_order_gift_claims').fetchone()['n'] == 1


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


def test_expired_offer_rejects_stale_gift_confirmation_without_consuming_claim(gift_setup):
    client, user, body, p = gift_setup
    p['ends_at'] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    with db.tx() as conn:
        conn.execute("UPDATE app_settings SET settings=jsonb_set(settings,'{first_order_gift}',%s::jsonb) WHERE brand_id='ops-brand'", (db.json(p),))
    quote = client().post('/api/gifts/quote', json=body)
    assert quote.status_code == 200, quote.text
    assert quote.json()['quote']['first_order_gift']['status'] == 'expired'
    assert not any(item.get('is_gift') for item in quote.json()['quote']['items'])
    body.update(expected_gift=True, expected_gift_version=1)
    stale = client().post('/api/orders', json=body, headers={'Idempotency-Key': uuid4().hex})
    assert stale.status_code == 409 and stale.json()['detail']['code'] == 'GIFT_CHANGED'
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM first_order_gift_claims').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) n FROM orders').fetchone()['n'] == 0


def test_older_owner_form_cannot_silently_clear_expiry_and_owner_can_stop_early(gift_setup, monkeypatch):
    customer_client, user, _, p = gift_setup
    p['ends_at'] = (datetime.now(timezone.utc)+timedelta(days=7)).isoformat()
    with db.tx() as conn:
        conn.execute("UPDATE app_settings SET settings=jsonb_set(settings,'{first_order_gift}',%s::jsonb) WHERE brand_id='ops-brand'", (db.json(p),))
    client = customer_client()
    client.app.dependency_overrides[auth.current_user] = lambda: user | {'role': 'owner'}
    monkeypatch.setattr(auth, 'require_owner_step_up', lambda *_: None)
    payload = {'policy': {key: p[key] for key in ('enabled', 'starts_at', 'items')},
               'expected_version': 1, 'reason': 'إيقاف مبكر مع حفظ الموعد'}
    payload['policy']['enabled'] = False
    stopped = client.put('/api/gifts/policy', json=payload, headers={'Idempotency-Key': uuid4().hex})
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()['policy']['enabled'] is False
    assert datetime.fromisoformat(stopped.json()['policy']['ends_at']) == datetime.fromisoformat(p['ends_at'])
    payload['expected_version'] = 2
    payload['policy']['ends_at'] = None
    explicit = client.put('/api/gifts/policy', json=payload, headers={'Idempotency-Key': uuid4().hex})
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()['policy']['ends_at'] is None


def test_unverified_contact_and_other_brand_cannot_get_a_gift(gift_setup):
    client,user,body,_=gift_setup
    user['customer_verified_at']=None
    assert client().post('/api/gifts/quote',json=body).status_code==403
    body['brand_id']='ops-other'
    assert client().post('/api/gifts/quote',json=body).status_code==403


def test_backup_restore_preserves_used_phone_without_regranting(gift_setup):
    client,user,body,_=gift_setup
    created=client().post('/api/orders',json=body,headers={'Idempotency-Key':uuid4().hex})
    assert created.status_code==200,created.text
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        archive,_=exports.build_archive(conn,full=True)
    target='kps_restore_'+uuid4().hex
    url=os.environ['TEST_DATABASE_URL']
    try:
        exports.restore_archive(archive,target_url=url,target_schema=target)
        with psycopg.connect(url,row_factory=dict_row) as conn:
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
            assert conn.execute("SELECT count(*) n FROM first_order_gift_claims WHERE brand_id='ops-brand' AND phone='+966512345678'").fetchone()['n']==1
            typed=catalog.QuoteBody.model_validate(body)
            quote=catalog.quote_order(conn,typed)
            settings=conn.execute("SELECT settings FROM app_settings WHERE brand_id='ops-brand'").fetchone()['settings']
            assert gift.prepare(conn,typed,quote,user,settings) is None
            assert quote['first_order_gift']['status']=='claimed'
    finally:
        # Only the exact new UUID schema owned by this test may be removed.
        with psycopg.connect(url,autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(target)))


def test_failure_after_claim_insert_rolls_back_order_claim_and_replay_key(gift_setup):
    _,user,body,_=gift_setup
    with db.tx() as conn:
        conn.execute("CREATE FUNCTION fail_synthetic_gift() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic gift failure'; END $$")
        conn.execute('CREATE TRIGGER fail_synthetic_gift AFTER INSERT ON first_order_gift_claims FOR EACH ROW EXECUTE FUNCTION fail_synthetic_gift()')
    typed=catalog.QuoteBody.model_validate(body);key=uuid4().hex
    with pytest.raises(psycopg.Error):
        orders._create_order(typed,None,user,key)
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM orders').fetchone()['n']==0
        assert conn.execute('SELECT count(*) n FROM first_order_gift_claims').fetchone()['n']==0
        conn.execute('DROP TRIGGER fail_synthetic_gift ON first_order_gift_claims')
    retry=orders._create_order(typed,None,user,key)
    if isinstance(retry,str):retry=json.loads(retry)
    assert retry['order']['snapshot']['first_order_gift']['status']=='eligible'
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM first_order_gift_claims').fetchone()['n']==1
