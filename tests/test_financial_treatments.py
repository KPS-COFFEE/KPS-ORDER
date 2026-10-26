"""Exact-channel independent treatments preserve debt and source records."""
from copy import deepcopy
from datetime import date,timedelta
from types import SimpleNamespace
from contextlib import nullcontext
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from app import financial_treatments as ft, db


def values(cash=-550,card=0):
    return dict(revision=2,reconciliation_complete=True,has_channel_variance=True,
        financial_approval_allowed=True,cash_variance_halalas=cash,bank_difference_halalas=card,
        system_cash_halalas=2500,system_bank_halalas=0,cash_received_halalas=1950,bank_halalas=0)


def proposal(kind='outstanding',cash=-550,card=0):
    body=dict(expected_revision=2,kind=kind,cash_variance_halalas=cash,card_variance_halalas=card,
        evidence_reference='TEST signed receipt REF-123',note='TEST independently documented actual facts')
    if kind=='outstanding':body.update(liable_party_type='customer',liable_party_name='TEST Customer',
        followup_user_id='accountant',due_on=date.today()+timedelta(days=7))
    return ft.Proposal(**body)


def treatment(kind='outstanding',cash=-550,card=0,allow=False):
    return dict(id='ft',status='approved',requested_by='accountant',
        payload=proposal(kind,cash,card).model_dump(mode='json'),
        approval=dict(approved_by='owner',allow_period_approval_with_outstanding=allow))


def test_receivable_approval_never_becomes_receipt_or_fully_paid():
    original=values();before=deepcopy(original)
    result=ft.apply(original,treatment(allow=True))
    assert result['financial_state']=='review_approved_outstanding'
    assert result['outstanding_receivable_halalas']==550
    assert result['outstanding_payable_halalas']==0
    assert result['period_approval_allowed'] and not result['financially_complete']
    assert result['followup_user_id']=='accountant' and result['due_on']
    assert original==before


def test_outstanding_without_explicit_owner_period_allowance_blocks_period():
    result=ft.apply(values(),treatment())
    assert not result['period_approval_allowed'] and not result['financially_complete']
    assert result['outstanding_receivable_halalas']==550


def test_offsetting_channels_remain_two_obligations_not_zero():
    result=ft.apply(values(-10000,10000),treatment(cash=-10000,card=10000,allow=True))
    assert result['outstanding_receivable_halalas']==10000
    assert result['outstanding_payable_halalas']==10000
    assert not result['financially_complete']


def test_documented_approved_reclassification_is_linked_and_preserves_raw():
    original=values(10000,-10000);before=deepcopy(original)
    result=ft.apply(original,treatment('documented_channel_correction',10000,-10000))
    assert result['financially_complete'] and result['period_approval_allowed']
    assert result['channel_reclassification']==dict(cash_halalas=10000,card_halalas=-10000,treatment_id='ft')
    assert result['cash_variance_halalas']==10000 and result['bank_difference_halalas']==-10000
    assert original==before


@pytest.mark.parametrize('cash,card',[(-1,0),(-100,50),(0,0)])
def test_method_correction_cannot_write_off_or_offset_different_amounts(cash,card):
    body=proposal('documented_channel_correction',cash,card)
    with pytest.raises(HTTPException):ft.validate_proposal(body,values(cash,card))


def test_changed_revision_or_channels_cannot_reuse_approved_treatment():
    result=ft.apply(values(-551),treatment())
    assert not result['period_approval_allowed'] and result['financial_state']=='pending_review'
    with pytest.raises(HTTPException):ft.validate_proposal(proposal(),{**values(),'revision':3})


def test_missing_source_evidence_cannot_be_approved_by_treatment():
    result=ft.apply({**values(),'financial_approval_allowed':False},treatment(allow=True))
    assert not result['period_approval_allowed'] and not result['financially_complete']


def test_outstanding_requires_party_followup_and_due():
    body=proposal().model_copy(update={'followup_user_id':None})
    with pytest.raises(HTTPException) as error:ft.validate_proposal(body,values())
    assert error.value.detail['code']=='FINANCIAL_RECEIVABLE_DETAILS_REQUIRED'


def mock_approval(monkeypatch,user_id='owner',role='owner',party='customer',allow=True):
    c=Mock();v=values();row=treatment();row['status']='pending';row['payload']['liable_party_type']=party
    report=dict(id='r',shift_id='s',user_id='driver',brand_id='kps',branch_ids=['b'],snapshot={})
    monkeypatch.setattr(db,'tx',lambda:nullcontext(c))
    monkeypatch.setattr(db,'idempotent',lambda conn,uid,key,scope,payload,fn:fn())
    monkeypatch.setattr(ft,'source',lambda *args:(report,{'id':'settlement'},v))
    monkeypatch.setattr(ft,'selected',lambda *args:row)
    monkeypatch.setattr(ft,'custody_actors',lambda *args:{'driver'})
    monkeypatch.setattr(ft,'eligible_followup',lambda *args:dict(id='accountant'))
    from app import financial_integrity
    monkeypatch.setattr(financial_integrity,'require_verified',lambda *args:True)
    audit=Mock();monkeypatch.setattr(db,'audit',audit)
    body=ft.Approval(expected_revision=2,evidence_reference='TEST bank document separately checked',
        note='TEST reviewed exact source and responsibility',confirmed=True,
        allow_period_approval_with_outstanding=allow)
    return c,audit,lambda:ft.approve('r','ft',body,SimpleNamespace(headers={}),user=dict(id=user_id,role=role))


def test_proposer_cannot_approve_own_treatment(monkeypatch):
    c,audit,call=mock_approval(monkeypatch,user_id='accountant',role='admin',allow=False)
    with pytest.raises(HTTPException) as error:call()
    assert error.value.detail['code']=='FINANCIAL_TREATMENT_SELF_APPROVAL'
    audit.assert_not_called()


@pytest.mark.parametrize('party,allow',[('employee',False),('customer',True)])
def test_reserved_employee_liability_and_period_exception_require_owner(monkeypatch,party,allow):
    c,audit,call=mock_approval(monkeypatch,user_id='other-accountant',role='admin',party=party,allow=allow)
    with pytest.raises(HTTPException) as error:call()
    assert error.value.detail['code']=='FINANCIAL_TREATMENT_OWNER_REQUIRED'
    audit.assert_not_called()


def test_owner_approval_records_exact_independent_evidence_without_money_write(monkeypatch):
    c,audit,call=mock_approval(monkeypatch)
    result=call()
    assert result['outstanding_receivable_halalas']==550
    assert result['financial_state']=='review_approved_outstanding'
    assert audit.call_count==1
    record=audit.call_args.args[-1]
    assert record['approved_by']=='owner' and record['requested_by']=='accountant'
    assert record['cash_variance_halalas']==-550 and record['card_variance_halalas']==0
    assert record['no_money_posted'] and record['no_payroll_deduction']
    c.execute.assert_not_called()


def test_actual_cash_custodian_cannot_approve_even_when_not_proposer(monkeypatch):
    c,audit,call=mock_approval(monkeypatch)
    monkeypatch.setattr(ft,'custody_actors',lambda *args:{'owner','driver'})
    with pytest.raises(HTTPException) as error:call()
    assert error.value.detail['code']=='FINANCIAL_TREATMENT_SELF_APPROVAL'
    audit.assert_not_called()


def test_partial_and_complete_actual_recovery_preserve_exact_remaining_channels():
    row=treatment(cash=-550,card=100,allow=False)
    row['recoveries']=[dict(status='approved',payload=dict(direction='receivable_recovery',cash_amount_halalas=200,card_amount_halalas=0))]
    result=ft.apply(values(-550,100),row)
    assert result['outstanding_receivable_halalas']==350 and result['outstanding_payable_halalas']==100
    assert not result['financially_complete']
    row['recoveries'] += [dict(status='approved',payload=dict(direction='receivable_recovery',cash_amount_halalas=350,card_amount_halalas=0)),
        dict(status='approved',payload=dict(direction='payable_settlement',cash_amount_halalas=0,card_amount_halalas=100))]
    result=ft.apply(values(-550,100),row)
    assert result['financial_state']=='settled_by_actual_recovery'
    assert result['financially_complete'] and result['period_approval_allowed']
    assert result['outstanding_receivable_halalas']==result['outstanding_payable_halalas']==0


def test_pending_recovery_does_not_reduce_receivable():
    row=treatment();row['recoveries']=[dict(status='pending',payload=dict(direction='receivable_recovery',cash_amount_halalas=550,card_amount_halalas=0))]
    assert ft.apply(values(),row)['outstanding_receivable_halalas']==550


def recovery(**changes):
    body=dict(direction='receivable_recovery',cash_amount_halalas=550,card_amount_halalas=0,
        cash_custody_event_id='actual-source',occurred_on=date.today(),
        evidence_reference='TEST actual signed receipt',note='TEST actual later cash receipt')
    body.update(changes);return ft.Recovery(**body)


def test_recovery_rejects_fake_cash_receipt_or_wrong_amount():
    from datetime import datetime,timezone
    report=dict(created_at=datetime.now(timezone.utc)-timedelta(days=1),brand_id='kps',branch_ids=['b'])
    c=Mock();c.execute.return_value.fetchone.return_value=None
    with pytest.raises(HTTPException) as error:ft.validate_recovery(c,{},report,treatment(),recovery())
    assert error.value.detail['code']=='RECOVERY_CASH_SOURCE_MISMATCH'
    with pytest.raises(HTTPException):ft.validate_recovery(c,{},report,treatment(),recovery(cash_amount_halalas=551))
    with pytest.raises(HTTPException):ft.validate_recovery(c,{},report,treatment(),recovery(cash_custody_event_id=None))


def test_recovery_uses_existing_actual_source_without_posting_money():
    from datetime import datetime,timezone
    report=dict(created_at=datetime.now(timezone.utc)-timedelta(days=1),brand_id='kps',branch_ids=['b'])
    c=Mock();c.execute.return_value.fetchone.return_value=dict(brand_id='kps',branch_ids=['b'],
        amount_halalas=550,created_at=datetime.now(timezone.utc),kind='direct_receipt',actor_id='cashier')
    assert ft.validate_recovery(c,{},report,treatment(),recovery())==['cashier']
    assert all(call.args[0].lstrip().upper().startswith('SELECT') for call in c.execute.call_args_list)

from test_operations import ops
from test_accounting import accountant
from test_driver_accounting_settlements import make_report


def prepared(ops,cash=9450,card=5000):
    actor,post,_,_=ops;accountant();snapshot,_=make_report()
    base='/api/accounting/driver-settlements/settlement-report'
    post('accountant',base+'/approve-balance',dict(expected_revision=0,bank_halalas=card,cash_due_halalas=10000,note='TEST actual terminal paper reviewed'))
    post('accountant',base+'/receive-cash',dict(expected_revision=1,cash_received_halalas=cash,
        received_on=date.today().isoformat(),receipt_reference='TEST actual physical receipt',note='TEST actual count recorded separately'))
    return actor,post,base,snapshot


def actual_proposal(base,post,cash=-550,card=0,kind='outstanding'):
    body=proposal(kind,cash,card).model_dump(mode='json');body['followup_user_id']='ops-accountant' if kind=='outstanding' else None
    return post('accountant',base+'/treatments',body,key='TEST-treatment-proposal'),body


@pytest.mark.postgres
def test_independent_outstanding_treatment_preserves_sources_and_is_not_paid(ops):
    actor,post,base,snapshot=prepared(ops)
    post('accountant',base+'/confirm-variance',dict(expected_revision=2,confirmed=True,note='TEST a note alone cannot settle',action_taken='TEST follow up note'),status=409)
    created,body=actual_proposal(base,post)
    approval=dict(expected_revision=2,confirmed=True,evidence_reference='TEST independent source document reviewed',note='TEST independently approved customer receivable',allow_period_approval_with_outstanding=True)
    path=base+'/treatments/'+created['id']+'/approve'
    post('accountant',path,approval,status=403)
    before={}
    with db.tx() as c:
        before['settlements']=c.execute('SELECT count(*) n FROM driver_accounting_settlements').fetchone()['n']
        before['cash']=c.execute('SELECT count(*) n FROM custody_events').fetchone()['n']
    approved=post('owner',path,approval,key='TEST-independent-approve')
    assert post('owner',path,approval,key='TEST-independent-approve')==approved
    assert approved['outstanding_receivable_halalas']==550
    row=actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]
    assert not row['financially_complete'] and row['period_approval_allowed']
    assert row['financial_state']=='review_approved_outstanding' and row['outstanding_receivable_halalas']==550
    assert row['followup_user_id']=='ops-accountant'
    with db.tx() as c:
        assert c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',('settlement-report',)).fetchone()['snapshot']==snapshot
        assert c.execute('SELECT count(*) n FROM driver_accounting_settlements').fetchone()['n']==before['settlements']
        assert c.execute('SELECT count(*) n FROM custody_events').fetchone()['n']==before['cash']
        assert c.execute('SELECT count(*) n FROM collections').fetchone()['n']==0
    post('accountant',base+'/approve-balance',dict(expected_revision=2,bank_halalas=5000,cash_due_halalas=9450,note='TEST cannot erase independently approved debt'),status=409)


@pytest.mark.postgres
def test_opposite_channels_need_documented_independent_correction(ops):
    actor,post,base,_=prepared(ops,cash=11000,card=4000)
    created,_=actual_proposal(base,post,cash=1000,card=-1000,kind='documented_channel_correction')
    path=base+'/treatments/'+created['id']+'/approve'
    post('owner',path,dict(expected_revision=2,confirmed=True,evidence_reference='TEST actual terminal receipt proves correction',note='TEST cash payment incorrectly classified card',allow_period_approval_with_outstanding=False))
    row=actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]
    assert row['cash_variance_halalas']==1000 and row['bank_difference_halalas']==-1000
    assert row['channel_reclassification']['cash_halalas']==1000
    assert row['financially_complete'] and row['financial_state']=='corrected_by_approved_document'
    assert row['net_difference_halalas']==0


@pytest.mark.postgres
def test_later_actual_cash_recovery_requires_independent_source_and_prevents_duplicate_effect(ops):
    actor,post,base,_=prepared(ops)
    created,_=actual_proposal(base,post)
    post('owner',base+'/treatments/'+created['id']+'/approve',dict(expected_revision=2,confirmed=True,
        evidence_reference='TEST independent receivable source',note='TEST receivable approved without claiming payment',allow_period_approval_with_outstanding=False))
    path=base+'/treatments/'+created['id']+'/recoveries'
    body=recovery(cash_custody_event_id='TEST-later-source').model_dump(mode='json')
    post('accountant',path,body,status=422)
    with db.tx() as c:
        c.execute("INSERT INTO custody_events(id,shift_id,amount_halalas,kind,reference_id,actor_id,reason) VALUES('TEST-later-source','settlement-manager',550,'direct_receipt','TEST-later-receipt','ops-manager','TEST actual later recovery')")
    recovery_row=post('accountant',path,body,key='TEST-later-recovery')
    assert actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]['outstanding_receivable_halalas']==550
    approval=dict(confirmed=True,evidence_reference='TEST independent actual receipt checked',note='TEST actual later money received in current custody')
    decision=path+'/'+recovery_row['id']+'/approve'
    post('accountant',decision,approval,status=403)
    result=post('owner',decision,approval,key='TEST-recovery-approved')
    assert post('owner',decision,approval,key='TEST-recovery-approved')==result
    post('owner',decision,approval,status=409)
    row=actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]
    assert row['outstanding_receivable_halalas']==0 and row['financially_complete']
    assert row['financial_state']=='settled_by_actual_recovery'
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM custody_events WHERE id='TEST-later-source'").fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action=%s",(ft.RECOVERY_APPROVED,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM collections').fetchone()['n']==0


@pytest.mark.parametrize('kind',['transfer_in','transfer_out'])
def test_internal_custody_transfer_cannot_settle_a_second_receivable(kind):
    from datetime import datetime,timezone
    report=dict(created_at=datetime.now(timezone.utc)-timedelta(days=1),brand_id='kps',branch_ids=['b'])
    c=Mock();c.execute.return_value.fetchone.return_value=dict(brand_id='kps',branch_ids=['b'],
        amount_halalas=550,created_at=datetime.now(timezone.utc),kind=kind,actor_id='cashier',custodian_id='receiver')
    with pytest.raises(HTTPException) as error:ft.validate_recovery(c,{},report,treatment(),recovery())
    assert error.value.detail['code']=='RECOVERY_CASH_SOURCE_MISMATCH'


def test_historical_receivable_cannot_be_recovered_after_source_revision_changes(monkeypatch):
    c=Mock();row=treatment();monkeypatch.setattr(ft,'history',lambda *args:[row])
    with pytest.raises(HTTPException) as error:ft.outstanding_source(c,'r','ft',{**values(),'revision':3})
    assert error.value.detail['code']=='FINANCIAL_TREATMENT_STALE'


def test_receiving_custodian_is_identified_separately_from_cash_recorder():
    from datetime import datetime,timezone
    report=dict(created_at=datetime.now(timezone.utc)-timedelta(days=1),brand_id='kps',branch_ids=['b'])
    c=Mock();c.execute.return_value.fetchone.return_value=dict(brand_id='kps',branch_ids=['b'],
        amount_halalas=550,created_at=datetime.now(timezone.utc),kind='direct_receipt',actor_id='recorder',custodian_id='receiver')
    assert set(ft.validate_recovery(c,{},report,treatment(),recovery()))=={'recorder','receiver'}


def test_shared_receipt_treatment_is_attributed_once_with_exact_leg_evidence(monkeypatch):
    import sys
    parent=treatment(cash=-1000)
    parent['payload'].update(workday_id='w',declaration_id='decl',receipt_id='receipt')
    monkeypatch.setitem(sys.modules,'app.shared_financial_treatments',SimpleNamespace(shared_approved=lambda *args:parent))
    monkeypatch.setattr(ft,'recovery_history',lambda *args:[])
    report=dict(id='r',shift_id='leg1',snapshot={'shift':{'submission':dict(shared_workday_id='w',shared_treatment_id='ft',
        shared_declaration_id='decl',shared_receipt_id='receipt',shared_allocation_id='allocation',receipt_difference_halalas=550)}})
    c=Mock();c.execute.return_value.fetchone.return_value=dict(legs=[dict(shift_id='leg1',receipt_difference_halalas=550),
        dict(shift_id='leg2',receipt_difference_halalas=450)])
    projected=ft.shared_for_report(c,report,values(-550,0))
    assert projected['payload']['cash_variance_halalas']==-550
    assert projected['payload']['source_shared_cash_variance_halalas']==-1000
    assert ft.apply(values(-550,0),projected)['outstanding_receivable_halalas']==550
    c.execute.return_value.fetchone.return_value['legs'][1]['receipt_difference_halalas']=449
    assert ft.shared_for_report(c,report,values(-550,0)) is None


def test_shared_receipt_approval_cannot_hide_an_additional_driver_or_card_difference():
    report=dict(snapshot={'shift':{'submission':dict(shared_workday_id='w',shared_treatment_id='ft',
        shared_declaration_id='decl',shared_receipt_id='receipt',shared_allocation_id='allocation',receipt_difference_halalas=550)}})
    c=Mock()
    assert ft.shared_for_report(c,report,values(-551,0)) is None
    assert ft.shared_for_report(c,report,values(-550,1)) is None
    c.execute.assert_not_called()


@pytest.mark.postgres
def test_financial_treatment_and_evidence_audit_records_are_database_immutable(ops):
    for action in (ft.PROPOSED,ft.APPROVED,ft.RECOVERY_APPROVED,'accounting.refund_item_allocation.approved'):
        with db.tx() as c:
            db.audit(c,'ops-owner',action,'report','TEST-report',{'treatment_id':'TEST-immutable'})
            identifier=c.execute('SELECT id FROM audit_events WHERE action=%s ORDER BY created_at DESC,id DESC LIMIT 1',(action,)).fetchone()['id']
        with pytest.raises(Exception):
            with db.tx() as c:c.execute('UPDATE audit_events SET details=%s WHERE id=%s',(db.json({'changed':True}),identifier))
        with pytest.raises(Exception):
            with db.tx() as c:c.execute('DELETE FROM audit_events WHERE id=%s',(identifier,))
        with db.tx() as c:assert c.execute('SELECT details FROM audit_events WHERE id=%s',(identifier,)).fetchone()['details']=={'treatment_id':'TEST-immutable'}
