"""Real bag custody review is independent of the original driver receipt."""
from datetime import date,timedelta
from uuid import uuid4
import pytest
from app import db
from app import cash_bag_custody as custody
from test_enbishan_dual_brand import dual
from test_central_workdays import central
from test_aggregate_driver_closure import declare_separate_bag,rows
from test_shared_financial_treatments import independent_reviewer

pytestmark=pytest.mark.postgres


def prepared(central,monkeypatch,actual=650):
    from app import reporting
    monkeypatch.setattr(reporting,'generate_files',lambda *_:None)
    _,actor,post,_,_=central
    independent_reviewer()
    manager,driver,path,declared=declare_separate_bag(central,'حفر الباطن')
    received=post('manager',path+'/physical-receipt',{'declaration_id':declared['declaration_id'],'received_halalas':700,'confirmed':True,'reference':'Actual sealed bag received by manager'})
    bid=declared['cash_bag']['id'];review='/api/cash-bags/'+bid+'/custody-review'
    allocation={'declaration_id':declared['declaration_id'],'receipt_id':received['receipt_id'],'legs':rows(driver['shifts']),'confirmed':True,'reason':'Original receipts attributed by brand evidence'}
    return manager,driver,path,declared,bid,review,allocation


def count_bag(central,bid,actual=650):
    _,_,post,_,_=central
    return post('accountant','/api/cash-bags/'+bid+'/count',{'received_cash_halalas':actual,'card_halalas':900,'reference':'Actual bag collected and counted by accountant','confirmed':True})


def payload(count,receipt,amount=-50):
    return {'expected_count_id':count['count_id'],'expected_receipt_id':receipt,'expected_revision':1,'kind':'outstanding',
        'cash_variance_halalas':amount,'card_variance_halalas':0,'evidence_reference':'Independent sealed bag custody documents',
        'note':'Custody difference after manager receipt, no driver liability assumed','liable_party_type':'merchant',
        'liable_party_name':'Merchant pending custody investigation','followup_user_id':'dual-accountant','due_on':(date.today()+timedelta(days=2)).isoformat()}


def approval(data,allow=False):
    return {k:data[k] for k in ('expected_count_id','expected_receipt_id','expected_revision')}|{'confirmed':True,
        'evidence_reference':'Independent review of both immutable custody witnesses','note':'Retain separately documented custody outstanding without money posting',
        'allow_period_approval_with_outstanding':allow}


def test_actual_count_required_even_when_manager_received_then_matching_count_allows_attribution(central,monkeypatch):
    _,actor,post,_,_=central
    manager,driver,path,declared,bid,review,allocation=prepared(central,monkeypatch)
    blocked=post('accountant',path+'/physical-allocation',allocation,status=409)
    assert blocked['detail']['code']=='CASH_BAG_ACCOUNTING_RECEIPT_REQUIRED'
    view=actor('accountant').get(path+'/physical-closure').json()
    assert view['state']=='awaiting_accounting_receipt' and not view['can_allocate']
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM shared_driver_closure_allocations').fetchone()
        assert not c.execute('SELECT 1 FROM custody_transfers').fetchone()
    counted=count_bag(central,bid,700)
    done=post('accountant',path+'/physical-allocation',allocation)
    assert done['allocation_id']
    assert actor('accountant').get(review).json()['financial_state']=='matched'


def test_distinct_custody_variance_review_and_owner_period_authority_preserve_originals(central,monkeypatch):
    _,actor,post,_,_=central
    manager,driver,path,declared,bid,review,allocation=prepared(central,monkeypatch)
    counted=count_bag(central,bid)
    before=actor('accountant').get(review).json()
    assert before['original_received_halalas']==700 and before['count_received_halalas']==650
    assert before['source']=={'cash_variance_halalas':-50,'card_variance_halalas':0}
    assert before['can_propose'] and not before['financially_complete']
    blocked=post('accountant',path+'/physical-allocation',allocation,status=409)
    assert blocked['detail']['code']=='CASH_BAG_CUSTODY_REVIEW_REQUIRED'
    data=payload(counted,allocation['receipt_id']);key=uuid4().hex
    proposed=post('accountant',review,data,key=key)
    assert post('accountant',review,data,key=key)==proposed
    post('accountant',review,data,status=409)
    post('driver',review,data,status=403)
    post('accountant',review+'/'+proposed['id']+'/approve',approval(data),status=403)
    post('reviewer',review+'/'+proposed['id']+'/approve',approval(data,True),status=403)
    key=uuid4().hex
    approved=post('owner',review+'/'+proposed['id']+'/approve',approval(data,True),key=key)
    assert post('owner',review+'/'+proposed['id']+'/approve',approval(data,True),key=key)==approved
    post('owner',review+'/'+proposed['id']+'/approve',approval(data,True),status=409)
    done=post('accountant',path+'/physical-allocation',allocation)
    assert done['allocation_id']
    after=actor('accountant').get(review).json()
    assert after['financial_state']=='review_approved_outstanding' and after['period_approval_allowed']
    assert not after['financially_complete'] and after['outstanding_receivable_halalas']==50
    with db.tx() as c:
        assert c.execute('SELECT received_halalas FROM shared_driver_closure_receipts WHERE id=%s',(allocation['receipt_id'],)).fetchone()['received_halalas']==700
        assert c.execute('SELECT received_cash_halalas FROM driver_cash_bag_counts WHERE id=%s',(counted['count_id'],)).fetchone()['received_cash_halalas']==650
        assert c.execute("SELECT sum(amount_halalas) n FROM custody_transfers WHERE closure AND status='confirmed'").fetchone()['n']==700
        assert not c.execute("SELECT 1 FROM custody_events WHERE kind='adjustment'").fetchone()
        rows_=c.execute("SELECT * FROM audit_events WHERE entity_type='cash_bag' AND entity_id=%s AND action=%s",(bid,custody.ft.APPROVED)).fetchall()
        assert len(rows_)==1 and rows_[0]['details']['no_money_posted'] and rows_[0]['details']['no_payroll_deduction']
        result=custody.apply_report(c,{'shift_id':driver['shift']['id'],'snapshot':{'shift':{'submission':{'shared_declaration_id':declared['declaration_id']}}}},
            {'period_approval_allowed':True,'financially_complete':True,'overall_financially_complete':True})
        assert result['period_approval_allowed'] and not result['financially_complete']
        assert result['financial_state']=='review_approved_outstanding'


@pytest.mark.parametrize('changed',['count','receipt','amount','card'])
def test_custody_proposal_rejects_stale_or_different_financial_source(central,monkeypatch,changed):
    _,actor,post,_,_=central
    _,_,_,_,bid,review,allocation=prepared(central,monkeypatch)
    counted=count_bag(central,bid);data=payload(counted,allocation['receipt_id'])
    data[{'count':'expected_count_id','receipt':'expected_receipt_id','amount':'cash_variance_halalas','card':'card_variance_halalas'}[changed]]='stale' if changed in {'count','receipt'} else 1
    post('accountant',review,data,status=409)
    assert actor('accountant').get(review).json()['treatments']==[]


def test_independent_review_without_owner_period_permission_keeps_final_period_blocked(central,monkeypatch):
    _,actor,post,_,_=central
    _,driver,path,declared,bid,review,allocation=prepared(central,monkeypatch)
    counted=count_bag(central,bid);data=payload(counted,allocation['receipt_id'])
    proposed=post('accountant',review,data)
    post('reviewer',review+'/'+proposed['id']+'/approve',approval(data))
    post('accountant',path+'/physical-allocation',allocation)
    state=actor('accountant').get(review).json()
    assert state['financial_state']=='review_approved_outstanding' and not state['period_approval_allowed']
    assert state['outstanding_receivable_halalas']==50 and not state['financially_complete']
    with db.tx() as c:
        result=custody.apply_report(c,{'shift_id':driver['shift']['id'],'snapshot':{'shift':{'submission':{'shared_declaration_id':declared['declaration_id']}}}}, {'period_approval_allowed':True})
        assert not result['period_approval_allowed'] and result['financial_state']=='cash_bag_custody_review_pending'


def test_custody_rejected_proposal_can_be_replaced_but_stale_decision_cannot_execute(central,monkeypatch):
    _,actor,post,_,_=central
    _,_,_,_,bid,review,allocation=prepared(central,monkeypatch)
    counted=count_bag(central,bid);data=payload(counted,allocation['receipt_id']);proposed=post('accountant',review,data)
    bad={**approval(data),'expected_count_id':'stale'}
    post('owner',review+'/'+proposed['id']+'/approve',bad,status=409)
    rejected={k:data[k] for k in ('expected_count_id','expected_receipt_id','expected_revision')}|{'note':'Original custody evidence requires clarification'}
    post('owner',review+'/'+proposed['id']+'/reject',rejected)
    post('owner',review+'/'+proposed['id']+'/approve',approval(data),status=409)
    replacement=post('accountant',review,data)
    assert replacement['id']!=proposed['id']
    with db.tx() as c:c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-owner' AND brand_id='enbishan'")
    post('owner',review+'/'+replacement['id']+'/approve',approval(data,True),status=404)


@pytest.mark.parametrize('actual,direction,kind,sign',[(650,'receivable_recovery','direct_receipt',1),(750,'payable_settlement','expense',-1)])
def test_actual_partial_then_full_recovery_uses_existing_evidence_and_never_rewrites_bag(central,monkeypatch,actual,direction,kind,sign):
    _,actor,post,_,_=central
    manager,driver,path,declared,bid,review,allocation=prepared(central,monkeypatch)
    counted=count_bag(central,bid,actual);data=payload(counted,allocation['receipt_id'],actual-700)
    proposed=post('accountant',review,data)
    post('reviewer',review+'/'+proposed['id']+'/approve',approval(data))
    post('accountant',path+'/physical-allocation',allocation)
    for index in range(2):
        event_id=uuid4().hex
        with db.tx() as c:
            kps=next(s for s in manager['shifts'] if s['brand_id']=='kps')
            c.execute('INSERT INTO custody_events(id,shift_id,amount_halalas,kind,reference_id,actor_id,reason) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                (event_id,kps['id'],25*sign,kind,'Synthetic actual later custody movement '+str(index),'dual-manager','Actual recovery witness; review posts no money'))
            before=c.execute('SELECT count(*) n FROM custody_events').fetchone()['n']
        available=actor('accountant').get(review+'/'+proposed['id']+'/recovery-sources',params={'direction':direction})
        assert available.status_code==200,available.text
        assert event_id in {r['id'] for r in available.json()['sources']}
        recovery={k:data[k] for k in ('expected_count_id','expected_receipt_id')}|{'direction':direction,'cash_amount_halalas':25,'card_amount_halalas':0,
            'cash_custody_event_id':event_id,'evidence_reference':'Actual canonical custody event reference','note':'Apply only actual custody recovery, no new receipt created','occurred_on':date.today().isoformat()}
        route=review+'/'+proposed['id']+'/recoveries';key=uuid4().hex
        requested=post('accountant',route,recovery,key=key)
        assert post('accountant',route,recovery,key=key)==requested
        decision={k:data[k] for k in ('expected_count_id','expected_receipt_id')}|{'confirmed':True,'evidence_reference':'Independent canonical movement review','note':'Approve actual movement evidence only'}
        post('accountant',route+'/'+requested['id']+'/approve',decision,status=403)
        key=uuid4().hex
        saved=post('owner',route+'/'+requested['id']+'/approve',decision,key=key)
        assert post('owner',route+'/'+requested['id']+'/approve',decision,key=key)==saved
        state=actor('accountant').get(review).json()
        amount=state['outstanding_receivable_halalas']+state['outstanding_payable_halalas']
        assert amount==(25 if index==0 else 0)
        assert state['financially_complete']==(index==1)
        assert state['period_approval_allowed']==(index==1)
        with db.tx() as c:
            assert c.execute('SELECT count(*) n FROM custody_events').fetchone()['n']==before
            assert c.execute('SELECT received_cash_halalas FROM driver_cash_bag_counts WHERE id=%s',(counted['count_id'],)).fetchone()['received_cash_halalas']==actual
            assert c.execute('SELECT received_halalas FROM shared_driver_closure_receipts WHERE id=%s',(allocation['receipt_id'],)).fetchone()['received_halalas']==700
    assert state['financial_state']=='settled_by_actual_recovery'


def test_recovery_rejects_source_used_by_report_and_stale_count_without_money_changes(central,monkeypatch):
    _,actor,post,_,_=central
    manager,_,_,_,bid,review,allocation=prepared(central,monkeypatch)
    counted=count_bag(central,bid);data=payload(counted,allocation['receipt_id']);proposed=post('accountant',review,data)
    post('reviewer',review+'/'+proposed['id']+'/approve',approval(data))
    event_id=uuid4().hex
    with db.tx() as c:
        kps=next(s for s in manager['shifts'] if s['brand_id']=='kps')
        c.execute("INSERT INTO custody_events(id,shift_id,amount_halalas,kind,reference_id,actor_id,reason) VALUES(%s,%s,50,'direct_receipt','Actual source','dual-manager','Actual later movement')",(event_id,kps['id']))
    recovery={k:data[k] for k in ('expected_count_id','expected_receipt_id')}|{'direction':'receivable_recovery','cash_amount_halalas':50,'card_amount_halalas':0,'cash_custody_event_id':event_id,
        'evidence_reference':'Actual canonical cash source witness','note':'Recovery must not reuse consumed source','occurred_on':date.today().isoformat()}
    route=review+'/'+proposed['id']+'/recoveries'
    post('accountant',route,{**recovery,'expected_count_id':'old-count'},status=409)
    requested=post('accountant',route,recovery)
    with db.tx() as c:db.audit(c,'dual-owner',custody.ft.RECOVERY_APPROVED,'report','independent-report',{'cash_custody_event_id':event_id,'recovery_id':'prior','treatment_id':'independent-treatment'})
    decision={k:data[k] for k in ('expected_count_id','expected_receipt_id')}|{'confirmed':True,'evidence_reference':'Independent source verification','note':'Cross entity duplicate must remain blocked'}
    blocked=post('owner',route+'/'+requested['id']+'/approve',decision,status=409)
    assert blocked['detail']['code']=='RECOVERY_SOURCE_ALREADY_USED'
    assert actor('accountant').get(review).json()['outstanding_receivable_halalas']==50


def test_returned_old_bag_does_not_block_replacement_counted_declaration(central,monkeypatch):
    from app import reporting
    from test_central_workdays import inspection
    monkeypatch.setattr(reporting,'generate_files',lambda *_:None)
    _,actor,post,_,_=central
    manager,driver,path,old=declare_separate_bag(central,'حفر الباطن')
    post('manager',path+'/physical-return',{'declaration_id':old['declaration_id'],'reason':'Original reference requires correction before any handover'})
    current=post('driver',path+'/physical-submit',{'cash_halalas':700,'card_halalas':900,'vehicle_inspection':inspection(1010),
        'handover_branch_id':'kps-branch-1','bag_reference':'Replacement actual sealed bag reference'})
    received=post('manager',path+'/physical-receipt',{'declaration_id':current['declaration_id'],'received_halalas':700,'confirmed':True,'reference':'Actual replacement bag handover'})
    count_bag(central,current['cash_bag']['id'],700)
    done=post('accountant',path+'/physical-allocation',{'declaration_id':current['declaration_id'],'receipt_id':received['receipt_id'],'legs':rows(driver['shifts']),'confirmed':True,'reason':'Verified original per-brand receipts for replacement bag'})
    with db.tx() as c:
        report=c.execute('SELECT * FROM closure_reports WHERE id=%s',(done['report_ids'][0],)).fetchone()
        reviews=custody.report_reviews(c,report)
        assert [r['bag_id'] for r in reviews]==[current['cash_bag']['id']]
        assert reviews[0]['financially_complete']
        custody.require_report_receipt(c,report)
        assert c.execute('SELECT 1 FROM driver_cash_bag_counts WHERE bag_id=%s',(old['cash_bag']['id'],)).fetchone() is None


def test_observed_card_slip_difference_requires_exact_review_without_claiming_bank_settlement(central,monkeypatch):
    _,actor,post,_,_=central
    _,_,path,_,bid,review,allocation=prepared(central,monkeypatch)
    counted=post('accountant','/api/cash-bags/'+bid+'/count',{'received_cash_halalas':700,'card_halalas':850,'reference':'Actual cash counted and terminal slips differ','confirmed':True})
    current=actor('accountant').get(review).json()
    assert current['source']=={'cash_variance_halalas':0,'card_variance_halalas':-50}
    assert current['bank_settlement_included'] is False
    blocked=post('accountant',path+'/physical-allocation',allocation,status=409)
    assert blocked['detail']['code']=='CASH_BAG_CUSTODY_REVIEW_REQUIRED'
    data={**payload(counted,allocation['receipt_id'],0),'card_variance_halalas':-50}
    post('accountant',review,{**data,'card_variance_halalas':0},status=409)
    proposed=post('accountant',review,data)
    post('owner',review+'/'+proposed['id']+'/approve',approval(data,True))
    state=actor('accountant').get(review).json()
    assert state['outstanding_receivable_halalas']==50 and not state['financially_complete']
    assert state['count_card_halalas']==850 and state['declared_card_halalas']==900
    post('accountant',path+'/physical-allocation',allocation)
    with db.tx() as c:
        assert c.execute('SELECT card_halalas FROM shared_driver_closure_declarations WHERE id=%s',(allocation['declaration_id'],)).fetchone()['card_halalas']==900


def test_shared_bag_outstanding_summary_counts_physical_obligation_once_and_withholds_unscoped_amounts():
    review={'bag_id':'same','financially_complete':False,'period_approval_allowed':True,'outstanding_receivable_halalas':50,'outstanding_payable_halalas':0}
    result=custody.summarize([review,review])
    assert result['custody_outstanding_receivable_halalas']==50 and len(result['cash_bag_custody_reviews'])==1
    masked={k:review[k] for k in ('bag_id','financially_complete','period_approval_allowed')}|{'custody_amounts_withheld':True}
    assert custody.summarize([masked])['custody_outstanding_receivable_halalas'] is None
