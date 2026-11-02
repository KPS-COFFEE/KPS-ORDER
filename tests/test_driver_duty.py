from uuid import uuid4
import pytest
from app import db
from app.driver_duty import Duration
from pydantic import ValidationError
from test_operations import ops

@pytest.mark.parametrize('minutes',[True,0,29,1441,60.5,'480'])
def test_duration_rejects_invalid_input(minutes):
    with pytest.raises(ValidationError):Duration(minutes=minutes,reason='Owner set schedule')

@pytest.mark.postgres
def test_duty_owner_delegation_snapshot_and_replay(ops):
    actor,post,order,shifts=ops
    url='/api/driver-duty/ops-driver'
    body={'minutes':480,'reason':'Owner sets eight hour duty'}
    def put(who,payload,status=200,key=None):
        response=actor(who).put(url,json=payload,headers={'Idempotency-Key':key or uuid4().hex})
        assert response.status_code==status,response.text
        return response.json()
    put('manager',body,403)
    put('driver',body,403)
    key=uuid4().hex
    assert put('owner',body,key=key)==put('owner',body,key=key)
    manager,driver=shifts()
    with db.tx() as c:
        row=c.execute('SELECT opened_at,scheduled_end_at FROM shifts WHERE id=%s',(driver,)).fetchone()
        assert (row['scheduled_end_at']-row['opened_at']).total_seconds()==480*60
        c.execute("UPDATE users SET extra_permissions='[\"driver_duty_manage\"]'::jsonb WHERE id='ops-manager'")
    put('manager',{'minutes':360,'reason':'General manager changes future duty'})
    with db.tx() as c:
        assert c.execute('SELECT scheduled_end_at FROM shifts WHERE id=%s',(driver,)).fetchone()['scheduled_end_at']==row['scheduled_end_at']
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='driver_duty.configured' AND entity_id='ops-driver'").fetchone()['n']==2
    assert actor('manager').get('/api/driver-duty').status_code==200

@pytest.mark.postgres
@pytest.mark.parametrize('explicit_scope',[False,True])
def test_driver_can_join_open_manager_with_shared_branch_subset(ops,explicit_scope):
    actor,post,_,_=ops
    with db.tx() as c:
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES('ops-extra',2,'ops-extra','ops-brand','Extra','حفر الباطن','{}')")
        c.execute("UPDATE users SET branch_ids='[\"ops-branch\",\"ops-extra\"]'::jsonb WHERE id='ops-driver'")
    manager=post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch']})['shift']['id']
    result=post('driver','/api/shifts',{'kind':'driver','manager_shift_id':manager,
        'branch_ids':['ops-branch','ops-extra'] if explicit_scope else []})['shift']
    assert result['branch_ids']==['ops-branch']
    post('driver','/api/shifts',{'kind':'driver','manager_shift_id':manager},status=409)


@pytest.mark.postgres
@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('period,start,later', [('morning','08:00:00','18:00:00'),('evening','18:00:00','08:00:00')])
def test_any_authorized_driver_joins_open_am_or_pm_manager_after_customer_window(ops,monkeypatch,period,start,later):
    from app import service_hours
    from test_morning_delivery import at
    actor,post,_,_=ops
    bid='kps-branch-2'
    clock=[at(start)]
    monkeypatch.setattr(service_hours,'now_riyadh',lambda:clock[0])
    with db.tx() as c:
        c.execute("UPDATE users SET branch_ids=%s WHERE id IN ('ops-manager','ops-driver')",(db.json([bid]),))
        c.execute("UPDATE branches SET settings=settings||%s WHERE id=%s",(db.json({'active':True,'delivery_enabled':True,'delivery_hours':service_hours.DEFAULT_DELIVERY_HOURS}),bid))
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'",(db.json({'morning_delivery':{'enabled':True,'version':1,'execution_branch_ids':[bid],'manager_user_ids':['ops-manager'],'preparer_user_ids':['ops-branch']}}),))
    manager=post('manager','/api/shifts',{'kind':'manager','branch_ids':[bid],'delivery_period':period})['shift']
    clock[0]=at(later)
    opened=post('driver','/api/shifts',{'kind':'driver','manager_shift_id':manager['id']})['shift']
    assert opened['branch_ids']==[bid]
    assert opened['delivery_window_snapshot']['period']==period
    assert opened['delivery_operational_date']==manager['delivery_operational_date']
    post('driver','/api/shifts',{'kind':'driver','manager_shift_id':manager['id']},status=409)


@pytest.mark.postgres
@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('change', ['manager_revoked','morning_disabled','branch_disabled','manager_closed'])
def test_open_morning_manager_does_not_bypass_fresh_authorization(ops,monkeypatch,change):
    from app import service_hours
    from test_morning_delivery import at
    actor,post,_,_=ops
    bid='kps-branch-2'
    monkeypatch.setattr(service_hours,'now_riyadh',lambda:at('08:00:00'))
    with db.tx() as c:
        c.execute("UPDATE users SET branch_ids=%s WHERE id IN ('ops-manager','ops-driver')",(db.json([bid]),))
        c.execute("UPDATE branches SET settings=settings||%s WHERE id=%s",(db.json({'active':True,'delivery_enabled':True}),bid))
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'",(db.json({'morning_delivery':{'enabled':True,'version':1,'execution_branch_ids':[bid],'manager_user_ids':['ops-manager'],'preparer_user_ids':['ops-branch']}}),))
    manager=post('manager','/api/shifts',{'kind':'manager','branch_ids':[bid],'delivery_period':'morning'})['shift']
    with db.tx() as c:
        if change=='manager_revoked':c.execute("UPDATE brands SET settings=jsonb_set(settings,'{morning_delivery,manager_user_ids}','[]') WHERE id='kps'")
        elif change=='morning_disabled':c.execute("UPDATE brands SET settings=jsonb_set(settings,'{morning_delivery,enabled}','false') WHERE id='kps'")
        elif change=='branch_disabled':c.execute("UPDATE branches SET settings=jsonb_set(settings,'{delivery_enabled}','false') WHERE id=%s",(bid,))
        else:c.execute("UPDATE shifts SET status='closing' WHERE id=%s",(manager['id'],))
    post('driver','/api/shifts',{'kind':'driver','manager_shift_id':manager['id']},status=403 if change in ('manager_revoked','morning_disabled') else 409)
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM shifts WHERE user_id='ops-driver'").fetchone()


def test_driver_staffing_inherits_manager_window_without_opening_customer_hours(monkeypatch):
    from datetime import date
    from copy import deepcopy
    from app import service_hours
    from app.operations import driver_shift_window
    from test_morning_delivery import at
    monkeypatch.setattr(service_hours,'now_riyadh',lambda:at('08:00:00'))
    class Result:
        def fetchone(self):return {'settings':{'services':{'delivery':True}}}
    class Connection:
        def execute(self,*args):return Result()
    windows={'b':{'hours':service_hours.DEFAULT_DELIVERY_HOURS,'window_start':'2026-10-19T16:00:00+03:00','window_end':'2026-10-20T04:00:00+03:00'}}
    manager={'delivery_operational_date':date(2026,10,19),'delivery_window_snapshot':{'period':'evening','branches':windows}}
    before=deepcopy(manager)
    branch={'id':'b','city':'حفر الباطن','settings':{'active':True,'delivery_enabled':True,'delivery_hours':service_hours.DEFAULT_DELIVERY_HOURS}}
    day,snapshot=driver_shift_window(Connection(),'kps',[branch],manager,{'active':True,'role':'delivery_manager','id':'m','brand_id':'kps'})
    assert day==date(2026,10,19)
    assert snapshot['branches']==windows and snapshot['staffing_admission_basis']=='open_manager'
    assert manager==before
    snapshot['branches']['b']['window_start']='different'
    assert manager==before
    assert not service_hours.hours_status(service_hours.DEFAULT_DELIVERY_HOURS)['open']
