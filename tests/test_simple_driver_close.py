"""Actual receipt and closure are atomic, scoped and safe to retry."""
import pytest
from app import db, operations
from tests.test_operations import ops, delivered, begin_driver_closure
from tests.test_hardening_operations import request, principal, race
from tests.test_driver_cash_handover import submit_cash

pytestmark=pytest.mark.postgres

@pytest.mark.parametrize('counted',[900,1000,1100])
@pytest.mark.parametrize('transfer_exists',[False,True])
def test_submitted_receipt_and_approval_once(ops,counted,transfer_exists):
    actor,post,order,shifts=ops
    manager,driver=shifts();delivered(post,order,driver)
    begin_driver_closure(post,driver)
    if transfer_exists:
        submit_cash(post,driver,manager,counted)
    else:
        post('manager',f'/api/shifts/{driver}/submit',{'terminal_total_halalas':0,'counted_halalas':counted,'handover_halalas':counted,'exception_reason':'TEST manager counted before receipt'})
    body={'received_halalas':counted,'confirmed':True,'exception_reason':'TEST actual discrepancy' if counted!=1000 else ''}
    url=f'/api/shifts/{driver}/driver-close'
    result=post('manager',url,body,key='TEST-single-close')
    assert post('manager',url,body,key='TEST-single-close')==result
    post('manager',url,body,status=409)
    with db.tx() as c:
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
        assert s['status']=='closed' and s['submission']['difference_halalas']==counted-1000
        assert c.execute('SELECT count(*) n FROM closure_reports WHERE shift_id=%s',(driver,)).fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM custody_events WHERE shift_id=%s AND kind='transfer_in'",(manager,)).fetchone()['n']==1
        assert c.execute("SELECT sum(amount_halalas) n FROM custody_events WHERE shift_id=%s AND kind='transfer_in'",(manager,)).fetchone()['n']==counted

@pytest.mark.parametrize('status',['open','closing'])
def test_delegated_close_and_notification(ops,status):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    if status=='closing':begin_driver_closure(post,driver)
    body={'terminal_total_halalas':0,'counted_halalas':0,'received_halalas':0,'confirmed':True,'exception_reason':'TEST phone unavailable'}
    url=f'/api/shifts/{driver}/driver-close'
    post('manager',url,{**body,'exception_reason':''},status=422)
    post('driver',url,body,status=403)
    post('other',url,body,status=404)
    post('manager',url,{**body,'confirmed':False},status=422)
    result=post('manager',url,body,key='TEST-delegated-close')
    assert post('manager',url,body,key='TEST-delegated-close')==result
    with db.tx() as c:
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
        assert s['status']=='closed'
        assert s['submission']['closure_exception']['delegated'] is True
        assert s['vehicle_log']['end_exception']['reason']==body['exception_reason']
    notices=actor('owner').get('/api/report-notifications').json()['notifications']
    assert len(notices)==1 and notices[0]['closure_exception']['delegated'] is True
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'")
    post('manager',url,body,key='TEST-delegated-close',status=403)

def test_unresolved_task_rolls_back_everything(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts();oid=order()
    post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})
    post('manager',f'/api/shifts/{driver}/driver-close',{'counted_halalas':0,'received_halalas':0,'confirmed':True,'exception_reason':'TEST unavailable'},status=409)
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(driver,)).fetchone()['status']=='open'
        assert c.execute('SELECT count(*) n FROM custody_transfers WHERE from_shift_id=%s',(driver,)).fetchone()['n']==0

def test_different_keys_race_cannot_repeat_financial_effects(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    user=principal('manager')
    from tests.test_shift_vehicle import inspection
    body=operations.DriverClose(vehicle_inspection=inspection(1025,100),terminal_total_halalas=0,counted_halalas=0,received_halalas=0,confirmed=True,exception_reason='TEST unavailable')
    results=race([
        lambda:operations.simple_driver_close(driver,body,request('TEST-race-a'),user),
        lambda:operations.simple_driver_close(driver,body,request('TEST-race-b'),user),
    ])
    assert sorted(r[0] for r in results)==[200,409]
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM closure_reports WHERE shift_id=%s',(driver,)).fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM custody_events WHERE shift_id=%s AND kind='transfer_in'",(manager,)).fetchone()['n']==1

def test_failed_operational_approval_does_not_confirm_or_move_cash(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts();delivered(post,order,driver)
    begin_driver_closure(post,driver)
    _,tid=submit_cash(post,driver,manager,900,reference='TEST atomic receipt')
    late_order=order()
    with db.tx() as c:c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by) VALUES('late-synthetic-task',%s,'ops-driver',%s,'assigned','ops-manager')",(late_order,driver))
    post('manager',f'/api/shifts/{driver}/driver-close',{'received_halalas':900,'confirmed':True},status=409)
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(driver,)).fetchone()['status']=='submitted'
        t=c.execute('SELECT * FROM custody_transfers WHERE id=%s',(tid,)).fetchone()
        assert t['status']=='pending' and t['confirmed_by'] is None
        assert c.execute('SELECT count(*) n FROM custody_events WHERE reference_id=%s',(tid,)).fetchone()['n']==0

def test_retained_actual_cash_preserved_and_financial_treatment_is_deferred(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts();delivered(post,order,driver)
    begin_driver_closure(post,driver)
    post('driver',f'/api/shifts/{driver}/submit',{'terminal_total_halalas':0,'counted_halalas':1000,'handover_halalas':800,'retained_halalas':200},status=422)
    post('manager',f'/api/shifts/{driver}/submit',{'terminal_total_halalas':0,'counted_halalas':1000,'handover_halalas':800,'retained_halalas':200,'exception_reason':'TEST authorized manager retained count'})
    url=f'/api/shifts/{driver}/driver-close'
    post('manager',url,{'received_halalas':800,'counted_halalas':800,'confirmed':True,'exception_reason':'TEST retained'},status=422)
    post('manager',url,{'received_halalas':800,'confirmed':True})
    with db.tx() as c:
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
        assert s['submission']['counted_halalas']==1000
        assert s['submission']['retained_halalas']==200
        assert s['submission']['financial_review_required'] is True
        assert c.execute('SELECT amount_halalas FROM retained_carryovers WHERE from_shift_id=%s',(driver,)).fetchone()['amount_halalas']==200

def test_another_manager_cannot_close_or_claim_receipt(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    with db.tx() as c:c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('ops-manager2','TEST other manager','delivery_manager','ops-brand','[\"ops-branch\"]')")
    post('manager2',f'/api/shifts/{driver}/driver-close',{'received_halalas':0,'counted_halalas':0,'confirmed':True,'exception_reason':'TEST unavailable'},status=403)
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(driver,)).fetchone()['status']=='open'

@pytest.mark.parametrize('submitted',[False,True])
def test_owner_monitoring_does_not_claim_manager_receipt(ops,submitted):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    if submitted:
        begin_driver_closure(post,driver)
        submit_cash(post,driver,manager)
    body={'terminal_total_halalas':None if submitted else 0,'received_halalas':0,'counted_halalas':None if submitted else 0,
          'confirmed':True,'exception_reason':'TEST unavailable' if not submitted else ''}
    result=post('owner',f'/api/shifts/{driver}/driver-close',body,status=403)
    assert result['detail']['code']=='DRIVER_CLOSE_MANAGER_ONLY'
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(driver,)).fetchone()['status']==('submitted' if submitted else 'open')
        assert c.execute('SELECT count(*) n FROM custody_transfers WHERE from_shift_id=%s',(driver,)).fetchone()['n']==int(submitted)
    post('manager',f'/api/shifts/{driver}/driver-close',body)
    with db.tx() as c:
        assert c.execute('SELECT status,approved_by FROM shifts WHERE id=%s',(driver,)).fetchone()=={'status':'closed','approved_by':'ops-manager'}

def test_legacy_other_recipient_is_explained_without_moving_cash(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    begin_driver_closure(post,driver)
    _,tid=submit_cash(post,driver,manager,reference='TEST legacy recipient')
    # Synthetic legacy record only; the runtime never rewrites a recipient.
    with db.tx() as c:c.execute("UPDATE custody_transfers SET recipient_user_id='ops-owner' WHERE id=%s",(tid,))
    result=post('manager',f'/api/shifts/{driver}/driver-close',{'received_halalas':0,'confirmed':True},status=409)
    assert result['detail']['code']=='DRIVER_RECEIPT_OTHER_RECIPIENT'
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(driver,)).fetchone()['status']=='submitted'
        assert c.execute('SELECT status,confirmed_by,recipient_user_id FROM custody_transfers WHERE id=%s',(tid,)).fetchone()=={'status':'pending','confirmed_by':None,'recipient_user_id':'ops-owner'}
        assert c.execute('SELECT count(*) n FROM custody_events WHERE reference_id=%s',(tid,)).fetchone()['n']==0


def test_close_cannot_silently_ignore_a_conflicting_already_witnessed_receipt(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts();delivered(post,order,driver)
    begin_driver_closure(post,driver)
    _,tid=submit_cash(post,driver,manager,1000)
    post('manager',f'/api/custody/{tid}/confirm',{'received_halalas':900,'confirmed':True})
    blocked=post('manager',f'/api/shifts/{driver}/driver-close',{'received_halalas':1000,'confirmed':True},status=409)
    assert blocked['detail']['code']=='RECORDED_RECEIPT_CHANGED'
    with db.tx() as c:
        assert c.execute('SELECT amount_halalas FROM custody_transfers WHERE id=%s',(tid,)).fetchone()['amount_halalas']==900
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(driver,)).fetchone()['status']=='submitted'
        assert c.execute('SELECT count(*) n FROM closure_reports WHERE shift_id=%s',(driver,)).fetchone()['n']==0
    post('manager',f'/api/shifts/{driver}/driver-close',{'received_halalas':900,'confirmed':True})
