"""Real shared-workday report SQL: two ledgers, one physical mission clock."""
import pytest

from app import db
from test_enbishan_dual_brand import dual, shared_dual  # noqa: F401: fixture dependency


@pytest.mark.postgres
def test_shared_mission_uses_first_acceptance_across_legs_once_and_hides_denied_leg(shared_dual):
    (client, actor, _, _, order), _, driver, legs = shared_dual
    native_order, foreign_order = order('kps'), order('enbishan')
    physical_id = driver['shift']['id']
    physical_brand = next(s['brand_id'] for s in driver['shifts'] if s['id'] == physical_id)
    departure_branch = 'kps-branch-1' if physical_brand == 'kps' else 'enbishan-test-1'
    trip_id = 'shared-hours-physical-trip'
    with db.tx() as c:
        c.execute("UPDATE brands SET settings=settings||'{\"operational_day_start\":\"16:00\"}'::jsonb WHERE id=ANY(%s)", (['kps', 'enbishan'],))
        c.execute("""UPDATE shifts SET opened_at='2026-10-27 16:00+03',
            scheduled_end_at='2026-10-28 04:00+03',submitted_at='2026-10-28 04:00+03',
            status='submitted' WHERE id=ANY(%s)""", ([s['id'] for s in driver['shifts']],))
        for brand, oid, accepted, delivered in [
            ('kps', native_order, '2026-10-27 17:00+03', '2026-10-27 18:00+03'),
            ('enbishan', foreign_order, '2026-10-27 16:00+03', '2026-10-27 19:00+03'),
        ]:
            c.execute("""INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,
                accepted_at,picked_up_at,delivered_at) VALUES(%s,%s,'dual-driver',%s,'delivered',
                'dual-manager',%s,'2026-10-27 17:15+03',%s)""",
                ('shared-hours-'+brand, oid, legs[brand][1], accepted, delivered))
        c.execute("""INSERT INTO driver_work_trips(id,brand_id,shift_id,driver_id,
            departure_branch_id,return_branch_id,departed_at,returned_at,departure_evidence,return_evidence)
            VALUES(%s,%s,%s,'dual-driver',%s,'enbishan-test-1','2026-10-27 17:15+03',
            '2026-10-27 20:00+03','{}','{"source":"driver_explicit_confirmation"}')""",
            (trip_id, physical_brand, physical_id, departure_branch))
        for oid in (native_order, foreign_order):
            c.execute('INSERT INTO driver_work_trip_orders(trip_id,order_id) VALUES(%s,%s)', (trip_id, oid))

    def report(brand='kps'):
        response = actor('owner', brand).get('/api/driver-work-hours/report', params={
            'start': '2026-10-27', 'end': '2026-10-27', 'driver_id': 'dual-driver'})
        assert response.status_code == 200, response.text
        data = response.json()
        assert len(data['drivers']) == 1
        return data, data['drivers'][0]

    data, row = report()
    # The earliest acceptance belongs to the OTHER leg, not trip.shift_id.
    assert row['actual_seconds'] == 4 * 3600
    assert row['active_work_seconds'] == 0
    assert row['duty_seconds'] == row['shift_seconds'] == 12 * 3600
    assert row['non_delivery_seconds'] == 8 * 3600
    assert row['work_utilization_percent'] == 33.33
    assert not row['work_review_required'], row['work_review_reasons']
    assert row['trip_count'] == 1 and row['order_count'] == 2
    assert len(row['work_trip_evidence']) == 1
    assert data['totals']['actual_seconds'] == row['actual_seconds']
    assert data['totals']['duty_seconds'] == row['duty_seconds']
    assert {'kps-branch-1','enbishan-test-1'} <= {b['id'] for b in data['work_branches']}
    assert all(b['name'] for b in data['work_branches'])
    assert 'enbishan-test-1' not in {b['id'] for b in data['branches']}
    # A secondary-brand view still finds the shared account and same union;
    # the two views are intentionally not additive reports.
    _, secondary = report('enbishan')
    assert secondary['actual_seconds'] == row['actual_seconds']
    assert secondary['duty_seconds'] == row['duty_seconds']
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-owner' AND brand_id='enbishan'")
    denied, hidden = report()
    assert hidden['actual_seconds'] == hidden['active_work_seconds'] == 0
    assert hidden['work_review_required']
    assert hidden['work_clock_status'] == 'needs_review'
    assert 'incomplete_physical_scope' in hidden['work_review_reasons']
    assert hidden['trips'] == hidden['work_trip_evidence'] == []
    assert hidden['order_count'] == 0
    assert hidden['duty_seconds'] == 12 * 3600
    assert denied['work_branches'] == []
    assert all(item[field] is None for item in hidden['completed_orders']
               for field in ('trip_id','departed_at','returned_at','return_branch_id'))
    # Generic denial must not expose foreign leg/order identity or trip timing.
    serialized = str(denied)
    assert foreign_order not in serialized
    assert legs['enbishan'][1] not in serialized
    assert trip_id not in serialized
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_work_trip_orders WHERE trip_id=%s', (trip_id,)).fetchone()['n'] == 2
        assert c.execute('SELECT returned_at IS NOT NULL returned FROM driver_work_trips WHERE id=%s', (trip_id,)).fetchone()['returned']
