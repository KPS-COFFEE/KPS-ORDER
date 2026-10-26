"""Manager physical custody may end before accounting without brand invention."""
from uuid import uuid4
import pytest
from test_enbishan_dual_brand import dual
from fastapi import HTTPException
from app import db
from app.aggregate_driver_closure import guard_financial_allocation
from test_aggregate_driver_closure import central, submit_physical, rows


@pytest.mark.postgres
def test_manager_owner_physical_end_next_workday_and_later_accounting(central,monkeypatch):
    from app import reporting
    monkeypatch.setattr(reporting,'generate_files',lambda *_:None)
    _,actor,post,_,_=central
    manager,driver,driver_path,driver_decl=submit_physical(central)
    manager_path='/api/central-operations/workdays/'+manager['shift']['id']
    declaration={'cash_halalas':700,'recipient_user_id':'dual-owner','reference':'Complete actual manager bag','confirmed':True}
    post('manager',manager_path+'/manager-physical-submit',declaration,status=409)
    driver_receipt=post('manager',driver_path+'/physical-receipt',{'declaration_id':driver_decl['declaration_id'],'received_halalas':700,'confirmed':True,'reference':'Actual driver cash received'})
    key=uuid4().hex
    manager_decl=post('manager',manager_path+'/manager-physical-submit',declaration,key=key)
    assert post('manager',manager_path+'/manager-physical-submit',declaration,key=key)['declaration_id']==manager_decl['declaration_id']
    with db.tx() as c:
        for leg in manager['shifts']:
            row=c.execute('SELECT status,submission,staffing_ended_at FROM shifts WHERE id=%s',(leg['id'],)).fetchone()
            assert row['status']=='submitted' and row['staffing_ended_at'] is not None
            assert 'counted_halalas' not in row['submission']
    owner_receipt={'declaration_id':manager_decl['declaration_id'],'received_halalas':700,'confirmed':True}
    post('manager',manager_path+'/manager-physical-receipt',owner_receipt,status=403)
    post('owner',manager_path+'/manager-physical-receipt',{**owner_receipt,'confirmed':False},status=422)
    received=post('owner',manager_path+'/manager-physical-receipt',owner_receipt)
    assert received['state']=='awaiting_accounting'
    next_manager=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    assert next_manager['shift']['id']!=manager['shift']['id']
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM closure_reports').fetchone()['n']==0
        for leg in manager['shifts']:assert c.execute('SELECT staffing_ended_at FROM shifts WHERE id=%s',(leg['id'],)).fetchone()['staffing_ended_at'] is not None
    with pytest.raises(HTTPException):
        with db.tx() as c:guard_financial_allocation(c,shift_id=manager['shift']['id'])
    amounts={driver['shifts'][i]['brand_id']:amount for i,amount in enumerate((600,100))}
    allocation={'declaration_id':manager_decl['declaration_id'],'receipt_id':received['receipt_id'],'confirmed':True,'reason':'Verified owner bag attributed by original brand documents',
        'legs':[{'shift_id':s['id'],'cash_halalas':amounts[s['brand_id']],'evidence':'Verified owner bag brand evidence'} for s in manager['shifts']]}
    post('manager',manager_path+'/manager-physical-allocation',allocation,status=403)
    denied=post('owner',manager_path+'/manager-physical-allocation',allocation,status=403)
    assert denied['detail']['code']=='SHARED_CUSTODY_SELF_APPROVAL'
    post('accountant',manager_path+'/manager-physical-allocation',allocation,status=409)
    post('accountant',driver_path+'/physical-allocation',{'declaration_id':driver_decl['declaration_id'],'receipt_id':driver_receipt['receipt_id'],'confirmed':True,'reason':'Verified allocation of actual driver cash and card','legs':rows(driver['shifts'])})
    done=post('accountant',manager_path+'/manager-physical-allocation',allocation)
    assert done['state']=='allocated' and len(done['report_ids'])==2
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM closure_reports').fetchone()['n']==4
        assert c.execute('SELECT cash_halalas FROM shared_manager_closure_declarations WHERE id=%s',(manager_decl['declaration_id'],)).fetchone()['cash_halalas']==700
        old=[c.execute('SELECT status,submission FROM shifts WHERE id=%s',(s['id'],)).fetchone() for s in manager['shifts']]
        assert all(s['status']=='closed' and s['submission']['provenance']=='accounting_allocation' for s in old)
        for leg in next_manager['shifts']:
            assert c.execute('SELECT coalesce(sum(amount_halalas),0) n FROM custody_events WHERE shift_id=%s',(leg['id'],)).fetchone()['n']==0
        guard_financial_allocation(c,shift_id=manager['shift']['id'])


@pytest.mark.postgres
def test_manager_pre_receipt_return_appends_new_revision_and_preserves_original(central):
    _,actor,post,_,_=central
    manager=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    path='/api/central-operations/workdays/'+manager['shift']['id']
    first=post('manager',path+'/manager-physical-submit',{'cash_halalas':700,'recipient_user_id':'dual-owner','reference':'Initial bag declaration','confirmed':True})
    returned=post('owner',path+'/manager-physical-return',{'declaration_id':first['declaration_id'],'reason':'Manager typed a wrong actual cash amount'})
    assert returned['state']=='returned'
    second=post('manager',path+'/manager-physical-submit',{'cash_halalas':650,'recipient_user_id':'dual-owner','reference':'Corrected actual bag declaration','confirmed':True})
    assert second['declaration']['revision']==2 and second['declaration_id']!=first['declaration_id']
    post('owner',path+'/manager-physical-receipt',{'declaration_id':first['declaration_id'],'received_halalas':700,'confirmed':True},status=409)
    post('owner',path+'/manager-physical-receipt',{'declaration_id':second['declaration_id'],'received_halalas':650,'confirmed':True})
    post('owner',path+'/manager-physical-return',{'declaration_id':second['declaration_id'],'reason':'Cannot overwrite a received bag'},status=409)
    with db.tx() as c:
        originals=c.execute('SELECT cash_halalas FROM shared_manager_closure_declarations ORDER BY revision').fetchall()
        assert [r['cash_halalas'] for r in originals]==[700,650]


@pytest.mark.postgres
@pytest.mark.parametrize('actual',[0,650,750])
def test_manager_independent_actual_receipt_preserves_difference_and_allows_next_workday(central,actual):
    _,actor,post,_,_=central
    manager=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    path='/api/central-operations/workdays/'+manager['shift']['id']
    declared=post('manager',path+'/manager-physical-submit',{'cash_halalas':700,'recipient_user_id':'dual-owner','reference':'Independent bag declaration','confirmed':True})
    body={'declaration_id':declared['declaration_id'],'received_halalas':actual,'confirmed':True}
    key=uuid4().hex
    receipt=post('owner',path+'/manager-physical-receipt',body,key=key)
    assert receipt['state']=='receipt_difference_review'
    assert receipt['declaration']['cash_halalas']==700
    assert receipt['receipt']['received_halalas']==actual
    assert receipt['receipt_difference_halalas']==actual-700
    assert post('owner',path+'/manager-physical-receipt',body,key=key)['receipt_id']==receipt['receipt_id']
    next_manager=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    assert next_manager['shift']['id']!=manager['shift']['id']
    legs=[{'shift_id':s['id'],'cash_halalas':700 if i==0 else 0,'evidence':'Verified original bag evidence'} for i,s in enumerate(manager['shifts'])]
    blocked=post('accountant',path+'/manager-physical-allocation',{'declaration_id':declared['declaration_id'],'receipt_id':receipt['receipt_id'],'legs':legs,'reason':'Cannot conceal independent receipt difference','confirmed':True},status=409)
    assert blocked['detail']['code']=='SHARED_RECEIPT_REVIEW_REQUIRED'
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM shared_manager_closure_receipts').fetchone()['n']==1
        assert c.execute('SELECT 1 FROM custody_transfers').fetchone() is None
        assert c.execute('SELECT 1 FROM shared_manager_closure_allocations').fetchone() is None


@pytest.mark.postgres
def test_manager_opens_next_workday_before_cash_receipt_and_corrects_old_bag(central):
    _,actor,post,_,_=central
    manager=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    path='/api/central-operations/workdays/'+manager['shift']['id']
    declaration={'cash_halalas':700,'recipient_user_id':'dual-owner','reference':'Cash held pending actual treasury receipt','confirmed':True}
    first=post('manager',path+'/manager-physical-submit',declaration)
    assert first['operational_release'] and first['receipt'] is None
    assert first['recipient']['display_name']
    next_day=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    assert next_day['shift']['id']!=manager['shift']['id']
    post('manager','/api/central-operations/workdays',{'kind':'manager'},status=409)
    post('manager',path+'/manager-physical-return',{'declaration_id':first['declaration_id'],'reason':'Correct mistyped bag amount without resuming duty'})
    corrected=post('manager',path+'/manager-physical-submit',{**declaration,'cash_halalas':650})
    assert corrected['declaration']['revision']==2
    assert corrected['operational_release']==first['operational_release']
    assert corrected['receipt'] is None
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM shared_manager_closure_receipts').fetchone()['n']==0
        assert c.execute('SELECT count(*) n FROM manager_operational_releases').fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM custody_events').fetchone()['n']==0
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(next_day['shift']['id'],)).fetchone()['status']=='open'
    received=post('owner',path+'/manager-physical-receipt',{'declaration_id':corrected['declaration_id'],'received_halalas':650,'confirmed':True})
    assert received['state']=='awaiting_accounting'
    assert received['operational_release']==first['operational_release']


@pytest.mark.postgres
def test_only_explicitly_authorized_treasury_recipient_receives_without_owner(central):
    _,actor,post,_,_=central
    with db.tx() as c:
        c.execute("UPDATE users SET permissions=%s WHERE id='dual-accountant'",(db.json(['accounting_review','custody_view','custody_transfer']),))
    manager=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    path='/api/central-operations/workdays/'+manager['shift']['id']
    declared=post('manager',path+'/manager-physical-submit',{'cash_halalas':700,'recipient_user_id':'dual-accountant','reference':'Authorized treasury receives independently','confirmed':True})
    next_day=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    assert next_day['shift']['id']!=manager['shift']['id']
    body={'declaration_id':declared['declaration_id'],'received_halalas':700,'confirmed':True}
    post('owner',path+'/manager-physical-receipt',body,status=403)
    received=post('accountant',path+'/manager-physical-receipt',body)
    assert received['receipt']['received_by']=='dual-accountant'
    # Receipt does not approve accounting; the same custodian cannot allocate.
    assert not received['can_allocate']


@pytest.mark.postgres
def test_ungranted_accountant_cannot_be_named_cash_recipient(central):
    _,actor,post,_,_=central
    manager=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    path='/api/central-operations/workdays/'+manager['shift']['id']
    post('manager',path+'/manager-physical-submit',{'cash_halalas':700,'recipient_user_id':'dual-accountant','reference':'No treasury permission granted','confirmed':True},status=422)


@pytest.mark.postgres
def test_missing_treasury_recipient_does_not_block_operational_closure(central):
    _,actor,post,_,_=central
    manager=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    path='/api/central-operations/workdays/'+manager['shift']['id']
    declared=post('manager',path+'/manager-physical-submit',{'cash_halalas':700,'reference':'Cash remains in manager custody until treasury assignment','confirmed':True})
    assert declared['recipient'] is None and declared['receipt'] is None
    assert declared['operational_release']
    post('manager','/api/central-operations/workdays',{'kind':'manager'})
    post('owner',path+'/manager-physical-receipt',{'declaration_id':declared['declaration_id'],'received_halalas':700,'confirmed':True},status=403)


@pytest.mark.postgres
def test_open_next_shift_releases_legacy_submitted_bag_without_cash_receipt(central):
    _,actor,post,_,_=central
    manager=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    with db.tx() as c:
        day=c.execute('SELECT workday_id FROM central_workday_legs WHERE shift_id=%s',(manager['shift']['id'],)).fetchone()['workday_id']
        identifier=uuid4().hex
        c.execute("INSERT INTO shared_manager_closure_declarations(id,workday_id,revision,cash_halalas,expected_snapshot,recorded_by,recipient_user_id,reference) VALUES(%s,%s,1,1,'[]','dual-manager','dual-owner','Legacy pre-upgrade pending bag')",(identifier,day))
        for leg in manager['shifts']:
            c.execute("UPDATE shifts SET status='submitted',submission=%s WHERE id=%s",(db.json({'shared_manager_declaration_id':identifier,'physical_state':'awaiting_owner_receipt','financial_state':'awaiting_allocation'}),leg['id']))
    next_day=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    assert next_day['shift']['id']!=manager['shift']['id']
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM manager_operational_releases').fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM shared_manager_closure_receipts').fetchone()['n']==0
        assert c.execute('SELECT cash_halalas FROM shared_manager_closure_declarations WHERE id=%s',(identifier,)).fetchone()['cash_halalas']==1
