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
        accounts = [('dual-owner','owner','kps'),('dual-accountant','admin','kps'),('dual-manager','delivery_manager','kps'),('dual-driver','driver','kps'),
                    ('dual-kps-customer','customer','kps'),('dual-enbishan-customer','customer','enbishan')]
        for identifier, role, brand in accounts:
            c.execute('INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES(%s,%s,%s,%s,%s)',
                      (identifier, 'SYNTHETIC '+identifier, role, brand, db.json(['kps-branch-1'] if brand=='kps' else ['enbishan-test-1'])))
            if role != 'customer':
                c.execute('INSERT INTO staff_brand_memberships(user_id,brand_id,branch_ids,granted_by) VALUES(%s,%s,%s,%s)',
                          (identifier, 'enbishan', db.json(['enbishan-test-1','enbishan-test-2']), 'dual-owner'))
            if role == 'admin':
                c.execute('UPDATE users SET permissions=%s WHERE id=%s',
                          (db.json(['accounting_reports','accounting_review']),identifier))
    # Mock only the external SMS transport/configuration boundary. Mandatory
    # four-digit challenge, purpose, session, account and brand checks stay real.
    from app import sms_verification
    references = set()
    def sms_start(phone, reference, lang='ar'):
        references.add(reference)
        return reference, datetime.now(timezone.utc) + timedelta(minutes=5)
    monkeypatch.setattr(sms_verification, 'start', sms_start)
    monkeypatch.setattr(sms_verification, 'check', lambda reference, code: reference in references and code == '0123')
    monkeypatch.setattr(auth, '_provider_ready', lambda provider: None)
    with db.tx() as c:
        c.execute("UPDATE users SET phone=CASE id WHEN 'dual-owner' THEN '+966500001001' ELSE '+966500001002' END WHERE id IN ('dual-owner','dual-manager')")
        c.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled,delivery_proven_at,sms_provider) VALUES('kps','sms','active',true,now(),'tawked') ON CONFLICT(brand_id,channel) DO UPDATE SET state='active',enabled=true,delivery_proven_at=now(),sms_provider='tawked'")
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
        client.cookies.clear()
        client.headers['X-KPS-Context']='customer' if name.endswith('customer') else 'work'
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
                (identifier,brand,branch,'dual-'+brand+'-customer',db.json({'items':[],'branch_name':branch,'delivery_workflow':'manager_dispatch'}),db.json({'name':'SYNTHETIC'})))
        return identifier
    actor('owner')
    challenge = client.post('/api/identity/owner/sms/request')
    assert challenge.status_code == 200, challenge.text
    verified = client.post('/api/identity/owner/sms/verify', json={'challenge_id':challenge.json()['challenge_id'], 'code':'0123'})
    assert verified.status_code == 200, verified.text
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
        body['items'][0]['product_id']='dual-'+brand
        body['customer']['location']={'lat':29.0,'lng':46.9}
        outside=client.post('/api/quote',json=body)
        assert outside.status_code==409 and outside.json()['detail']['code']=='OUTSIDE_COVERAGE'


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
    client.headers['X-KPS-Context']='customer'
    monkeypatch.setenv('PUBLIC_BASE_URL','https://order.enbishan.com')
    page=client.get('/')
    assert page.status_code==200
    assert 'data-storefront-brand="enbishan"' in page.text
    assert '0550472662' not in page.text and 'kps.coffe' not in page.text
    assert client.get('/api/catalog').json()['brand']['id']=='enbishan'
    assert client.get('/api/catalog?brand_id=kps').status_code==403
    assert client.get('/api/branches?brand_id=kps').status_code==403
    client.cookies.clear()
    client.headers['X-KPS-Context']='work'
    assert 'data-storefront-brand="enbishan"' in client.get('/').text
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
    orders={};parallel=[]
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
        parallel.append((body,dict(client.cookies),dict(client.headers)))
    def create_concurrently(case):
        from fastapi.testclient import TestClient
        body,cookies,headers=case
        isolated_client=TestClient(client.app,headers=headers)
        isolated_client.cookies.update(cookies)
        return isolated_client.post('/api/orders',json=body,headers={'Idempotency-Key':uuid4().hex})
    with ThreadPoolExecutor(max_workers=2) as workers:
        concurrent=list(workers.map(create_concurrently,parallel))
    assert all(r.status_code==200 for r in concurrent),[r.text for r in concurrent]
    assert {r.json()['order']['brand_id'] for r in concurrent}=={'kps','enbishan'}
    assert len({r.json()['order']['id'] for r in concurrent})==2
    assert actor('kps-customer').get('/api/orders/'+orders['enbishan']['id']).status_code==404
    assert actor('enbishan-customer').get('/api/orders/'+orders['kps']['id']).status_code==404


@pytest.mark.postgres
@pytest.mark.parametrize('method',['cash','card','split'])
def test_collections_settlements_and_database_reject_cross_brand_cash(dual,method):
    import psycopg
    _, actor, post, shifts, order=dual
    ks=shifts('kps');es=shifts('enbishan')
    ko,eo=order('kps'),order('enbishan')
    for brand,oid,shift in [('kps',ko,ks[1]),('enbishan',eo,es[1])]:
        result=post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':shift},brand)
        import base64,io
        from PIL import Image
        task=result['task']['id']
        post('driver','/api/tasks/'+task+'/transition',{'status':'accepted'},brand)
        image=io.BytesIO();Image.new('RGB',(12,12),'white').save(image,'PNG')
        def photo(stage):
            return post('driver','/api/tasks/'+task+'/photos',{'stage':stage,'content_type':'image/png',
                'data_base64':base64.b64encode(image.getvalue()).decode()},brand)['id']
        post('driver','/api/tasks/'+task+'/transition',{'status':'picked_up','photo_id':photo('picked_up')},brand)
        payment={'method':method,'amount_halalas':1000}
        if method=='cash':payment['tendered_halalas']=2000
        if method=='card':payment['reference']='SYNTHETIC-CARD'
        if method=='split':payment.update(cash_halalas=400,card_halalas=600,tendered_halalas=500,reference='SYNTHETIC-SPLIT')
        paid=post('driver','/api/orders/'+oid+'/collect',payment,brand)
        assert paid['change_halalas']=={'cash':1000,'card':0,'split':100}[method]
        post('driver','/api/tasks/'+task+'/transition',{'status':'delivered','photo_id':photo('delivered'),'proof':'SYNTHETIC handover'},brand)
        with db.tx() as c:
            rows=c.execute('SELECT s.brand_id,ce.amount_halalas FROM custody_events ce JOIN shifts s ON s.id=ce.shift_id WHERE ce.shift_id=%s',(shift,)).fetchall()
            assert all(r['brand_id']==brand for r in rows)
            assert sum(r['amount_halalas'] for r in rows)=={'cash':1000,'card':0,'split':400}[method]
        report=actor('owner',brand).get('/api/dashboard')
        assert report.status_code==200,report.text
        accounting=actor('accountant',brand).get('/api/accounting/reports')
        assert accounting.status_code==200,accounting.text
        assert accounting.json()['totals']['cash']=={'cash':1000,'card':0,'split':400}[method]
        assert accounting.json()['totals']['card']=={'cash':0,'card':1000,'split':600}[method]
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


@pytest.mark.postgres
def test_both_brand_driver_closures_and_saved_reports_are_independent(dual):
    _,actor,post,shifts,_=dual
    ks=shifts('kps');es=shifts('enbishan')
    from app.shift_vehicle import CHECKS
    for brand,(manager,driver) in [('enbishan',es),('kps',ks)]:
        post('driver','/api/shifts/'+driver+'/closing',{},brand,status=409)
        asked=post('driver','/api/driver-closure/'+driver+'/request',{'reason':'SYNTHETIC finished test shift'},brand)
        post('manager','/api/driver-closure/'+driver+'/decision',{'decision':'approved',
             'request_id':asked['authorization']['id'],'reason':'SYNTHETIC manager approval'},brand)
        post('driver','/api/shifts/'+driver+'/closing',{},brand)
        post('driver','/api/shifts/'+driver+'/submit',{'counted_halalas':0,'handover_halalas':0,'terminal_total_halalas':0,
             'handover':{'recipient_user_id':'dual-manager','to_shift_id':manager,'reference':'TEST ONLY'},
             'vehicle_inspection':{'odometer_km':1020,'fuel_percent':100,'checks':{k:'ok' for k in CHECKS}}},brand)
        with db.tx() as c:
            transfer=c.execute('SELECT id FROM custody_transfers WHERE from_shift_id=%s',(driver,)).fetchone()['id']
        post('manager','/api/custody/'+transfer+'/confirm',{},brand)
        result=post('manager','/api/shifts/'+driver+'/approve',{},brand)
        assert actor('owner',brand).get('/api/reports/'+result['report_id']).status_code==200
        assert actor('owner','kps' if brand=='enbishan' else 'enbishan').get('/api/reports/'+result['report_id']).status_code==404


def test_bad_isolated_startup_never_opens_a_database_connection(monkeypatch):
    monkeypatch.setenv('ENBISHAN_ISOLATED_ENABLED','true')
    monkeypatch.setenv('APP_ENV','testing')
    monkeypatch.setenv('DATABASE_URL','postgresql://synthetic@remote.invalid/kps_live')
    def forbidden(*args,**kwargs):raise AssertionError('A database connection was attempted')
    monkeypatch.setattr(db,'tx',forbidden)
    with pytest.raises(RuntimeError):db.migrate()


@pytest.mark.postgres
def test_shared_endpoints_keep_employee_permission_denials(dual):
    _,actor,post,_,_=dual
    with db.tx() as c:
        c.execute("UPDATE users SET permission_denials='[\"tasks_view\"]' WHERE id='dual-driver'")
        c.execute("UPDATE users SET permission_denials='[\"orders_view\",\"drivers_view\",\"delivery_assign\"]' WHERE id='dual-manager'")
    assert actor('driver').get('/api/multibrand/driver-tasks').status_code==403
    assert actor('driver','enbishan').get('/api/multibrand/inbox').status_code==403
    assert actor('manager').get('/api/multibrand/inbox').status_code==403
    assert actor('manager','enbishan').get('/api/multibrand/pool').status_code==403
    now=datetime.now(timezone.utc)
    post('manager','/api/multibrand/dedications',{'driver_id':'dual-driver','brand_id':'enbishan',
        'starts_at':now.isoformat(),'ends_at':(now+timedelta(hours=1)).isoformat(),'reason':'SYNTHETIC'},status=403)


@pytest.mark.postgres
def test_first_staff_login_returns_selected_brand_and_same_identity(dual):
    client,_,_,_,_=dual
    with db.tx() as c:
        c.execute("UPDATE users SET username='synthetic.dual.manager',password_hash=%s WHERE id='dual-manager'",
                  (auth.hash_password('Synthetic-only-password!'),))
    client.headers['X-Operations-Brand']='enbishan'
    result=client.post('/api/auth/login',json={'username':'synthetic.dual.manager','password':'Synthetic-only-password!'})
    assert result.status_code==403,result.text
    requested=client.post('/api/identity/staff/code/request',json={'phone':'+966500001002','brand_id':'kps'})
    assert requested.status_code==200,requested.text
    result=client.post('/api/identity/staff/code/verify',json={'challenge_id':requested.json()['challenge_id'],'code':'0123'})
    assert result.status_code==200,result.text
    user=result.json()['user']
    assert user['id']=='dual-manager' and user['brand_id']=='enbishan'
    assert {b['id'] for b in user['operations_brands']}=={'kps','enbishan'}
    assert client.get('/api/session').json()['user']['brand_id']=='enbishan'
    client.headers['X-Operations-Brand']='kps'
    assert client.get('/api/session').json()['user']['id']=='dual-manager'


@pytest.mark.postgres
def test_shared_administration_preserves_accountant_role_and_permissions(dual):
    _,actor,post,_,_=dual
    for brand in ['kps','enbishan']:
        session=actor('accountant',brand).get('/api/session')
        assert session.status_code==200,session.text
        user=session.json()['user']
        assert user['id']=='dual-accountant' and user['role']=='admin' and user['brand_id']==brand
        assert actor('accountant',brand).get('/api/accounting/reports').status_code==200
        assert actor('accountant',brand).get('/api/multibrand/pool').status_code==403
        post('accountant','/api/multibrand/memberships',{'user_id':'dual-driver','brand_id':'enbishan',
             'branch_ids':['enbishan-test-1']},brand,method='put',status=403)


def test_private_backup_registry_preserves_shared_grants_without_business_export():
    from app.exports import TABLES, AUTH_TABLES
    for table in ('staff_brand_memberships', 'driver_brand_dedications'):
        assert table in TABLES
        assert table in AUTH_TABLES


@pytest.mark.postgres
def test_dual_brand_private_backup_restore_preserves_memberships(dual):
    import os
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from app.exports import build_archive, restore_archive
    _, _, post, _, _ = dual
    now = datetime.now(timezone.utc)
    post('owner', '/api/multibrand/dedications', {
        'driver_id':'dual-driver', 'brand_id':'enbishan',
        'starts_at':now.isoformat(), 'ends_at':(now+timedelta(hours=1)).isoformat(),
        'reason':'SYNTHETIC backup restoration verification'})
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        archive, manifest = build_archive(c, full=True)
        memberships = c.execute('SELECT * FROM staff_brand_memberships ORDER BY user_id,brand_id').fetchall()
        dedications = c.execute('SELECT * FROM driver_brand_dedications ORDER BY id').fetchall()
    schema = 'kps_restore_' + uuid4().hex
    target = os.environ['TEST_DATABASE_URL']
    try:
        result = restore_archive(archive, target_url=target, target_schema=schema)
        with psycopg.connect(target, row_factory=dict_row) as c:
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            assert c.execute('SELECT * FROM staff_brand_memberships ORDER BY user_id,brand_id').fetchall() == memberships
            assert c.execute('SELECT * FROM driver_brand_dedications ORDER BY id').fetchall() == dedications
        # Source remains present after restoration into the distinct new schema.
        with db.tx() as c:
            assert c.execute('SELECT count(*) AS n FROM staff_brand_memberships').fetchone()['n'] == len(memberships)
    finally:
        with psycopg.connect(target, autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))


def test_projected_owner_cannot_downgrade_home_sms_step_up(monkeypatch):
    from fastapi import HTTPException
    user = {'id':'synthetic-owner', 'role':'owner', 'session_id':'synthetic-session',
            'brand_id':'enbishan', 'home_brand_id':'kps'}
    seen = []
    monkeypatch.setattr(auth, 'staff_email_retired', lambda brand, **kw: seen.append(brand) or brand == 'kps')
    class NoProof:
        def execute(self, query, params):
            assert "owner_sms_" in query
            return self
        def fetchone(self):
            return None
    with pytest.raises(HTTPException) as rejected:
        auth.require_owner_step_up(NoProof(), user)
    assert rejected.value.status_code == 403
    assert seen == ['kps']


def test_projected_owner_sms_provider_uses_home_identity(monkeypatch):
    from app import employee_sms, sms_rollout
    seen = []
    monkeypatch.setattr(sms_rollout, 'employee_sms_enabled', lambda user: seen.append(user['brand_id']) or user['brand_id']=='kps')
    employee_sms._owner_sms_enabled({'id':'test', 'brand_id':'enbishan', 'home_brand_id':'kps'})
    assert seen == ['kps']
