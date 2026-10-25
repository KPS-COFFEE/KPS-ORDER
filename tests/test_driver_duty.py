from uuid import uuid4
import pytest
from app import db
from app.driver_duty import Duration
from pydantic import ValidationError
from test_operations import ops

@pytest.mark.parametrize('minutes',[True,0,29,1441,60.5,'480'])
def test_duration_rejects_invalid_input(minutes):
    with pytest.raises(ValidationError):Duration(minutes=minutes,reason='Owner set schedule')

@pytest.mark.postgres
def test_duty_owner_delegation_snapshot_and_replay(ops):
    actor,post,order,shifts=ops
    url='/api/driver-duty/driver'
    body={'minutes':480,'reason':'Owner sets eight hour duty'}
    def put(who,payload,status=200,key=None):
        response=actor(who).put(url,json=payload,headers={'Idempotency-Key':key or uuid4().hex})
        assert response.status_code==status,response.text
        return response.json()
    put('manager',body,403)
    put('driver',body,403)
    key=uuid4().hex
    assert put('owner',body,key=key)==put('owner',body,key=key)
    manager,driver=shifts()
    with db.tx() as c:
        row=c.execute('SELECT opened_at,scheduled_end_at FROM shifts WHERE id=%s',(driver,)).fetchone()
        assert (row['scheduled_end_at']-row['opened_at']).total_seconds()==480*60
        c.execute("UPDATE users SET extra_permissions='[\"driver_duty_manage\"]'::jsonb WHERE id='manager'")
    put('manager',{'minutes':360,'reason':'General manager changes future duty'})
    with db.tx() as c:
        assert c.execute('SELECT scheduled_end_at FROM shifts WHERE id=%s',(driver,)).fetchone()['scheduled_end_at']==row['scheduled_end_at']
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='driver_duty.configured' AND entity_id='driver'").fetchone()['n']==2
    assert actor('manager').get('/api/driver-duty').status_code==200
