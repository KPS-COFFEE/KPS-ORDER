"""Real isolated DB: device boundary, durable claim, replay and receipt separation."""
from uuid import uuid4
import pytest
from app import auth, db
from test_operations import ops


@pytest.mark.postgres
def test_branch_print_device_queue_isolation_and_no_automatic_reprint(ops):
    actor,post,order,shifts=ops
    oid=order('pickup')
    post('driver','/api/printing/devices',{'branch_id':'ops-branch','label':'Denied'},status=403)
    pair=post('manager','/api/printing/devices',{'branch_id':'ops-branch','label':'Synthetic Windows'})
    client=actor('customer')
    result=client.post('/api/printing/bridge/pair',json={'pairing_code':pair['pairing_code'],'printer_name':'Synthetic printer'})
    assert result.status_code==200,result.text
    token=result.json()['token']
    assert client.post('/api/printing/bridge/pair',json={'pairing_code':pair['pairing_code'],'printer_name':'Duplicate'}).status_code==401
    device=pair['id'];body={'device_id':device,'kind':'order'}
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
    post('manager','/api/printing/devices/'+device+'/revoke',{'reason':'Replace branch computer'})
    assert actor('customer').post('/api/printing/bridge/next',json={},headers=headers).status_code==401
    assert actor('customer').get('/api/printing/windows-bridge.zip').status_code==403
    bundle=actor('manager').get('/api/printing/windows-bridge.zip')
    assert bundle.status_code==200 and bundle.content.startswith(b'PK')


@pytest.mark.postgres
def test_vehicle_initial_entry_then_manager_only_changes(ops):
    actor,post,order,shifts=ops
    body={'plate':'TEST 123','owned_by_kps':False,'odometer_km':100}
    client=actor('driver')
    assert client.put('/api/identity/driver/vehicle',json=body).status_code==200
    assert client.put('/api/identity/driver/vehicle',json={**body,'plate':'OTHER'}).status_code==409
    assert actor('customer').put('/api/identity/driver/vehicle',json=body).status_code==403
    assert actor('driver2').put('/api/identity/driver/vehicle',json={**body,'owned_by_kps':'false'}).status_code==422


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
        conn.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES('ops-second',2,'second-source','ops-brand','Second branch','Test','{}')")
    pair = post('owner', '/api/printing/devices', {'branch_id':'ops-second','label':'Second'})
    result = actor('customer').post('/api/printing/bridge/pair', json={'pairing_code':pair['pairing_code'],'printer_name':'Test second printer'})
    assert result.status_code == 200
    token = result.json()['token']
    oid = order()
    body = {'device_id':pair['id']}
    post('manager', '/api/printing/orders/'+oid, body, status=404)
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
    post('manager','/api/printing/devices',{'branch_id':'ops-second','label':'Replacement'},status=422)
    for reason in ({}, {'reason':'  '}):
        post('manager','/api/printing/devices/'+pair['id']+'/revoke',reason,status=422)
    post('branch','/api/printing/devices/'+pair['id']+'/revoke',{'reason':'Unauthorized'},status=403)
    post('manager','/api/printing/devices/'+pair['id']+'/revoke',{'reason':'Replace faulty computer'})
    with db.tx() as conn:
        event = conn.execute("SELECT details FROM audit_events WHERE action='printer.revoked' AND entity_id=%s",(pair['id'],)).fetchone()
        assert event['details']['reason'] == 'Replace faulty computer'
