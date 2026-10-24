"""Two-stage accounting approval without mutating driver/manager closures."""
from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from app import db
from app.accounting import DriverBalanceApproval, DriverCashReceipt, driver_settlement_values, settlement_action, DriverVarianceConfirmation, deficit_rows
from test_accounting import accountant
from test_operations import ops

TZ = ZoneInfo('Asia/Riyadh')


def test_waiting_cash_is_not_a_shortage_and_original_is_preserved():
    snapshot = {'shift': {'submission': {'terminal_total_halalas': 1200, 'handover_halalas': 8000}}}
    before = deepcopy(snapshot)
    waiting = dict(stage='balance_approved', revision=1, bank_halalas=1250, cash_due_halalas=7500, cash_received_halalas=None)
    values = driver_settlement_values(snapshot, waiting)
    assert values['pending_cash_halalas'] == 7500
    assert values['cash_difference_halalas'] is None
    assert values['original_cash_halalas'] == 8000
    done = driver_settlement_values(snapshot, {**waiting, 'stage': 'cash_received', 'cash_received_halalas': 7300})
    assert done['cash_difference_halalas'] == -200
    assert done['pending_cash_halalas'] == 0
    assert snapshot == before


@pytest.mark.parametrize('expected_bank,expected_cash,bank,due,received,net,shortage,surplus', [
    (25350,19250,23100,21500,None,None,0,0),  # Najashi: cash still pending.
    (25350,19250,23100,21500,21500,0,0,0),  # Card/cash reclassification balances.
    (18850,4800,15900,7800,7800,50,0,50),   # Riyadh: actual total is 0.50 SAR higher.
    (5000,10000,4900,9900,9700,-400,400,0), # Genuine total shortage remains visible.
    (5000,10000,6000,9000,9000,0,0,0),     # Reverse cash/card reclassification.
])
def test_net_settlement_preserves_channel_differences_and_original(expected_bank,expected_cash,bank,due,received,net,shortage,surplus):
    snapshot={'shift':{'submission':{'terminal_total_halalas':expected_bank,'handover_halalas':expected_cash,
        'expected_card_halalas':expected_bank,'expected_halalas':expected_cash}},'summary':{}}
    original=deepcopy(snapshot)
    event=dict(revision=1,balance_approved=True,bank_halalas=bank,cash_due_halalas=due,cash_received_halalas=received)
    values=driver_settlement_values(snapshot,event)
    assert values['bank_difference_halalas']==bank-expected_bank
    assert values['cash_variance_halalas']==(received-expected_cash if received is not None else None)
    assert values['cash_difference_halalas']==(received-due if received is not None else None)
    assert values['net_difference_halalas']==net
    assert values['reconciliation_complete']==(received is not None)
    assert values['shortage_halalas']==shortage and values['surplus_halalas']==surplus
    assert values['system_total_halalas']==expected_bank+expected_cash
    assert values['actual_total_halalas']==(bank+received if received is not None else None)
    assert values['original_bank_halalas']==expected_bank and values['original_cash_halalas']==expected_cash
    assert snapshot==original


def test_cash_first_is_pending_and_audit_classification_uses_changed_values():
    snapshot={'shift':{'submission':{'terminal_total_halalas':5000,'handover_halalas':10000}}}
    cash_first=dict(revision=1,balance_approved=False,bank_halalas=5000,cash_due_halalas=10000,cash_received_halalas=9300)
    values=driver_settlement_values(snapshot,cash_first)
    assert not values['reconciliation_complete'] and values['net_difference_halalas'] is None
    assert values['shortage_halalas']==values['surplus_halalas']==0
    assert values['cash_variance_halalas']==-700 and values['bank_difference_halalas'] is None
    assert settlement_action(snapshot,None,cash_first)=='cash_receipt'
    approved={**cash_first,'revision':2,'balance_approved':True}
    assert settlement_action(snapshot,cash_first,approved)=='approval'
    corrected={**approved,'bank_halalas':5500,'cash_due_halalas':9500}
    assert settlement_action(snapshot,cash_first,corrected)=='correction'
    final=driver_settlement_values(snapshot,corrected)
    assert final['net_difference_halalas']==-200 and final['shortage_halalas']==200
    pending={**cash_first,'cash_received_halalas':None,'balance_approved':True}
    assert settlement_action(snapshot,None,pending)=='approval'
    assert settlement_action(snapshot,pending,{**pending,'bank_halalas':4900})=='correction'
    assert settlement_action(snapshot,pending,{**pending,'cash_received_halalas':10000})=='cash_receipt'


@pytest.mark.parametrize('amount', [-1, 1.5, True, 100000000001])
def test_approval_rejects_invalid_money(amount):
    with pytest.raises(ValidationError):
        DriverBalanceApproval(expected_revision=0, bank_halalas=0, cash_due_halalas=amount, note='TEST reason')


def make_report(snapshot=None):
    # Use a daytime fixture so calendar-day assertions do not depend on CI running
    # before the 04:00 operational-day boundary. Boundary behavior has dedicated tests.
    opened = (datetime.now(TZ)-timedelta(days=3)).replace(hour=12, minute=0, second=0, microsecond=0)
    snapshot = snapshot or {'shift': {'user_id': 'ops-driver', 'submission': {'terminal_total_halalas': 5000, 'handover_halalas': 10000}}, 'summary': {}}
    with db.tx() as c:
        c.execute("""INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,closed_at)
          VALUES('settlement-manager','manager','ops-manager','ops-brand',%s,'closed',%s,%s)""",
          (db.json(['ops-branch']),opened,opened+timedelta(hours=4)))
        c.execute("""INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,closed_at,delivery_operational_date,manager_shift_id)
          VALUES('settlement-shift','driver','ops-driver','ops-brand',%s,'closed',%s,%s,%s,'settlement-manager')""",
          (db.json(['ops-branch']),opened,opened+timedelta(hours=3),opened.date()))
        c.execute("""INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot)
          VALUES('settlement-report','settlement-shift','driver','ops-brand',%s,'ops-driver',%s)""",
          (db.json(['ops-branch']),db.json(snapshot)))
    return snapshot, opened


@pytest.mark.postgres
def test_delayed_cash_amendment_approval_idempotency_and_immutability(ops):
    actor, post, _, _ = ops
    accountant()
    snapshot, opened = make_report()
    base = '/api/accounting/driver-settlements'
    url = base+'/settlement-report'
    balance = {'expected_revision': 0, 'bank_halalas': 4900, 'cash_due_halalas': 9900, 'note': 'TEST reviewed original balance'}
    event = post('accountant',url+'/approve-balance',balance,key='settle-approval')
    assert event['stage'] == 'balance_approved'
    assert post('accountant',url+'/approve-balance',balance,key='settle-approval')['id'] == event['id']
    post('accountant',url+'/approve-balance',balance,status=409)
    balance.update(expected_revision=1, cash_due_halalas=9800, note='TEST correction with explicit reason')
    post('accountant',url+'/approve-balance',balance)
    with db.tx() as c:
        # Simulate elapsed time without changing any immutable approval.
        assert c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',('settlement-report',)).fetchone()['snapshot'] == snapshot
    row = actor('accountant').get(base).json()['settlements'][0]
    assert row['stage'] == 'balance_approved' and row['cash_difference_halalas'] is None
    assert row['due_on'] == (opened.date()+timedelta(days=1)).isoformat()
    assert row['overdue'] is True
    assert row['pending_cash_halalas'] == 9800 and len(row['history']) == 2
    receipt = {'expected_revision': 2, 'cash_received_halalas': 9700, 'received_on': datetime.now(TZ).date().isoformat(), 'receipt_reference': 'TEST RECEIPT 3 DAYS LATER', 'note': 'TEST physical count shortage 1 SAR'}
    post('accountant',url+'/receive-cash',{**receipt, 'received_on': (datetime.now(TZ).date()+timedelta(days=1)).isoformat()},status=422)
    post('accountant',url+'/receive-cash',{**receipt, 'received_on': (opened.date()-timedelta(days=1)).isoformat()},status=422)
    event = post('accountant',url+'/receive-cash',receipt,key='settle-receipt')
    assert post('accountant',url+'/receive-cash',receipt,key='settle-receipt')['id'] == event['id']
    post('accountant',url+'/receive-cash',receipt,status=409)
    # A genuine, unconfirmed discrepancy can still be corrected, while the
    # recorded physical receipt remains fixed. Confirmation makes it final.
    post('accountant',url+'/confirm-variance',{'expected_revision':3,'confirmed':True,
        'note':'TEST genuine shortage reviewed','action_taken':'TEST document and follow up'})
    post('accountant',url+'/approve-balance',{**balance, 'expected_revision': 3},status=409)
    row = actor('accountant').get(base).json()['settlements'][0]
    assert row['cash_difference_halalas'] == -100 and row['pending_cash_halalas'] == 0
    assert row['history'][-1]['actor_name'] == 'TEST ACCOUNTANT'
    assert row['history'][-1]['received_on'] != opened.date().isoformat()
    with db.tx() as c:
        assert c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',('settlement-report',)).fetchone()['snapshot'] == snapshot
    with pytest.raises(Exception):
        with db.tx() as c: c.execute('DELETE FROM driver_accounting_settlements WHERE id=%s',(event['id'],))


@pytest.mark.postgres
def test_scope_permissions_and_receipt_requires_balance(ops):
    actor, post, _, _ = ops
    accountant();make_report()
    base = '/api/accounting/driver-settlements'
    url = base+'/settlement-report'
    approval = {'expected_revision': 0, 'bank_halalas': 5000, 'cash_due_halalas': 10000, 'note': 'TEST reviewed'}
    receipt = {'expected_revision': 1, 'cash_received_halalas': 10000, 'received_on': datetime.now(TZ).date().isoformat(), 'receipt_reference': 'TEST', 'note': 'TEST receipt'}
    post('accountant',url+'/receive-cash',receipt,status=409)
    for who in ('driver', 'manager', 'customer'):
        assert actor(who).get(base).status_code == 403
        post(who,url+'/approve-balance',approval,status=403)
        post(who,url+'/receive-cash',receipt,status=403)
    assert actor('other').get(base).json()['settlements'] == []
    post('other',url+'/approve-balance',approval,status=404)
    with db.tx() as c: c.execute("UPDATE users SET branch_ids='[]'::jsonb WHERE id='ops-accountant'")
    assert actor('accountant').get(base).json()['settlements'] == []
    post('accountant',url+'/approve-balance',approval,status=404)


@pytest.mark.postgres
def test_manager_correction_is_scoped_reasoned_and_preserves_driver_original(ops):
    actor, post, _, _ = ops
    accountant()
    original = {'counted_halalas': 10000, 'handover_halalas': 10000, 'retained_halalas': 0,
                'expected_halalas': 10000, 'terminal_total_halalas': 5000, 'difference_halalas': 0}
    with db.tx() as c:
        c.execute("""INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status)
          VALUES('review-manager','manager','ops-manager','ops-brand',%s,'open')""",(db.json(['ops-branch']),))
        c.execute("""INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,manager_shift_id,submission)
          VALUES('review-driver','driver','ops-driver','ops-brand',%s,'submitted','review-manager',%s)""",(db.json(['ops-branch']),db.json(original)))
        c.execute("""INSERT INTO custody_transfers(id,from_shift_id,to_shift_id,recipient_user_id,amount_halalas,reference,closure,created_by)
          VALUES('review-transfer','review-driver','review-manager','ops-manager',10000,'TEST',true,'ops-driver')""")
    url='/api/shifts/review-driver/review-amounts'
    body={'counted_halalas': 9900, 'terminal_total_halalas': 4900, 'reason': 'TEST recount corrected before approval'}
    post('manager',url,{**body,'reason':' '},status=422)
    post('driver',url,body,status=403)
    post('accountant',url,body,status=403)
    result=post('manager',url,body,key='manager-correction')
    assert result['submission']['driver_original_submission'] == original
    assert result['submission']['handover_halalas'] == 9900
    assert post('manager',url,body,key='manager-correction') == result
    with db.tx() as c:
        assert c.execute("SELECT amount_halalas FROM custody_transfers WHERE id='review-transfer'").fetchone()['amount_halalas']==9900
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='shift.manager_amount_review'").fetchone()['n']==1
        c.execute("UPDATE custody_transfers SET status='confirmed' WHERE id='review-transfer'")
    post('manager',url,{**body,'counted_halalas':9800},status=409)


@pytest.mark.postgres
@pytest.mark.parametrize('expected_bank,expected_cash,bank,due,received,net', [
    (25350,19250,23100,21500,21500,0),
    (18850,4800,15900,7800,7800,50),
    (5000,10000,4900,9900,9700,-400),
])
def test_real_net_reconciliation_pending_receipt_scope_replay_and_immutable_audit(ops,expected_bank,expected_cash,bank,due,received,net):
    actor,post,_,_=ops
    accountant()
    snapshot={'shift':{'user_id':'ops-driver','submission':{'terminal_total_halalas':expected_bank,'handover_halalas':expected_cash,
        'expected_card_halalas':expected_bank,'expected_halalas':expected_cash}},'summary':{}}
    make_report(snapshot)
    endpoint='/api/accounting/driver-settlements/settlement-report'
    approval={'expected_revision':0,'bank_halalas':bank,'cash_due_halalas':due,'note':'TEST manual payment-method reconciliation'}
    event=post('accountant',endpoint+'/approve-balance',approval,key='net-approval')
    assert event['action_type']=='correction'
    assert not event['after']['reconciliation_complete']
    assert event['after']['shortage_halalas']==event['after']['surplus_halalas']==0
    assert post('accountant',endpoint+'/approve-balance',approval,key='net-approval')==event
    row=actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]
    assert row['history'][0]['action_type']=='correction'
    assert row['net_difference_halalas'] is None and row['pending_cash_halalas']==due
    for who in ('driver','manager','customer'):
        post(who,endpoint+'/receive-cash',{'expected_revision':1,'cash_received_halalas':received,
            'received_on':datetime.now(TZ).date().isoformat(),'receipt_reference':'TEST','note':'TEST forbidden'},status=403)
    receipt={'expected_revision':1,'cash_received_halalas':received,'received_on':datetime.now(TZ).date().isoformat(),
             'receipt_reference':'TEST actual manually counted cash','note':'TEST actual receipt after manual review'}
    event=post('accountant',endpoint+'/receive-cash',receipt,key='net-receipt')
    assert event['action_type']=='cash_receipt' and event['after']['reconciliation_complete']
    assert event['after']['net_difference_halalas']==net
    assert post('accountant',endpoint+'/receive-cash',receipt,key='net-receipt')==event
    row=actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]
    assert row['shortage_halalas']==max(0,-net) and row['surplus_halalas']==max(0,net)
    assert row['bank_difference_halalas']==bank-expected_bank and row['cash_variance_halalas']==received-expected_cash
    assert row['history'][-1]['action_type']=='cash_receipt'
    with db.tx() as c:
        assert c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',('settlement-report',)).fetchone()['snapshot']==snapshot
        records=c.execute("SELECT details FROM audit_events WHERE action LIKE 'accounting.driver_settlement.%' ORDER BY created_at,id").fetchall()
        assert len(records)==2
        by_type={r['details']['action_type']:r['details'] for r in records}
        assert by_type['correction']['reason']==approval['note']
        assert by_type['correction']['before']['original_bank_halalas']==expected_bank
        assert by_type['cash_receipt']['reason']==receipt['note']
        assert by_type['cash_receipt']['before']['net_difference_halalas'] is None
        assert by_type['cash_receipt']['after']['net_difference_halalas']==net


@pytest.mark.postgres
def test_cash_first_approval_classifies_correction_without_second_receipt(ops):
    actor,post,_,_=ops
    accountant()
    snapshot={'shift':{'user_id':'ops-driver','submission':{'terminal_total_halalas':25350,'handover_halalas':19250,
        'expected_card_halalas':25350,'expected_halalas':19250}},'summary':{}}
    make_report(snapshot)
    endpoint='/api/accounting/driver-settlements/settlement-report'
    receipt={'expected_revision':0,'cash_received_halalas':21500,'received_on':datetime.now(TZ).date().isoformat(),
             'receipt_reference':'TEST cash first','note':'TEST physical receipt before bank paper'}
    first=post('accountant',endpoint+'/receive-cash',receipt,key='cash-first')
    assert first['action_type']=='cash_receipt' and not first['after']['reconciliation_complete']
    assert first['after']['shortage_halalas']==first['after']['surplus_halalas']==0
    approval={'expected_revision':1,'bank_halalas':23100,'cash_due_halalas':21500,'note':'TEST manual card reclassification'}
    final=post('accountant',endpoint+'/approve-balance',approval,key='cash-first-approval')
    assert final['action_type']=='correction'
    assert final['after']['reconciliation_complete'] and final['after']['net_difference_halalas']==0
    assert final['receipt_reference']==receipt['receipt_reference']
    assert final['cash_received_halalas']==21500
    assert post('accountant',endpoint+'/approve-balance',approval,key='cash-first-approval')==final
    row=actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]
    assert [e['action_type'] for e in row['history']]==['cash_receipt','correction']
    with db.tx() as c:
        assert c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',('settlement-report',)).fetchone()['snapshot']==snapshot
        assert c.execute('SELECT count(*) n FROM driver_accounting_settlements WHERE report_id=%s',('settlement-report',)).fetchone()['n']==2


@pytest.mark.parametrize('overrides', [
    {'expected_revision':True},{'expected_revision':1.5},{'expected_revision':0},
    {'confirmed':'true'},{'confirmed':1},{'action_taken':'  '},{'note':'  '},
])
def test_variance_confirmation_requires_strict_revision_flag_and_reasoned_action(overrides):
    with pytest.raises(ValidationError):
        DriverVarianceConfirmation(**({'expected_revision':2,'confirmed':True,'note':'TEST reason','action_taken':'TEST reviewed action'}|overrides))


@pytest.mark.postgres
@pytest.mark.parametrize('received,net',[(9500,-500),(10500,500)])
def test_shortage_and_surplus_require_explicit_versioned_confirmation(ops,received,net):
    actor,post,_,_=ops
    accountant();snapshot,opened=make_report()
    endpoint='/api/accounting/driver-settlements/settlement-report'
    body={'expected_revision':1,'confirmed':True,'note':'TEST reviewed genuine difference',
          'action_taken':'TEST documented manual follow-up; no salary deduction'}
    post('accountant',endpoint+'/approve-balance',{'expected_revision':0,'bank_halalas':5000,
        'cash_due_halalas':10000,'note':'TEST balance approval'})
    post('accountant',endpoint+'/confirm-variance',body,status=409)
    post('accountant','/api/accounting/reviews/settlement-report',{'note':'TEST legacy must not approve new variance'},status=409)
    receipt={'expected_revision':1,'cash_received_halalas':received,'received_on':datetime.now(TZ).date().isoformat(),
             'receipt_reference':'TEST actual counted receipt','note':'TEST record actual; not variance approval'}
    post('accountant',endpoint+'/receive-cash',receipt)
    row=actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]
    assert row['variance_pending'] and row['variance_confirmation'] is None
    assert row['net_difference_halalas']==net
    body['expected_revision']=2
    for who in ('driver','manager','customer'):
        post(who,endpoint+'/confirm-variance',body,status=403)
    post('other',endpoint+'/confirm-variance',body,status=404)
    post('accountant',endpoint+'/confirm-variance',{**body,'confirmed':False},status=422)
    post('accountant',endpoint+'/confirm-variance',{**body,'expected_revision':1},status=409)
    with db.tx() as c:
        scoped_user=c.execute("SELECT * FROM users WHERE id='ops-accountant'").fetchone()
        lo=opened.replace(hour=0,minute=0,second=0,microsecond=0)
        pending_rows=deficit_rows(c,scoped_user,lo,lo+timedelta(days=1),['ops-branch'],'','ar')
        assert len(pending_rows)==1 and pending_rows[0]['review_id'] is None
        assert pending_rows[0]['approved_shortage']==0
        old_events=c.execute('SELECT * FROM driver_accounting_settlements WHERE report_id=%s ORDER BY revision',('settlement-report',)).fetchall()
        original_reports=c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',('settlement-report',)).fetchone()['snapshot']
        movement_counts=tuple(c.execute('SELECT (SELECT count(*) FROM collections) collections,(SELECT count(*) FROM custody_events) custody').fetchone().values())
    confirmed=post('accountant',endpoint+'/confirm-variance',body,key='variance-confirmation')
    assert confirmed['action']=='accounting.variance_confirmed' and confirmed['actor_name']=='TEST ACCOUNTANT'
    details=confirmed['details']
    assert details['settlement_revision']==2 and details['driver_id']=='ops-driver'
    assert details['net_difference_halalas']==net
    assert details['shortage_halalas']==max(0,-net) and details['surplus_halalas']==max(0,net)
    assert details['action_taken']==body['action_taken'] and details['reason']==body['note']
    assert details['reviewer_id']=='ops-accountant' and details['no_payroll_deduction']
    assert post('accountant',endpoint+'/confirm-variance',body,key='variance-confirmation')==confirmed
    post('accountant',endpoint+'/confirm-variance',body,status=409)
    post('accountant',endpoint+'/confirm-variance',{**body,'note':'TEST changed reason'},key='variance-confirmation',status=409)
    row=actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]
    assert not row['variance_pending'] and row['variance_confirmation']['id']==confirmed['id']
    with db.tx() as c:
        assert c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',('settlement-report',)).fetchone()['snapshot']==snapshot==original_reports
        assert c.execute('SELECT * FROM driver_accounting_settlements WHERE report_id=%s ORDER BY revision',('settlement-report',)).fetchall()==old_events
        assert tuple(c.execute('SELECT (SELECT count(*) FROM collections) collections,(SELECT count(*) FROM custody_events) custody').fetchone().values())==movement_counts
        events=c.execute("SELECT * FROM audit_events WHERE action='accounting.variance_confirmed'").fetchall()
        assert len(events)==1 and events[0]['details']==details
        reviewed_rows=deficit_rows(c,scoped_user,lo,lo+timedelta(days=1),['ops-branch'],'','ar')
        assert len(reviewed_rows)==1 and reviewed_rows[0]['review_id']==confirmed['id']
        assert reviewed_rows[0]['approved_shortage']==max(0,-net)
        c.execute("UPDATE users SET branch_ids='[]'::jsonb WHERE id='ops-accountant'")
    # A saved idempotent response never bypasses current report scope.
    post('accountant',endpoint+'/confirm-variance',body,key='variance-confirmation',status=404)


@pytest.mark.postgres
def test_balanced_reclassification_cannot_create_variance_confirmation(ops):
    actor,post,_,_=ops
    accountant();make_report()
    endpoint='/api/accounting/driver-settlements/settlement-report'
    post('accountant',endpoint+'/approve-balance',{'expected_revision':0,'bank_halalas':4000,
        'cash_due_halalas':11000,'note':'TEST reclassified card to cash'})
    post('accountant',endpoint+'/receive-cash',{'expected_revision':1,'cash_received_halalas':11000,
        'received_on':datetime.now(TZ).date().isoformat(),'receipt_reference':'TEST full received','note':'TEST actual receipt'})
    response=post('accountant',endpoint+'/confirm-variance',{'expected_revision':2,'confirmed':True,
        'note':'TEST cannot confirm absent variance','action_taken':'TEST reviewed'},status=409)
    row=actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]
    assert row['reconciliation_complete'] and row['net_difference_halalas']==0
    assert not row['variance_pending'] and row['variance_confirmation'] is None
    with db.tx() as c:assert c.execute("SELECT count(*) n FROM audit_events WHERE action='accounting.variance_confirmed'").fetchone()['n']==0


@pytest.mark.postgres
def test_owner_cannot_confirm_own_variance(ops):
    _,post,_,_=ops
    with db.tx() as c:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status) VALUES('self-variance-manager','manager','ops-owner','ops-brand',%s,'closed')",(db.json(['ops-branch']),))
        c.execute("INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES('self-variance-report','self-variance-manager','manager','ops-brand',%s,'ops-owner',%s)",
            (db.json(['ops-branch']),db.json({'shift':{'user_id':'ops-owner','submission':{}},'summary':{}})))
    result=post('owner','/api/accounting/driver-settlements/self-variance-report/confirm-variance',
        {'expected_revision':1,'confirmed':True,'note':'TEST cannot confirm own file','action_taken':'TEST denied'},status=403)
    assert result['detail']['code']=='SELF_SETTLEMENT'


@pytest.mark.postgres
def test_pending_variance_correction_preserves_actual_cash_and_invalidates_old_revision(ops):
    actor,post,_,_=ops
    accountant();snapshot,_=make_report()
    endpoint='/api/accounting/driver-settlements/settlement-report'
    post('accountant',endpoint+'/approve-balance',{'expected_revision':0,'bank_halalas':4900,'cash_due_halalas':10000,'note':'TEST initial bank paper'})
    received=post('accountant',endpoint+'/receive-cash',{'expected_revision':1,'cash_received_halalas':10000,
        'received_on':datetime.now(TZ).date().isoformat(),'receipt_reference':'TEST fixed physical receipt','note':'TEST actual cash counted'})
    assert received['after']['net_difference_halalas']==-100
    with db.tx() as c:
        old=c.execute('SELECT * FROM driver_accounting_settlements WHERE report_id=%s ORDER BY revision',('settlement-report',)).fetchall()
    correction={'expected_revision':2,'bank_halalas':5100,'cash_due_halalas':10000,'note':'TEST corrected bank after recount'}
    corrected=post('accountant',endpoint+'/approve-balance',correction,key='pending-variance-correction')
    assert corrected['action_type']=='correction' and corrected['revision']==3
    assert corrected['cash_received_halalas']==10000 and corrected['receipt_reference']==received['receipt_reference']
    assert corrected['after']['net_difference_halalas']==100
    assert post('accountant',endpoint+'/approve-balance',correction,key='pending-variance-correction')==corrected
    confirm={'expected_revision':2,'confirmed':True,'note':'TEST reviewed current genuine surplus','action_taken':'TEST documented surplus'}
    post('accountant',endpoint+'/confirm-variance',confirm,status=409)
    confirmation=post('accountant',endpoint+'/confirm-variance',{**confirm,'expected_revision':3})
    assert confirmation['details']['settlement_revision']==3 and confirmation['details']['surplus_halalas']==100
    post('accountant',endpoint+'/approve-balance',{**correction,'expected_revision':3,'bank_halalas':5000},status=409)
    with db.tx() as c:
        assert c.execute('SELECT * FROM driver_accounting_settlements WHERE report_id=%s AND revision<=2 ORDER BY revision',('settlement-report',)).fetchall()==old
        assert c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',('settlement-report',)).fetchone()['snapshot']==snapshot


@pytest.mark.postgres
def test_correcting_pending_difference_to_balanced_closes_without_confirmation(ops):
    actor,post,_,_=ops
    accountant();make_report()
    endpoint='/api/accounting/driver-settlements/settlement-report'
    post('accountant',endpoint+'/approve-balance',{'expected_revision':0,'bank_halalas':4900,'cash_due_halalas':10000,'note':'TEST mistaken initial bank'})
    post('accountant',endpoint+'/receive-cash',{'expected_revision':1,'cash_received_halalas':10000,
        'received_on':datetime.now(TZ).date().isoformat(),'receipt_reference':'TEST actual fixed cash','note':'TEST receipt'})
    final=post('accountant',endpoint+'/approve-balance',{'expected_revision':2,'bank_halalas':5000,'cash_due_halalas':10000,'note':'TEST paper corrected to match'})
    assert final['after']['net_difference_halalas']==0 and final['cash_received_halalas']==10000
    row=actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]
    assert not row['variance_pending'] and row['variance_confirmation'] is None
    post('accountant',endpoint+'/approve-balance',{'expected_revision':3,'bank_halalas':5100,'cash_due_halalas':10000,'note':'TEST balanced is final'},status=409)


@pytest.mark.parametrize('overrides', [
    {'expected_revision':0},{'expected_revision':True},{'expected_revision':1.5},
    {'cash_received_halalas':-1},{'cash_received_halalas':100000000001},
    {'cash_received_halalas':True},{'cash_received_halalas':1.5},{'cash_received_halalas':'21500'},
    {'confirmed':'true'},{'confirmed':1},{'note':'  '},{'note':'x'*2001},
])
def test_cash_receipt_correction_validates_strict_money_revision_confirmation_and_reason(overrides):
    from app.accounting import DriverCashReceiptCorrection
    with pytest.raises(ValidationError):
        DriverCashReceiptCorrection(**({'expected_revision':2,'cash_received_halalas':21500,
            'confirmed':True,'note':'TEST recount corrected receipt'}|overrides))


def prepare_cash_correction(post):
    snapshot={'shift':{'user_id':'ops-driver','submission':{'terminal_total_halalas':25350,
        'handover_halalas':19250,'expected_card_halalas':25350,'expected_halalas':19250}},'summary':{}}
    original,opened=make_report(snapshot)
    endpoint='/api/accounting/driver-settlements/settlement-report'
    post('accountant',endpoint+'/approve-balance',{'expected_revision':0,'bank_halalas':23100,
        'cash_due_halalas':21500,'note':'TEST verified bank paper'})
    receipt=post('accountant',endpoint+'/receive-cash',{'expected_revision':1,'cash_received_halalas':23100,
        'received_on':datetime.now(TZ).date().isoformat(),'receipt_reference':'TEST original receipt reference',
        'note':'TEST original entry mistaken as bank amount'})
    return endpoint,original,opened,receipt



def request_and_approve_cash(post,endpoint,body,key=None):
    ticket=post('accountant',endpoint+'/cash-correction-requests',
        {k:v for k,v in body.items() if k!='confirmed'},key=(key+'-request') if key else None)
    result=post('owner',endpoint+'/cash-correction-requests/'+ticket['id']+'/approve',
        {k:v for k,v in body.items() if k!='cash_received_halalas'},key=key)
    assert result['ticket']['status']=='approved'
    return result['settlement']


@pytest.mark.postgres
def test_ticket_owner_approval_appends_recount_without_rewriting_originals(ops):
    actor,post,_,_=ops
    accountant();endpoint,snapshot,_,receipt=prepare_cash_correction(post)
    body={'expected_revision':2,'cash_received_halalas':21500,'confirmed':True,
          'note':'TEST verified cash 215 SAR; previous 231 was a typing error'}
    with db.tx() as c:
        old=c.execute('SELECT * FROM driver_accounting_settlements WHERE report_id=%s ORDER BY revision',('settlement-report',)).fetchall()
        custody=c.execute('SELECT * FROM custody_events ORDER BY id').fetchall()
        collections=c.execute('SELECT * FROM collections ORDER BY id').fetchall()
    ticket=post('accountant',endpoint+'/cash-correction-requests',{k:v for k,v in body.items() if k!='confirmed'},key='cash-ticket')
    assert ticket['status']=='pending' and ticket['previous_cash_received_halalas']==23100
    assert ticket['cash_received_halalas']==21500 and ticket['requester_name']=='TEST ACCOUNTANT'
    pending=actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]
    assert pending['revision']==2 and not pending['can_request_cash_correction']
    post('accountant',endpoint+'/cash-correction-requests',{k:v for k,v in body.items() if k!='confirmed'},status=409)
    approval={k:v for k,v in body.items() if k!='cash_received_halalas'}
    path=endpoint+'/cash-correction-requests/'+ticket['id']+'/approve'
    approved=post('owner',path,approval,key='cash-ticket-approve')
    corrected=approved['settlement']
    assert approved['ticket']['status']=='approved' and approved['ticket']['decision']['actor_name']=='owner'
    assert corrected['revision']==3 and corrected['action_type']=='cash_receipt_correction'
    assert corrected['bank_halalas']==23100 and corrected['cash_due_halalas']==21500 and corrected['cash_received_halalas']==21500
    assert corrected['received_on']==receipt['received_on'] and corrected['receipt_reference']==receipt['receipt_reference']
    assert corrected['before']['net_difference_halalas']==1600 and corrected['after']['net_difference_halalas']==0
    assert corrected['after']['shortage_halalas']==corrected['after']['surplus_halalas']==0
    assert post('owner',path,approval,key='cash-ticket-approve')==approved
    post('owner',path,approval,status=409)
    post('owner',path,{**approval,'note':'TEST different payload'},key='cash-ticket-approve',status=409)
    row=actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]
    assert row['revision']==3 and row['actual_total_halalas']==44600 and not row['variance_pending']
    assert row['history'][-1]['action_type']=='cash_receipt_correction'
    assert row['cash_correction_requests'][0]['status']=='approved' and row['variance_confirmation'] is None
    card=actor('accountant').get('/api/accounting/manager-cards').json()['cards'][0]['drivers'][0]
    assert card['history'][-1]['action_type']=='cash_receipt_correction'
    assert card['history'][-2]['cash_received_halalas']==23100 and card['history'][-1]['cash_received_halalas']==21500
    assert card['cash_correction_requests'][0]['status']=='approved'
    assert card['cash_correction_requests'][0]['decision']['reason']==body['note']
    with db.tx() as c:
        assert c.execute('SELECT * FROM driver_accounting_settlements WHERE report_id=%s AND revision<=2 ORDER BY revision',('settlement-report',)).fetchall()==old
        assert c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',('settlement-report',)).fetchone()['snapshot']==snapshot
        assert c.execute('SELECT * FROM custody_events ORDER BY id').fetchall()==custody
        assert c.execute('SELECT * FROM collections ORDER BY id').fetchall()==collections
        audits=c.execute("SELECT * FROM audit_events WHERE action='accounting.driver_settlement.cash_corrected' AND entity_id='settlement-report'").fetchall()
        assert len(audits)==1 and audits[0]['actor_id']=='ops-owner'
        assert audits[0]['details']['reason']==body['note'] and audits[0]['details']['ticket_id']==ticket['id']
        assert audits[0]['details']['before']['cash_received_halalas']==23100 and audits[0]['details']['after']['cash_received_halalas']==21500
    with pytest.raises(Exception):
        with db.tx() as c:c.execute('UPDATE driver_accounting_settlements SET cash_received_halalas=0 WHERE id=%s',(corrected['id'],))


@pytest.mark.postgres
def test_ticket_permissions_scope_no_direct_bypass_and_strict_approval(ops):
    actor,post,_,_=ops
    accountant();endpoint,_,_,_=prepare_cash_correction(post)
    body={'expected_revision':2,'cash_received_halalas':21500,'note':'TEST cash recount request'}
    direct={**body,'confirmed':True}
    assert post('owner',endpoint+'/correct-cash-receipt',direct,status=409)['detail']['code']=='TICKET_REQUIRED'
    post('accountant',endpoint+'/correct-cash-receipt',direct,status=403)
    for who in ('manager','driver','customer','branch'):
        post(who,endpoint+'/cash-correction-requests',body,status=403)
    post('other',endpoint+'/cash-correction-requests',body,status=404)
    post('accountant',endpoint+'/cash-correction-requests',{**body,'expected_revision':1},status=409)
    post('accountant',endpoint+'/cash-correction-requests',{**body,'cash_received_halalas':23100},status=409)
    ticket=post('accountant',endpoint+'/cash-correction-requests',body,key='scoped-cash-ticket')
    path=endpoint+'/cash-correction-requests/'+ticket['id']+'/approve'
    approval={'expected_revision':2,'confirmed':True,'note':'TEST owner reviewed request'}
    post('accountant',path,approval,status=403)
    post('other',path,approval,status=404)
    post('owner',path,{**approval,'confirmed':False},status=422)
    post('owner',path,{**approval,'expected_revision':1},status=409)
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]'::jsonb WHERE id='ops-accountant'")
    post('accountant',endpoint+'/cash-correction-requests',body,key='scoped-cash-ticket',status=404)
    assert actor('accountant').get(endpoint+'/cash-correction-requests').status_code==404
    with db.tx() as c:assert c.execute('SELECT count(*) n FROM driver_accounting_settlements WHERE report_id=%s',('settlement-report',)).fetchone()['n']==2


@pytest.mark.postgres
@pytest.mark.parametrize('finalized',['confirmation','day','month','revision'])
def test_ticket_approval_rechecks_revision_and_final_freezes(ops,finalized):
    actor,post,_,_=ops
    accountant();endpoint,_,opened,_=prepare_cash_correction(post)
    ticket=post('accountant',endpoint+'/cash-correction-requests',{'expected_revision':2,'cash_received_halalas':21500,'note':'TEST request before later review'})
    if finalized=='confirmation':
        post('accountant',endpoint+'/confirm-variance',{'expected_revision':2,'confirmed':True,'note':'TEST surplus verified','action_taken':'TEST follow up recorded surplus'})
    elif finalized=='day':
        with db.tx() as c:c.execute("""INSERT INTO accounting_daily_closures(id,manager_shift_id,brand_id,branch_ids,operational_date,snapshot,approved_by,note)
          VALUES('cash-correction-day','settlement-manager','ops-brand',%s,%s,'{}','ops-owner','TEST frozen day')""",(db.json(['ops-branch']),opened.date()))
    elif finalized=='month':
        with db.tx() as c:c.execute("""INSERT INTO accounting_months(id,brand_id,month,branch_ids,scope_hash,snapshot,closed_by,reason)
          VALUES('cash-correction-month','ops-brand',%s,%s,'TEST frozen scope','{}','ops-owner','TEST frozen month')""",(opened.date().replace(day=1),db.json(['ops-branch'])))
    else:
        post('accountant',endpoint+'/approve-balance',{'expected_revision':2,'bank_halalas':23200,'cash_due_halalas':21500,'note':'TEST changed balance after ticket'})
        assert actor('accountant').get(endpoint+'/cash-correction-requests').json()['requests'][0]['status']=='stale'
    post('owner',endpoint+'/cash-correction-requests/'+ticket['id']+'/approve',{'expected_revision':2,'confirmed':True,'note':'TEST must not bypass later review'},status=409)
    with db.tx() as c:assert c.execute("SELECT count(*) n FROM audit_events WHERE action='accounting.driver_settlement.cash_corrected'").fetchone()['n']==0


@pytest.mark.postgres
def test_ticket_rejection_and_self_approval_preserve_financial_state(ops):
    _,post,_,_=ops
    accountant();endpoint,_,_,_=prepare_cash_correction(post)
    body={'expected_revision':2,'cash_received_halalas':21500,'note':'TEST recount needs review'}
    own=post('owner',endpoint+'/cash-correction-requests',body)
    post('owner',endpoint+'/cash-correction-requests/'+own['id']+'/approve',{'expected_revision':2,'confirmed':True,'note':'TEST self approval denied'},status=403)
    rejection=post('owner',endpoint+'/cash-correction-requests/'+own['id']+'/reject',{'note':'TEST owner withdrew own request'},key='reject-own-ticket')
    assert rejection['status']=='rejected'
    assert post('owner',endpoint+'/cash-correction-requests/'+own['id']+'/reject',{'note':'TEST owner withdrew own request'},key='reject-own-ticket')==rejection
    ticket=post('accountant',endpoint+'/cash-correction-requests',body)
    path=endpoint+'/cash-correction-requests/'+ticket['id']
    post('accountant',path+'/reject',{'note':'TEST accountant cannot reject'},status=403)
    post('owner',path+'/reject',{'note':'TEST original cash verified; request rejected'})
    post('owner',path+'/approve',{'expected_revision':2,'confirmed':True,'note':'TEST rejected ticket cannot apply'},status=409)
    with db.tx() as c:assert c.execute('SELECT count(*) n FROM driver_accounting_settlements WHERE report_id=%s',('settlement-report',)).fetchone()['n']==2


@pytest.mark.postgres
def test_balanced_receipt_error_still_requires_ticket_and_new_variance_confirmation(ops):
    actor,post,_,_=ops
    accountant();endpoint,_,_,_=prepare_cash_correction(post)
    request_and_approve_cash(post,endpoint,{'expected_revision':2,'cash_received_halalas':21500,'confirmed':True,'note':'TEST balanced initial correction'})
    row=actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]
    assert row['can_request_cash_correction'] and row['net_difference_halalas']==0
    result=request_and_approve_cash(post,endpoint,{'expected_revision':3,'cash_received_halalas':21400,'confirmed':True,'note':'TEST further actual recount proves shortage'})
    assert result['after']['net_difference_halalas']==-100
    row=actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]
    assert row['variance_pending'] and row['variance_confirmation'] is None


@pytest.mark.parametrize('amount',[0,100000000000])
def test_cash_correction_money_boundaries_are_accepted(amount):
    from app.accounting import DriverCashCorrectionRequest
    assert DriverCashCorrectionRequest(expected_revision=1,cash_received_halalas=amount,note='TEST boundary').cash_received_halalas==amount


@pytest.mark.postgres
@pytest.mark.parametrize('cash_first',[False,True])
def test_cash_ticket_requires_both_recorded_cash_and_approved_balance(ops,cash_first):
    _,post,_,_=ops
    accountant();make_report();endpoint='/api/accounting/driver-settlements/settlement-report'
    if cash_first:
        post('accountant',endpoint+'/receive-cash',{'expected_revision':0,'cash_received_halalas':23100,'received_on':datetime.now(TZ).date().isoformat(),'receipt_reference':'TEST cash first','note':'TEST received before balance paper'})
    else:
        post('accountant',endpoint+'/approve-balance',{'expected_revision':0,'bank_halalas':5000,'cash_due_halalas':10000,'note':'TEST balance before receipt'})
    result=post('accountant',endpoint+'/cash-correction-requests',{'expected_revision':1,'cash_received_halalas':21500,'note':'TEST incomplete reconciliation denied'},status=409)
    assert result['detail']['code']==('RECONCILIATION_REQUIRED' if cash_first else 'CASH_NOT_RECEIVED')
