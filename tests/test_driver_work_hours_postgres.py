"""Explicit trips retain task authorization and never infer a branch return."""
import pytest
from app import db
from test_operations import ops

@pytest.mark.postgres
def test_trip_multiorder_scope_replay_and_confirmed_return(ops):
    actor,post,order,shifts=ops
    manager,sid=shifts()
    ids=[order(),order()]
    with db.tx() as c:
        for i,oid in enumerate(ids):
            c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,picked_up_at) VALUES(%s,%s,'ops-driver',%s,'picked_up','ops-manager',now())",('hours-task-'+str(i),oid,sid))
    body={'shift_id':sid,'branch_id':'ops-branch','order_ids':ids}
    started=post('driver','/api/driver-work-hours/depart',body,key='hours-depart')
    assert post('driver','/api/driver-work-hours/depart',body,key='hours-depart')==started
    post('driver','/api/driver-work-hours/depart',body,status=409)
    tid=started['trip']['id']
    post('owner','/api/shifts/'+sid+'/closing',status=409)
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='open'
    post('driver2','/api/driver-work-hours/'+tid+'/return',{'branch_id':'ops-branch'},status=404)
    post('driver','/api/driver-work-hours/'+tid+'/return',{'branch_id':'ops-branch-other'},status=403)
    post('driver','/api/driver-work-hours/'+tid+'/return',{'branch_id':'ops-branch'},status=409)
    r=actor('owner').get('/api/driver-work-hours/report')
    assert r.status_code==200,r.text
    row=r.json()['drivers'][0]
    assert row['pending_trip_count']==1 and row['delivery_seconds']==0
    assert row['trips'][0]['duration_seconds'] is None
    with db.tx() as c:c.execute("UPDATE driver_tasks SET status='delivered',delivered_at=now() WHERE order_id=ANY(%s)",(ids,))
    result=post('driver','/api/driver-work-hours/'+tid+'/return',{'branch_id':'ops-branch'},key='hours-return')
    assert result['trip']['returned_at']
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_work_trips').fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM driver_work_trip_orders').fetchone()['n']==2
        assert c.execute('SELECT count(*) n FROM driver_work_trip_events').fetchone()['n']==2
    assert actor('other').get('/api/driver-work-hours/report?driver_id=ops-driver').status_code==404
    assert actor('driver').get('/api/driver-work-hours/report').status_code==403

@pytest.mark.postgres
def test_operational_setting_read_and_historical_unknown(ops):
    actor,post,order,shifts=ops
    _,sid=shifts()
    oid=order()
    with db.tx() as c:
        c.execute("UPDATE brands SET settings=settings||'{\"operational_day_start\":\"05:30\"}'::jsonb WHERE id='ops-brand'")
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,picked_up_at,delivered_at) VALUES('historical-hours',%s,'ops-driver',%s,'delivered','ops-manager',now(),now())",(oid,sid))
    r=actor('owner').get('/api/driver-work-hours/report')
    assert r.status_code==200,r.text
    report=r.json()
    assert report['operational_day_start']=='05:30'
    row=report['drivers'][0]
    assert row['missing_trip_evidence'] and row['waiting_seconds']==0 and row['trip_count']==0
    assert row['delivery_seconds']==0

@pytest.mark.postgres
def test_authorized_task_transfer_keeps_each_driver_trip_evidence(ops):
    actor,post,order,shifts=ops
    manager,sid=shifts();oid=order()
    with db.tx() as c:
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,picked_up_at) VALUES('hours-transfer-old',%s,'ops-driver',%s,'picked_up','ops-manager',now())",(oid,sid))
    first=post('driver','/api/driver-work-hours/depart',{'shift_id':sid,'branch_id':'ops-branch','order_ids':[oid]})
    second_shift=post('driver2','/api/shifts',{'kind':'driver','branch_ids':['ops-branch'],'manager_shift_id':manager})['shift']['id']
    # Canonical handover creates a terminal original task and a new picked-up task.
    with db.tx() as c:
        c.execute("UPDATE driver_tasks SET status='transferred' WHERE id='hours-transfer-old'")
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,picked_up_at) VALUES('hours-transfer-new',%s,'ops-driver2',%s,'picked_up','ops-manager',now())",(oid,second_shift))
    second=post('driver2','/api/driver-work-hours/depart',{'shift_id':second_shift,'branch_id':'ops-branch','order_ids':[oid]})
    assert first['trip']['id']!=second['trip']['id']
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_work_trip_orders WHERE order_id=%s',(oid,)).fetchone()['n']==2
