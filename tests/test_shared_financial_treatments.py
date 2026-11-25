"""Shared custody differences are independently approved, remain outstanding,
and never manufacture receipts, payroll deductions or balancing adjustments."""
from datetime import date, timedelta
from uuid import uuid4
import pytest
from app import db
from test_enbishan_dual_brand import dual
from test_aggregate_driver_closure import central, submit_physical, rows

pytestmark=pytest.mark.postgres


def independent_reviewer():
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,permissions) VALUES('dual-reviewer','Independent synthetic accountant','admin','kps','[\"kps-branch-1\"]','[\"accounting_reports\",\"accounting_review\"]')")
        c.execute("INSERT INTO staff_brand_memberships(user_id,brand_id,branch_ids,granted_by) VALUES('dual-reviewer','enbishan','[\"enbishan-test-1\",\"enbishan-test-2\"]','dual-owner')")


def proposal_payload(declared,receipt):
    return {'declaration_id':declared['declaration_id'],'receipt_id':receipt['receipt_id'],'expected_revision':1,'kind':'outstanding',
        'cash_variance_halalas':-50,'card_variance_halalas':0,'evidence_reference':'Documented independent bag count witness',
        'note':'Original documents establish merchant receivable, no automatic driver liability',
        'liable_party_type':'merchant','liable_party_name':'Synthetic merchant pending source investigation','followup_user_id':'dual-accountant','due_on':(date.today()+timedelta(days=2)).isoformat()}


@pytest.mark.parametrize('family',['driver','manager'])
def test_exact_source_independent_approval_and_actual_allocation_keep_outstanding(central,monkeypatch,family):
    from app import reporting
    from app.aggregate_driver_closure import guard_financial_allocation
    from fastapi import HTTPException
    monkeypatch.setattr(reporting,'generate_files',lambda *_:None)
    _,actor,post,_,_=central
    independent_reviewer()
    if family=='driver':
        manager,day,path,declared=submit_physical(central)
        receipt=post('manager',path+'/physical-receipt',{'declaration_id':declared['declaration_id'],'received_halalas':650,'confirmed':True,'reference':'Independent actual bag count'})
    else:
        day=post('manager','/api/central-operations/workdays',{'kind':'manager'})
        path='/api/central-operations/workdays/'+day['shift']['id']
        declared=post('manager',path+'/manager-physical-submit',{'cash_halalas':700,'recipient_user_id':'dual-owner','reference':'Original manager declaration','confirmed':True})
        receipt=post('owner',path+'/manager-physical-receipt',{'declaration_id':declared['declaration_id'],'received_halalas':650,'confirmed':True})
    treatments=path+'/financial-treatments'
    payload=proposal_payload(declared,receipt)
    post('manager',treatments,payload,status=403)
    post('accountant',treatments,{**payload,'cash_variance_halalas':-49},status=409)
    post('accountant',treatments,{**payload,'receipt_id':'unrelated-receipt'},status=409)
    key=uuid4().hex
    proposed=post('accountant',treatments,payload,key=key)
    assert post('accountant',treatments,payload,key=key)==proposed
    approval={'declaration_id':declared['declaration_id'],'receipt_id':receipt['receipt_id'],'expected_revision':1,'confirmed':True,
        'evidence_reference':'Independent verified source review','note':'Approve documented amount as outstanding, no deduction or receipt'}
    post('accountant',treatments+'/'+proposed['id']+'/approve',approval,status=403)
    if family=='manager':post('owner',treatments+'/'+proposed['id']+'/approve',approval,status=403)
    key=uuid4().hex
    approved=post('reviewer',treatments+'/'+proposed['id']+'/approve',approval,key=key)
    assert post('reviewer',treatments+'/'+proposed['id']+'/approve',approval,key=key)==approved
    view=actor('accountant').get(treatments).json()
    assert view['financial_state']=='review_approved_outstanding'
    assert view['financially_complete'] is False and view['outstanding_receivable_halalas']==50
    if family=='driver':
        legs=rows(day['shifts']);legs[0].update(received_halalas=550,receipt_difference_halalas=50)
        done=post('accountant',path+'/physical-allocation',{'declaration_id':declared['declaration_id'],'receipt_id':receipt['receipt_id'],'confirmed':True,'reason':'Verified attribution of declaration, receipt and approved outstanding amount','legs':legs})
    else:
        legs=[{'shift_id':s['id'],'cash_halalas':650 if i==0 else 0,'receipt_difference_halalas':50 if i==0 else 0,'evidence':'Verified source brand bag documents'} for i,s in enumerate(day['shifts'])]
        done=post('accountant',path+'/manager-physical-allocation',{'declaration_id':declared['declaration_id'],'receipt_id':receipt['receipt_id'],'confirmed':True,'reason':'Verified attribution of actual bag and approved outstanding amount','legs':legs})
    assert done['state']=='allocated_with_outstanding'
    with db.tx() as c:
        shifts=[s['id'] for s in day['shifts']]
        assert c.execute('SELECT sum(amount_halalas) n FROM custody_transfers WHERE from_shift_id=ANY(%s) AND closure AND status=\'confirmed\'',(shifts,)).fetchone()['n']==650
        assert c.execute("SELECT 1 FROM custody_events WHERE shift_id=ANY(%s) AND kind='adjustment'",(shifts,)).fetchone() is None
        snapshots=c.execute('SELECT snapshot FROM closure_reports WHERE shift_id=ANY(%s)',(shifts,)).fetchall()
        assert sum(r['snapshot']['shift']['submission']['receipt_difference_halalas'] for r in snapshots)==50
        assert all(r['snapshot']['shift']['submission']['shared_treatment_id']==proposed['id'] for r in snapshots)
        with pytest.raises(HTTPException):guard_financial_allocation(c,shift_id=day['shift']['id'])
    inbox=actor('accountant').get('/api/shared-'+family+'-closures').json()['workdays']
    assert any(r['anchor_shift_id']==day['shift']['id'] for r in inbox)


@pytest.mark.parametrize('actual',[0,650,750])
def test_shared_treatment_amount_is_not_a_receipt_or_full_settlement(actual):
    from app.financial_treatments import apply
    variance=actual-700
    values={'revision':1,'cash_variance_halalas':variance,'bank_difference_halalas':0,'has_channel_variance':True,'reconciliation_complete':True,'financial_approval_allowed':True}
    treatment={'id':'approved-original-source','status':'approved','payload':{'kind':'outstanding','cash_variance_halalas':variance,'card_variance_halalas':0,'liable_party_type':'merchant','liable_party_name':'Documented merchant','followup_user_id':'accountant','due_on':'2026-12-01'},'approval':{'allow_period_approval_with_outstanding':True}}
    reviewed=apply(values,treatment)
    assert reviewed['financially_complete'] is False
    assert reviewed['financial_state']=='review_approved_outstanding'
    assert reviewed['outstanding_receivable_halalas']==max(0,-variance)
    assert reviewed['outstanding_payable_halalas']==max(0,variance)
