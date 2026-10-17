from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from app import db
from test_operations import ops

@pytest.mark.postgres
def test_driver_cannot_bypass_closure_authorization_using_direct_submit_or_delegation(ops):
    actor,post,order,shifts=ops;manager,sid=shifts()
    body={'terminal_total_halalas':0,'counted_halalas':0,'handover_halalas':0,'retained_halalas':0}
    response=actor('driver').post(f'/api/shifts/{sid}/submit',json=body,headers={'Idempotency-Key':uuid4().hex})
    assert response.status_code==409,response.text
    body={'terminal_total_halalas':0,'counted_halalas':0,'received_halalas':0,'confirmed':True,'exception_reason':'Attempted bypass'}
    response=actor('driver').post(f'/api/shifts/{sid}/driver-close',json=body,headers={'Idempotency-Key':uuid4().hex})
    assert response.status_code==403,response.text
    with db.tx() as c:
        s=c.execute('SELECT status,submission FROM shifts WHERE id=%s',(sid,)).fetchone()
        assert s['status']=='open' and s['submission'] is None
        assert c.execute('SELECT count(*) n FROM closure_reports WHERE shift_id=%s',(sid,)).fetchone()['n']==0

@pytest.mark.postgres
def test_schedule_change_invalidates_early_approval_and_approver_revocation_blocks_transition(ops):
    actor,post,order,shifts=ops;manager,sid=shifts()
    first=post('driver',f'/api/driver-closure/{sid}/request',{'reason':'Early departure'})
    post('manager',f'/api/driver-closure/{sid}/decision',{'decision':'approved','request_id':first['authorization']['id'],'reason':'Approved'})
    response=actor('manager').put(f'/api/driver-closure/{sid}/schedule',json={'scheduled_end_at':(datetime.now(timezone.utc)+timedelta(hours=8)).isoformat(),'reason':'Individual shift roster'},headers={'Idempotency-Key':uuid4().hex})
    assert response.status_code==200,response.text
    post('driver',f'/api/shifts/{sid}/closing',status=409)
    request=post('driver',f'/api/driver-closure/{sid}/request',{'reason':'Emergency'})
    post('manager',f'/api/driver-closure/{sid}/decision',{'decision':'approved','request_id':request['authorization']['id'],'reason':'Approved emergency'})
    with db.tx() as c:c.execute("UPDATE users SET active=false WHERE id='ops-manager'")
    post('driver',f'/api/shifts/{sid}/closing',status=409)
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='open'
