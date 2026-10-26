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
            assert row['status']=='submitted' and row['staffing_ended_at'] is None
            assert 'counted_halalas' not in row['submission']
    owner_receipt={'declaration_id':manager_decl['declaration_id'],'received_halalas':700,'confirmed':True}
    post('manager',manager_path+'/manager-physical-receipt',owner_receipt,status=403)
    post('owner',manager_path+'/manager-physical-receipt',{**owner_receipt,'received_halalas':699},status=422)
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
