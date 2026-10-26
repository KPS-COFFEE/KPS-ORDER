"""An actual19.50 receipt against25 stays intact through documented source review."""
from copy import deepcopy
from datetime import datetime,timedelta
from uuid import uuid4
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app import db
from app.order_financial_treatments import Proposal,Approval,credited_total,validate
from test_operations import ops
from test_accounting import accountant
from test_delivery_workflow_controls import picked_up


def proposal(**kw):
    return Proposal(expected_version=2,case_id='case',kind='price_correction',expected_collected_halalas=1950,
        corrected_total_halalas=1950,evidence_reference='Approved menu price record',note='Original approved price was19.50',**kw)


def test_approved_source_credit_preserves_original_and_never_invents_receipt():
    order=dict(id='order',total_halalas=2500);before=deepcopy(order)
    assert credited_total(order,[dict(credit_halalas=550,original_total_halalas=2500)])==1950
    assert order==before
    with pytest.raises(HTTPException):credited_total(order,[dict(credit_halalas=2501,original_total_halalas=2500)])


@pytest.mark.parametrize('value',[None,True,'550',-550])
def test_missing_or_coerced_price_credit_is_not_financial_evidence(value):
    with pytest.raises(HTTPException):credited_total(dict(total_halalas=2500),[dict(credit_halalas=value,original_total_halalas=2500)])


def test_price_proposal_binds_actual_receipt_and_current_source():
    body=proposal();order=dict(version=2)
    validate(body,order,'case',1950,2500)
    for version,case,collected in [(3,'case',1950),(2,'other',1950),(2,'case',2000)]:
        with pytest.raises(HTTPException):validate(body,dict(version=version),case,collected,2500)
    with pytest.raises(HTTPException):validate(body.model_copy(update={'corrected_total_halalas':1900}),order,'case',1950,2500)
    with pytest.raises(ValidationError):Proposal(**{**body.model_dump(),'corrected_total_halalas':True})


def source(oid):
    with db.tx() as c:
        return dict(order=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone(),
            collections=c.execute('SELECT * FROM collections WHERE order_id=%s ORDER BY id',(oid,)).fetchall(),
            refunds=c.execute('SELECT * FROM refunds WHERE order_id=%s ORDER BY id',(oid,)).fetchall(),
            custody=c.execute('SELECT * FROM custody_events ORDER BY id').fetchall())


def shortfall(ops,*,fiscal=False):
    actor,post,_,_=ops;accountant()
    if fiscal:
        with db.tx() as c:c.execute("UPDATE users SET brand_id='kps',branch_ids='[\"kps-branch-1\"]' WHERE id='ops-accountant'")
        post('owner','/api/fiscal/policy',dict(enabled=True,phase='unconfirmed',issuance_mode='owner_requested',reason='Synthetic invoice at print'))
    oid,tid,manager,driver=picked_up(ops,total=2500)
    if fiscal:
        post('owner',f'/api/fiscal/orders/{oid}/print')
    post('driver',f'/api/orders/{oid}/collect',dict(method='cash',amount_halalas=1950,tendered_halalas=1950,shortfall_reason='Receipt19.50 against original25 requires source price evidence'))
    post('driver',f'/api/tasks/{tid}/transition',dict(status='delivered',proof='Actual delivery with partial recorded receipt'))
    return oid,manager,driver


@pytest.mark.postgres
@pytest.mark.parametrize('ops',[None,'fiscal-kps'],indirect=True)
def test_source_price_credit25_to1950_has_no_cash_refund_and_preserves_invoice(ops,monkeypatch):
    monkeypatch.setenv('KPS_ORDER_SOURCE_TREATMENTS_ENABLED','true')
    actor,post,_,_=ops
    with db.tx() as c:fiscal=c.execute("SELECT brand_id FROM users WHERE id='ops-owner'").fetchone()['brand_id']=='kps'
    oid,manager,driver=shortfall(ops,fiscal=fiscal);original=source(oid)
    with db.tx() as c:invoice=c.execute("SELECT document FROM fiscal_documents WHERE order_id=%s AND kind='invoice'",(oid,)).fetchone()
    url='/api/accounting/order-financial-treatments/orders/'+oid
    context=actor('accountant').get(url).json()
    payload=dict(expected_version=context['version'],case_id=context['case_id'],kind='price_correction',expected_collected_halalas=1950,
        corrected_total_halalas=1950,evidence_reference='Original menu effective at sale record001',note='Verified approved source price19.50, original entry25 erroneous')
    post('driver',url,payload,status=403);post('other',url,payload,status=404)
    key=uuid4().hex;pending=post('accountant',url,payload,key=key)
    assert post('accountant',url,payload,key=key)==pending
    approval=dict(confirmed=True,evidence_reference='Independent source approval001',note='Independent source price document verified')
    post('accountant',url+'/'+pending['id']+'/approve',approval,status=403)
    key=uuid4().hex;approved=post('owner',url+'/'+pending['id']+'/approve',approval,key=key)
    assert post('owner',url+'/'+pending['id']+'/approve',approval,key=key)==approved
    assert approved['effective_total_halalas']==1950 and approved['price_credit_halalas']==550
    assert approved['actual_collected_halalas']==1950 and approved['pending_amount_halalas']==0
    assert approved['financially_complete'] and approved['effective_payment_status']=='paid'
    assert source(oid)==original
    with db.tx() as c:
        assert c.execute("SELECT document FROM fiscal_documents WHERE order_id=%s AND kind='invoice'",(oid,)).fetchone()==invoice
        if fiscal:
            credit=c.execute("SELECT * FROM fiscal_documents WHERE order_id=%s AND kind='credit_note'",(oid,)).fetchone()
            assert credit['refund_id'] is None and credit['document']['total_halalas']==550
            assert credit['document']['treatment_id']==pending['id'] and credit['document']['no_money_posted']
        from app.order_financial_cases import guard_financial_closure
        guard_financial_closure(c,shift_id=driver)
    resolved=post('accountant',f'/api/order-financial-cases/orders/{oid}/resolve',dict(reason='Source credit matches actual19.50 without extra receipt'))
    assert resolved['financial_case']['status']=='resolved'
    # The corrected net value also limits an eventual item refund.
    from app.refund_line_values import ceiling
    from app.order_financial_treatments import valuation_order
    with db.tx() as c:assert ceiling(valuation_order(c,original['order']),{'coffee':1},{})==1950


@pytest.mark.postgres
def test_approved_order_receivable_stays_due_until_actual_later_collection(ops,monkeypatch):
    monkeypatch.setenv('KPS_ORDER_SOURCE_TREATMENTS_ENABLED','true')
    actor,post,_,_=ops;oid,manager,driver=shortfall(ops)
    from app.accounting import TZ
    url='/api/accounting/order-financial-treatments/orders/'+oid;context=actor('accountant').get(url).json();original=source(oid)
    payload=dict(expected_version=context['version'],case_id=context['case_id'],kind='outstanding',expected_collected_halalas=1950,
        evidence_reference='Customer acknowledged unpaid amount550',note='Confirmed customer debt, no employee deduction',
        liable_party_type='customer',liable_party_name='Documented customer account',followup_user_id='ops-accountant',due_on=(datetime.now(TZ).date()+timedelta(days=1)).isoformat())
    pending=post('accountant',url,payload)
    approved=post('owner',url+'/'+pending['id']+'/approve',dict(confirmed=True,evidence_reference='Customer receivable reviewed independently',
        note='Owner approves period review while customer debt remains',allow_period_approval_with_outstanding=True))
    assert approved['pending_amount_halalas']==550 and approved['outstanding_receivable_halalas']==550
    assert approved['financial_state']=='review_approved_outstanding' and not approved['financially_complete']
    assert source(oid)==original
    with db.tx() as c:
        from app.order_financial_cases import guard_financial_closure
        guard_financial_closure(c,shift_id=driver)
    post('accountant',f'/api/order-financial-cases/orders/{oid}/resolve',dict(reason='A review approval cannot fabricate collection'),status=409)
    context=actor('accountant').get(f'/api/order-financial-cases/orders/{oid}/recovery-context').json()
    post('accountant',f'/api/order-financial-cases/orders/{oid}/recover',dict(version=context['version'],method='cash',amount_halalas=550,
        shift_id=manager,reference='Later actual customer receipt001',reason='Customer paid remaining original debt'))
    current=actor('accountant').get(url).json()
    assert current['pending_amount_halalas']==0 and current['actual_collected_halalas']==2500 and current['financially_complete']


def test_item_returns_reduce_claim_and_net_tender_together_without_renewing_debt(monkeypatch):
    from unittest.mock import Mock
    from app import order_financial_treatments as source_treatments,order_amendments
    order=dict(id='order',total_halalas=2500)
    monkeypatch.setattr(source_treatments,'effective_total',lambda *args:1950)
    monkeypatch.setattr(source_treatments,'returned_credit',lambda *args:1000)
    monkeypatch.setattr(order_amendments,'balance',lambda *args:950)
    monkeypatch.setattr(source_treatments,'history',lambda *args:[])
    values=source_treatments.state(Mock(),order)
    assert values['effective_total_halalas']==1950 and values['effective_claim_halalas']==950
    assert values['refund_credit_halalas']==1000 and values['actual_collected_halalas']==950
    assert values['pending_amount_halalas']==0 and values['financially_complete']
    assert values['financial_state']=='settled_by_source_price_credit'


def test_old_case_receivable_cannot_authorize_a_new_case(monkeypatch):
    from unittest.mock import Mock
    from app import order_financial_treatments as source_treatments,order_amendments
    order=dict(id='order',total_halalas=2500)
    monkeypatch.setattr(source_treatments,'effective_total',lambda *args:2500)
    monkeypatch.setattr(source_treatments,'returned_credit',lambda *args:0)
    monkeypatch.setattr(order_amendments,'balance',lambda *args:1950)
    monkeypatch.setattr(source_treatments,'history',lambda *args:[dict(status='approved',payload=dict(kind='outstanding',case_id='old-case',source_effective_total_halalas=2500),approval=dict(details=dict(allow_period_approval_with_outstanding=True)))])
    assert not source_treatments.state(Mock(),order,'new-case')['period_approval_allowed']


def test_source_treatments_default_off_does_not_disable_historical_valuation(monkeypatch):
    from unittest.mock import Mock
    from app import order_financial_treatments as treatment
    monkeypatch.delenv('KPS_ORDER_SOURCE_TREATMENTS_ENABLED',raising=False)
    assert not treatment.enabled()
    with pytest.raises(HTTPException) as error:treatment.scoped(Mock(),dict(role='owner'),'order')
    assert error.value.status_code==404 and error.value.detail['code']=='ORDER_SOURCE_TREATMENTS_DISABLED'
    monkeypatch.setattr(treatment,'price_credit_rows',lambda *args:[dict(credit_halalas=550,original_total_halalas=2500)])
    assert treatment.effective_total(Mock(),dict(id='order',total_halalas=2500))==1950
    monkeypatch.setenv('KPS_ORDER_SOURCE_TREATMENTS_ENABLED','true')
    assert treatment.enabled()


def test_actual_receipt_match_alone_does_not_complete_pending_review(monkeypatch):
    from unittest.mock import Mock
    from app import order_financial_treatments as source_treatments,order_amendments
    order=dict(id='order',total_halalas=2500)
    monkeypatch.setattr(source_treatments,'effective_total',lambda *args:2500)
    monkeypatch.setattr(order_amendments,'balance',lambda *args:2500)
    monkeypatch.setattr(source_treatments,'history',lambda *args:[])
    pending=source_treatments.state(Mock(),order,'case')
    assert pending['amounts_matched'] and not pending['financially_complete'] and pending['financial_state']=='pending_review'
    reviewed=source_treatments.state(Mock(),order,'case',review_resolved=True)
    assert reviewed['financially_complete'] and reviewed['financial_state']=='matched'


@pytest.mark.postgres
def test_production_invoice_correction_after_closed_shift_preserves_originals_and_owner_decisions(ops,monkeypatch):
    from tests.test_operations import begin_driver_closure
    monkeypatch.setenv('KPS_INVOICE_PRICE_CORRECTIONS_ENABLED','true')
    monkeypatch.setenv('KPS_ORDER_SOURCE_TREATMENTS_ENABLED','false')
    actor,post,_,_=ops
    oid,manager,driver=shortfall(ops)
    begin_driver_closure(post,driver)
    post('manager',f'/api/shifts/{driver}/submit',dict(terminal_total_halalas=0,counted_halalas=1950,handover_halalas=1950,exception_reason='TEST actual partial collection'))
    post('manager',f'/api/shifts/{driver}/driver-close',dict(received_halalas=1950,confirmed=True))
    with db.tx() as c:
        closed=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
        reports=c.execute('SELECT * FROM closure_reports WHERE shift_id=%s ORDER BY id',(driver,)).fetchall()
        assert closed['status']=='closed'
    before=source(oid)
    monkeypatch.setenv('APP_ENV','production')
    url='/api/accounting/order-financial-treatments/orders/'+oid
    context=actor('accountant').get(url).json()
    assert context['allowed_kinds']==['price_correction'] and not context['can_approve']
    body=dict(expected_version=context['version'],case_id=context['case_id'],kind='price_correction',expected_collected_halalas=1950,
        corrected_total_halalas=1950,evidence_reference='TEST approved original price record',note='TEST original price confirmed19.50')
    approval=dict(confirmed=True,evidence_reference='TEST independent owner price evidence',note='TEST reviewed original price record')
    first=post('accountant',url,body)
    for decision in ('approve','return','reject'):
        post('accountant',url+'/'+first['id']+'/'+decision,approval if decision=='approve' else dict(note='TEST no authority'),status=403)
    returned=post('owner',url+'/'+first['id']+'/return',dict(note='TEST attach dated approved price list'))
    assert returned['responsible_user_id']=='ops-accountant'
    post('accountant',url,body,status=422)
    second=post('accountant',url,{**body,'resubmits_treatment_id':first['id']})
    rejected=post('owner',url+'/'+second['id']+'/reject',dict(note='TEST reject incomplete source document'))
    assert rejected['status']=='rejected'
    third=post('accountant',url,{**body,'resubmits_treatment_id':first['id']})
    key=uuid4().hex
    approved=post('owner',url+'/'+third['id']+'/approve',approval,key=key)
    assert post('owner',url+'/'+third['id']+'/approve',approval,key=key)==approved
    post('owner',url+'/'+third['id']+'/approve',approval,status=409)
    assert approved['price_credit_halalas']==550 and approved['actual_collected_halalas']==1950
    assert source(oid)==before
    with db.tx() as c:
        assert c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()==closed
        assert c.execute('SELECT * FROM closure_reports WHERE shift_id=%s ORDER BY id',(driver,)).fetchall()==reports
        assert c.execute("SELECT count(*) n FROM audit_events WHERE entity_id=%s AND action='accounting.order_financial_treatment.approved'",(oid,)).fetchone()['n']==1


@pytest.mark.parametrize('role,requester,actor,actors',[('admin','a','b',set()),('owner','a','a',set()),('owner','a','b',{'b'})])
def test_owner_decisions_require_independence_for_all_decision_routes(role,requester,actor,actors):
    from app.order_financial_treatments import independent_owner
    with pytest.raises(HTTPException) as exc:
        independent_owner(dict(id=actor,role=role),dict(requested_by=requester),actors)
    assert exc.value.status_code==403


@pytest.mark.postgres
def test_concurrent_owner_invoice_approvals_create_one_credit(ops,monkeypatch):
    from app import order_financial_treatments as treatment
    from tests.test_hardening_operations import principal,request,race
    monkeypatch.setenv('KPS_INVOICE_PRICE_CORRECTIONS_ENABLED','true')
    actor,post,_,_=ops;oid,_,_=shortfall(ops)
    url='/api/accounting/order-financial-treatments/orders/'+oid
    context=actor('accountant').get(url).json()
    pending=post('accountant',url,dict(expected_version=context['version'],case_id=context['case_id'],kind='price_correction',expected_collected_halalas=1950,
        corrected_total_halalas=1950,evidence_reference='TEST dated source price reference',note='TEST original price confirmed19.50'))
    before=source(oid);owner=principal('owner')
    approval=Approval(confirmed=True,evidence_reference='TEST owner reviewed price source',note='TEST independent owner approval')
    results=race([lambda:treatment.approve(oid,pending['id'],approval,request('TEST-price-race-a'),owner),
                  lambda:treatment.approve(oid,pending['id'],approval,request('TEST-price-race-b'),owner)])
    assert sorted(r[0] for r in results)==[200,409]
    assert source(oid)==before
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM audit_events WHERE entity_id=%s AND action=%s",(oid,treatment.APPROVED)).fetchone()['n']==1
        assert treatment.effective_total(c,before['order'])==1950


def test_invoice_approval_is_an_explicit_owner_grant_not_an_admin_default():
    from app import employee_permissions as permissions
    from app.order_financial_treatments import can_decide,independent_owner
    code='invoice_price_corrections_approve'
    admin=dict(id='delegate',role='admin',active=True,permissions=['accounting_review'])
    assert not can_decide(admin)
    # Putting a named extra capability in the legacy admin list is not a grant.
    assert not can_decide({**admin,'permissions':['accounting_review',code]})
    delegated={**admin,'extra_permissions':[code]}
    assert can_decide(delegated)
    independent_owner(delegated,dict(requested_by='accountant',payload=dict(kind='price_correction')),set())
    assert not can_decide({**delegated,'extra_permissions':[]})
    assert not can_decide({**delegated,'permission_denials':['accounting_review']})
    assert not can_decide({**delegated,'active':False})
    assert code not in permissions.eligible('admin')
    assert code in permissions.grantable('admin')
    assert all(code not in permissions.grantable(role) for role in ('driver','delivery_manager','branch_manager'))
    assert next(row for row in permissions.catalog() if row['code']==code)['owner_approval_required']
    for row,actors in [(dict(requested_by='delegate',payload=dict(kind='price_correction')),set()),
                       (dict(requested_by='accountant',payload=dict(kind='price_correction')),{'delegate'}),
                       (dict(requested_by='accountant',payload=dict(kind='outstanding')),set())]:
        with pytest.raises(HTTPException) as denied:independent_owner(delegated,row,actors)
        assert denied.value.status_code==403


@pytest.mark.postgres
def test_invoice_delegate_is_revocable_and_order_scoped_without_self_approval(ops,monkeypatch):
    monkeypatch.setenv('KPS_INVOICE_PRICE_CORRECTIONS_ENABLED','true')
    actor,post,_,_=ops;oid,_,_=shortfall(ops)
    url='/api/accounting/order-financial-treatments/orders/'+oid
    context=actor('owner').get(url).json()
    body=dict(expected_version=context['version'],case_id=context['case_id'],kind='price_correction',expected_collected_halalas=1950,
        corrected_total_halalas=1950,evidence_reference='TEST approved original price record',note='TEST original price confirmed19.50')
    pending=post('owner',url,body)
    decision=url+'/'+pending['id']+'/approve'
    approval=dict(confirmed=True,evidence_reference='TEST delegated reviewer evidence',note='TEST independent delegated approval')
    post('accountant',decision,approval,status=403)
    with db.tx() as c:c.execute("UPDATE users SET extra_permissions=%s WHERE id='ops-accountant'",(db.json(['invoice_price_corrections_approve']),))
    assert actor('accountant').get(url).json()['can_approve']
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-accountant'")
    post('accountant',decision,approval,status=404)
    with db.tx() as c:c.execute("UPDATE users SET branch_ids=%s,brand_id='ops-other' WHERE id='ops-accountant'",(db.json(['ops-branch']),))
    post('accountant',decision,approval,status=404)
    with db.tx() as c:c.execute("UPDATE users SET brand_id='ops-brand',extra_permissions='[]' WHERE id='ops-accountant'")
    post('accountant',decision,approval,status=403)
    with db.tx() as c:c.execute("UPDATE users SET extra_permissions=%s WHERE id='ops-accountant'",(db.json(['invoice_price_corrections_approve']),))
    before=source(oid)
    approved=post('accountant',decision,approval)
    assert approved['price_credit_halalas']==550 and source(oid)==before
