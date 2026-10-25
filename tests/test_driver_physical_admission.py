"""Any open authorized manager, one physical driver shift, one scoped duration."""
from uuid import uuid4
import pytest
from app import db
from app.shift_vehicle import CHECKS
from test_enbishan_dual_brand import dual

pytestmark=pytest.mark.postgres


def inspection():
    return {'odometer_km':1000,'fuel_percent':100,'checks':{k:'ok' for k in CHECKS}}


def test_standalone_evening_manager_accepts_shared_endpoint_driver(dual,monkeypatch):
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED','true')
    _,_,post,_,_=dual
    manager=post('manager','/api/shifts',{'kind':'manager'},'kps')['shift']
    driver=post('driver','/api/central-operations/workdays',{
        'kind':'driver','manager_shift_id':manager['id'],'vehicle_inspection':inspection()})['shift']
    assert driver['manager_shift_id']==manager['id'] and driver['brand_id']=='kps'
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM shifts WHERE user_id='dual-driver'").fetchone()['n']==1
    post('driver','/api/central-operations/workdays',{
        'kind':'driver','manager_shift_id':manager['id'],'vehicle_inspection':inspection()},status=409)


def test_direct_canonical_driver_open_cannot_create_second_physical_brand_shift(dual):
    _,_,post,_,_=dual
    first=post('manager','/api/shifts',{'kind':'manager'},'kps')['shift']
    second=post('manager','/api/shifts',{'kind':'manager'},'enbishan')['shift']
    post('driver','/api/shifts',{'kind':'driver','manager_shift_id':first['id'],'vehicle_inspection':inspection()},'kps')
    denied=post('driver','/api/shifts',{'kind':'driver','manager_shift_id':second['id'],'vehicle_inspection':inspection()},'enbishan',status=409)
    assert denied['detail']['code']=='SHIFT_ALREADY_OPEN'
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM shifts WHERE user_id='dual-driver' AND status<>'closed'").fetchone()['n']==1


def test_global_duration_requires_delegate_scope_for_every_driver_brand(dual):
    _,actor,_,_,_=dual
    with db.tx() as c:
        c.execute("UPDATE users SET extra_permissions='[\"driver_duty_manage\"]'::jsonb WHERE id='dual-manager'")
        c.execute("UPDATE staff_brand_memberships SET branch_ids='[\"enbishan-test-1\"]'::jsonb WHERE user_id='dual-manager' AND brand_id='enbishan'")
    path='/api/driver-duty/dual-driver'
    body={'minutes':480,'reason':'GM sets one physical driver duration'}
    denied=actor('manager','kps').put(path,json=body,headers={'Idempotency-Key':uuid4().hex})
    assert denied.status_code==404,denied.text
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM driver_duty_profiles WHERE driver_id='dual-driver'").fetchone()
        c.execute("UPDATE staff_brand_memberships SET branch_ids='[\"enbishan-test-1\",\"enbishan-test-2\"]'::jsonb WHERE user_id='dual-manager' AND brand_id='enbishan'")
    result=actor('manager','kps').put(path,json=body,headers={'Idempotency-Key':uuid4().hex})
    assert result.status_code==200,result.text
    with db.tx() as c:
        assert c.execute("SELECT minutes FROM driver_duty_profiles WHERE driver_id='dual-driver'").fetchone()['minutes']==480
