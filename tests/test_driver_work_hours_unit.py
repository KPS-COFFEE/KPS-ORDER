from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from app.driver_work_hours import metrics,union,subtract,Movement
Z=ZoneInfo('Asia/Riyadh')
T=datetime(2026,10,18,16,tzinfo=Z)
def at(m):return T+timedelta(minutes=m)
def shift(a=0,b=120):return {'opened_at':at(a),'closed_at':at(b)}
def trip(a,b,**kw):return {'departed_at':at(a),'returned_at':at(b) if b is not None else None,**kw}
def calc(ss=None,bs=(),ts=(),lo=0,hi=120,unknown=(),stops=()):return metrics(ss or [shift()],bs,ts,at(lo),at(hi),at(120),stops,unknown)
def test_two_orders_one_trip_one_hour():
    m=calc(ts=[trip(0,60,order_count=2)])
    assert m['delivery_seconds']==3600 and m['waiting_seconds']==3600

def test_overlapping_shifts_and_trips_count_once():
    m=calc(ss=[shift(),shift(30,90)],ts=[trip(0,60),trip(30,90)])
    assert m['shift_seconds']==7200 and m['delivery_seconds']==5400

def test_proven_breaks_stops_union_and_ready_waiting():
    m=calc(bs=[(at(30),at(60))],stops=[(at(45),at(75))])
    assert m['actual_seconds']==4500 and m['break_seconds']==2700 and m['waiting_seconds']==4500

def test_pending_return_excluded_from_delivery_and_waiting():
    m=calc(ts=[trip(30,None)])
    assert m['delivery_seconds']==0 and m['waiting_seconds']==1800 and m['unclassified_seconds']==5400

def test_operational_day_boundary_splits_without_duplication():
    ts=[trip(-30,30)]
    a=calc(ss=[shift(-60,60)],ts=ts,lo=-60,hi=0)
    b=calc(ss=[shift(-60,60)],ts=ts,lo=0,hi=60)
    assert a['delivery_seconds']==b['delivery_seconds']==1800
    assert a['shift_seconds']+b['shift_seconds']==7200
    assert at(0).utcoffset()==timedelta(hours=3)

def test_historical_missing_return_never_invented_waiting():
    m=calc(unknown=[(at(0),at(120))])
    assert m['waiting_seconds']==0 and m['unclassified_seconds']==7200

def test_break_delivery_overlap_never_double_counts():
    m=calc(ts=[trip(0,90)],bs=[(at(30),at(60))])
    assert m['delivery_seconds']==3600 and m['actual_seconds']==5400
    assert m['delivery_seconds']+m['waiting_seconds']==m['actual_seconds']

@pytest.mark.parametrize('data',[{'branch_id':'x','lat':float('nan')},{'branch_id':'x','lat':91},{'branch_id':'x','lng':181},{'branch_id':'x','server_time':'fake'}])
def test_untrusted_coordinates_or_timestamp_rejected(data):
    with pytest.raises(ValueError):Movement(**data)

def test_submitted_shift_ends_work_before_later_financial_approval():
    s=shift(0,120);s['submitted_at']=at(60)
    assert calc(ss=[s])['shift_seconds']==3600

def test_close_guard_requires_return_before_financial_close():
    from app.driver_work_hours import guard_shift_completion
    from fastapi import HTTPException
    class Connection:
        def execute(self,*args):return self
        def fetchone(self):return {'exists':True}
    with pytest.raises(HTTPException) as e:guard_shift_completion(Connection(),{'kind':'driver','id':'shift'})
    assert e.value.status_code==409
    guard_shift_completion(Connection(),{'kind':'manager','id':'shift'})


def test_empty_report_scope_never_authorizes_historical_shift_or_driver():
    from app.driver_work_hours import report_scope_contains
    assert not report_scope_contains([], set())
    assert not report_scope_contains([], {'branch-a'})
    assert not report_scope_contains(['branch-a'], set())
    assert not report_scope_contains(['branch-a','branch-b'], {'branch-a'})
    assert report_scope_contains(['branch-a'], {'branch-a','branch-b'})
def test_transferred_order_preserves_each_drivers_actual_work():
    # Same order legitimately rides two drivers' separately evidenced trips.
    first=calc(ts=[trip(0,45,order_ids=['transferred-order'])])
    second=calc(ts=[trip(30,90,order_ids=['transferred-order'])])
    assert first['delivery_seconds']==2700
    assert second['delivery_seconds']==3600
    assert first['delivery_seconds']+second['delivery_seconds']==6300


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
