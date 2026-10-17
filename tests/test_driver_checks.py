"""Temporary driver prompts, manager approval and odometer corrections."""
import pytest
from app import db, shift_vehicle
from test_operations import ops
from test_fleet import delegate
from test_shift_vehicle import inspection
pytestmark=pytest.mark.postgres

def setup(ops):
    actor,post,order,shifts=ops
    with db.tx() as c:
        c.execute("UPDATE users SET vehicle='{\"plate\":\"TEST CHECK CAR\",\"type\":\"Test car\",\"odometer_km\":1000}' WHERE id='ops-driver'")
    mid=post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})['shift']['id']
    sid=post('driver','/api/shifts',{'kind':'driver','manager_shift_id':mid,'vehicle_inspection':inspection(1000,50)})['shift']['id']
    delegate(actor)
    return actor,post,sid

def request(post,kind='odometer'):
    return post('fawzi','/api/driver-checks',{'driver_ids':['ops-driver'],'kind':kind})['ids'][0]

def test_odometer_roundtrip_audit_idempotency_and_lower_correction(ops):
    actor,post,sid=setup(ops);rid=request(post)
    assert len(actor('driver').get('/api/driver-checks').json()['requests'])==1
    reply=post('driver',f'/api/driver-checks/{rid}/reply',{'value':1050},key='same-reply')
    assert post('driver',f'/api/driver-checks/{rid}/reply',{'value':1050},key='same-reply')==reply
    assert not actor('driver').get('/api/driver-checks').json()['requests']
    approval=post('fawzi',f'/api/driver-checks/{rid}/decision',{'decision':'approved'},key='same-approval')
    assert post('fawzi',f'/api/driver-checks/{rid}/decision',{'decision':'approved'},key='same-approval')==approval
    d=actor('fawzi').get('/api/driver-checks').json()['drivers'][0]
    assert d['odometer_km']==1050
    post('fawzi','/api/driver-checks/odometer/correct',{'driver_id':'ops-driver','value':950,'expected_revision':d['revision'],'reason':'TEST digit correction'})
    with db.tx() as c:
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone()
        assert s['status']=='open' and s['vehicle_log']['start']['odometer_km']==1000
        assert s['vehicle_log']['awaiting_refuel']
        assert shift_vehicle.odometer_floor(s['vehicle_log'])==950
        assert s['vehicle_log']['odometer_adjusted']
        events=c.execute("SELECT details FROM audit_events WHERE action='fleet.odometer' ORDER BY created_at").fetchall()
        assert len(events)==2 and events[-1]['details']['previous_odometer_km']==1050
        m=c.execute("SELECT vehicle_metrics(%s,'closed','{}','') m",(db.json({**s['vehicle_log'],'end':{'odometer_km':980,'fuel_percent':100}}),)).fetchone()['m']
        assert m['actual_distance'] is None and m['efficiency'] is None
    post('driver','/api/shifts/'+sid+'/refuel',{'odometer_km':960,'liters':'5'})
    post('driver','/api/shifts/'+sid+'/refuel',{'odometer_km':959,'liters':'5'},status=422)
    post('fawzi','/api/driver-checks/odometer/correct',{'driver_id':'ops-driver','value':955,'expected_revision':d['revision'],'reason':'TEST stale form'},status=409)

@pytest.mark.parametrize('level',[0,25,50,75,100])
def test_fuel_only_clears_gate_after_explicit_approval_and_preserves_shift(ops,level):
    actor,post,sid=setup(ops)
    with db.tx() as c:
        c.execute('UPDATE shifts SET available=false WHERE id=%s',(sid,))
        before=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone()
    rid=request(post,'fuel');post('driver',f'/api/driver-checks/{rid}/reply',{'value':level})
    with db.tx() as c:assert c.execute('SELECT vehicle_log FROM shifts WHERE id=%s',(sid,)).fetchone()['vehicle_log']['awaiting_refuel']
    post('fawzi',f'/api/driver-checks/{rid}/decision',{'decision':'approved'})
    with db.tx() as c:
        after=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone()
        assert not after['vehicle_log']['awaiting_refuel']
        assert after['vehicle_log']['fuel_readiness']['temporary_exception']==(level<100)
        assert after['vehicle_log']['start']==before['vehicle_log']['start']
        assert after['vehicle_log']['refuels']==[]
        for k in ('id','opened_at','status','available','opening_halalas','manager_shift_id'):assert before[k]==after[k]
    post('fawzi',f'/api/driver-checks/{rid}/decision',{'decision':'approved'},status=409)

def test_expiry_selection_scope_and_revocation(ops):
    actor,post,sid=setup(ops);rid=request(post)
    assert not actor('driver2').get('/api/driver-checks').json()['requests']
    post('driver2',f'/api/driver-checks/{rid}/reply',{'value':1234},status=404)
    post('other',f'/api/driver-checks/{rid}/decision',{'decision':'approved'},status=404)
    post('driver','/api/driver-checks',{'driver_ids':['ops-driver'],'kind':'fuel'},status=403)
    post('manager','/api/driver-checks',{'driver_ids':['ops-driver'],'kind':'fuel'},status=403)
    with db.tx() as c:c.execute("UPDATE driver_check_requests SET expires_at=now()-interval '1 second' WHERE id=%s",(rid,))
    assert not actor('driver').get('/api/driver-checks').json()['requests']
    post('driver',f'/api/driver-checks/{rid}/reply',{'value':1234},status=409)
    assert actor('fawzi').get('/api/driver-checks').json()['requests'][0]['ui_status']=='expired'
    rid=request(post);post('driver',f'/api/driver-checks/{rid}/reply',{'value':1010})
    with db.tx() as c:c.execute("UPDATE users SET permissions='[]' WHERE id='ops-fawzi'")
    post('fawzi',f'/api/driver-checks/{rid}/decision',{'decision':'approved'},status=403)

def test_replacement_rejection_stale_segment_and_invalid_reply(ops):
    actor,post,sid=setup(ops);old=request(post,'fuel');rid=request(post,'fuel')
    post('driver',f'/api/driver-checks/{old}/reply',{'value':50},status=409)
    post('driver',f'/api/driver-checks/{rid}/reply',{'value':51},status=422)
    post('driver',f'/api/driver-checks/{rid}/reply',{'value':50})
    post('fawzi',f'/api/driver-checks/{rid}/decision',{'decision':'rejected'})
    with db.tx() as c:assert c.execute('SELECT vehicle_log FROM shifts WHERE id=%s',(sid,)).fetchone()['vehicle_log']['awaiting_refuel']
    rid=request(post);post('driver',f'/api/driver-checks/{rid}/reply',{'value':1010})
    with db.tx() as c:c.execute("UPDATE shifts SET vehicle_log=jsonb_set(vehicle_log,'{start,recorded_at}',to_jsonb((now()+interval '1 minute')::text)) WHERE id=%s",(sid,))
    post('fawzi',f'/api/driver-checks/{rid}/decision',{'decision':'approved'},status=409)

def test_older_reply_cannot_overwrite_newer_reading_or_branch_scope(ops):
    actor,post,sid=setup(ops);rid=request(post)
    post('driver',f'/api/driver-checks/{rid}/reply',{'value':1005})
    post('driver','/api/shifts/'+sid+'/refuel',{'odometer_km':1020,'liters':'5'})
    post('fawzi',f'/api/driver-checks/{rid}/decision',{'decision':'approved'},status=409)
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-fawzi'")
    assert not actor('fawzi').get('/api/driver-checks').json()['requests']
    post('fawzi',f'/api/driver-checks/{rid}/decision',{'decision':'approved'},status=404)
