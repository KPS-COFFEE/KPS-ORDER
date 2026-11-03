"""Per-driver deadlines, independent completion, and one audited extension."""
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from app import db
from app.accounting_statements import closure_deadline
from test_accounting import accountant
from test_driver_accounting_settlements import make_report
from test_operations import ops
from test_accounting_manager_shift_day import duty

TZ=ZoneInfo('Asia/Riyadh')


@pytest.mark.parametrize('morning,local,hours',[(False,True,12),(False,False,72),(True,True,24),(True,False,24)])
def test_deadline_uses_frozen_shift_end(morning,local,hours):
    end=datetime(2026,10,21,4,tzinfo=TZ)
    row=dict(day=date(2026,10,20),branch_ids=['b'],delivery_window_snapshot={
        'period':'morning' if morning else 'evening','branches':{'b':{'window_end':end.isoformat()}}})
    result=closure_deadline(row,{'b'} if local else set())
    assert result['due_at']==end+timedelta(hours=hours)
    if local and not morning:assert result['due_at'].hour==16
    extension={'details':{'due_at':(result['due_at']+timedelta(hours=48)).isoformat()}}
    assert closure_deadline(row,set(),extension)['due_at']==result['due_at']+timedelta(hours=48)


@pytest.mark.postgres
def test_external_extension_once_scoped_and_audited(ops):
    actor,post,_,_=ops
    accountant();make_report()
    url='/api/accounting/driver-settlements/settlement-report/extend-deadline'
    post('accountant',url,{'note':'TEST local forbidden'},status=409)
    with db.tx() as c:c.execute("UPDATE branches SET city='الرياض' WHERE id='ops-branch'")
    before=actor('accountant').get('/api/accounting/manager-cards').json()['cards'][0]
    assert before['completion_percent']==0 and before['drivers'][0]['can_extend']
    result=post('accountant',url,{'note':'TEST cash transfer delayed'},key='deadline-once')
    assert post('accountant',url,{'note':'TEST cash transfer delayed'},key='deadline-once')==result
    post('accountant',url,{'note':'TEST duplicate'},status=409)
    post('manager',url,{'note':'TEST unauthorized'},status=403)
    after=actor('accountant').get('/api/accounting/manager-cards').json()['cards'][0]
    driver=after['drivers'][0]
    assert not driver['can_extend'] and driver['extension']['actor_name']=='TEST ACCOUNTANT'
    assert datetime.fromisoformat(driver['due_at'])-datetime.fromisoformat(driver['original_due_at'])==timedelta(hours=48)
    post('accountant','/api/accounting/driver-settlements/settlement-report/approve-balance',{
        'expected_revision':0,'bank_halalas':5000,'cash_due_halalas':10000,'note':'TEST reviewed'})
    card=actor('accountant').get('/api/accounting/manager-cards').json()['cards'][0]
    assert card['completion_percent']==50 and card['balance_completed']==1 and card['cash_completed']==0
    day=card['day']
    report=actor('accountant').get(f'/api/accounting/reports?group=driver_statement&start={day}&end={day}').json()
    assert isinstance(report['settlements'],list) and len(report['settlements'])==1
    assert report['deadline_extensions'][0]['details']['reason']=='TEST cash transfer delayed'
    assert 'TEST cash transfer delayed' in report['rows'][0]['adjustments']


@pytest.mark.postgres
def test_cash_receipt_does_not_stop_unresolved_variance_deadline(ops):
    actor,_,_,_=ops
    accountant();_,opened=make_report()
    with db.tx() as c:
        # Insert historical receipt evidence at fixture creation. Final shifts
        # and settlement events remain immutable throughout the regression.
        c.execute("""INSERT INTO driver_accounting_settlements
          (id,report_id,brand_id,revision,stage,balance_approved,bank_halalas,cash_due_halalas,
           cash_received_halalas,received_on,receipt_reference,note,actor_id,created_at)
          VALUES('test-early-cash','settlement-report','ops-brand',1,'cash_received',true,4900,10000,
           10000,%s,'TEST witnessed receipt','TEST unresolved card shortage','ops-accountant',%s)""",
          (opened.date(),opened+timedelta(hours=5)))
    card=actor('accountant').get('/api/accounting/manager-cards').json()['cards'][0]
    row=card['drivers'][0]
    assert row['stage']=='cash_received' and row['variance_pending']
    assert row['overdue'] and card['overdue_count']==1
    assert not card['ready']


@pytest.mark.postgres
def test_missing_driver_report_keeps_deadline_and_accounting_responsibility(ops):
    actor,_,_,_=ops;accountant()
    with db.tx() as c:
        # Same optional-column fixture used by manager-shift-day tests. Its
        # production column lives in the central multibrand migration set.
        c.execute('ALTER TABLE shifts ADD COLUMN IF NOT EXISTS staffing_ended_at timestamptz')
        sid=duty(c,'missing-deadline','2026-01-01',driver_status='submitted')
        c.execute("UPDATE shifts SET staffing_ended_at=closed_at WHERE id=%s",(sid,))
    cards=actor('accountant').get('/api/accounting/manager-cards').json()['cards']
    card=next(r for r in cards if r['id']=='test-manager-missing-deadline')
    child=card['pending_drivers'][0]
    assert child['id']==sid and child['responsible']=='accounting'
    assert child['due_at']=='2026-01-02T16:00:00+03:00'
    assert child['overdue'] and card['overdue_count']==1 and card['overdue']
    assert not card['ready'] and not card['drivers']


@pytest.mark.postgres
def test_overdue_open_manager_is_visible_without_ending_or_approving_it(ops):
    actor,post,_,_=ops;accountant()
    with db.tx() as c:
        sid=duty(c,'overdue-open','2026-01-01',manager_status='open',driver_status='open')
        c.execute("UPDATE shifts SET closed_at=NULL,scheduled_end_at='2026-01-02T04:00:00+03' WHERE id=ANY(%s)",([sid,'test-manager-overdue-open'],))
    cards=actor('accountant').get('/api/accounting/manager-cards').json()['cards']
    card=next(r for r in cards if r['id']=='test-manager-overdue-open')
    assert not card['operationally_closed'] and not card['financial_ledger_closed']
    assert card['manager_completion_overdue'] and card['overdue']
    assert {'reason':'manager_shift_open','responsible':'delivery_manager'} in card['blockers']
    assert card['pending_drivers'][0]['responsible']=='delivery_manager'
    assert not card['ready'] and not card['drivers']
    post('accountant','/api/accounting/manager-cards/'+card['id']+'/approve-day',{'note':'TEST cannot approve an open duty'},status=409)
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(card['id'],)).fetchone()['status']=='open'
        assert not c.execute('SELECT 1 FROM accounting_daily_closures WHERE manager_shift_id=%s',(card['id'],)).fetchone()


@pytest.mark.postgres
def test_overdue_open_manager_does_not_hide_existing_child_report(ops):
    actor,_,_,_=ops;accountant();snapshot,_=make_report()
    with db.tx() as c:
        sid=duty(c,'open-with-report','2026-01-01',manager_status='open')
        c.execute("UPDATE shifts SET closed_at=NULL,scheduled_end_at=now()-interval '1 day' WHERE id='test-manager-open-with-report'")
        snapshot['shift']['id']=sid
        for event in snapshot['custody_events']:event['shift_id']=sid
        c.execute("""INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot)
          VALUES('test-open-parent-report',%s,'driver','ops-brand','["ops-branch"]','ops-driver',%s)""",(sid,db.json(snapshot)))
    card=next(r for r in actor('accountant').get('/api/accounting/manager-cards').json()['cards'] if r['id']=='test-manager-open-with-report')
    assert not card['operationally_closed'] and not card['ready']
    assert [r['id'] for r in card['drivers']]==['test-open-parent-report']
    assert not card['pending_drivers']
    assert card['drivers'][0]['system_total_halalas']==15000
    with db.tx() as c:
        assert c.execute("SELECT snapshot FROM closure_reports WHERE id='test-open-parent-report'").fetchone()['snapshot']==snapshot
