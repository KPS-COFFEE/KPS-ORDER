"""Self-service fleet and fuel transitions, against an isolated PostgreSQL DB."""
import io,base64
from PIL import Image
import pytest
from app import db
from test_operations import ops
from test_fleet import delegate
from test_shift_vehicle import inspection
pytestmark=pytest.mark.postgres


def test_fawzi_create_handover_preserves_shift_and_vehicle_intervals(ops):
    actor,post,order,shifts=ops
    with db.tx() as c:c.execute("UPDATE users SET vehicle='{\"plate\":\"TEST OLD 123\",\"type\":\"Test old\"}' WHERE id='ops-driver'")
    mid,sid=shifts()
    client=delegate(actor)
    body={'vehicle':{'plate':'TEST NEW 123','type':'Test car','odometer_km':5000},'branch_ids':['ops-branch']}
    response=client.post('/api/fleet/vehicles',json=body,headers={'Idempotency-Key':'new-car-once'})
    assert response.status_code==200,response.text
    vid=response.json()['vehicle_id']
    assert client.post('/api/fleet/vehicles',json=body,headers={'Idempotency-Key':'new-car-once'}).json()==response.json()
    assert client.post('/api/fleet/vehicles',json=body,headers={'Idempotency-Key':'duplicate-car-test'}).status_code==409
    assert client.post('/api/fleet/vehicles',json={**body,'branch_ids':['ops-other-branch']},headers={'Idempotency-Key':'out-of-scope-car'}).status_code==403
    with db.tx() as c:
        before=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone()
        start=before['vehicle_log']['start']['odometer_km']
        old_plate=before['vehicle_log']['vehicle']['plate']
    change={'action':'handover','driver_id':'ops-driver','revision':0,'reason':'TEST ONLY breakdown replacement','outgoing':inspection(start+20,40),'incoming':inspection(5000,50)}
    url='/api/fleet/vehicles/'+vid+'/management'
    result=client.post(url,json=change,headers={'Idempotency-Key':'handover-once'})
    assert result.status_code==200,result.text
    assert client.post(url,json=change,headers={'Idempotency-Key':'handover-once'}).json()==result.json()
    with db.tx() as c:
        after=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone()
        for key in ('id','opened_at','status','manager_shift_id','opening_halalas','available'):
            assert before[key]==after[key]
        assert after['vehicle_log']['awaiting_refuel']
        assert after['vehicle_log']['previous_segments'][0]['vehicle']['plate']==old_plate
        assert after['vehicle_log']['start']['odometer_km']==5000
        rows=c.execute('SELECT * FROM fleet_operating_segments WHERE shift_id=%s ORDER BY segment_no',(sid,)).fetchall()
        assert len(rows)==2 and rows[0]['metrics']['actual_distance']==20
        assert rows[1]['metrics']['start_odometer']==5000
        assert rows[0]['returned_at'] and rows[1]['returned_at'] is None
    history=client.get('/api/fleet/vehicles/'+vid)
    assert history.status_code==200,history.text
    assert history.json()['rows'][0]['start_odometer']==5000
    # Released original car is usable while the same financial shift stays open.
    old=next(v for v in client.get('/api/fleet/vehicles').json()['vehicles'] if v['plate']==old_plate)
    assign=client.post('/api/fleet/vehicles/'+old['id']+'/management',json={'action':'assign','revision':old['details']['management_revision'],'driver_id':'ops-driver2','reason':'TEST released vehicle'},headers={'Idempotency-Key':'assign-released-car'})
    assert assign.status_code==200,assign.text
    actor('driver')
    assert client.post('/api/fleet/vehicles',json={**body,'vehicle':{**body['vehicle'],'plate':'TEST DENIED'}}).status_code==403


def test_single_step_refuel_and_manager_exception_preserve_manual_pause(ops):
    actor,post,order,shifts=ops
    mid=post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})['shift']['id']
    sid=post('driver','/api/shifts',{'kind':'driver','manager_shift_id':mid,'vehicle_inspection':inspection(1000,50)})['shift']['id']
    with db.tx() as c:c.execute('UPDATE shifts SET available=false WHERE id=%s',(sid,))
    receipt=io.BytesIO();Image.new('RGB',(12,12),'white').save(receipt,'PNG')
    client=actor('driver')
    upload=client.post('/api/driver-evidence/shifts/'+sid,json={'data_base64':base64.b64encode(receipt.getvalue()).decode(),'content_type':'image/png'},headers={'Idempotency-Key':'fuel-receipt-test'})
    assert upload.status_code==200,upload.text
    payload={'liters':'10','odometer_km':1010,'receipt_id':upload.json()['id'],'confirm_full':True}
    response=post('driver','/api/shifts/'+sid+'/refuel',payload,key='full-once')
    assert post('driver','/api/shifts/'+sid+'/refuel',payload,key='full-once')==response
    with db.tx() as c:
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone()
        assert not s['available'] and not s['vehicle_log']['awaiting_refuel']
        assert s['vehicle_log']['start']['fuel_percent']==50
        assert s['vehicle_log']['fuel_readiness']['fuel_percent']==100
        assert len(s['vehicle_log']['refuels'])==1
    sid2=post('driver2','/api/shifts',{'kind':'driver','manager_shift_id':mid,'vehicle_inspection':inspection(1000,30)})['shift']['id']
    r=post('driver2','/api/fuel-exceptions',{'phase':'activation','manager_shift_id':mid,'shift_id':sid2,'reason':'aldrees_unavailable','fuel_percent':30,'odometer_km':1000})
    post('manager','/api/fuel-exceptions/'+r['id'],{'decision':'approved'})
    with db.tx() as c:
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(sid2,)).fetchone()
        assert not s['vehicle_log']['awaiting_refuel']
        assert c.execute('SELECT status FROM fuel_exceptions WHERE id=%s',(r['id'],)).fetchone()['status']=='consumed'
