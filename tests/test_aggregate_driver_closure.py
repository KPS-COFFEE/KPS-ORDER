"""Actual single physical declarations must never manufacture brand actuals."""
from uuid import uuid4
import pytest
from test_enbishan_dual_brand import dual
from fastapi import HTTPException
from app import db
from app.aggregate_driver_closure import AllocationLeg, validate_allocation, guard_financial_allocation
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
    post('manager',path+'/physical-receipt',{**receipt_body,'received_halalas':699},status=422)
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
