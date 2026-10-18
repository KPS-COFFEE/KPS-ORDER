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


@pytest.mark.postgres
def test_operational_boundary_multiorder_cross_branch_trip_reconciles(ops):
    actor, post, order, shifts = ops
    _, sid = shifts()
    orders = [order(), order()]
    with db.tx() as c:
        c.execute("UPDATE brands SET settings=settings||'{\"operational_day_start\":\"05:30\"}'::jsonb WHERE id='ops-brand'")
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city) VALUES('hours-return-branch',2,'hours-return-source','ops-brand','فرع العودة','حفر الباطن')")
        c.execute("UPDATE shifts SET opened_at='2026-10-17 05:00+03',submitted_at='2026-10-17 07:00+03',branch_ids='[\"ops-branch\",\"hours-return-branch\"]'::jsonb WHERE id=%s", (sid,))
        c.execute("INSERT INTO driver_work_trips(id,brand_id,shift_id,driver_id,departure_branch_id,return_branch_id,departed_at,returned_at,departure_evidence,return_evidence) VALUES('boundary-trip','ops-brand',%s,'ops-driver','ops-branch','hours-return-branch','2026-10-17 05:15+03','2026-10-17 06:00+03','{\"source\":\"synthetic_test\"}','{\"source\":\"synthetic_test\"}')", (sid,))
        for index, oid in enumerate(orders):
            c.execute("INSERT INTO driver_work_trip_orders(trip_id,order_id) VALUES('boundary-trip',%s)", (oid,))
            c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,picked_up_at,delivered_at) VALUES(%s,%s,'ops-driver',%s,'delivered','ops-manager','2026-10-17 05:15+03','2026-10-17 05:45+03')", ('boundary-task-'+str(index),oid,sid))
    reports = []
    for start, end in [('2026-10-16','2026-10-16'),('2026-10-17','2026-10-17'),('2026-10-16','2026-10-17')]:
        result = actor('owner').get('/api/driver-work-hours/report', params={'start':start,'end':end})
        assert result.status_code == 200, result.text
        reports.append(result.json())
    earlier, later, combined = reports
    assert earlier['totals']['delivery_seconds'] == 900
    assert later['totals']['delivery_seconds'] == 1800
    assert combined['totals']['delivery_seconds'] == 2700
    assert combined['totals']['order_count'] == 2
    assert combined['totals']['trip_count'] == 1
    for key in ('shift_seconds','actual_seconds','delivery_seconds','waiting_seconds','break_seconds'):
        assert earlier['totals'][key] + later['totals'][key] == combined['totals'][key]
    trip = combined['drivers'][0]['trips'][0]
    assert trip['order_count'] == 2 and trip['duration_seconds'] == 2700
    assert trip['departure_branch_id'] == 'ops-branch'
    assert trip['return_branch_id'] == 'hours-return-branch'


@pytest.mark.postgres
def test_real_report_eight_hour_shift_six_hour_orders_and_two_hour_remainder(ops):
    actor,post,order,shifts=ops
    _,sid=shifts()
    ids=[order(),order()]
    with db.tx() as c:
        c.execute("UPDATE brands SET settings=settings||'{\"operational_day_start\":\"05:30\"}'::jsonb WHERE id='ops-brand'")
        c.execute("UPDATE shifts SET opened_at='2026-10-17 16:00+03',submitted_at='2026-10-18 00:00+03' WHERE id=%s",(sid,))
        for index,(pickup,delivered) in enumerate([('16:00','20:00'),('18:00','22:00')]):
            c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,picked_up_at,delivered_at) VALUES(%s,%s,'ops-driver',%s,'delivered','ops-manager',%s,%s)",
                      ('activity-'+str(index),ids[index],sid,'2026-10-17 '+pickup+'+03','2026-10-17 '+delivered+'+03'))
    response=actor('owner').get('/api/driver-work-hours/report',params={'start':'2026-10-17','end':'2026-10-17'})
    assert response.status_code==200,response.text
    report=response.json();d=report['drivers'][0]
    assert d['shift_seconds']==28800
    assert d['order_activity_seconds']==21600
    assert d['remaining_shift_seconds']==7200
    assert d['order_utilization_percent']==75
    assert d['order_count']==2
    # Historic branch return is still unknown; task timestamps prove only pickup-to-delivery.
    assert d['delivery_seconds']==0 and d['missing_trip_evidence']
    assert report['totals']['order_activity_seconds']==21600
    assert report['totals']['order_utilization_percent']==75
