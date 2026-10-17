from datetime import datetime
from app import manager_workday,service_hours

def test_operational_day_boundaries_are_saudi_overnight():
    for hour,day in [(16,13),(23,13),(0,12),(4,12),(15,12)]:
        value=datetime(2026,10,13,hour,tzinfo=service_hours.RIYADH)
        assert service_hours.delivery_operational_date(value).day==day

def test_current_query_uses_shift_identity_and_not_calendar_day():
    assert manager_workday.current_orders(None,{'id':'manager'})==('FALSE',[])
    shift={'id':'shift','branch_ids':['branch'],'opened_at':'2026-10-13T16:00:00+03:00'}
    sql,params=manager_workday.current_orders(shift,{'id':'manager'})
    assert params==['shift',['branch'],'manager',shift['opened_at']]
    assert 'ws.manager_shift_id=%s' in sql
    assert 'o.created_at' not in sql
    assert 'o.branch_id=ANY(%s)' in sql
