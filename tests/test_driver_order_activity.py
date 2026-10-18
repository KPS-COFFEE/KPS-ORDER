"""Candidate-only completed-order activity; prior-image contracts stay in their existing suites."""
import pytest
from app import db
from tests.test_operations import ops
from tests.test_driver_work_hours_unit import at, shift


def test_eight_hour_shift_six_hours_completed_delivery_without_double_counting():
    from app.driver_work_hours import order_activity
    tasks=[dict(order_id='a',status='delivered',picked_up_at=at(0),delivered_at=at(240)),
           dict(order_id='b',status='delivered',picked_up_at=at(120),delivered_at=at(360))]
    m=order_activity([shift(0,480)],tasks,[],at(0),at(480),at(480))
    assert m['order_activity_seconds']==21600
    assert m['remaining_shift_seconds']==7200
    assert m['order_utilization_percent']==75


def test_activity_clips_days_breaks_and_separates_active_and_missing_timestamps():
    from app.driver_work_hours import order_activity
    tasks=[dict(order_id='a',status='delivered',picked_up_at=at(-60),delivered_at=at(90)),
           dict(order_id='b',status='problem',picked_up_at=at(60),delivered_at=None),
           dict(order_id='missing',status='delivered',picked_up_at=None,delivered_at=at(110))]
    m=order_activity([shift()],tasks,[(at(30),at(60))],at(0),at(120),at(120))
    assert m['order_activity_seconds']==3600
    assert m['active_delivery_seconds']==1800
    assert m['remaining_shift_seconds']==1800
    assert m['missing_order_timing_count']==1
    assert m['order_utilization_percent']==50


def test_no_orders_does_not_claim_a_driver_was_on_break():
    from app.driver_work_hours import order_activity
    m=order_activity([shift()],[],[],at(0),at(120),at(120))
    assert m['remaining_shift_seconds']==7200
    assert m['order_activity_seconds']==m['active_delivery_seconds']==0


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
