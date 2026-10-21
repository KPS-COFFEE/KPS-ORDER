"""Two-stage accounting approval without mutating driver/manager closures."""
from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from app import db
from app.accounting import DriverBalanceApproval, DriverCashReceipt, driver_settlement_values
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


@pytest.mark.parametrize('amount', [-1, 1.5, True, 100000000001])
def test_approval_rejects_invalid_money(amount):
    with pytest.raises(ValidationError):
        DriverBalanceApproval(expected_revision=0, bank_halalas=0, cash_due_halalas=amount, note='TEST reason')


def make_report():
    opened = datetime.now(TZ)-timedelta(days=3)
    snapshot = {'shift': {'user_id': 'ops-driver', 'submission': {'terminal_total_halalas': 5000, 'handover_halalas': 10000}}, 'summary': {}}
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
