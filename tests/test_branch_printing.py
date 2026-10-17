"""Real isolated DB: device boundary, durable claim, replay and receipt separation."""
from uuid import uuid4
import pytest
from app import auth, db
from test_operations import ops


def approve_test_device(actor, post, device, token, who='owner'):
    job=post(who,'/api/printing/devices/'+device+'/test')
    post(who,'/api/printing/devices/'+device+'/approve',{'job_id':job['id'],'paper_confirmed':True},status=409)
    headers={'Authorization':'Bearer '+token}
    result=actor('customer').post('/api/printing/bridge/next',json={},headers=headers)
    assert result.json()['job']['id']==job['id']
    assert result.json()['job']['payload']['kind']=='test'
    assert actor('customer').post('/api/printing/bridge/jobs/'+job['id']+'/ack',json={'status':'submitted'},headers=headers).status_code==200
    post(who,'/api/printing/devices/'+device+'/approve',{'job_id':job['id'],'paper_confirmed':False},status=409)
    post(who,'/api/printing/devices/'+device+'/approve',{'job_id':job['id'],'paper_confirmed':True})


@pytest.mark.postgres
def test_branch_print_device_queue_isolation_and_no_automatic_reprint(ops):
    actor,post,order,shifts=ops
    oid=order('delivery')
    post('driver','/api/printing/devices',{'branch_id':'ops-branch','label':'Denied'},status=403)
    pair=post('owner','/api/printing/devices',{'branch_id':'ops-branch','label':'Synthetic Windows'})
    client=actor('customer')
    result=client.post('/api/printing/bridge/pair',json={'pairing_code':pair['pairing_code'],'printer_name':'Synthetic printer'})
    assert result.status_code==200,result.text
    token=result.json()['token']
    assert client.post('/api/printing/bridge/pair',json={'pairing_code':pair['pairing_code'],'printer_name':'Duplicate'}).status_code==401
    device=pair['id'];body={'device_id':device,'kind':'order'}
    post('manager','/api/printing/orders/'+oid,body,status=409)
    approve_test_device(actor,post,device,token)
    job=post('manager','/api/printing/orders/'+oid,body,key='print-stable-replay')
    assert post('manager','/api/printing/orders/'+oid,body,key='print-stable-replay')==job
    post('manager','/api/printing/orders/'+oid,body,status=409)
    post('other','/api/printing/orders/'+oid,body,status=404)
    post('driver','/api/printing/orders/'+oid,body,status=403)
    headers={'Authorization':'Bearer '+token}
    client=actor('customer')
    claimed=client.post('/api/printing/bridge/next',json={},headers=headers)
    assert claimed.status_code==200,claimed.text
    payload=claimed.json()['job'];assert payload['id']==job['id']
    assert '10.00' in str(payload['payload']['lines'])
    assert client.post('/api/printing/bridge/next',json={},headers=headers).json()['job'] is None
    for _ in range(2):
        assert client.post('/api/printing/bridge/jobs/'+job['id']+'/ack',json={'status':'submitted'},headers=headers).status_code==200
    assert client.post('/api/printing/bridge/jobs/not-mine/ack',json={'status':'submitted'},headers=headers).status_code==404
    prep=post('manager','/api/printing/orders/'+oid,{'device_id':device,'kind':'preparation'})
    payload=actor('customer').post('/api/printing/bridge/next',json={},headers=headers).json()['job']['payload']
    assert not any('ر.س' in line['text'] or 'SAR' in line['text'] for line in payload['lines'])
    post('manager','/api/printing/orders/'+oid,{**body,'reprint_reason':'Synthetic checked queue and paper'})
    with db.tx() as conn:
        assert conn.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='ready'
        assert conn.execute('SELECT token_hash FROM branch_print_devices WHERE id=%s',(device,)).fetchone()['token_hash']==auth.token_hash(token)
    post('owner','/api/printing/devices/'+device+'/revoke',{'reason':'Replace branch computer'})
    assert actor('customer').post('/api/printing/bridge/next',json={},headers=headers).status_code==401
    assert actor('customer').get('/api/printing/windows-bridge.zip').status_code==403
    bundle=actor('owner').get('/api/printing/windows-bridge.zip')
    assert bundle.status_code==200 and bundle.content.startswith(b'PK')


@pytest.mark.postgres
def test_vehicle_profile_is_owner_managed_and_legacy_writes_are_forbidden(ops):
    actor,post,order,shifts=ops
    body={'plate':'TEST 123','owned_by_kps':False}
    assert actor('driver').put('/api/identity/driver/vehicle',json=body).status_code==403
    assert actor('customer').put('/api/identity/driver/vehicle',json=body).status_code==403
    assert actor('manager').patch('/api/identity/staff/ops-driver',json={'vehicle':body}).status_code==403
    response=actor('owner').patch('/api/identity/staff/ops-driver',json={'vehicle':body})
    assert response.status_code==200,response.text
    assert response.json()['user']['vehicle']['plate']=='TEST 123'
    assert response.json()['user']['vehicle']['owned_by_kps'] is False
    assert actor('driver').put('/api/identity/driver/vehicle',json={**body,'plate':'OTHER'}).status_code==403
    with db.tx() as conn:
        assert conn.execute("SELECT vehicle FROM users WHERE id='ops-driver'").fetchone()['vehicle']==response.json()['user']['vehicle']


@pytest.mark.postgres
def test_driver_sees_only_fully_covering_manager_and_clear_missing_selection(ops):
    actor,post,order,shifts=ops
    manager=post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})['shift']['id']
    result=post('driver','/api/shifts',{'kind':'driver'},status=409)
    assert result['detail']['code']=='MANAGER_SHIFT_REQUIRED'
    with db.tx() as conn:
        conn.execute("UPDATE users SET branch_ids=%s WHERE id='ops-driver'",(db.json(['ops-branch','extra-scope']),))
    assert actor('driver').get('/api/shifts/current').json()['available_manager_shifts']==[]


@pytest.mark.postgres
def test_manager_selects_destination_and_settings_are_locked(ops):
    actor, post, order, _ = ops
    with db.tx() as conn:
        conn.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES('ops-second',2,'second-source','ops-brand','Second branch','حفر الباطن','{}')")
    pair = post('owner', '/api/printing/devices', {'branch_id':'ops-second','label':'Second'})
    result = actor('customer').post('/api/printing/bridge/pair', json={'pairing_code':pair['pairing_code'],'printer_name':'Test second printer'})
    assert result.status_code == 200
    token = result.json()['token']
    approve_test_device(actor,post,pair['id'],token,who='owner')
    oid = order()
    body = {'device_id':pair['id']}
    post('manager', '/api/printing/orders/'+oid, body, status=404)
    assert actor('manager').get('/api/printing/devices').json()['devices'] == []
    post('manager', '/api/printing/devices', {'branch_id':'ops-second','label':'Outside scope'}, status=404)
    for who in ('branch','driver','customer'):
        assert actor(who).get('/api/printing/devices').status_code == 403
        post(who, '/api/printing/devices', {'branch_id':'ops-branch','label':'Forbidden'}, status=403)
    with db.tx() as conn:
        conn.execute("UPDATE users SET branch_ids=%s WHERE id='ops-manager'", (db.json(['ops-branch','ops-second']),))
    job = post('manager', '/api/printing/orders/'+oid, body, key='destination-job')
    assert post('manager', '/api/printing/orders/'+oid, body, key='destination-job') == job
    post('manager', '/api/printing/orders/'+oid, body, status=409)
    claimed = actor('customer').post('/api/printing/bridge/next', json={}, headers={'Authorization':'Bearer '+token}).json()['job']
    assert claimed['id'] == job['id']
    assert 'Second branch' in str(claimed['payload'])
    with db.tx() as conn:
        assert conn.execute('SELECT branch_id FROM orders WHERE id=%s',(oid,)).fetchone()['branch_id'] == 'ops-branch'
        assert conn.execute('SELECT branch_id FROM branch_print_jobs WHERE id=%s',(job['id'],)).fetchone()['branch_id'] == 'ops-second'
    post('owner','/api/printing/devices',{'branch_id':'ops-second','label':'Replacement'},status=422)
    for reason in ({}, {'reason':'  '}):
        post('owner','/api/printing/devices/'+pair['id']+'/revoke',reason,status=422)
    post('branch','/api/printing/devices/'+pair['id']+'/revoke',{'reason':'Unauthorized'},status=403)
    post('owner','/api/printing/devices/'+pair['id']+'/revoke',{'reason':'Replace faulty computer'})
    with db.tx() as conn:
        event = conn.execute("SELECT details FROM audit_events WHERE action='printer.revoked' AND entity_id=%s",(pair['id'],)).fetchone()
        assert event['details']['reason'] == 'Replace faulty computer'


@pytest.mark.postgres
def test_physical_acceptance_scope_and_retest_gate(ops):
    actor,post,order,_=ops
    paired=post('owner','/api/printing/devices',{'branch_id':'ops-branch','label':'Acceptance'})
    token=actor('customer').post('/api/printing/bridge/pair',json={'pairing_code':paired['pairing_code'],'printer_name':'Synthetic'}).json()['token']
    device=paired['id'];oid=order();body={'device_id':device}
    for who in ('branch','driver'):
        post(who,'/api/printing/devices/'+device+'/test',status=403)
        post(who,'/api/printing/devices/'+device+'/approve',{'job_id':'other','paper_confirmed':True},status=403)
    post('other','/api/printing/devices/'+device+'/test',status=404)
    approve_test_device(actor,post,device,token)
    queued=post('manager','/api/printing/orders/'+oid,body)
    test=post('owner','/api/printing/devices/'+device+'/test',key='test-replay')
    assert post('owner','/api/printing/devices/'+device+'/test',key='test-replay')==test
    post('manager','/api/printing/orders/'+order(),body,status=409)
    headers={'Authorization':'Bearer '+token}
    received=actor('customer').post('/api/printing/bridge/next',json={},headers=headers).json()['job']
    assert received['id']==test['id']  # pending orders stay gated during retest
    actor('customer').post('/api/printing/bridge/jobs/'+test['id']+'/ack',json={'status':'uncertain'},headers=headers)
    post('owner','/api/printing/devices/'+device+'/approve',{'job_id':test['id'],'paper_confirmed':True},status=409)
    approve_test_device(actor,post,device,token)
    assert actor('customer').post('/api/printing/bridge/next',json={},headers=headers).json()['job']['id']==queued['id']


@pytest.mark.postgres
def test_printer_only_admin_has_no_other_operational_access(ops):
    actor,post,order,_=ops
    with db.tx() as conn:
        conn.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,permissions) VALUES('ops-printeradmin','Printer technician','admin','ops-brand',%s,%s)",(db.json(['ops-branch']),db.json(['branch_printers_manage'])))
    for path in ('/api/orders','/api/admin/config','/api/identity/staff'):
        assert actor('printeradmin').get(path).status_code==403
    pair=post('printeradmin','/api/printing/devices',{'branch_id':'ops-branch','label':'Scoped setup'})
    post('printeradmin','/api/printing/devices',{'branch_id':'ops-branch-other','label':'Forbidden'},status=404)
    assert actor('printeradmin').get('/api/printing/windows-bridge.zip').status_code==200
    token=actor('customer').post('/api/printing/bridge/pair',json={'pairing_code':pair['pairing_code'],'printer_name':'Synthetic printer'}).json()['token']
    approve_test_device(actor,post,pair['id'],token,who='printeradmin')
    post('printeradmin','/api/printing/orders/'+order(),{'device_id':pair['id']},status=403)
    pending=post('printeradmin','/api/printing/devices',{'branch_id':'ops-branch','label':'Pending','reason':'Replace'})
    with db.tx() as conn:
        conn.execute("UPDATE users SET permissions='[]' WHERE id='ops-printeradmin'")
    assert actor('printeradmin').get('/api/printing/devices').status_code==403
    assert actor('customer').post('/api/printing/bridge/pair',json={'pairing_code':pending['pairing_code'],'printer_name':'Revoked permission'}).status_code==403


@pytest.mark.postgres
def test_manager_printer_management_is_scoped_revocable_and_requires_paper_acceptance(ops):
    actor, post, order, _ = ops
    pair = post('manager', '/api/printing/devices', {'branch_id':'ops-branch','label':'Manager setup'})
    post('manager', '/api/printing/devices', {'branch_id':'ops-branch-other','label':'Other brand'}, status=404)
    assert [d['id'] for d in actor('manager').get('/api/printing/devices').json()['devices']] == [pair['id']]
    assert actor('manager').get('/api/printing/windows-bridge.zip').status_code == 200
    client = actor('customer')
    result = client.post('/api/printing/bridge/pair', json={'pairing_code':pair['pairing_code'],'printer_name':'Synthetic manager printer'})
    assert result.status_code == 200
    approve_test_device(actor, post, pair['id'], result.json()['token'], who='manager')
    pending = post('manager', '/api/printing/devices', {'branch_id':'ops-branch','label':'Pending','reason':'Replacement'})
    with db.tx() as conn:
        conn.execute("UPDATE users SET permission_denials=%s WHERE id='ops-manager'", (db.json(['branch_printers_manage']),))
    assert actor('manager').get('/api/printing/devices').status_code == 403
    post('manager', '/api/printing/devices/'+pair['id']+'/test', status=403)
    post('manager', '/api/printing/devices/'+pair['id']+'/revoke', {'reason':'Denied'}, status=403)
    assert client.post('/api/printing/bridge/pair', json={'pairing_code':pending['pairing_code'],'printer_name':'Denied'}).status_code == 403
    with db.tx() as conn:
        conn.execute("UPDATE users SET permission_denials='[]',branch_ids='[]' WHERE id='ops-manager'")
    assert actor('manager').get('/api/printing/devices').json()['devices'] == []
    post('manager', '/api/printing/devices/'+pair['id']+'/test', status=404)
    post('manager', '/api/printing/devices/'+pair['id']+'/revoke', {'reason':'Outside scope'}, status=404)


@pytest.mark.postgres
def test_both_receipts_are_atomic_ordered_and_replay_safe(ops):
    actor,post,order,shifts=ops
    oid=order('delivery')
    pair=post('owner','/api/printing/devices',{'branch_id':'ops-branch','label':'Two receipts'})
    client=actor('customer')
    token=client.post('/api/printing/bridge/pair',json={'pairing_code':pair['pairing_code'],'printer_name':'Synthetic'}).json()['token']
    approve_test_device(actor,post,pair['id'],token)
    body={'device_id':pair['id'],'kind':'both'}
    result=post('manager','/api/printing/orders/'+oid,body,key='both-replay')
    assert len(result['ids'])==2
    assert post('manager','/api/printing/orders/'+oid,body,key='both-replay')==result
    headers={'Authorization':'Bearer '+token}
    for identifier,kind in zip(result['ids'],['order','preparation']):
        job=client.post('/api/printing/bridge/next',json={},headers=headers).json()['job']
        assert job['id']==identifier and job['payload']['kind']==kind
        assert job['payload']['language']=='en'
        assert any('SAR' in line['text'] for line in job['payload']['lines']) == (kind=='order')
        assert client.post('/api/printing/bridge/jobs/'+identifier+'/ack',json={'status':'submitted'},headers=headers).status_code==200
    assert client.post('/api/printing/bridge/next',json={},headers=headers).json()['job'] is None
    post('manager','/api/printing/orders/'+oid,body,status=409)
    # An existing preparation receipt must roll back the first insert in a pair.
    second=order('delivery')
    post('manager','/api/printing/orders/'+second,{'device_id':pair['id'],'kind':'preparation'})
    post('manager','/api/printing/orders/'+second,body,status=409)
    with db.tx() as conn:
        rows=conn.execute('SELECT kind FROM branch_print_jobs WHERE order_id=%s',(second,)).fetchall()
        assert [row['kind'] for row in rows]==['preparation']


@pytest.mark.postgres
def test_product_print_translation_edit_is_scoped_and_used_in_queue(ops):
    actor,post,order,shifts=ops
    with db.tx() as conn:
        conn.execute("INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES('ops-product','ops-brand','قهوة تجربة','Test',1000,%s)",
                     (db.json({'enabled':False,'lifecycle':'draft'}),))
    oid=order('delivery')
    body={'settings':{'print_labels_en':{'قهوة تجربة':'Test Coffee'}}}
    assert actor('driver').patch('/api/admin/products/ops-product',json=body,headers={'Idempotency-Key':str(uuid4())}).status_code==403
    assert actor('other').patch('/api/admin/products/ops-product',json=body,headers={'Idempotency-Key':str(uuid4())}).status_code==404
    response=actor('owner').patch('/api/admin/products/ops-product',json=body,headers={'Idempotency-Key':str(uuid4())})
    assert response.status_code==200,response.text
    detail=actor('owner').get('/api/orders/'+oid).json()['order']
    assert detail['print_labels_en']['ops-product']['قهوة تجربة']=='Test Coffee'
    assert actor('other').get('/api/orders/'+oid).status_code==404
    assert actor('owner').patch('/api/admin/products/ops-product',json={'settings':{'print_labels_en':{'x':[]}}},headers={'Idempotency-Key':str(uuid4())}).status_code==422
    pair=post('owner','/api/printing/devices',{'branch_id':'ops-branch','label':'Translation test'})
    paired=actor('customer').post('/api/printing/bridge/pair',json={'pairing_code':pair['pairing_code'],'printer_name':'Test'}).json()
    approve_test_device(actor,post,pair['id'],paired['token'])
    post('manager','/api/printing/orders/'+oid,{'device_id':pair['id'],'kind':'both'})
    job=actor('customer').post('/api/printing/bridge/next',json={},headers={'Authorization':'Bearer '+paired['token']}).json()['job']
    assert job['payload']['language']=='en'
    assert any(line['text']=='Test Coffee' for line in job['payload']['lines'])
    with db.tx() as conn:
        assert conn.execute('SELECT snapshot FROM orders WHERE id=%s',(oid,)).fetchone()['snapshot']['items'][0]['name']=='قهوة تجربة'


@pytest.mark.postgres
def test_guided_setup_is_branch_bound_private_and_reports_real_connectivity(ops):
    import base64
    import io
    import json
    import zipfile
    actor, post, _, _ = ops
    body={'branch_id':'ops-branch','label':'Guided test','reason':'Replace synthetic setup'}
    post('driver','/api/printing/setup',body,status=403)
    post('other','/api/printing/setup',body,status=404)
    package=post('owner','/api/printing/setup',body)
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(package['bundle']))) as bundle:
        config=json.loads(bundle.read('setup.json'))
        assert config['branch_id']=='ops-branch'
        assert {'Start.cmd','KpsPrintBridge.cs','logo.png'} <= set(bundle.namelist())
    def device():
        return next(x for x in actor('owner').get('/api/printing/devices').json()['devices'] if x['id']==package['id'])
    assert device()['connection_status']=='pending'
    redemption={'pairing_code':config['pairing_code'],'printer_name':'Synthetic Windows printer'}
    response=actor('customer').post('/api/printing/bridge/pair',json=redemption)
    assert response.status_code==200
    assert device()['connection_status']=='online' and not device()['approved_at']
    assert actor('customer').post('/api/printing/bridge/pair',json=redemption).status_code==401
    with db.tx() as conn:
        conn.execute("UPDATE branch_print_devices SET last_seen_at=now()-interval '2 minutes' WHERE id=%s",(package['id'],))
    assert device()['connection_status']=='offline'
    token=response.json()['token']
    actor('customer').post('/api/printing/bridge/next',json={},headers={'Authorization':'Bearer '+token})
    assert device()['connection_status']=='online'
    post('owner','/api/printing/devices/'+package['id']+'/revoke',{'reason':'Reset synthetic pairing'})
    assert device()['connection_status']=='revoked'
    assert actor('customer').post('/api/printing/bridge/next',json={},headers={'Authorization':'Bearer '+token}).status_code==401
