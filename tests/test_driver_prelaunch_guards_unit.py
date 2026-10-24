from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
import pytest
from fastapi import HTTPException
from app import driver_closure, driver_readiness

NOW=datetime(2026,10,17,12,tzinfo=timezone.utc)

def shift(**overrides):
    return {'id':'s','kind':'driver','status':'open','scheduled_end_at':None,**overrides}

def test_individual_schedule_is_required_and_delivery_window_is_not_used():
    s=shift(delivery_window_snapshot={'window_end':'2026-10-17T04:00:00+03:00'})
    assert driver_closure.needs_approval(s,NOW)
    assert driver_closure.needs_approval(shift(scheduled_end_at=NOW+timedelta(hours=1)),NOW)
    assert not driver_closure.needs_approval(shift(scheduled_end_at=NOW),NOW)

def test_missing_rejected_or_stale_approval_never_begins_closure():
    c=Mock();c.execute.return_value.fetchone.return_value={'n':NOW}
    for approval in (None,{'status':'requested'},{'status':'rejected'},
                     {'status':'approved','scheduled_end_at':'changed'}):
        with pytest.raises(HTTPException) as exc:
            driver_closure.guard(c,{'id':'driver'},shift(closure_authorization=approval))
        assert exc.value.status_code==409
        assert exc.value.detail['code']=='EARLY_CLOSURE_APPROVAL_REQUIRED'

def test_approval_rechecks_approver_and_does_not_change_shift(monkeypatch):
    c=Mock();c.execute.return_value.fetchone.side_effect=[{'n':NOW},{'id':'m','role':'delivery_manager','active':True}]
    check=Mock();monkeypatch.setattr(driver_closure,'manager',check)
    s=shift(closure_authorization={'status':'approved','scheduled_end_at':None,'decided_by':'m'})
    driver_closure.guard(c,{'id':'d'},s)
    check.assert_called_once()
    assert s['status']=='open'
    assert all('UPDATE' not in call.args[0] for call in c.execute.call_args_list)

def test_revoked_approval_does_not_allow_early_closure():
    c=Mock();c.execute.return_value.fetchone.side_effect=[{'n':NOW},{'role':'delivery_manager','active':False}]
    with pytest.raises(HTTPException):
        driver_closure.guard(c,{},shift(closure_authorization={'status':'approved','scheduled_end_at':None,'decided_by':'m'}))

def test_closed_history_does_not_need_new_approval():
    c=Mock();driver_closure.guard(c,{},shift(status='closed'))
    c.execute.assert_not_called()

def roster(**overrides):
    return {'active':True,'shift_id':'s','shift_status':'open','active_tasks':0,'branch_ids':['b'],**overrides}

def test_roster_inactive_and_no_shift_never_claim_available():
    c=Mock()
    assert driver_readiness.describe(c,roster(active=False))[0]=='inactive'
    assert driver_readiness.describe(c,roster(shift_id=None))[0]=='no_active_shift'
    assert driver_readiness.describe(c,roster(shift_status='closing'))[0]=='closing_or_closed'
    c.execute.assert_not_called()

def test_roster_fuel_pending_distinct_from_refuel_requirement():
    c=Mock();c.execute.return_value.fetchone.side_effect=[{'id':'s','vehicle_log':{'awaiting_refuel':True}}, {'exists':1}]
    assert driver_readiness.describe(c,roster())==('fuel_exception_pending','manager_review_fuel_exception',[])
    c.execute.return_value.fetchone.side_effect=[{'id':'s','vehicle_log':{'awaiting_refuel':True}}, None]
    assert driver_readiness.describe(c,roster())[0]=='waiting_for_fuel'

def test_available_requires_canonical_assignment_guard_for_branch(monkeypatch):
    from app import operations
    c=Mock();s={'id':'s','brand_id':'kps','vehicle_log':{},'available':True}
    c.execute.return_value.fetchone.side_effect=[s,None]
    guard=Mock(side_effect=HTTPException(409,'manager unavailable'))
    monkeypatch.setattr(operations,'eligible_driver_shift',guard)
    assert driver_readiness.describe(c,roster())[0]=='manager_or_scope_not_ready'
    guard.assert_called_once_with(c,s,{'brand_id':'kps','branch_id':'b'})
    c.execute.return_value.fetchone.side_effect=[s,None];guard.side_effect=None
    assert driver_readiness.describe(c,roster())==('available','none',['b'])
