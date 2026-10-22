"""Per-driver deadlines, independent completion, and one audited extension."""
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from app import db
from app.accounting_statements import closure_deadline
from test_accounting import accountant
from test_driver_accounting_settlements import make_report
from test_operations import ops

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
