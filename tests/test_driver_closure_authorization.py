from uuid import uuid4
import pytest
from app import db, driver_closure
from test_operations import ops

@pytest.mark.postgres
def test_unscheduled_driver_cannot_close_until_manager_approves_and_reject_keeps_open(ops):
    actor,post,order,shifts=ops
    manager,sid=shifts()
    post('driver',f'/api/shifts/{sid}/closing',status=409)
    request=post('driver',f'/api/driver-closure/{sid}/request',{'reason':'Personal emergency'})
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='open'
    approval=request['authorization']
    post('driver',f'/api/driver-closure/{sid}/decision',{'decision':'approved','request_id':approval['id'],'reason':'forbidden'},status=403)
    post('other',f'/api/driver-closure/{sid}/decision',{'decision':'approved','request_id':approval['id'],'reason':'wrong brand'},status=404)
    post('manager',f'/api/driver-closure/{sid}/decision',{'decision':'rejected','request_id':approval['id'],'reason':'Please continue shift'})
    post('driver',f'/api/shifts/{sid}/closing',status=409)
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='open'
    request=post('driver',f'/api/driver-closure/{sid}/request',{'reason':'Emergency confirmed'})
    decision=post('manager',f'/api/driver-closure/{sid}/decision',{'decision':'approved','request_id':request['authorization']['id'],'reason':'Approved early departure'})
    assert decision['status']=='open'
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='open'
    post('driver',f'/api/shifts/{sid}/closing')
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='closing'
        assert c.execute('SELECT count(*) n FROM closure_reports WHERE shift_id=%s',(sid,)).fetchone()['n']==0

@pytest.mark.postgres
@pytest.mark.parametrize('exception',[False,True])
def test_actual_assignment_eligibility_before_and_after_fuel_readiness(ops,exception):
    from tests.test_shift_vehicle import inspection
    actor,post,order,shifts=ops
    mid=post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})['shift']['id']
    sid=post('driver','/api/shifts',{'kind':'driver','manager_shift_id':mid,'vehicle_inspection':inspection(1000,65)})['shift']['id']
    with db.tx() as c:
        assert c.execute('SELECT available FROM shifts WHERE id=%s',(sid,)).fetchone()['available'] is True
    oid=order()
    post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':sid},status=409)
    roster=actor('manager').get('/api/operations/drivers').json()['drivers']
    row=next(x for x in roster if x['id']=='ops-driver')
    assert row['availability']=='waiting_for_fuel' and not row['assignment_eligible']
    readiness={'odometer_km':1005,'fuel_percent':100}
    if exception:
        request=post('driver','/api/fuel-exceptions',{'phase':'activation','shift_id':sid,'manager_shift_id':mid,'reason':'aldrees_limit_reached','fuel_percent':65,'odometer_km':1005})
        row=next(x for x in actor('manager').get('/api/operations/drivers').json()['drivers'] if x['id']=='ops-driver')
        assert row['availability']=='fuel_exception_pending'
        post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':sid},status=409)
        post('manager','/api/fuel-exceptions/'+request['id'],{'decision':'approved'})
        readiness.update(fuel_percent=65,fuel_exception_id=request['id'])
    if not exception:
        post('driver','/api/fuel-exceptions/shifts/'+sid+'/ready',readiness,status=409)
        post('driver','/api/shifts/'+sid+'/refuel',{'liters':'10.00','odometer_km':1005})
    post('driver','/api/fuel-exceptions/shifts/'+sid+'/ready',readiness)
    row=next(x for x in actor('manager').get('/api/operations/drivers').json()['drivers'] if x['id']=='ops-driver')
    assert row['assignment_eligible'] and 'ops-branch' in row['eligible_branch_ids']
    post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':sid})

@pytest.mark.postgres
def test_fuel_exception_confirmation_preserves_manager_pause(ops):
    from tests.test_shift_vehicle import inspection
    actor,post,order,shifts=ops
    mid=post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})['shift']['id']
    sid=post('driver','/api/shifts',{'kind':'driver','manager_shift_id':mid,'vehicle_inspection':inspection(1000,65)})['shift']['id']
    request=post('driver','/api/fuel-exceptions',{'phase':'activation','shift_id':sid,'manager_shift_id':mid,'reason':'aldrees_limit_reached','fuel_percent':65,'odometer_km':1005})
    post('manager','/api/fuel-exceptions/'+request['id'],{'decision':'approved'})
    post('manager',f'/api/shifts/{sid}/availability',{'available':False})
    ready=post('driver',f'/api/fuel-exceptions/shifts/{sid}/ready',{'odometer_km':1005,'fuel_percent':65,'fuel_exception_id':request['id']})
    assert ready['available'] is False
    with db.tx() as c:
        row=c.execute('SELECT available,vehicle_log FROM shifts WHERE id=%s',(sid,)).fetchone()
        assert row['available'] is False
        assert row['vehicle_log']['awaiting_refuel'] is False
        assert row['vehicle_log']['fuel_readiness']['fuel_percent']==65
        assert c.execute('SELECT status FROM fuel_exceptions WHERE id=%s',(request['id'],)).fetchone()['status']=='consumed'
    post('manager','/api/orders/'+order()+'/assign',{'driver_shift_id':sid},status=409)
    with db.tx() as c:
        before=c.execute('SELECT available,vehicle_log FROM shifts WHERE id=%s',(sid,)).fetchone()
        audit_count=c.execute("SELECT count(*) n FROM audit_events WHERE action='fuel.ready' AND entity_id=%s",(sid,)).fetchone()['n']
    # Exact repeated confirmation is idempotent even with a fresh request key;
    # it cannot clear the manager's pause or record another readiness event.
    again=post('driver',f'/api/fuel-exceptions/shifts/{sid}/ready',{'odometer_km':1005,'fuel_percent':65,'fuel_exception_id':request['id']})
    assert again==ready and again['available'] is False
    with db.tx() as c:
        assert c.execute('SELECT available,vehicle_log FROM shifts WHERE id=%s',(sid,)).fetchone()==before
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='fuel.ready' AND entity_id=%s",(sid,)).fetchone()['n']==audit_count
        assert c.execute('SELECT status FROM fuel_exceptions WHERE id=%s',(request['id'],)).fetchone()['status']=='consumed'
    post('driver',f'/api/fuel-exceptions/shifts/{sid}/ready',{'odometer_km':1006,'fuel_percent':65,'fuel_exception_id':request['id']},status=409)
    post('manager','/api/orders/'+order()+'/assign',{'driver_shift_id':sid},status=409)


@pytest.mark.postgres
def test_withdrawal_is_scoped_idempotent_and_never_closes_shift(ops):
    actor,post,order,shifts=ops
    manager,sid=shifts()
    first=post('driver',f'/api/driver-closure/{sid}/request',{'reason':'Temporary request'})
    duplicate=post('driver',f'/api/driver-closure/{sid}/request',{'reason':'Repeat click'})
    assert duplicate['authorization']['id']==first['authorization']['id']
    post('manager',f'/api/driver-closure/{sid}/withdraw',{'request_id':first['authorization']['id'],'reason':'Forbidden'},status=403)
    withdrawn=post('driver',f'/api/driver-closure/{sid}/withdraw',{'request_id':first['authorization']['id'],'reason':'Continue working'})
    assert withdrawn['status']=='open' and withdrawn['authorization']['status']=='withdrawn'
    post('driver',f'/api/driver-closure/{sid}/withdraw',{'request_id':first['authorization']['id'],'reason':'Repeat withdrawal'})
    post('manager',f'/api/driver-closure/{sid}/decision',{'decision':'approved','request_id':first['authorization']['id'],'reason':'Too late'},status=409)
    post('driver',f'/api/shifts/{sid}/closing',status=409)
    fresh=post('driver',f'/api/driver-closure/{sid}/request',{'reason':'New emergency'})
    assert fresh['authorization']['id']!=first['authorization']['id']
    post('driver',f'/api/driver-closure/{sid}/withdraw',{'request_id':first['authorization']['id'],'reason':'Delayed old withdrawal'},status=409)
    with db.tx() as c:
        assert c.execute('SELECT closure_authorization FROM shifts WHERE id=%s',(sid,)).fetchone()['closure_authorization']['id']==fresh['authorization']['id']
    with db.tx() as c:
        row=c.execute('SELECT status,available FROM shifts WHERE id=%s',(sid,)).fetchone()
        assert row['status']=='open' and row['available'] is True
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='driver_closure.withdraw' AND entity_id=%s",(sid,)).fetchone()['n']==1

@pytest.mark.postgres
def test_pending_early_closure_does_not_stop_assignment_or_record_departure(ops):
    actor,post,order,shifts=ops
    manager,sid=shifts()
    request=post('driver',f'/api/driver-closure/{sid}/request',{'reason':'Request permission only'})
    assigned=post('manager','/api/orders/'+order()+'/assign',{'driver_shift_id':sid})
    assert assigned['task']['shift_id']==sid
    with db.tx() as c:
        row=c.execute('SELECT status,available,submitted_at,closed_at FROM shifts WHERE id=%s',(sid,)).fetchone()
        assert row['status']=='open' and row['available'] is True
        assert row['submitted_at'] is None and row['closed_at'] is None
    post('manager',f'/api/driver-closure/{sid}/decision',{'decision':'approved','request_id':request['authorization']['id'],'reason':'Permission only'})
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='open'
    post('driver',f'/api/shifts/{sid}/closing')
    # Approval and beginning closure do not silently complete the pending task.
    with db.tx() as c:
        assert c.execute('SELECT status FROM driver_tasks WHERE id=%s',(assigned['task']['id'],)).fetchone()['status']=='assigned'
        assert c.execute('SELECT count(*) n FROM closure_reports WHERE shift_id=%s',(sid,)).fetchone()['n']==0
