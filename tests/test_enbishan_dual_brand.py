"""Actual local PostgreSQL dual-brand journeys with synthetic identities only."""
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import auth, db, multibrand
from test_core import _branch


@pytest.fixture
def dual(request, monkeypatch, safe_test_environment):
    monkeypatch.setenv('ENBISHAN_ISOLATED_ENABLED', 'true')
    client = request.getfixturevalue('client')
    with db.tx() as c:
        c.execute("INSERT INTO brands(id,name,settings) VALUES('enbishan','ENBISHAN TEST ONLY','{\"services\":{\"delivery\":true}}')")
        c.execute("INSERT INTO app_settings(brand_id,settings) VALUES('enbishan','{\"customer_phone_required\":false}')")
        settings = _branch()['settings']
        c.execute('UPDATE branches SET settings=%s WHERE id=%s', (db.json(settings), 'kps-branch-1'))
        for number, name in [(1, 'الربوة'), (2, 'المحمدية')]:
            c.execute('INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                      (f'enbishan-test-{number}', number, f'test-{number}', 'enbishan', 'TEST ONLY '+name, 'حفر الباطن', db.json(settings)))
        for brand in ['kps', 'enbishan']:
            c.execute('INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,%s,%s,%s,%s,%s)',
                ('dual-'+brand, brand, 'TEST ONLY '+brand, 'TEST', 1000 if brand=='kps' else 2000,
                 db.json({'enabled': True, 'source_verified': True})))
        accounts = [('dual-owner','owner','kps'),('dual-manager','delivery_manager','kps'),('dual-driver','driver','kps'),
                    ('dual-kps-customer','customer','kps'),('dual-enbishan-customer','customer','enbishan')]
        for identifier, role, brand in accounts:
            c.execute('INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES(%s,%s,%s,%s,%s)',
                      (identifier, 'SYNTHETIC '+identifier, role, brand, db.json(['kps-branch-1'] if brand=='kps' else ['enbishan-test-1'])))
            if role != 'customer':
                c.execute('INSERT INTO staff_brand_memberships(user_id,brand_id,branch_ids,granted_by) VALUES(%s,%s,%s,%s)',
                          (identifier, 'enbishan', db.json(['enbishan-test-1','enbishan-test-2']), 'dual-owner'))
    credentials = {}
    def actor(name, brand=None):
        identifier = 'dual-'+name
        if identifier not in credentials:
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            with db.tx() as c:
                c.execute('INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at,step_up_at) VALUES(%s,%s,%s,%s,now()+interval \'1 hour\',now())',
                          (uuid4().hex, auth.token_hash(token), auth.token_hash(csrf), identifier))
            credentials[identifier] = token, csrf
        token, csrf = credentials[identifier]
        client.cookies.set(auth.COOKIE, token)
        client.cookies.set(auth.CSRF_COOKIE, csrf)
        client.headers['X-CSRF-Token'] = csrf
        client.headers.pop('X-Operations-Brand', None)
        if brand:
            client.headers['X-Operations-Brand'] = brand
        return client
    def post(name, path, body, brand='kps', status=200, method='post', key=None):
        r = getattr(actor(name,brand),method)(path, json=body, headers={'Idempotency-Key':key or uuid4().hex})
        assert r.status_code == status, r.text
        return r.json()
    def shifts(brand):
        manager = post('manager','/api/shifts',{'kind':'manager'},brand,key='same-manager-open-key')['shift']['id']
        from app.shift_vehicle import CHECKS
        driver = post('driver','/api/shifts',{'kind':'driver','manager_shift_id':manager,
            'vehicle_inspection':{'odometer_km':1000,'fuel_percent':100,'checks':{k:'ok' for k in CHECKS}}},brand,key='same-driver-open-key')['shift']['id']
        return manager, driver
    def order(brand):
        identifier = uuid4().hex
        branch = 'kps-branch-1' if brand=='kps' else 'enbishan-test-1'
        with db.tx() as c:
            c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,%s,%s,%s,'delivery','ready','pending','cash',1000,0,1000,%s,%s)",
                (identifier,brand,branch,'dual-'+brand+'-customer',db.json({'items':[],'branch_name':branch}),db.json({'name':'SYNTHETIC'})))
        return identifier
    return client, actor, post, shifts, order


def test_isolation_blocks_remote_and_runtime_database(monkeypatch):
    monkeypatch.setenv('ENBISHAN_ISOLATED_ENABLED','true')
    for env, url in [('production','postgresql://local@127.0.0.1/test_enbishan_synthetic'),
                     ('testing','postgresql://fake@remote.invalid/test_enbishan_synthetic'),
                     ('testing','postgresql://local@127.0.0.1/kps_live')]:
        monkeypatch.setenv('APP_ENV',env)
        monkeypatch.setenv('DATABASE_URL',url)
        with pytest.raises(RuntimeError):multibrand.isolation_guard()


@pytest.mark.postgres
def test_real_session_context_and_immediate_revocation(dual):
    client, actor, post, _, _ = dual
    assert {b['id'] for b in actor('manager').get('/api/multibrand/context').json()['brands']} == {'kps','enbishan'}
    assert actor('manager','enbishan').get('/api/session').json()['user']['brand_id']=='enbishan'
    assert actor('manager','enbishan').get('/api/session').json()['user']['id']=='dual-manager'
    assert actor('kps-customer','enbishan').get('/api/session').status_code==403
    assert actor('manager','foreign').get('/api/session').status_code==403
    with db.tx() as c:c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-manager'")
    assert actor('manager','enbishan').get('/api/session').status_code==403
    assert actor('manager').get('/api/session').status_code==200


@pytest.mark.postgres
def test_both_catalogs_branches_quotes_and_foreign_item_rejection(dual):
    client, actor, post, _, _ = dual
    for brand in ['kps','enbishan']:
        data=client.get('/api/catalog',params={'brand_id':brand}).json()
        assert data['brand']['id']==brand
        assert all(p['brand_id']==brand for p in data['products'])
        branches=client.get('/api/branches',params={'brand_id':brand}).json()['branches']
        assert all(b['brand_id']==brand for b in branches)
        branch='kps-branch-1' if brand=='kps' else 'enbishan-test-1'
        body={'brand_id':brand,'branch_id':branch,'service':'delivery','payment_method':'cash',
              'items':[{'product_id':'dual-'+brand,'quantity':1}],
              'customer':{'name':'SYNTHETIC','phone':'0500000001','location':{'lat':28.4,'lng':45.9},'address':'TEST ONLY','neighborhood':'اختبار فقط'}}
        response=client.post('/api/quote',json=body)
        assert response.status_code==200,response.text
        quote=response.json()['quote']
        assert quote['total_halalas']==(1500 if brand=='kps' else 2500)
        body['items'][0]['product_id']='dual-'+('enbishan' if brand=='kps' else 'kps')
        assert client.post('/api/quote',json=body).status_code==422


@pytest.mark.postgres
def test_simultaneous_shared_driver_dispatch_and_financial_scopes(dual):
    client, actor, post, shifts, order = dual
    ks=shifts('kps');es=shifts('enbishan')
    ko,eo=order('kps'),order('enbishan')
    post('manager','/api/orders/'+ko+'/assign',{'driver_shift_id':ks[1]})
    post('manager','/api/orders/'+eo+'/assign',{'driver_shift_id':es[1]},'enbishan')
    for brand, oid in [('kps',ko),('enbishan',eo)]:
        tasks=actor('driver',brand).get('/api/tasks').json()['tasks']
        assert len(tasks)==1 and tasks[0]['order_id']==oid
        assert actor('driver',brand).get('/api/orders/'+(eo if brand=='kps' else ko)).status_code==404
    all_tasks=actor('driver').get('/api/multibrand/driver-tasks').json()['tasks']
    assert {t['brand_id'] for t in all_tasks}=={'kps','enbishan'}
    with db.tx() as c:
        rows=c.execute("SELECT s.brand_id,t.driver_id,count(*) n FROM driver_tasks t JOIN shifts s ON s.id=t.shift_id GROUP BY s.brand_id,t.driver_id").fetchall()
        assert {r['brand_id'] for r in rows}=={'kps','enbishan'}
        assert {r['driver_id'] for r in rows}=={'dual-driver'}


@pytest.mark.postgres
def test_dedication_blocks_other_brand_and_overlapping_periods(dual):
    _, actor, post, shifts, order = dual
    ks=shifts('kps');es=shifts('enbishan')
    now=datetime.now(timezone.utc)
    body={'driver_id':'dual-driver','brand_id':'enbishan','starts_at':(now-timedelta(minutes=1)).isoformat(),
          'ends_at':(now+timedelta(hours=8)).isoformat(),'reason':'SYNTHETIC shift dedication'}
    post('manager','/api/multibrand/dedications',body)
    post('manager','/api/multibrand/dedications',body,status=409)
    post('manager','/api/orders/'+order('kps')+'/assign',{'driver_shift_id':ks[1]},status=409)
    post('manager','/api/orders/'+order('enbishan')+'/assign',{'driver_shift_id':es[1]},'enbishan')


@pytest.mark.postgres
def test_enbishan_host_defaults_and_cross_brand_request_rejection(dual,monkeypatch):
    client, _, _, _, _=dual
    monkeypatch.setenv('PUBLIC_BASE_URL','https://order.enbishan.com')
    page=client.get('/')
    assert page.status_code==200
    assert 'data-storefront-brand="enbishan"' in page.text
    assert '0550472662' not in page.text and 'kps.coffe' not in page.text
    assert client.get('/api/catalog').json()['brand']['id']=='enbishan'
    assert client.get('/api/catalog?brand_id=kps').status_code==403
    assert client.get('/api/branches?brand_id=kps').status_code==403


@pytest.mark.postgres
def test_receipt_number_sequences_are_distinct_and_kps_format_preserved(dual):
    from app.order_numbering import allocate
    with db.tx() as c:
        kps=allocate(c,'kps-branch-1')
        enb=allocate(c,'enbishan-test-1')
        assert kps.startswith('HFR1-') and enb.startswith('ENB-HFR1-')
        assert kps!=enb


@pytest.mark.postgres
def test_current_verified_checkout_order_history_and_replay_for_both_brands(dual):
    client, actor, post, shifts, _=dual
    shifts('kps');shifts('enbishan')
    orders={}
    for brand in ['kps','enbishan']:
        with db.tx() as c:
            c.execute("UPDATE app_settings SET settings=settings||'{\"customer_phone_required\":true}'::jsonb WHERE brand_id=%s",(brand,))
            c.execute("UPDATE users SET phone=%s,phone_sms_verified_at=now() WHERE id=%s",('+966500000001','dual-'+brand+'-customer'))
        actor(brand+'-customer')
        with db.tx() as c:c.execute("UPDATE sessions SET customer_verified_at=now() WHERE user_id=%s",('dual-'+brand+'-customer',))
        body={'brand_id':brand,'branch_id':'kps-branch-1' if brand=='kps' else 'enbishan-test-1',
              'service':'delivery','payment_method':'cash','items':[{'product_id':'dual-'+brand,'quantity':1}],
              'customer':{'name':'SYNTHETIC','phone':'0500000001','neighborhood':'اختبار فقط','address':'TEST ONLY',
                          'location':{'lat':28.4,'lng':45.9}}}
        key=uuid4().hex
        first=client.post('/api/orders',json=body,headers={'Idempotency-Key':key})
        assert first.status_code==200,first.text
        assert client.post('/api/orders',json=body,headers={'Idempotency-Key':key}).json()==first.json()
        orders[brand]=first.json()['order']
        assert orders[brand]['snapshot']['order_reference'].startswith('ENB-' if brand=='enbishan' else 'HFR1-')
        assert all(o['brand_id']==brand for o in client.get('/api/orders').json()['orders'])
    assert actor('kps-customer').get('/api/orders/'+orders['enbishan']['id']).status_code==404
    assert actor('enbishan-customer').get('/api/orders/'+orders['kps']['id']).status_code==404


@pytest.mark.postgres
def test_collections_settlements_and_database_reject_cross_brand_cash(dual):
    import psycopg
    _, actor, post, shifts, order=dual
    ks=shifts('kps');es=shifts('enbishan')
    ko,eo=order('kps'),order('enbishan')
    for brand,oid,shift in [('kps',ko,ks[1]),('enbishan',eo,es[1])]:
        result=post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':shift},brand)
        with db.tx() as c:c.execute("UPDATE driver_tasks SET status='picked_up' WHERE id=%s",(result['task']['id'],))
        paid=post('driver','/api/orders/'+oid+'/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':2000},brand)
        assert paid['change_halalas']==1000
        with db.tx() as c:
            rows=c.execute('SELECT s.brand_id,ce.amount_halalas FROM custody_events ce JOIN shifts s ON s.id=ce.shift_id WHERE ce.shift_id=%s',(shift,)).fetchall()
            assert rows and all(r['brand_id']==brand for r in rows)
        report=actor('owner',brand).get('/api/dashboard')
        assert report.status_code==200,report.text
    with pytest.raises(psycopg.Error):
        with db.tx() as c:
            c.execute("INSERT INTO custody_transfers(id,from_shift_id,to_shift_id,recipient_user_id,amount_halalas,reference,created_by) VALUES(%s,%s,%s,'dual-manager',1,'SYNTHETIC','dual-driver')",(uuid4().hex,ks[1],es[0]))


def test_enbishan_vat_seller_never_falls_back_to_kps_and_qr_decodes():
    from copy import deepcopy
    from app import fiscal
    from test_fiscal import sample,decode
    from PIL import Image
    import zxingcpp
    order=sample();order['brand_id']='enbishan'
    before=deepcopy(order)
    with pytest.raises(ValueError):fiscal.build(order,'ENB-TEST',datetime.now(timezone.utc))
    seller={'legal_name':'SYNTHETIC ENBISHAN SELLER','vat_enabled':True,'vat_number':'300000000000003','address':'TEST ONLY'}
    doc=fiscal.build(order,'ENB-TEST',datetime.now(timezone.utc),seller=seller)
    assert order==before and doc['seller']==seller and fiscal.SELLER!=seller
    assert decode(doc['qr_text'])[2]==seller['vat_number']
    matrix=fiscal.qr(doc)['matrix'];size=len(matrix)
    image=Image.new('L',(size,size),255)
    image.putdata([0 if cell=='1' else 255 for row in matrix for cell in row])
    scanned=zxingcpp.read_barcode(image.resize((544,544),Image.Resampling.NEAREST))
    assert scanned and scanned.text==doc['qr_text']


@pytest.mark.postgres
def test_membership_owner_step_up_branch_scope_and_dedication_permissions(dual):
    client,actor,post,_,_=dual
    body={'user_id':'dual-driver','brand_id':'enbishan','branch_ids':['enbishan-test-1']}
    post('manager','/api/multibrand/memberships',body,method='put',status=403)
    post('owner','/api/multibrand/memberships',{**body,'branch_ids':['kps-branch-1']},method='put',status=422)
    post('owner','/api/multibrand/memberships',body,method='put')
    assert actor('driver','enbishan').get('/api/session').json()['user']['branch_ids']==['enbishan-test-1']
    with db.tx() as c:c.execute("UPDATE sessions SET step_up_at=now()-interval '1 hour' WHERE user_id='dual-owner'")
    post('owner','/api/multibrand/memberships',body,method='put',status=403)


@pytest.mark.postgres
def test_persisted_enbishan_tax_invoice_and_foreign_read_are_isolated(dual):
    from test_fiscal import sample, decode
    client,actor,post,_,_=dual
    seller={'entity_id':'synthetic-enbishan','legal_name':'SYNTHETIC ENBISHAN SELLER','vat_enabled':True,
            'vat_number':'300000000000003','address':'TEST ONLY - not an approved legal profile'}
    snapshot=sample()['snapshot']
    snapshot.update(receipt_profile=seller,order_reference='ENB-HFR1-261017-0001')
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES('dual-tax-enb','enbishan','enbishan-test-1','dual-enbishan-customer','delivery','ready','pending','cash',3950,0,3950,%s,'{}')",(db.json(snapshot),))
    policy=actor('owner','enbishan').get('/api/fiscal/policy').json()
    post('owner','/api/fiscal/policy',{'issuance_mode':'owner_requested','enabled':True,'phase':'unconfirmed',
        'reason':'SYNTHETIC TEST ONLY','expected_policy_id':policy['policy_id']},'enbishan')
    response=post('owner','/api/fiscal/orders/dual-tax-enb/print',{},'enbishan')
    document=response['order']['fiscal_document']
    assert document['seller']==seller and document['brand_id']=='enbishan'
    assert document['number']=='ENB-HFR1-261017-0001'
    assert decode(document['qr_text'])[2]=='300000000000003'
    assert actor('owner','kps').get('/api/fiscal/orders/dual-tax-enb/documents').status_code==404
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM fiscal_documents WHERE order_id='dual-tax-enb'").fetchone()['n']==1
