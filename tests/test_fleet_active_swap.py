"""Real transaction, scope, replay and driver-notice contracts for fleet exceptions."""
from copy import deepcopy
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import auth, db, employee_permissions, fleet_management
from test_operations import ops
from test_fleet import delegate
from test_shift_vehicle import inspection


def test_active_swap_requires_an_explicit_grant_and_never_grants_driver_authority():
    for user in ({'role':'admin','permissions':['vehicle_maintenance_manage']},
                 {'role':'driver','extra_permissions':['fleet_active_swap']}):
        with pytest.raises(HTTPException) as error:fleet_management.require_active_swap(user)
        assert error.value.status_code==403
    fleet_management.require_active_swap({'role':'admin','permissions':['vehicle_maintenance_manage','fleet_active_swap']})
    fleet_management.require_active_swap({'role':'owner'})
    assert 'fleet_active_swap' not in employee_permissions.grantable('driver')


@pytest.fixture
def two_drivers(ops):
    actor,post,order,shifts=ops
    with db.tx() as c:
        c.execute("UPDATE users SET vehicle=%s WHERE id='ops-driver'",(db.json({'plate':'TEST FIRST 1','type':'Test first','odometer_km':1000}),))
        c.execute("UPDATE users SET vehicle=%s WHERE id='ops-driver2'",(db.json({'plate':'TEST SECOND 2','type':'Test second','odometer_km':3000}),))
    mid,sid=shifts()
    sid2=post('driver2','/api/shifts',{'kind':'driver','manager_shift_id':mid,'vehicle_inspection':inspection(3000,100)})['shift']['id']
    oid=order();tid=post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':sid})['task']['id']
    delegate(actor)
    with db.tx() as c:
        c.execute("UPDATE users SET permissions=%s WHERE id='ops-fawzi'",(db.json(['vehicle_maintenance_manage','fleet_active_swap']),))
        c.execute('UPDATE shifts SET available=false WHERE id=%s',(sid,))
    client=actor('fawzi')
    cars=client.get('/api/fleet/vehicles').json()['vehicles']
    a=next(v for v in cars if v['plate']=='TEST FIRST 1')
    b=next(v for v in cars if v['plate']=='TEST SECOND 2')
    body={'action':'active_swap','driver_id':'ops-driver2','revision':int(a['details'].get('management_revision',0)),
          'counterpart_vehicle_id':b['id'],'counterpart_revision':int(b['details'].get('management_revision',0)),
          'expected_driver_ids':['ops-driver','ops-driver2'],'confirmed':True,
          'reason':'Synthetic operator approved vehicle swap',
          'incoming':inspection(1010,100),'outgoing':inspection(3010,75)}
    return actor,post,a,b,body,sid,sid2,tid


@pytest.mark.postgres
def test_active_swap_is_atomic_audited_without_otp_and_preserves_tasks_money_and_shifts(two_drivers,monkeypatch):
    actor,post,a,b,body,sid,sid2,tid=two_drivers
    monkeypatch.setattr(auth,'require_owner_step_up',lambda *_:pytest.fail('No additional OTP for this approved operational exception'))
    with db.tx() as c:
        before=c.execute('SELECT * FROM driver_tasks WHERE id=%s',(tid,)).fetchone()
        originals={r['id']:deepcopy(r) for r in c.execute('SELECT * FROM shifts WHERE id=ANY(%s)',([sid,sid2],)).fetchall()}
    url='/api/fleet/vehicles/'+a['id']+'/management';key=uuid4().hex
    first=post('fawzi',url,body,key=key)
    assert post('fawzi',url,body,key=key)==first
    assert first['entered_by']==first['approved_by']=='ops-fawzi'
    with db.tx() as c:
        assert c.execute('SELECT * FROM driver_tasks WHERE id=%s',(tid,)).fetchone()==before
        assert not c.execute('SELECT 1 FROM custody_transfers').fetchone()
        assert not c.execute('SELECT 1 FROM collections').fetchone()
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='fleet.driver_notice'").fetchone()['n']==2
        events=c.execute("SELECT actor_id,details FROM audit_events WHERE action='fleet.manage' AND details->>'action'='active_swap'").fetchall()
        assert len(events)==2 and all(e['actor_id']==e['details']['entered_by']==e['details']['approved_by']=='ops-fawzi' for e in events)
        for shift_id,new_plate,refuel in ((sid,b['plate'],True),(sid2,a['plate'],False)):
            after=c.execute('SELECT * FROM shifts WHERE id=%s',(shift_id,)).fetchone();old=originals[shift_id]
            assert after['status']=='open' and after['user_id']==old['user_id']
            assert after['available']==old['available'] and after['manager_shift_id']==old['manager_shift_id']
            log=after['vehicle_log'];assert log['vehicle']['plate']==new_plate and log['awaiting_refuel'] is refuel
            assert log['previous_segments'][0]['start']==old['vehicle_log']['start']
            assert log['previous_segments'][0]['handover']['approved_by']=='ops-fawzi'
            assert log['start']['recorded_by']=='ops-fawzi'
            assert c.execute('SELECT count(*) n FROM fleet_operating_segments WHERE shift_id=%s',(shift_id,)).fetchone()['n']==2
    own=actor('driver').get('/api/driver-checks').json()['vehicle_notices']
    assert len(own)==1 and own[0]['details']['new_plate']==b['plate']
    other=actor('driver2').get('/api/driver-checks').json()['vehicle_notices']
    assert len(other)==1 and other[0]['id']!=own[0]['id']
    notice=own[0]['id'];client=actor('driver2')
    denied=client.post('/api/driver-checks/vehicle-notices/'+notice+'/read',json={},headers={'Idempotency-Key':uuid4().hex})
    assert denied.status_code==404
    path='/api/driver-checks/vehicle-notices/'+notice+'/read';key=uuid4().hex
    read=post('driver',path,{},key=key);assert post('driver',path,{},key=key)==read
    assert not actor('driver').get('/api/driver-checks').json()['vehicle_notices']
    assert len(actor('driver2').get('/api/driver-checks').json()['vehicle_notices'])==1


@pytest.mark.postgres
@pytest.mark.parametrize('change,code',[
    ({'confirmed':False},422),({'counterpart_revision':99},409),
    ({'expected_driver_ids':['ops-driver']},404),({'counterpart_vehicle_id':'foreign-car'},404),
    ({'incoming':inspection(999,100)},422),({'outgoing':None},422)])
def test_invalid_swap_rolls_back_both_vehicle_intervals_and_notices(two_drivers,change,code):
    actor,post,a,b,body,sid,sid2,tid=two_drivers
    with db.tx() as c:before=c.execute('SELECT id,vehicle_log FROM shifts WHERE id=ANY(%s) ORDER BY id',([sid,sid2],)).fetchall()
    result=actor('fawzi').post('/api/fleet/vehicles/'+a['id']+'/management',json=body|change,headers={'Idempotency-Key':uuid4().hex})
    assert result.status_code==code,result.text
    with db.tx() as c:
        assert c.execute('SELECT id,vehicle_log FROM shifts WHERE id=ANY(%s) ORDER BY id',([sid,sid2],)).fetchall()==before
        assert not c.execute("SELECT 1 FROM audit_events WHERE action='fleet.driver_notice'").fetchone()


@pytest.mark.postgres
def test_live_permission_revocation_blocks_saved_swap_replay(two_drivers):
    actor,post,a,b,body,*_=two_drivers;path='/api/fleet/vehicles/'+a['id']+'/management';key=uuid4().hex
    post('fawzi',path,body,key=key)
    with db.tx() as c:c.execute("UPDATE users SET permissions='[\"vehicle_maintenance_manage\"]' WHERE id='ops-fawzi'")
    denied=actor('fawzi').post(path,json=body,headers={'Idempotency-Key':key})
    assert denied.status_code==403,denied.text
