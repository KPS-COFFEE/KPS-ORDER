from uuid import uuid4
import pytest
from app import db
from tests.test_operations import ops, delivered, begin_driver_closure


def submit_cash(post, driver, manager, counted=0, *, branch=False, reference='TEST-BAG'):
    receiver = ({'recipient_user_id':'ops-branch','receiving_branch_id':'ops-branch'} if branch
                else {'recipient_user_id':'ops-manager','to_shift_id':manager})
    result=post('driver',f'/api/shifts/{driver}/submit',{'terminal_total_halalas':0,
        'counted_halalas':counted,'handover_halalas':counted,'handover':{**receiver,'reference':reference}})
    with db.tx() as c:
        transfers=c.execute('SELECT id FROM custody_transfers WHERE from_shift_id=%s AND closure',(driver,)).fetchall()
        assert len(transfers)==1
    return result,transfers[0]['id']


@pytest.mark.postgres
@pytest.mark.parametrize('city',['حفر الباطن','رفحاء','الدمام'])
def test_all_cities_require_linked_manager_receipt_and_scope(ops,city):
    actor,post,order,shifts=ops
    with db.tx() as c:c.execute('UPDATE branches SET city=%s WHERE id=%s',(city,'ops-branch'))
    manager,driver=shifts()
    begin_driver_closure(post,driver)
    _,tid=submit_cash(post,driver,manager)
    branch_body={'from_shift_id':driver,'recipient_user_id':'ops-branch','receiving_branch_id':'ops-branch','amount_halalas':0,'reference':'REJECT BRANCH','closure':True}
    post('driver','/api/custody',branch_body,status=403)
    body={'from_shift_id':driver,'recipient_user_id':'ops-manager','to_shift_id':manager,'amount_halalas':0,'reference':'MANAGER ALL CITIES','closure':True}
    post('driver','/api/custody',{**body,'recipient_user_id':'ops-branch'},status=422)
    post('driver','/api/custody',body,status=409)
    post('branch',f'/api/custody/{tid}/confirm',status=404)
    post('manager',f'/api/shifts/{driver}/approve',status=409)
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'")
    post('manager',f'/api/custody/{tid}/confirm',status=403)
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[\"ops-branch\"]' WHERE id='ops-manager'")
    post('manager',f'/api/custody/{tid}/confirm',key='actual-manager-receipt')
    post('manager',f'/api/custody/{tid}/confirm',key='actual-manager-receipt')
    post('manager',f'/api/shifts/{driver}/approve')
    with db.tx() as c:
        t=c.execute('SELECT * FROM custody_transfers WHERE id=%s',(tid,)).fetchone()
        assert t['confirmed_by']=='ops-manager' and t['receiving_branch_id'] is None


@pytest.mark.postgres
@pytest.mark.parametrize('counted',[900,1000,1100])
def test_manager_approves_driver_shortage_match_surplus_after_receipt(ops,counted):
    actor,post,order,shifts=ops
    manager,driver=shifts();delivered(post,order,driver)
    begin_driver_closure(post,driver)
    submitted,tid=submit_cash(post,driver,manager,counted)
    assert 'difference_halalas' not in submitted['submission']
    sub=actor('manager').get(f'/api/shifts/{driver}').json()['shift']['submission']
    assert sub['difference_halalas']==counted-1000
    reason={'exception_reason':'Actual count reviewed'} if counted!=1000 else {}
    post('manager',f'/api/shifts/{driver}/approve',reason,status=409)
    post('driver',f'/api/custody/{tid}/confirm',status=404)
    post('manager',f'/api/custody/{tid}/confirm')
    result=post('manager',f'/api/shifts/{driver}/approve',reason,key='review-once')
    assert post('manager',f'/api/shifts/{driver}/approve',reason,key='review-once')==result
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(driver,)).fetchone()['status']=='closed'
        assert c.execute("SELECT coalesce(sum(amount_halalas),0) n FROM custody_events WHERE shift_id=%s AND kind='transfer_in'",(manager,)).fetchone()['n']==counted


@pytest.mark.postgres
def test_remote_driver_bag_requires_real_branch_account_not_manager(ops):
    actor,post,order,shifts=ops
    with db.tx() as c:c.execute("UPDATE brands SET settings=settings || '{\"driver_cash_receipt_policy\":\"branch_by_city\"}'::jsonb WHERE id='ops-brand'")
    with db.tx() as c:c.execute("UPDATE branches SET city='رفحاء' WHERE id='ops-branch'")
    manager,driver=shifts();delivered(post,order,driver)
    begin_driver_closure(post,driver)
    _,tid=submit_cash(post,driver,manager,1000,branch=True,reference='RAF-BAG-1')
    body={'from_shift_id':driver,'recipient_user_id':'ops-branch','amount_halalas':1000,'reference':'RAF-BAG-1','closure':True,'receiving_branch_id':'ops-branch'}
    post('driver','/api/custody',{**body,'to_shift_id':manager},status=403)
    post('driver','/api/custody',body,status=409)
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
    with db.tx() as c:c.execute("UPDATE brands SET settings=settings || '{\"driver_cash_receipt_policy\":\"branch_by_city\"}'::jsonb WHERE id='ops-brand'")
    manager,driver=shifts()
    begin_driver_closure(post,driver)
    body={'from_shift_id':driver,'recipient_user_id':'ops-branch','receiving_branch_id':'ops-branch','amount_halalas':0,'reference':'ZERO','closure':True}
    from app.shift_vehicle import CHECKS
    post('driver',f'/api/shifts/{driver}/submit',{'terminal_total_halalas':0,'counted_halalas':0,'handover_halalas':0,'vehicle_inspection':{'odometer_km':1025,'fuel_percent':100,'checks':{k:'ok' for k in CHECKS}},'handover':{'recipient_user_id':'ops-branch','receiving_branch_id':'ops-branch','reference':'ZERO'}},status=403)
    with db.tx() as c:c.execute("UPDATE branches SET city='رفحاء' WHERE id='ops-branch'")
    _,tid=submit_cash(post,driver,manager,branch=True,reference='ZERO')
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
    begin_driver_closure(post,driver)
    submitted,tid=submit_cash(post,driver,manager)
    sub=submitted['submission']
    assert sub['payment_terminal']['terminal_id']=='TID-001'
    put('manager',{**body,'terminal_id':'TID-002','reason':'Replacement'})
    post('manager',f'/api/custody/{tid}/confirm')
    rid=post('manager',f'/api/shifts/{driver}/approve')['report_id']
    page=actor('manager').get(f'/api/reports/{rid}/renderings/v5/print')
    assert page.status_code==200,page.text
    assert 'TID-001' in page.text and 'TID-002' not in page.text
    assert 'متوافق' in page.text and 'عهدة نقدية مستلمة' in page.text


@pytest.mark.postgres
def test_branch_receipt_replay_cannot_bypass_revoked_scope(ops):
    actor,post,order,shifts=ops
    with db.tx() as c:c.execute("UPDATE brands SET settings=settings || '{\"driver_cash_receipt_policy\":\"branch_by_city\"}'::jsonb WHERE id='ops-brand'")
    with db.tx() as c:c.execute("UPDATE branches SET city='رفحاء' WHERE id='ops-branch'")
    manager,driver=shifts()
    begin_driver_closure(post,driver)
    _,tid=submit_cash(post,driver,manager,branch=True,reference='REPLAY-BAG')
    post('branch',f'/api/custody/{tid}/confirm',key='receipt-once')
    with db.tx() as c:
        receipt=c.execute('SELECT confirmed_by FROM custody_transfers WHERE id=%s',(tid,)).fetchone()
        assert receipt['confirmed_by']=='ops-branch'
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-branch'")
    post('branch',f'/api/custody/{tid}/confirm',key='receipt-once',status=403)


@pytest.mark.postgres
@pytest.mark.parametrize('counted,retained',[(0,0),(900,0),(1000,100),(1100,0)])
def test_delegated_closure_notifies_owner_and_preserves_receipt_gate(ops,counted,retained):
    actor,post,order,shifts=ops
    manager,driver=shifts();delivered(post,order,driver)
    begin_driver_closure(post,driver,closing_actor='manager')
    body={'terminal_total_halalas':0,'counted_halalas':counted,'handover_halalas':counted-retained,'retained_halalas':retained,
          'exception_reason':'TEST driver unavailable',
          'handover':{'recipient_user_id':'ops-manager','to_shift_id':manager,'reference':'TEST DELEGATED'}}
    # Raw client: delegated closure now also requires actual FULL inspection.
    from tests.test_shift_vehicle import inspection
    body['vehicle_inspection']=inspection(1025,100)
    def submit(who,data,key,status):
        r=actor(who).post(f'/api/shifts/{driver}/submit',json=data,headers={'Idempotency-Key':key})
        assert r.status_code==status,r.text
        return r.json()
    submit('manager',{**body,'exception_reason':''},'missing-reason',422)
    submit('other',body,'other-owner-denied',404)
    submit('driver',body,'driver-cannot-except',403)
    result=submit('manager',body,'manager-delegated-once',200)
    assert submit('manager',body,'manager-delegated-once',200)==result
    assert result['status']=='submitted'
    reason={'exception_reason':'TEST actual count reviewed'}
    post('manager',f'/api/shifts/{driver}/approve',reason,status=409)
    with db.tx() as c:
        t=c.execute('SELECT * FROM custody_transfers WHERE from_shift_id=%s',(driver,)).fetchone()
        assert t['status']=='pending' and t['amount_halalas']==counted-retained
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
        assert s['vehicle_log']['end']['fuel_percent']==100 and s['vehicle_log']['end_exception']['recorded_by']=='ops-manager'
    post('manager',f"/api/custody/{t['id']}/confirm")
    r=post('manager',f'/api/shifts/{driver}/approve',reason,key='approve-exception-once')
    assert post('manager',f'/api/shifts/{driver}/approve',reason,key='approve-exception-once')==r
    notices=actor('owner').get('/api/report-notifications').json()['notifications']
    assert len(notices)==1 and notices[0]['title']=='مقفل باستثناء من مدير التوصيل'
    assert notices[0]['closure_exception']['delegated'] is True
    assert notices[0]['approved_by_name']=='manager'
    assert len(actor('owner').get('/api/operations/alerts').json()['closure_exceptions'])==1
    assert actor('other').get('/api/report-notifications').json()['notifications']==[]
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM closure_reports WHERE shift_id=%s',(driver,)).fetchone()['n']==1
        snap=c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',(r['report_id'],)).fetchone()['snapshot']
        assert snap['shift']['submission']['difference_halalas']==counted-1000
    assert 'مقفل باستثناء' in actor('owner').get(f"/api/reports/{r['report_id']}/renderings/v5/print").text


@pytest.mark.postgres
@pytest.mark.parametrize('remote',[False,True])
def test_atomic_driver_submission_rolls_back_bad_handover_and_routes_actual_receiver(ops,remote):
    actor,post,order,shifts=ops
    if remote:
        with db.tx() as c:c.execute("UPDATE branches SET city='رفحاء' WHERE id='ops-branch'")
    manager,driver=shifts()
    begin_driver_closure(post,driver)
    from app.shift_vehicle import CHECKS
    body={'terminal_total_halalas':0,'counted_halalas':0,'handover_halalas':0,'vehicle_inspection':{'odometer_km':1000,'fuel_percent':100,'checks':{k:'ok' for k in CHECKS}},
          'handover':{'recipient_user_id':'ops-other','reference':'TEST BAD'}}
    post('driver',f'/api/shifts/{driver}/submit',body,status=422)
    with db.tx() as c:
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
        assert s['status']=='closing' and s['submission'] is None and 'end' not in s['vehicle_log']
        assert not c.execute('SELECT 1 FROM custody_transfers WHERE from_shift_id=%s',(driver,)).fetchone()
    body['handover']={'recipient_user_id':'ops-manager','to_shift_id':manager,'reference':'TEST MANAGER ALL CITIES'}
    result=post('driver',f'/api/shifts/{driver}/submit',body,key='atomic-submit-once')
    assert post('driver',f'/api/shifts/{driver}/submit',body,key='atomic-submit-once')==result
    with db.tx() as c:t=c.execute('SELECT * FROM custody_transfers WHERE from_shift_id=%s',(driver,)).fetchone()
    post('branch',f"/api/custody/{t['id']}/confirm",status=404)
    post('manager',f"/api/custody/{t['id']}/confirm")
    post('manager',f'/api/shifts/{driver}/approve')
    assert actor('owner').get('/api/report-notifications').json()['notifications']==[]


@pytest.mark.postgres
@pytest.mark.parametrize('actual',[0,900,1100])
def test_independent_actual_receipt_closes_operations_without_posting_adjustment(ops,actual,monkeypatch):
    from app import reporting
    monkeypatch.setattr(reporting,'generate_files',lambda *_:None)
    actor,post,order,shifts=ops
    manager,driver=shifts();delivered(post,order,driver)
    begin_driver_closure(post,driver)
    _,tid=submit_cash(post,driver,manager,1000)
    body={'received_halalas':actual,'confirmed':True}
    post('manager',f'/api/custody/{tid}/confirm',{**body,'confirmed':False},status=422)
    post('driver',f'/api/custody/{tid}/confirm',body,status=404)
    key=uuid4().hex
    received=post('manager',f'/api/custody/{tid}/confirm',body,key=key)
    assert post('manager',f'/api/custody/{tid}/confirm',body,key=key)==received
    post('manager',f'/api/shifts/{driver}/approve',{})
    with db.tx() as c:
        shift=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
        assert shift['status']=='closed'
        sub=shift['submission']
        assert sub['counted_halalas']==1000 and sub['handover_halalas']==1000
        assert sub['actual_handover_halalas']==actual and sub['receipt_difference_halalas']==actual-1000
        assert sub['financial_review_required'] is True and sub['financial_state']=='pending_accounting'
        assert c.execute("SELECT 1 FROM custody_events WHERE shift_id=%s AND kind='adjustment'",(driver,)).fetchone() is None
        assert c.execute('SELECT amount_halalas FROM custody_transfers WHERE id=%s',(tid,)).fetchone()['amount_halalas']==actual
        assert c.execute("SELECT sum(amount_halalas) n FROM custody_events WHERE shift_id=%s AND kind='transfer_in'",(manager,)).fetchone()['n']==actual
        report=c.execute('SELECT snapshot FROM closure_reports WHERE shift_id=%s',(driver,)).fetchone()['snapshot']
        assert report['shift']['submission']['handover_halalas']==1000
        assert report['shift']['submission']['actual_handover_halalas']==actual
        assert c.execute("SELECT count(*) n FROM custody_events WHERE reference_id=%s AND kind='transfer_out'",(tid,)).fetchone()['n']==1
    next_driver=post('driver','/api/shifts',{'kind':'driver','branch_ids':['ops-branch'],'manager_shift_id':manager})['shift']['id']
    assert next_driver!=driver


@pytest.mark.postgres
@pytest.mark.parametrize('counted',[900,1100])
def test_ledger_cash_difference_is_not_an_automatic_manager_adjustment(ops,counted,monkeypatch):
    from app import reporting
    monkeypatch.setattr(reporting,'generate_files',lambda *_:None)
    actor,post,order,shifts=ops
    manager,driver=shifts();delivered(post,order,driver)
    begin_driver_closure(post,driver)
    _,tid=submit_cash(post,driver,manager,counted)
    post('manager',f'/api/custody/{tid}/confirm')
    post('manager',f'/api/shifts/{driver}/approve',{})
    with db.tx() as c:
        sub=c.execute('SELECT submission FROM shifts WHERE id=%s',(driver,)).fetchone()['submission']
        assert sub['difference_halalas']==counted-1000 and sub['financial_review_required'] is True
        assert c.execute("SELECT 1 FROM custody_events WHERE shift_id=%s AND reference_id=%s",(driver,'closure-difference:'+driver)).fetchone() is None


@pytest.mark.postgres
def test_card_difference_does_not_require_manager_financial_approval(ops,monkeypatch):
    from app import reporting
    monkeypatch.setattr(reporting,'generate_files',lambda *_:None)
    actor,post,order,shifts=ops
    manager,driver=shifts();delivered(post,order,driver)
    begin_driver_closure(post,driver)
    post('driver',f'/api/shifts/{driver}/submit',{'terminal_total_halalas':100,'counted_halalas':1000,'handover_halalas':1000,'handover':{'recipient_user_id':'ops-manager','to_shift_id':manager,'reference':'Actual cash with unmatched terminal'}})
    with db.tx() as c:tid=c.execute('SELECT id FROM custody_transfers WHERE from_shift_id=%s AND closure',(driver,)).fetchone()['id']
    post('manager',f'/api/custody/{tid}/confirm')
    post('manager',f'/api/shifts/{driver}/approve',{})
    with db.tx() as c:
        sub=c.execute('SELECT submission FROM shifts WHERE id=%s',(driver,)).fetchone()['submission']
        assert sub['difference_halalas']==0 and sub['card_difference_halalas']==100
        assert sub['financial_review_required'] is True
        assert c.execute("SELECT 1 FROM custody_events WHERE shift_id=%s AND kind='adjustment'",(driver,)).fetchone() is None
