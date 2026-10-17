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
