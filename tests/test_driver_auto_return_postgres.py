"""Real mission records: acceptance, shared work, evidence and atomic return."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app import db
from app.driver_auto_return import ensure_mission, consider_point, set_return_branch
from test_operations import ops  # noqa: F401

pytestmark = pytest.mark.postgres


@pytest.fixture
def mission(ops):
    actor, post, order, shifts = ops
    _, sid = shifts()
    now = datetime.now(timezone.utc)-timedelta(minutes=4)
    with db.tx() as c:
        c.execute("UPDATE shifts SET opened_at=%s WHERE id=%s", (now-timedelta(hours=1), sid))
        c.execute("UPDATE branches SET settings=settings||%s WHERE id='ops-branch'", (db.json({'location_verified': True, 'location': {'lat': 28.4, 'lng': 45.9}}),))
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES('auto-return-other',2,'auto-return-other','ops-brand','Other return','حفر الباطن',%s)", (db.json({'location_verified': True, 'location': {'lat': 28.5, 'lng': 45.9}}),))
        user = c.execute("SELECT * FROM users WHERE id='ops-driver'").fetchone()
        c.execute("UPDATE users SET branch_ids=%s WHERE id='ops-driver'", (db.json(['ops-branch', 'auto-return-other']),))
        c.execute("UPDATE shifts SET branch_ids=%s WHERE id=%s", (db.json(['ops-branch', 'auto-return-other']), sid))
        user['branch_ids'] = ['ops-branch', 'auto-return-other']
        shift = c.execute('SELECT * FROM shifts WHERE id=%s', (sid,)).fetchone()
        c.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,created_at,expires_at) VALUES('auto-session','auto-session-token','auto-session-csrf','ops-driver',%s,%s)", (now-timedelta(hours=1), now+timedelta(hours=1)))
        user['session_id'] = 'auto-session'

    def accepted(key='a', accepted_at=None):
        oid = order()
        with db.tx() as c:
            task = c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,accepted_at) VALUES(%s,%s,'ops-driver',%s,'accepted','ops-manager',%s) RETURNING *", ('auto-task-'+key, oid, sid, accepted_at or now-timedelta(minutes=10))).fetchone()
            trip = ensure_mission(c, user, shift, task, now=now-timedelta(minutes=10))
        return task, trip

    def terminal(task, delivered_at=None):
        with db.tx() as c:
            c.execute("UPDATE driver_tasks SET status='delivered',picked_up_at=accepted_at,delivered_at=%s WHERE id=%s", (delivered_at or now-timedelta(minutes=2), task['id']))
            c.execute("UPDATE orders SET preparation_status='completed',delivery_status='delivered' WHERE id=%s", (task['order_id'],))

    def points(*, lat=28.4, prefix='p', live=True, offset=0):
        result = []
        for number, seconds in enumerate((-90, -60, -30, 0)):
            captured = now+timedelta(seconds=seconds+offset)
            p = {'id': prefix+str(number), 'lat': lat, 'lng': 45.92 if number==0 else 45.9,
                 'accuracy_m': 10, 'captured_at': captured}
            with db.tx() as c:
                c.execute("INSERT INTO driver_tracking_points(id,driver_id,brand_id,shift_id,session_id,lat,lng,accuracy_m,captured_at,received_at) VALUES(%s,'ops-driver','ops-brand',%s,'auto-session',%s,%s,%s,%s,%s)",
                          (p['id'], sid, p['lat'], p['lng'], p['accuracy_m'], captured, captured if live else now))
                result.append(consider_point(c, user, shift, p, captured if live else now))
        return result

    return user, shift, now, accepted, terminal, points


def current_trip():
    with db.tx() as c:
        return c.execute('SELECT * FROM driver_work_trips ORDER BY departed_at,id').fetchall()


def test_acceptance_opens_one_mission_and_second_order_links_without_duplicate(mission):
    user, shift, now, accepted, terminal, points = mission
    first, trip = accepted('a')
    second, same = accepted('b')
    trips = current_trip()
    assert len(trips) == 1
    with db.tx() as c:
        ensure_mission(c, user, shift, first, now=now)
        links = c.execute('SELECT order_id FROM driver_work_trip_orders WHERE trip_id=%s', (trips[0]['id'],)).fetchall()
    assert {r['order_id'] for r in links} == {first['order_id'], second['order_id']}
    assert len(current_trip()) == 1


def test_current_gps_dwell_closes_terminal_mission_once_and_preserves_financial_shift(mission):
    user, shift, now, accepted, terminal, points = mission
    task, _ = accepted()
    terminal(task)
    points()
    trips = current_trip()
    assert len(trips) == 1 and trips[0]['returned_at'] is not None
    assert trips[0]['return_branch_id'] == 'ops-branch'
    assert now-timedelta(seconds=60) <= trips[0]['returned_at'] <= now
    before = trips[0]['returned_at']
    points(prefix='retry')
    assert current_trip()[0]['returned_at'] == before
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM driver_work_trip_events WHERE trip_id=%s AND action='return'", (trips[0]['id'],)).fetchone()['n'] == 1
        untouched = c.execute('SELECT status,submitted_at,closed_at FROM shifts WHERE id=%s', (shift['id'],)).fetchone()
        assert untouched == {'status': 'open', 'submitted_at': None, 'closed_at': None}


def test_one_unfinished_order_prevents_return_even_if_other_order_delivered(mission):
    _, _, _, accepted, terminal, points = mission
    first, _ = accepted('a')
    accepted('b')
    terminal(first)
    points()
    assert current_trip()[0]['returned_at'] is None


def test_offline_backlog_cannot_turn_historical_branch_presence_into_live_return(mission):
    _, _, _, accepted, terminal, points = mission
    task, _ = accepted()
    terminal(task)
    points(live=False)
    assert current_trip()[0]['returned_at'] is None


def test_selected_alternate_return_branch_is_authoritative(mission):
    user, _, _, accepted, terminal, points = mission
    task, _ = accepted()
    terminal(task)
    trip = current_trip()[0]
    with db.tx() as c:
        set_return_branch(c, user, trip['id'], 'auto-return-other')
    points()
    assert current_trip()[0]['returned_at'] is None
    points(lat=28.5, prefix='correct', offset=180)
    assert current_trip()[0]['return_branch_id'] == 'auto-return-other'


def test_ungranted_return_destination_is_rejected_and_does_not_change_plan(mission):
    user, _, _, accepted, _, _ = mission
    accepted()
    trip = current_trip()[0]
    try:
        with db.tx() as c:
            set_return_branch(c, user, trip['id'], 'ops-branch-other')
    except HTTPException as exc:
        assert exc.status_code in (403,404,409)
    assert current_trip()[0]['planned_return_branch_id'] == 'ops-branch'


def test_revoked_tracking_session_never_certifies_return(mission, ops):
    user, shift, _, accepted, terminal, points = mission
    task, _ = accepted()
    terminal(task)
    with db.tx() as c:
        c.execute("UPDATE sessions SET revoked_at=now() WHERE id='auto-session'")
    from app import auth
    client = ops[0]('driver')
    client.app.dependency_overrides[auth.current_user] = lambda: user
    response = client.post('/api/operations/driver-location-batch', json={'shift_id': shift['id'], 'points': [{'id': 'revoked', 'captured_at': datetime.now(timezone.utc).isoformat(), 'lat':28.4, 'lng':45.9, 'accuracy_m':10}]})
    assert response.status_code == 401, response.text
    assert current_trip()[0]['returned_at'] is None


from test_enbishan_dual_brand import dual, shared_dual  # noqa: E402,F401


def test_two_native_and_one_secondary_brand_acceptances_share_one_physical_mission(shared_dual):
    from app import multibrand
    (_, _, _, _, order), _, driver, legs = shared_dual
    now = datetime.now(timezone.utc)
    orders = [('kps', order('kps')), ('kps', order('kps')), ('enbishan', order('enbishan'))]
    with db.tx() as c:
        native = c.execute("SELECT * FROM users WHERE id='dual-driver'").fetchone()
        native['session_id'] = c.execute("SELECT id FROM sessions WHERE user_id='dual-driver' AND revoked_at IS NULL AND expires_at>now() ORDER BY created_at DESC LIMIT 1").fetchone()['id']
        for index, (brand, oid) in enumerate(orders):
            shift = c.execute('SELECT * FROM shifts WHERE id=%s', (legs[brand][1],)).fetchone()
            principal = multibrand.project(c, native, brand)
            task = c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,accepted_at) VALUES(%s,%s,'dual-driver',%s,'accepted','dual-manager',%s) RETURNING *",
                             ('mixed-auto-'+str(index), oid, shift['id'], now)).fetchone()
            ensure_mission(c, principal, shift, task, now=now)
        trips = c.execute("SELECT * FROM driver_work_trips WHERE driver_id='dual-driver'").fetchall()
        assert len(trips) == 1
        links = c.execute('SELECT order_id FROM driver_work_trip_orders WHERE trip_id=%s', (trips[0]['id'],)).fetchall()
        assert {r['order_id'] for r in links} == {oid for _, oid in orders}
        assert trips[0]['returned_at'] is None
        assert c.execute("SELECT count(*) n FROM driver_work_trip_events WHERE action='depart' AND trip_id=%s", (trips[0]['id'],)).fetchone()['n'] == 1


def test_old_acceptance_is_not_backfilled_into_a_new_automatic_mission(mission):
    _, _, now, accepted, _, _ = mission
    accepted(accepted_at=now-timedelta(hours=2))
    assert current_trip() == []
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_work_trip_events').fetchone()['n'] == 0


def test_open_pickup_station_blocks_automatic_return(mission):
    _, shift, now, accepted, terminal, points = mission
    task, _ = accepted()
    terminal(task)
    trip = current_trip()[0]
    with db.tx() as c:
        c.execute("INSERT INTO driver_trip_stops(id,trip_id,brand_id,shift_id,kind,branch_id,task_ids,arrived_at,actor_id) VALUES('auto-open-pickup',%s,'ops-brand',%s,'pickup','ops-branch',%s,%s,'ops-driver')",
                  (trip['id'], shift['id'], db.json([task['id']]), now-timedelta(minutes=3)))
    points()
    assert current_trip()[0]['returned_at'] is None
    with db.tx() as c:
        assert c.execute("SELECT departed_at FROM driver_trip_stops WHERE id='auto-open-pickup'").fetchone()['departed_at'] is None


def test_reaccepted_order_after_closed_mission_has_a_new_exact_task_clock(mission):
    user, shift, now, accepted, _, points = mission
    first, _ = accepted()
    with db.tx() as c:
        c.execute("UPDATE driver_tasks SET status='transferred' WHERE id=%s", (first['id'],))
    points()
    first_trip = current_trip()[0]
    assert first_trip['returned_at'] is not None
    accepted_at = now+timedelta(seconds=60)
    with db.tx() as c:
        task = c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,accepted_at) VALUES('auto-reaccepted',%s,'ops-driver',%s,'accepted','ops-manager',%s) RETURNING *",
                         (first['order_id'], shift['id'], accepted_at)).fetchone()
        ensure_mission(c, user, shift, task, now=accepted_at)
        trips = c.execute('SELECT * FROM driver_work_trips ORDER BY departed_at,id').fetchall()
        assert len(trips) == 2
        assert trips[0]['id'] == first_trip['id'] and trips[0]['returned_at'] == first_trip['returned_at']
        assert trips[1]['returned_at'] is None and trips[1]['departed_at'] == accepted_at
        assert trips[0]['auto_task_ids'] == [first['id']]
        assert trips[1]['auto_task_ids'] == ['auto-reaccepted']
        assert c.execute('SELECT count(*) n FROM driver_work_trip_orders WHERE order_id=%s', (first['order_id'],)).fetchone()['n'] == 2


def test_real_task_acceptance_endpoint_creates_and_links_automatic_mission(mission, ops):
    from app import auth
    from uuid import uuid4
    user, shift, _, _, _, _ = mission
    actor, post, order, _ = ops
    oid = order()
    assigned = post('manager', '/api/orders/'+oid+'/assign', {'driver_shift_id':shift['id']})['task']
    client = actor('driver')
    client.app.dependency_overrides[auth.current_user] = lambda: user
    response = client.post('/api/tasks/'+assigned['id']+'/transition', json={'status':'accepted'},
                           headers={'Idempotency-Key':uuid4().hex})
    assert response.status_code == 200, response.text
    trips = current_trip()
    assert len(trips) == 1 and trips[0]['auto_task_ids'] == [assigned['id']]
    with db.tx() as c:
        assert c.execute('SELECT order_id FROM driver_work_trip_orders WHERE trip_id=%s', (trips[0]['id'],)).fetchone()['order_id'] == oid
        task = c.execute('SELECT * FROM driver_tasks WHERE id=%s', (assigned['id'],)).fetchone()
        assert task['status'] == 'accepted'
        assert trips[0]['departed_at'] == task['accepted_at']


@pytest.mark.parametrize('session_state',['missing','revoked'])
def test_unverified_session_cannot_create_mission_or_rewrite_acceptance(mission, ops, session_state):
    user, shift, _, _, _, _ = mission
    oid = ops[2]()
    with db.tx() as c:
        task = c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,accepted_at) VALUES('auto-unverified',%s,'ops-driver',%s,'accepted','ops-manager',now()) RETURNING *", (oid, shift['id'])).fetchone()
        candidate = dict(user)
        if session_state == 'missing':
            candidate.pop('session_id')
        else:
            c.execute("UPDATE sessions SET revoked_at=now() WHERE id='auto-session'")
        assert ensure_mission(c, candidate, shift, task) is None
        assert c.execute('SELECT * FROM driver_tasks WHERE id=%s', (task['id'],)).fetchone() == task
    assert current_trip() == []


def test_report_counts_acceptance_to_first_arrival_and_not_dwell_confirmation(mission, ops):
    _, _, now, accepted, terminal, points = mission
    first, _ = accepted('a')
    accepted('b')
    # Both orders must be completed before one physical return is certified.
    with db.tx() as c:
        tasks = c.execute("SELECT * FROM driver_tasks WHERE driver_id='ops-driver' ORDER BY id").fetchall()
    for task in tasks:
        terminal(task)
    points()
    trips = current_trip()
    assert len(trips) == 1
    trip = trips[0]
    assert trip['returned_at'] == now-timedelta(seconds=60)
    expected = (trip['returned_at']-first['accepted_at']).total_seconds()
    assert (now-trip['returned_at']).total_seconds() == 60
    actor = ops[0]
    params = {'start':(now-timedelta(days=1)).date().isoformat(), 'end':(now+timedelta(days=1)).date().isoformat(), 'driver_id':'ops-driver'}
    response = actor('owner').get('/api/driver-work-hours/report', params=params)
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data['drivers']) == 1
    driver = data['drivers'][0]
    assert driver['actual_seconds'] == expected
    assert data['totals']['actual_seconds'] == expected
    assert driver['active_work_seconds'] == 0
    assert driver['trip_count'] == 1
    assert len(driver['work_trip_evidence']) == 1
    assert not driver['work_review_required'], driver['work_review_reasons']
    points(prefix='duplicate-report')
    repeated = actor('owner').get('/api/driver-work-hours/report', params=params).json()
    assert repeated['drivers'][0]['actual_seconds'] == expected
