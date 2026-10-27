"""Actual single physical declarations must never manufacture brand actuals."""
from uuid import uuid4
import pytest
from test_enbishan_dual_brand import dual
from fastapi import HTTPException
from app import db
from app.aggregate_driver_closure import AllocationLeg, validate_allocation, guard_financial_allocation, receipt_difference
from test_central_workdays import central, open_days, inspection


def rows(shifts,cash=(600,100),card=(400,500)):
    return [{'shift_id':s['id'],'counted_halalas':cash[i],'received_halalas':cash[i],'retained_halalas':0,'terminal_total_halalas':card[i],'evidence':'Verified source receipt '+s['brand_id']} for i,s in enumerate(shifts)]


def submit_physical(central):
    _,actor,post,_,_=central
    manager,driver=open_days(central)
    path='/api/central-operations/workdays/'+driver['shift']['id']
    request=post('driver',path+'/request',{'reason':'Finished synthetic shift'})
    post('manager',path+'/decision',{'request_id':request['shift']['closure_authorization']['id'],'decision':'approved','reason':'Manager confirms synthetic shift end'})
    post('driver',path+'/closing',{})
    body={'cash_halalas':700,'card_halalas':900,'vehicle_inspection':inspection(1010)}
    key=uuid4().hex
    declared=post('driver',path+'/physical-submit',body,key=key)
    replay=post('driver',path+'/physical-submit',body,key=key)
    assert declared['declaration_id']==replay['declaration_id']
    return manager,driver,path,declared


@pytest.mark.postgres
def test_aggregate_original_receipt_allocation_preserved_and_accountant_only(central,monkeypatch):
    from app import reporting
    monkeypatch.setattr(reporting,'generate_files',lambda *_:None)
    _,actor,post,_,_=central
    manager,driver,path,declared=submit_physical(central)
    dec=declared['declaration']
    assert dec['cash_halalas']==700 and dec['card_halalas']==900
    assert 'expected_snapshot' not in dec
    assert 'expected_cash_halalas' not in declared
    with db.tx() as c:
        ss=c.execute('SELECT submission FROM shifts WHERE id=ANY(%s)',([s['id'] for s in driver['shifts']],)).fetchall()
        assert all('counted_halalas' not in s['submission'] for s in ss)
        assert not c.execute('SELECT 1 FROM custody_transfers WHERE from_shift_id=ANY(%s)',([s['id'] for s in driver['shifts']],)).fetchone()
    receipt_body={'declaration_id':dec['id'],'received_halalas':700,'confirmed':True,'reference':'Actual shared bag 001'}
    post('driver',path+'/physical-receipt',receipt_body,status=403)
    post('manager',path+'/physical-receipt',{**receipt_body,'confirmed':False},status=422)
    key=uuid4().hex
    receipt=post('manager',path+'/physical-receipt',receipt_body,key=key)
    assert post('manager',path+'/physical-receipt',receipt_body,key=key)['receipt_id']==receipt['receipt_id']
    assert receipt['state']=='awaiting_accounting'
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM shared_driver_closure_receipts').fetchone()['n']==1
        for leg in driver['shifts']:assert c.execute('SELECT staffing_ended_at FROM shifts WHERE id=%s',(leg['id'],)).fetchone()['staffing_ended_at'] is not None
    with pytest.raises(HTTPException) as error:
        with db.tx() as c:guard_financial_allocation(c,shift_id=manager['shift']['id'])
    assert error.value.detail['code']=='SHARED_DRIVER_ALLOCATION_PENDING'
    allocation={'declaration_id':dec['id'],'receipt_id':receipt['receipt_id'],'legs':rows(driver['shifts']),'reason':'Verified source records attribute both brand receipts','confirmed':True}
    post('manager',path+'/physical-allocation',allocation,status=403)
    post('driver',path+'/physical-allocation',allocation,status=403)
    wrong={**allocation,'legs':rows(driver['shifts'],cash=(601,100))}
    post('accountant',path+'/physical-allocation',wrong,status=422)
    done=post('accountant',path+'/physical-allocation',allocation)
    assert done['state']=='allocated' and len(done['report_ids'])==2
    with db.tx() as c:
        original=c.execute('SELECT * FROM shared_driver_closure_declarations WHERE id=%s',(dec['id'],)).fetchone()
        assert original['cash_halalas']==700 and original['card_halalas']==900
        assert c.execute('SELECT sum(amount_halalas) n FROM custody_transfers WHERE from_shift_id=ANY(%s) AND status=\'confirmed\'',([s['id'] for s in driver['shifts']],)).fetchone()['n']==700
        for record in c.execute('SELECT submission,status FROM shifts WHERE id=ANY(%s)',([s['id'] for s in driver['shifts']],)).fetchall():
            assert record['status']=='closed' and record['submission']['provenance']=='accounting_allocation'
        guard_financial_allocation(c,shift_id=manager['shift']['id'])
    with pytest.raises(Exception,match='Immutable financial record'):
        with db.tx() as c:c.execute('UPDATE shared_driver_closure_declarations SET cash_halalas=0 WHERE id=%s',(dec['id'],))


@pytest.mark.postgres
def test_receipted_workday_allows_next_physical_shift_without_losing_pending_accounting(central):
    _,actor,post,_,_=central
    manager,driver,path,declared=submit_physical(central)
    post('manager',path+'/physical-receipt',{'declaration_id':declared['declaration_id'],'received_halalas':700,'confirmed':True,'reference':'Actual bag'})
    next_shift=post('driver','/api/central-operations/workdays',{'kind':'driver','manager_shift_id':manager['shift']['id'],'vehicle_inspection':inspection(1010)})
    assert next_shift['shift']['id']!=driver['shift']['id']
    with db.tx() as c:
        old=c.execute('SELECT status FROM shifts WHERE id=%s',(driver['shift']['id'],)).fetchone()
        assert old['status']=='submitted'
        assert c.execute('SELECT 1 FROM shared_driver_closure_allocations').fetchone() is None
    inbox=actor('accountant').get('/api/shared-driver-closures')
    assert inbox.status_code==200 and len(inbox.json()['workdays'])==1


@pytest.mark.postgres
def test_return_preserves_first_declaration_and_requires_driver_resubmission(central):
    _,actor,post,_,_=central
    _,driver,path,declared=submit_physical(central)
    post('manager',path+'/physical-return',{'declaration_id':declared['declaration_id'],'reason':'Driver reported typing error'})
    replacement=post('driver',path+'/physical-submit',{'cash_halalas':650,'card_halalas':950,'vehicle_inspection':inspection(1010)})
    assert replacement['declaration']['revision']==2
    assert replacement['declaration_id']!=declared['declaration_id']
    with db.tx() as c:assert c.execute('SELECT count(*) n FROM shared_driver_closure_declarations').fetchone()['n']==2
    post('manager',path+'/physical-receipt',{'declaration_id':declared['declaration_id'],'received_halalas':700,'confirmed':True,'reference':'Stale receipt'},status=409)


def test_allocation_rejects_arbitrary_breakdown_totals_and_duplicate_legs():
    declaration={'cash_halalas':700,'card_halalas':900,'retained_halalas':0,'expected_snapshot':[{'shift_id':'k','permanent_float_halalas':0},{'shift_id':'e','permanent_float_halalas':0}]}
    receipt={'received_halalas':700}
    first=AllocationLeg(shift_id='k',counted_halalas=600,received_halalas=600,retained_halalas=0,terminal_total_halalas=400,evidence='Verified evidence')
    second=AllocationLeg(shift_id='e',counted_halalas=100,received_halalas=100,retained_halalas=0,terminal_total_halalas=500,evidence='Verified evidence')
    validate_allocation(declaration,receipt,[first,second])
    with pytest.raises(HTTPException):validate_allocation(declaration,receipt,[first,first])
    with pytest.raises(HTTPException):validate_allocation(declaration,receipt,[first,second.model_copy(update={'counted_halalas':101})])


def second_manager(central):
    _,_,post,_,_=central
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('dual-manager2','Second synthetic manager','delivery_manager','kps','[\"kps-branch-1\"]')")
        c.execute("INSERT INTO staff_brand_memberships(user_id,brand_id,branch_ids,granted_by) VALUES('dual-manager2','enbishan','[\"enbishan-test-1\",\"enbishan-test-2\"]','dual-owner')")
    return post('manager2','/api/central-operations/workdays',{'kind':'manager'})


@pytest.mark.postgres
def test_pending_shared_declaration_blocks_parent_handover_until_allocation(central,monkeypatch):
    from app import reporting
    monkeypatch.setattr(reporting,'generate_files',lambda *_:None)
    _,actor,post,_,_=central
    manager,driver,path,declared=submit_physical(central)
    target=second_manager(central)
    handover='/api/central-operations/workdays/'+manager['shift']['id']+'/handover-manager'
    payload={'target_manager_shift_id':target['shift']['id'],'reason':'Actual manager responsibility handover'}
    before=post('manager',handover,payload,status=409)
    assert before['detail']['code']=='SHARED_DRIVER_ALLOCATION_PENDING'
    receipt=post('manager',path+'/physical-receipt',{'declaration_id':declared['declaration_id'],'received_halalas':700,'confirmed':True,'reference':'Actual shared bag'})
    post('manager',handover,payload,status=409)
    post('accountant',path+'/physical-allocation',{'declaration_id':declared['declaration_id'],'receipt_id':receipt['receipt_id'],'confirmed':True,'reason':'Verified attribution of actual received amounts','legs':rows(driver['shifts'])})
    transferred=post('manager',handover,payload)
    assert transferred['handover_id']


@pytest.mark.postgres
@pytest.mark.parametrize('actual',[0,650,750])
def test_actual_receipt_difference_preserves_both_facts_and_operational_end(central,actual):
    _,actor,post,_,_=central
    manager,driver,path,declared=submit_physical(central)
    body={'declaration_id':declared['declaration_id'],'received_halalas':actual,'confirmed':True,'reference':'Witnessed independent actual count'}
    key=uuid4().hex
    received=post('manager',path+'/physical-receipt',body,key=key)
    assert received['state']=='receipt_difference_review'
    assert received['declaration']['cash_halalas']==700
    assert received['receipt']['received_halalas']==actual
    assert received['receipt_difference_halalas']==actual-700
    assert received['staffing_finished'] is True
    assert post('manager',path+'/physical-receipt',body,key=key)['receipt_id']==received['receipt_id']
    visible=actor('driver').get(path+'/physical-closure').json()
    assert 'receipt_difference_halalas' not in visible
    assert 'expected_cash_halalas' not in visible
    inbox=actor('accountant').get('/api/shared-driver-closures').json()['workdays']
    assert len(inbox)==1 and inbox[0]['receipt_review_required'] is True
    allocation={'declaration_id':declared['declaration_id'],'receipt_id':received['receipt_id'],'legs':rows(driver['shifts']),'reason':'Attempted attribution with unresolved receipt discrepancy','confirmed':True}
    error=post('accountant',path+'/physical-allocation',allocation,status=409)
    assert error['detail']['code']=='SHARED_RECEIPT_REVIEW_REQUIRED'
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM shared_driver_closure_receipts').fetchone()['n']==1
        assert c.execute('SELECT 1 FROM custody_transfers').fetchone() is None
        assert c.execute('SELECT 1 FROM shared_driver_closure_allocations').fetchone() is None
        assert c.execute('SELECT cash_halalas FROM shared_driver_closure_declarations WHERE id=%s',(declared['declaration_id'],)).fetchone()['cash_halalas']==700
        guard_financial_allocation_pending=False
        try:guard_financial_allocation(c,shift_id=manager['shift']['id'])
        except HTTPException:guard_financial_allocation_pending=True
        assert guard_financial_allocation_pending


def test_receipt_difference_uses_declared_handover_after_retained_float():
    declaration={'cash_halalas':20700,'retained_halalas':20000}
    assert receipt_difference(declaration,None) is None
    assert receipt_difference(declaration,{'received_halalas':700})==0
    assert receipt_difference(declaration,{'received_halalas':650})==-50
    assert receipt_difference(declaration,{'received_halalas':750})==50


def test_receipt_residual_allocation_requires_exact_independently_approved_source():
    declaration={'cash_halalas':700,'card_halalas':900,'retained_halalas':0,'expected_snapshot':[{'shift_id':'k','permanent_float_halalas':0},{'shift_id':'e','permanent_float_halalas':0}]}
    receipt={'received_halalas':650}
    first=AllocationLeg(shift_id='k',counted_halalas=600,received_halalas=550,retained_halalas=0,terminal_total_halalas=400,evidence='Original KPS bag evidence',receipt_difference_halalas=50)
    second=AllocationLeg(shift_id='e',counted_halalas=100,received_halalas=100,retained_halalas=0,terminal_total_halalas=500,evidence='Original Enbishan bag evidence')
    with pytest.raises(HTTPException):validate_allocation(declaration,receipt,[first,second])
    treatment={'status':'approved','payload':{'cash_variance_halalas':-50}}
    validate_allocation(declaration,receipt,[first,second],treatment)
    with pytest.raises(HTTPException):validate_allocation(declaration,receipt,[first,second],{'status':'approved','payload':{'cash_variance_halalas':-49}})
    with pytest.raises(HTTPException):validate_allocation(declaration,receipt,[first.model_copy(update={'receipt_difference_halalas':49}),second],treatment)


@pytest.mark.postgres
def test_short_declared_float_ends_operations_with_assigned_review_and_no_topup(central):
    _,actor,post,_,_=central
    request=post('manager','/api/driver-floats',{'driver_id':'dual-driver','amount_halalas':20000,'reference':'Actual approved original float','reason':'Standing float handed once and documented'})
    post('accountant','/api/driver-floats/'+request['id']+'/decision',{'approved':True,'confirmed':True,'reference':'Independent original float certification'})
    manager,driver,path,declared=submit_physical(central)
    assert declared['declaration']['retained_halalas']==700
    receipt=post('manager',path+'/physical-receipt',{'declaration_id':declared['declaration_id'],'received_halalas':0,'confirmed':True,'reference':'Zero bag, only actual 700 retained by driver'})
    assert receipt['staffing_finished'] is True
    with db.tx() as c:
        task=c.execute("SELECT details FROM audit_events WHERE action='shared_driver.float_followup' AND entity_id=%s",(receipt['workday_id'],)).fetchone()['details']
        assert task['shortfall_halalas']==19300 and task['responsible_user_id']=='dual-accountant'
        assert task['due_at'] and task['no_employee_liability_assumed'] is True
        assert c.execute('SELECT sum(amount_halalas) n FROM retained_carryovers WHERE from_shift_id=ANY(%s)',([s['id'] for s in driver['shifts']],)).fetchone()['n']==700
        assert c.execute("SELECT 1 FROM custody_events WHERE kind='adjustment'").fetchone() is None
    next_driver=post('driver','/api/central-operations/workdays',{'kind':'driver','manager_shift_id':manager['shift']['id'],'vehicle_inspection':inspection(1010)})
    assert all('opening_halalas' not in s for s in next_driver['shifts'])
    # Verify the actual carryover through the isolated canonical ledger. The
    # driver's blind response must not expose the expected opening balance.
    with db.tx() as c:
        source=c.execute('SELECT opening_halalas,permanent_float FROM shifts WHERE id=ANY(%s)',
            ([s['id'] for s in next_driver['shifts']],)).fetchall()
        assert sum(s['opening_halalas'] for s in source)==700
        assert sum((s['permanent_float'] or {}).get('amount',0) for s in source)==20000

def test_physical_receipt_preserves_pending_incentive_review_for_accounting(central, monkeypatch):
    """Physical receipt must not depend on resolving financial entitlement."""
    from app import driver_incentives
    _, actor, post, _, _ = central
    _, driver, path, declared = submit_physical(central)
    calls = []

    def pending_review(c, user, shift):
        calls.append(shift['id'])
        raise HTTPException(409, 'حدد سبب ومسؤولية الطلبات الملغية والمرتجعة')

    monkeypatch.setattr(driver_incentives, 'require_decisions', pending_review)
    body = {'declaration_id': declared['declaration_id'], 'received_halalas': 700,
            'confirmed': True, 'reference': 'Physical cash received; review pending'}
    receipt = post('manager', path + '/physical-receipt', body)
    assert receipt['state'] == 'awaiting_accounting'
    assert calls == []
    allocation = {'declaration_id': declared['declaration_id'],
                  'receipt_id': receipt['receipt_id'], 'legs': rows(driver['shifts']),
                  'reason': 'Verified brand source documents for allocation', 'confirmed': True}
    post('accountant', path + '/physical-allocation', allocation, status=409)
    assert calls
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM shared_driver_closure_receipts').fetchone()['n'] == 1
        assert not c.execute('SELECT 1 FROM shared_driver_closure_allocations').fetchone()
        original = c.execute('SELECT cash_halalas,card_halalas FROM shared_driver_closure_declarations WHERE id=%s',
                             (declared['declaration_id'],)).fetchone()
        assert original == {'cash_halalas': 700, 'card_halalas': 900}


def declare_separate_bag(central,city):
    _,actor,post,_,_=central
    with db.tx() as c:c.execute('UPDATE branches SET city=%s WHERE id=%s',(city,'kps-branch-1'))
    manager,driver=open_days(central)
    path='/api/central-operations/workdays/'+driver['shift']['id']
    request=post('driver',path+'/request',{'reason':'Finished synthetic shift'})
    post('manager',path+'/decision',{'request_id':request['shift']['closure_authorization']['id'],'decision':'approved','reason':'Manager confirms duty completion'})
    post('driver',path+'/closing',{})
    body={'cash_halalas':700,'card_halalas':900,'vehicle_inspection':inspection(1010),'handover_branch_id':'kps-branch-1','bag_reference':'SYNTHETIC separate driver bag','bag_delivered':True}
    key=uuid4().hex
    declared=post('driver',path+'/physical-submit',body,key=key)
    assert post('driver',path+'/physical-submit',body,key=key)['declaration_id']==declared['declaration_id']
    return manager,driver,path,declared


@pytest.mark.postgres
@pytest.mark.parametrize('city',['النعيرية','القيصومة','حفر الباطن'])
def test_separate_bag_physical_route_count_and_next_duty(central,city):
    _,actor,post,_,_=central
    manager,driver,path,declared=declare_separate_bag(central,city)
    bag=declared['cash_bag'];remote=city=='النعيرية'
    assert bag['destination']==('branch' if remote else 'manager_vault')
    count_path='/api/cash-bags/'+bag['id']+'/count'
    count={'received_cash_halalas':650,'card_halalas':900,'reference':'SYNTHETIC physical bag count','confirmed':True}
    post('driver',count_path,count,status=403)
    post('manager',count_path,count,status=403)
    if remote:
        assert declared['state']=='awaiting_accounting_receipt'
        post('manager',path+'/physical-receipt',{'declaration_id':declared['declaration_id'],'received_halalas':700,'confirmed':True,'reference':'Not actually received'},status=409)
        with db.tx() as c:assert not c.execute('SELECT 1 FROM shared_driver_closure_receipts').fetchone()
    else:
        post('accountant',count_path,count,status=409)
        post('manager','/api/cash-bags/workdays/'+manager['shift']['id']+'/vault-close',{'confirmed':True},status=409)
        post('manager',path+'/physical-receipt',{'declaration_id':declared['declaration_id'],'received_halalas':700,'confirmed':True,'reference':'SYNTHETIC physical manager receipt'})
    close='/api/cash-bags/workdays/'+manager['shift']['id']+'/vault-close'
    post('manager',close,{'confirmed':False},status=422)
    key=uuid4().hex
    ended=post('manager',close,{'confirmed':True},key=key)
    assert post('manager',close,{'confirmed':True},key=key)==ended
    next_manager=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    next_driver=post('driver','/api/central-operations/workdays',{'kind':'driver','manager_shift_id':next_manager['shift']['id'],'vehicle_inspection':inspection(1010)})
    assert next_driver['shift']['id']!=driver['shift']['id']
    post('accountant',count_path,{**count,'confirmed':False},status=422)
    key=uuid4().hex
    counted=post('accountant',count_path,count,key=key)
    assert post('accountant',count_path,count,key=key)==counted
    post('accountant',count_path,count,status=409)
    inbox=actor('accountant').get('/api/cash-bags')
    assert inbox.status_code==200,inbox.text
    actual=next(b for b in inbox.json()['bags'] if b['id']==bag['id'])
    assert actual['state']=='counted' and actual['cash_difference_halalas']==-50
    assert actual['counted_by_name']=='SYNTHETIC dual-accountant'
    with db.tx() as c:
        assert c.execute('SELECT cash_halalas FROM shared_driver_closure_declarations WHERE id=%s',(declared['declaration_id'],)).fetchone()['cash_halalas']==700
        assert c.execute('SELECT received_halalas FROM shared_driver_closure_receipts WHERE declaration_id=%s',(declared['declaration_id'],)).fetchone()['received_halalas']==(650 if remote else 700)
        assert not c.execute('SELECT 1 FROM custody_transfers').fetchone()
        assert not c.execute('SELECT 1 FROM shared_manager_closure_receipts').fetchone()
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(manager['shift']['id'],)).fetchone()['status']=='submitted'
    with pytest.raises(Exception,match='Immutable financial record'):
        with db.tx() as c:c.execute('UPDATE driver_cash_bag_counts SET received_cash_halalas=700 WHERE bag_id=%s',(bag['id'],))


@pytest.mark.postgres
def test_remote_bag_allocation_preserves_end_marker_and_never_credits_manager(central,monkeypatch):
    from app import reporting
    monkeypatch.setattr(reporting,'generate_files',lambda *_:None)
    _,actor,post,_,_=central
    manager,driver,path,declared=declare_separate_bag(central,'النعيرية')
    with db.tx() as c:original=c.execute('SELECT staffing_ended_at FROM shifts WHERE id=%s',(driver['shift']['id'],)).fetchone()['staffing_ended_at']
    post('accountant','/api/cash-bags/'+declared['cash_bag']['id']+'/count',{'received_cash_halalas':700,'card_halalas':900,'reference':'SYNTHETIC actual remote collection','confirmed':True})
    with db.tx() as c:receipt=c.execute('SELECT * FROM shared_driver_closure_receipts WHERE declaration_id=%s',(declared['declaration_id'],)).fetchone()
    payload={'declaration_id':declared['declaration_id'],'receipt_id':receipt['id'],'legs':rows(driver['shifts']),'reason':'Verified original per brand source records','confirmed':True}
    post('accountant',path+'/physical-allocation',payload,status=403)
    post('owner',path+'/physical-allocation',payload)
    with db.tx() as c:
        assert c.execute('SELECT staffing_ended_at FROM shifts WHERE id=%s',(driver['shift']['id'],)).fetchone()['staffing_ended_at']==original
        assert not c.execute('SELECT 1 FROM custody_events WHERE shift_id=ANY(%s) AND kind=%s',([s['id'] for s in manager['shifts']],'transfer_in')).fetchone()
        assert all(r['to_shift_id'] is None and r['recipient_user_id']=='dual-accountant' for r in c.execute('SELECT * FROM custody_transfers').fetchall())
