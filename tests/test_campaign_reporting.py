from datetime import date, datetime, timezone
import pytest
from fastapi import HTTPException
from app import campaign_reporting as reports


def test_riyadh_calendar_period_is_half_open():
    start,end=reports.period(date(2026,10,22),date(2026,10,22))
    assert start.astimezone(timezone.utc)==datetime(2026,10,21,21,tzinfo=timezone.utc)
    assert end.astimezone(timezone.utc)==datetime(2026,10,22,21,tzinfo=timezone.utc)


@pytest.mark.parametrize('start,end',[(date(2026,10,23),date(2026,10,22)),(date(2026,1,1),date(2026,4,4)),(date.max,date.max)])
def test_invalid_or_unbounded_period_rejected(start,end):
    with pytest.raises(HTTPException) as error:reports.period(start,end)
    assert error.value.status_code==422


@pytest.mark.parametrize('role',['customer','driver','branch_manager','delivery_staff','admin'])
def test_non_reporting_roles_fail_before_database_read(role):
    with pytest.raises(HTTPException) as error:
        reports.report_scope(None,{'role':role,'brand_id':'kps'},None)
    assert error.value.status_code==403


def test_denied_manager_and_morning_history_fail_closed():
    for fields in ({'permission_denials':['reports_view']},{'_morning_manager_context':{'shift_id':'morning'}}):
        with pytest.raises(HTTPException) as error:
            reports.report_scope(None,{'role':'delivery_manager','brand_id':'kps',**fields},None)
        assert error.value.status_code==403
