from uuid import uuid4
import pytest
from app import db
from tests.test_operations import ops, delivered


@pytest.mark.postgres
@pytest.mark.parametrize('counted',[900,1000,1100])
def test_manager_approves_driver_shortage_match_surplus_after_receipt(ops,counted):
    actor,post,order,shifts=ops
    manager,driver=shifts();delivered(post,order,driver)
    post('driver',f'/api/shifts/{driver}/closing')
    sub=post('driver',f'/api/shifts/{driver}/submit',{'counted_halalas':counted,'handover_halalas':counted})['submission']
    assert sub['difference_halalas']==counted-1000
    reason={'exception_reason':'Actual count reviewed'} if counted!=1000 else {}
    post('manager',f'/api/shifts/{driver}/approve',reason,status=409)
    tid=post('driver','/api/custody',{'from_shift_id':driver,'to_shift_id':manager,'amount_halalas':counted,'reference':'BAG-001','closure':True})['transfer']['id']
    post('driver',f'/api/custody/{tid}/confirm',status=404)
    post('manager',f'/api/custody/{tid}/confirm')
    if counted!=1000:post('manager',f'/api/shifts/{driver}/approve',{},status=422)
    result=post('manager',f'/api/shifts/{driver}/approve',reason,key='review-once')
    assert post('manager',f'/api/shifts/{driver}/approve',reason,key='review-once')==result
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(driver,)).fetchone()['status']=='closed'
        assert c.execute("SELECT coalesce(sum(amount_halalas),0) n FROM custody_events WHERE shift_id=%s AND kind='transfer_in'",(manager,)).fetchone()['n']==counted


@pytest.mark.postgres
def test_remote_driver_bag_requires_real_branch_account_not_manager(ops):
    actor,post,order,shifts=ops
    with db.tx() as c:c.execute("UPDATE branches SET city='رفحاء' WHERE id='ops-branch'")
    manager,driver=shifts();delivered(post,order,driver)
    post('driver',f'/api/shifts/{driver}/closing')
    post('driver',f'/api/shifts/{driver}/submit',{'counted_halalas':1000,'handover_halalas':1000})
    body={'from_shift_id':driver,'recipient_user_id':'ops-branch','amount_halalas':1000,'reference':'RAF-BAG-1','closure':True,'receiving_branch_id':'ops-branch'}
    post('driver','/api/custody',{**body,'to_shift_id':manager},status=403)
    tid=post('driver','/api/custody',body)['transfer']['id']
    assert actor('branch').get('/api/custody').json()['transfers'][0]['id']==tid
    post('manager',f'/api/custody/{tid}/confirm',status=404)
    post('other',f'/api/custody/{tid}/confirm',status=404)
    post('manager',f'/api/shifts/{driver}/approve',status=409)
    post('branch',f'/api/custody/{tid}/confirm',key='branch-receipt')
    post('branch',f'/api/custody/{tid}/confirm',key='branch-receipt')
    post('manager',f'/api/shifts/{driver}/approve')
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM custody_events WHERE shift_id=%s AND kind='transfer_in'",(manager,)).fetchone()['n']==0
        assert c.execute("SELECT count(*) n FROM custody_events WHERE reference_id=%s AND kind='transfer_out'",(tid,)).fetchone()['n']==1


@pytest.mark.postgres
def test_hafar_cannot_use_branch_bag_and_revoked_branch_cannot_confirm(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    post('driver',f'/api/shifts/{driver}/closing')
    post('driver',f'/api/shifts/{driver}/submit',{'counted_halalas':0,'handover_halalas':0})
    body={'from_shift_id':driver,'recipient_user_id':'ops-branch','receiving_branch_id':'ops-branch','amount_halalas':0,'reference':'ZERO','closure':True}
    post('driver','/api/custody',body,status=403)
    with db.tx() as c:c.execute("UPDATE branches SET city='رفحاء' WHERE id='ops-branch'")
    tid=post('driver','/api/custody',body)['transfer']['id']
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-branch'")
    post('branch',f'/api/custody/{tid}/confirm',status=403)


@pytest.mark.postgres
def test_terminal_manager_only_frozen_at_submission_and_in_report(ops):
    actor,post,order,shifts=ops
    path='/api/drivers/ops-driver/payment-terminal'
    body={'provider':'Test Bank','terminal_id':'TID-001','serial_number':'SN-001','model':'Test POS','notes':'Company device','reason':'Initial assignment'}
    def put(who,data,status=200):
        r=actor(who).put(path,json=data,headers={'Idempotency-Key':uuid4().hex})
        assert r.status_code==status,r.text
    put('driver',body,403);put('branch',body,403);put('other',body,404)
    put('manager',body)
    assert actor('driver').get(path).json()['payment_terminal']['terminal_id']=='TID-001'
    manager,driver=shifts()
    post('driver',f'/api/shifts/{driver}/closing')
    sub=post('driver',f'/api/shifts/{driver}/submit',{'counted_halalas':0,'handover_halalas':0})['submission']
    assert sub['payment_terminal']['terminal_id']=='TID-001'
    put('manager',{**body,'terminal_id':'TID-002','reason':'Replacement'})
    tid=post('driver','/api/custody',{'from_shift_id':driver,'to_shift_id':manager,'amount_halalas':0,'reference':'ZERO','closure':True})['transfer']['id']
    post('manager',f'/api/custody/{tid}/confirm')
    rid=post('manager',f'/api/shifts/{driver}/approve')['report_id']
    page=actor('manager').get(f'/api/reports/{rid}/renderings/v5/print')
    assert page.status_code==200,page.text
    assert 'TID-001' in page.text and 'TID-002' not in page.text
    assert 'متوافق' in page.text and 'عهدة نقدية مستلمة' in page.text


@pytest.mark.postgres
def test_branch_receipt_replay_cannot_bypass_revoked_scope(ops):
    actor,post,order,shifts=ops
    with db.tx() as c:c.execute("UPDATE branches SET city='رفحاء' WHERE id='ops-branch'")
    manager,driver=shifts()
    post('driver',f'/api/shifts/{driver}/closing')
    post('driver',f'/api/shifts/{driver}/submit',{'counted_halalas':0,'handover_halalas':0})
    tid=post('driver','/api/custody',{'from_shift_id':driver,'recipient_user_id':'ops-branch','receiving_branch_id':'ops-branch','amount_halalas':0,'reference':'REPLAY-BAG','closure':True})['transfer']['id']
    post('branch',f'/api/custody/{tid}/confirm',key='receipt-once')
    with db.tx() as c:
        receipt=c.execute('SELECT confirmed_by FROM custody_transfers WHERE id=%s',(tid,)).fetchone()
        assert receipt['confirmed_by']=='ops-branch'
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-branch'")
    post('branch',f'/api/custody/{tid}/confirm',key='receipt-once',status=403)
