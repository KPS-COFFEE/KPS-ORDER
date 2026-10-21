"""Real PostgreSQL acceptance for independent receipts and driver statements."""
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from app import db
from app.accounting import driver_settlement_values
from test_accounting import accountant
from test_driver_accounting_settlements import make_report
from test_operations import ops

TZ=ZoneInfo('Asia/Riyadh')


def test_variances_preserve_both_channels_without_netting_or_pending_shortage():
    snap={'shift':{'submission':{'expected_halalas':12000,'retained_halalas':2000,'handover_halalas':10000,'expected_card_halalas':5000,'terminal_total_halalas':5000}}}
    pending=driver_settlement_values(snap,dict(stage='balance_approved',balance_approved=True,revision=1,bank_halalas=5200,cash_due_halalas=9900,cash_received_halalas=None))
    assert pending['shortage_halalas']==0 and pending['surplus_halalas']==200
    assert pending['cash_variance_halalas'] is None
    final=driver_settlement_values(snap,dict(stage='cash_received',balance_approved=True,revision=2,bank_halalas=5200,cash_due_halalas=9900,cash_received_halalas=9800))
    assert final['shortage_halalas']==200 and final['surplus_halalas']==200
    assert final['cash_difference_halalas']==-100


def seed():
    accountant();snapshot,opened=make_report()
    other=deepcopy(snapshot);other['shift']['user_id']='statement-driver2'
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('statement-driver2','TEST SECOND DRIVER','driver','ops-brand','[\"ops-branch\"]')")
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,closed_at,delivery_operational_date,manager_shift_id) VALUES('statement-shift2','driver','statement-driver2','ops-brand','[\"ops-branch\"]','closed',%s,now(),%s,'settlement-manager')",(opened,opened.date()))
        c.execute("INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES('statement-report2','statement-shift2','driver','ops-brand','[\"ops-branch\"]','statement-driver2',%s)",(db.json(other),))
        c.execute("INSERT INTO audit_events(id,actor_id,action,entity_type,entity_id,details) VALUES('statement-manager-change','ops-manager','shift.manager_amount_review','shift','settlement-shift',%s)",(db.json({'before':{'terminal_total_halalas':4800,'handover_halalas':10200},'after':{'terminal_total_halalas':5000,'handover_halalas':10000},'reason':'TEST manager recount'}),))
    return snapshot,opened


def balance(post,rid='settlement-report',revision=0,bank=5000):
    return post('accountant',f'/api/accounting/driver-settlements/{rid}/approve-balance',{'expected_revision':revision,'bank_halalas':bank,'cash_due_halalas':10000,'note':'TEST paper reviewed without waiting for bank deposit'})


def cash(post,rid='settlement-report',revision=0,amount=10000):
    return post('accountant',f'/api/accounting/driver-settlements/{rid}/receive-cash',{'expected_revision':revision,'cash_received_halalas':amount,'received_on':datetime.now(TZ).date().isoformat(),'receipt_reference':'TEST TREASURY','note':'TEST physical receipt'},key='statement-cash-'+rid)


@pytest.mark.postgres
def test_manager_card_independent_driver_receipts_day_snapshot_and_audit(ops):
    actor,post,order,_=ops;original,opened=seed()
    path='/api/accounting/manager-cards'
    cards=actor('accountant').get(path).json()['cards']
    assert len(cards)==1 and cards[0]['total']==2 and cards[0]['completed']==0
    assert actor('other').get(path).json()['cards']==[]
    assert actor('driver').get(path).status_code==403
    post('accountant',path+'/settlement-manager/approve-day',{'note':'TEST premature approval'},status=409)
    # Cash can arrive first for one driver while the other paper is approved.
    receipt=cash(post,amount=9900)
    assert receipt['balance_approved'] is False
    assert cash(post,amount=9900)['id']==receipt['id']
    balance(post,'statement-report2')
    cards=actor('accountant').get(path).json()['cards'];assert cards[0]['completed']==0
    row=next(r for r in cards[0]['drivers'] if r['id']=='settlement-report')
    assert row['stage']=='cash_pending_balance' and row['shortage_halalas']==100
    balance(post,revision=1,bank=5100)
    cards=actor('accountant').get(path).json()['cards'];assert cards[0]['completed']==1
    post('accountant',path+'/settlement-manager/approve-day',{'note':'TEST second driver pending'},status=409)
    cash(post,'statement-report2',revision=1,amount=10200)
    day=opened.date().isoformat()
    # One delivered order with two task rows is still one completed order.
    oid=order()
    with db.tx() as c:
        for i in range(2):c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,delivered_at) VALUES(%s,%s,'ops-driver','settlement-shift','delivered','ops-manager',now())",('statement-task-'+str(i),oid))
    case={'branch_id':'ops-branch','subject_id':'ops-driver','occurred_on':day,'vehicle_plate':'TEST','amount_halalas':2500,'category':'order','note':'TEST operational violation reason'}
    post('accountant','/api/accounting/cases',case)
    post('accountant','/api/accounting/cases',{**case,'category':'traffic','amount_halalas':15000,'note':'TEST traffic violation reason'})
    url=f'/api/accounting/reports?group=driver_statement&start={day}&end={day}'
    data=actor('accountant').get(url).json()
    first=next(r for r in data['rows'] if r['driver_id']=='ops-driver')
    assert first['completed_orders']==1 and first['shortage']==100 and first['surplus']==100
    assert first['violations']==2500 and first['traffic']==15000
    assert 'TEST manager recount' in first['adjustments'] and 'TEST ACCOUNTANT' in first['adjustments']
    assert len(data['adjustment_history'])==5
    assert all(e['actor'] and e['reason'] and 'before_cash' in e for e in data['adjustment_history'])
    approved=post('accountant',path+'/settlement-manager/approve-day',{'note':'TEST reviewed all drivers'},key='day-final')
    assert post('accountant',path+'/settlement-manager/approve-day',{'note':'TEST reviewed all drivers'},key='day-final')==approved
    saved=actor('accountant').get('/api/accounting/days/'+approved['id']).json()
    assert saved['frozen'] and saved['totals']['completed_orders']==1
    assert saved['totals']['shortage']==100 and saved['totals']['surplus']==300
    assert actor('other').get('/api/accounting/days/'+approved['id']).status_code==404
    # Subsequent case updates cannot rewrite the saved daily approval.
    post('accountant','/api/accounting/cases',{**case,'note':'TEST later incident'})
    assert actor('accountant').get('/api/accounting/days/'+approved['id']).json()==saved
    with db.tx() as c:
        assert c.execute("SELECT snapshot FROM closure_reports WHERE id='settlement-report'").fetchone()['snapshot']==original
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action LIKE 'accounting.driver_settlement.%' AND details ? 'before' AND details ? 'after'").fetchone()['n']==4
    with pytest.raises(Exception):
        with db.tx() as c:c.execute('DELETE FROM accounting_daily_closures WHERE id=%s',(approved['id'],))
    for fmt in ('pdf','xlsx'):
        r=actor('accountant').get('/api/accounting/days/'+approved['id']+'?format='+fmt)
        assert r.status_code==200 and r.content.startswith(b'%PDF' if fmt=='pdf' else b'PK')


@pytest.mark.postgres
def test_month_statement_lists_every_day_and_keeps_driver_ownership(ops):
    actor,post,_,_=ops;_,opened=seed()
    balance(post);cash(post,revision=1,amount=9800)
    month=opened.date().replace(day=1)
    import calendar
    end=month.replace(day=calendar.monthrange(month.year,month.month)[1])
    url=f'/api/accounting/reports?group=driver_statement&driver_id=ops-driver&start={month}&end={end}'
    report=actor('accountant').get(url).json()
    assert len(report['rows'])==end.day
    assert {r['driver_id'] for r in report['rows']}=={'ops-driver'}
    assert report['drivers'][0]['shortage']==200
    assert report['totals']['pending_cash']==0
    assert actor('other').get(url).status_code==404
    assert actor('accountant').get(url+'&branch_ids=ops-branch-other').status_code==403
    for lang in ('ar','en'):
        for fmt in ('pdf','xlsx'):
            assert actor('accountant').get(url+'&lang='+lang+'&format='+fmt).status_code==200
