"""Real report SQL for accepted-to-return work, never synthetic attendance."""
import pytest
from app import db
from test_operations import ops


def setup(shifts):
    _,sid=shifts()
    with db.tx() as c:
        c.execute("UPDATE brands SET settings=settings||'{\"operational_day_start\":\"16:00\"}'::jsonb WHERE id='ops-brand'")
        c.execute("UPDATE shifts SET opened_at='2026-10-27 16:00+03',scheduled_end_at='2026-10-28 04:00+03',submitted_at='2026-10-28 04:00+03',status='submitted' WHERE id=%s",(sid,))
    return sid


def report(actor):
    r=actor('owner').get('/api/driver-work-hours/report',params={'start':'2026-10-27','end':'2026-10-27'})
    assert r.status_code==200,r.text
    return r.json()


def evidence(c,sid,oid,key,accepted,departed,returned,pickup=None,status='delivered'):
    c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,accepted_at,picked_up_at,delivered_at) VALUES(%s,%s,'ops-driver',%s,%s,'ops-manager',%s,%s,%s)",
              ('task-'+key,oid,sid,status,accepted,pickup or departed,returned))
    c.execute("INSERT INTO driver_work_trips(id,brand_id,shift_id,driver_id,departure_branch_id,return_branch_id,departed_at,returned_at,departure_evidence,return_evidence) VALUES(%s,'ops-brand',%s,'ops-driver','ops-branch','ops-branch',%s,%s,'{}','{\"source\":\"driver_explicit_confirmation\"}')",
              ('trip-'+key,sid,departed,returned))
    c.execute('INSERT INTO driver_work_trip_orders(trip_id,order_id) VALUES(%s,%s)',('trip-'+key,oid))


@pytest.mark.postgres
def test_twelve_hour_duty_seven_and_half_actual_includes_preparation_and_return_without_overlap(ops):
    actor,post,order,shifts=ops;sid=setup(shifts);a,b=order(),order()
    with db.tx() as c:
        evidence(c,sid,a,'a','2026-10-27 16:00+03','2026-10-27 16:30+03','2026-10-27 20:00+03')
        evidence(c,sid,b,'b','2026-10-27 19:00+03','2026-10-27 20:15+03','2026-10-27 23:30+03')
    d=report(actor);row=d['drivers'][0]
    assert row['actual_seconds']==7.5*3600
    assert row['duty_seconds']==12*3600 and row['duty_basis']=='scheduled'
    assert row['work_utilization_percent']==62.5
    assert row['non_delivery_seconds']==4.5*3600
    assert row['active_work_seconds']==0 and not row['work_review_required']
    assert d['totals']['actual_seconds']==row['actual_seconds']
    assert d['totals']['work_utilization_percent']==62.5
    from datetime import datetime
    from zoneinfo import ZoneInfo
    assert datetime.fromisoformat(row['work_trip_evidence'][0]['accepted_at']).astimezone(ZoneInfo('Asia/Riyadh')).hour==16
    # Report reads never alter staff release/financial workflow timestamps.
    with db.tx() as c:assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='submitted'


@pytest.mark.postgres
def test_late_manager_approval_and_missing_gps_do_not_change_mission_hours(ops):
    actor,post,order,shifts=ops;sid=setup(shifts);oid=order()
    with db.tx() as c:evidence(c,sid,oid,'a','2026-10-27 16:00+03','2026-10-27 17:00+03','2026-10-27 20:00+03')
    before=report(actor)['drivers'][0]
    with db.tx() as c:c.execute("UPDATE shifts SET closed_at='2026-10-28 12:00+03',staffing_ended_at='2026-10-28 12:00+03',available=false WHERE id=%s",(sid,))
    after=report(actor)['drivers'][0]
    assert before['actual_seconds']==after['actual_seconds']==4*3600
    assert after['work_utilization_percent']==before['work_utilization_percent']


@pytest.mark.postgres
def test_missing_acceptance_never_uses_pickup_as_invented_start(ops):
    actor,post,order,shifts=ops;sid=setup(shifts);oid=order()
    with db.tx() as c:evidence(c,sid,oid,'a',None,'2026-10-27 17:00+03','2026-10-27 20:00+03')
    row=report(actor)['drivers'][0]
    assert row['actual_seconds']==0 and row['work_review_required']
    assert 'missing_acceptance_timing' in row['work_review_reasons']


@pytest.mark.postgres
def test_missing_pickup_and_return_cannot_certify_zero_work(ops):
    actor,post,order,shifts=ops;sid=setup(shifts);oid=order()
    with db.tx() as c:c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,accepted_at,delivered_at) VALUES('unlinked',%s,'ops-driver',%s,'delivered','ops-manager','2026-10-27 16:00+03','2026-10-27 20:00+03')",(oid,sid))
    row=report(actor)['drivers'][0]
    assert row['work_review_required'] and row['work_clock_status']=='needs_review'
    assert 'missing_return_evidence' in row['work_review_reasons']


@pytest.mark.postgres
def test_configured_duty_and_remaining_share_one_denominator(ops):
    actor,post,order,shifts=ops;sid=setup(shifts);oid=order()
    with db.tx() as c:
        c.execute("UPDATE shifts SET submitted_at='2026-10-27 20:00+03' WHERE id=%s",(sid,))
        evidence(c,sid,oid,'a','2026-10-27 16:00+03','2026-10-27 17:00+03','2026-10-27 19:00+03')
    row=report(actor)['drivers'][0]
    assert row['shift_seconds']==4*3600 and row['duty_seconds']==12*3600
    assert row['actual_seconds']==3*3600 and row['non_delivery_seconds']==9*3600
    assert row['work_utilization_percent']==25


@pytest.mark.postgres
def test_work_outside_shift_is_preserved_and_flagged_even_when_shorter_than_shift(ops):
    actor,post,order,shifts=ops;sid=setup(shifts);oid=order()
    with db.tx() as c:
        c.execute("UPDATE shifts SET submitted_at='2026-10-27 18:00+03' WHERE id=%s",(sid,))
        evidence(c,sid,oid,'a','2026-10-27 17:40+03','2026-10-27 18:00+03','2026-10-27 19:00+03')
    row=report(actor)['drivers'][0]
    assert row['actual_seconds']==80*60
    assert row['work_review_required'] and 'work_outside_shift_evidence' in row['work_review_reasons']


@pytest.mark.postgres
def test_acceptance_before_day_boundary_counts_even_when_departure_is_next_day(ops):
    actor,post,order,shifts=ops;sid=setup(shifts);oid=order()
    with db.tx() as c:
        c.execute("UPDATE shifts SET submitted_at='2026-10-28 17:00+03' WHERE id=%s",(sid,))
        evidence(c,sid,oid,'a','2026-10-28 15:50+03','2026-10-28 16:10+03','2026-10-28 17:00+03')
    row=report(actor)['drivers'][0]
    assert row['actual_seconds']==10*60


@pytest.mark.postgres
@pytest.mark.parametrize('status',['transferred','reassigned','cancelled'])
def test_later_handover_or_cancellation_does_not_erase_confirmed_driver_work(ops,status):
    actor,post,order,shifts=ops;sid=setup(shifts);oid=order()
    with db.tx() as c:
        evidence(c,sid,oid,'a','2026-10-27 16:00+03','2026-10-27 17:00+03','2026-10-27 20:00+03',status=status)
    row=report(actor)['drivers'][0]
    assert row['actual_seconds']==4*3600
    assert not row['work_review_required']


@pytest.mark.postgres
def test_inconsistent_foreign_order_link_is_not_certified_or_exposed(ops):
    actor,post,order,shifts=ops;sid=setup(shifts);oid=order(branch='ops-branch-other')
    with db.tx() as c:
        evidence(c,sid,oid,'a','2026-10-27 16:00+03','2026-10-27 17:00+03','2026-10-27 20:00+03')
    row=report(actor)['drivers'][0]
    assert row['actual_seconds']==0 and row['work_review_required']
    assert not row['work_trip_evidence'] and not row['trips']
    assert 'ops-branch-other' not in str(row)
