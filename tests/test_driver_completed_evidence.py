"""Candidate-only completed-order evidence; historical app contracts remain separate."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from app import db
from test_operations import ops
Z=ZoneInfo('Asia/Riyadh')
T=datetime(2026,10,18,16,tzinfo=Z)
def at(m):return T+timedelta(minutes=m)
def shift(a=0,b=120):return {'opened_at':at(a),'closed_at':at(b)}


def test_completed_orders_retained_without_inventing_return_or_lateness():
    from app.driver_work_hours import completed_order_evidence
    tasks=[{'order_id':'a','status':'delivered','picked_up_at':at(10),'delivered_at':at(30)},
           {'order_id':'b','status':'delivered','picked_up_at':None,'delivered_at':at(40)},
           {'order_id':'c','status':'cancelled','picked_up_at':at(0),'delivered_at':at(50)}]
    report=completed_order_evidence(tasks,at(0),at(120),at(120))
    assert len(report['completed_orders'])==2
    assert report['completed_orders'][0]['delivery_seconds']==1200
    assert report['completed_orders'][0]['returned_at'] is None
    assert report['performance']=={'completed_order_count':2,'timed_order_count':1,'missing_pickup_count':1,
        'punctuality_available':False,'punctuality_unavailable_reason':'no_recorded_delivery_promise',
        'on_time_count':None,'late_count':None,'gps_gaps_affect_performance':False}


def test_completed_order_actual_return_distinct_from_declared_intent():
    from app.driver_work_hours import completed_order_evidence
    task={'order_id':'a','status':'delivered','picked_up_at':at(10),'delivered_at':at(30),
          'trip_id':'trip','departed_at':at(11),'returned_at':at(60),'return_branch_id':'branch',
          'next_destination':{'kind':'branch','branch_id':'branch','source':'driver_declared_intent'}}
    row=completed_order_evidence([task],at(0),at(120),at(120))['completed_orders'][0]
    assert row['returned_at']==at(60) and row['next_destination']['source']=='driver_declared_intent'
    task['returned_at']=at(20)
    assert completed_order_evidence([task],at(0),at(120),at(120))['completed_orders'][0]['returned_at'] is None


def test_completed_order_period_dedup_and_union_activity_no_parallel_double_count():
    from app.driver_work_hours import completed_order_evidence, order_activity
    a={'order_id':'a','status':'delivered','picked_up_at':at(0),'delivered_at':at(60)}
    b={'order_id':'b','status':'delivered','picked_up_at':at(30),'delivered_at':at(90)}
    future={'order_id':'future','status':'delivered','picked_up_at':at(30),'delivered_at':at(150)}
    assert completed_order_evidence([a,a,b,future],at(0),at(180),at(120))['performance']['completed_order_count']==2
    assert order_activity([shift()],[a,b],[],at(0),at(120),at(120))['order_activity_seconds']==5400
    assert completed_order_evidence([a],at(60),at(120),at(120))['performance']['completed_order_count']==1
    assert completed_order_evidence([a],at(0),at(60),at(120))['performance']['completed_order_count']==0


@pytest.mark.postgres
def test_completed_history_trip_return_and_scoped_destination(ops):
    actor,post,order,shifts=ops
    _,sid=shifts()
    ids=[order(),order()]
    with db.tx() as c:
        for i,oid in enumerate(ids):
            c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,picked_up_at) VALUES(%s,%s,'ops-driver',%s,'picked_up','ops-manager',now())",('completed-task-'+str(i),oid,sid))
    started=post('driver','/api/driver-work-hours/depart',{'shift_id':sid,'branch_id':'ops-branch','order_ids':ids})
    tid=started['trip']['id']
    with db.tx() as c:
        c.execute("UPDATE driver_tasks SET status='delivered',delivered_at=now() WHERE order_id=ANY(%s)",(ids,))
    result=post('driver','/api/driver-work-hours/'+tid+'/return',{'branch_id':'ops-branch'})
    report=actor('owner').get('/api/driver-work-hours/report').json()
    completed=next(d for d in report['drivers'] if d['driver_id']=='ops-driver')
    assert completed['performance']['completed_order_count']==2
    assert completed['performance']['on_time_count'] is None
    assert {o['order_id'] for o in completed['completed_orders']}==set(ids)
    assert all(o['returned_at']==result['trip']['returned_at'] for o in completed['completed_orders'])
    assert all(o['next_destination'] is None for o in completed['completed_orders'])
    with db.tx() as c:
        db.audit(c,'ops-driver','driver.next_destination','shift',sid,{'kind':'branch','branch_id':'ops-branch'})
        db.audit(c,'ops-driver2','driver.next_destination','shift',sid,{'kind':'external_enbishan'})
    completed=next(d for d in actor('owner').get('/api/driver-work-hours/report').json()['drivers'] if d['driver_id']=='ops-driver')
    assert all(o['next_destination']['branch_id']=='ops-branch' for o in completed['completed_orders'])
    assert all(o['next_destination']['source']=='driver_declared_intent' for o in completed['completed_orders'])
    assert actor('other').get('/api/driver-work-hours/report?driver_id=ops-driver').status_code==404
    assert actor('driver').get('/api/driver-work-hours/report').status_code==403
