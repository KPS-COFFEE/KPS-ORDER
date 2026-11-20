from datetime import datetime, timezone, timedelta
import pytest
from fastapi import HTTPException
from app.tracking_ingest import Point, Batch, validate_capture, trajectory_metrics

NOW=datetime(2026,10,26,12,tzinfo=timezone.utc)


def point(**kw):
    return Point(id='p1',captured_at=NOW,lat=28.4,lng=45.9,accuracy_m=10,**kw)


def test_point_json_timezone_strictness_and_bounds():
    raw='{"id":"p","captured_at":"2026-10-26T12:00:00Z","lat":28.4,"lng":45.9,"accuracy_m":10}'
    assert Point.model_validate_json(raw).captured_at==NOW
    import json
    assert Point.model_validate(json.loads(raw)).captured_at==NOW
    for bad in (raw.replace('00Z','00'),raw.replace('28.4','true'),raw.replace('28.4','91'),raw.replace('28.4','NaN')):
        with pytest.raises(ValueError):Point.model_validate_json(bad)


def test_capture_boundaries_do_not_allow_prior_session_or_shift():
    p=point()
    validate_capture(p,NOW,NOW,NOW)
    for opened,session in ((NOW+timedelta(seconds=1),NOW),(NOW,NOW+timedelta(seconds=1))):
        with pytest.raises(HTTPException):validate_capture(p,NOW,opened,session)
    with pytest.raises(HTTPException):validate_capture(p,NOW-timedelta(seconds=31),NOW-timedelta(hours=1),NOW-timedelta(hours=1))
    old=p.model_copy(update={'captured_at':NOW-timedelta(hours=25)})
    with pytest.raises(HTTPException):validate_capture(old,NOW,NOW-timedelta(days=2),NOW-timedelta(days=2))


def test_batch_bound():
    with pytest.raises(ValueError):Batch(shift_id='s',points=[])
    with pytest.raises(ValueError):Batch(shift_id='s',points=[point()]*101)


def test_distance_union_gaps_accuracy_and_teleports():
    def row(id,seconds,lng=45.9,accuracy=10):
        return {'id':id,'captured_at':NOW+timedelta(seconds=seconds),'lat':28.4,'lng':lng,'accuracy_m':accuracy}
    rows=[row('a',0),row('b',60,45.901),row('c',60,45.901),row('d',360,45.902),row('e',420,45.903,200),row('f',480,80)]
    result=trajectory_metrics(rows,NOW,NOW+timedelta(seconds=600))
    assert 90<result['gps_distance_m']<110
    assert result['measured_segment_seconds']==60
    assert result['tracking_coverage_percent']==10
    assert result['largest_capture_gap_seconds']==300
    assert result['distance_basis']=='gps_measured_segments'
    assert result['history_complete'] is False


def test_history_no_samples_does_not_claim_complete():
    r=trajectory_metrics([],NOW,NOW+timedelta(hours=1))
    assert r['tracking_coverage_percent']==0 and r['history_complete'] is False


@pytest.fixture
def tracking_data(database):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import auth,db,tracking_ingest
    app=FastAPI();app.include_router(tracking_ingest.router)
    user={'id':'tracking-driver','role':'driver','brand_id':'kps','branch_ids':['b-track'],'session_id':'track-session'}
    app.dependency_overrides[auth.current_user]=lambda:user
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('tracking-driver','Tracking','driver','kps',%s)",(db.json(['b-track']),))
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,opened_at) VALUES('track-shift','driver','tracking-driver','kps',%s,now()-interval '1 hour')",(db.json(['b-track']),))
        c.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at,created_at) VALUES('track-session','track-token','track-csrf','tracking-driver',now()+interval '1 hour',now()-interval '1 hour')")
    return TestClient(app),user


def batch_body(age=0,id='p'):
    return {'shift_id':'track-shift','points':[{'id':id,'captured_at':(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat(),'lat':28.4,'lng':45.9,'accuracy_m':10}]}


@pytest.mark.postgres
def test_offline_batch_idempotent_no_history_freshness(tracking_data):
    from app import db
    client,user=tracking_data
    body=batch_body(300)
    endpoint='/api/operations/driver-location-batch'
    assert client.post(endpoint,json=body).json()['accepted_ids']==['p']
    assert client.post(endpoint,json=body).json()['accepted_ids']==['p']
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM driver_tracking_points").fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM driver_latest_locations").fetchone()['n']==0
    fresh=batch_body(1,'fresh')
    assert client.post(endpoint,json=fresh).status_code==200
    older=batch_body(60,'older')
    assert client.post(endpoint,json=older).status_code==200
    with db.tx() as c:
        row=c.execute('SELECT * FROM driver_latest_locations').fetchone()
        assert row['updated_at'].isoformat()==fresh['points'][0]['captured_at']
        point=c.execute("SELECT * FROM driver_tracking_points WHERE id='p'").fetchone()
        assert (point['received_at']-point['captured_at']).total_seconds()>=300
    body['points'][0]['lat']=29
    assert client.post(endpoint,json=body).status_code==409


@pytest.mark.postgres
def test_batch_rejects_wrong_shift_session_revocation_and_old_session(tracking_data):
    from app import db
    client,user=tracking_data
    endpoint='/api/operations/driver-location-batch'
    invalid=batch_body();invalid['shift_id']='other'
    assert client.post(endpoint,json=invalid).status_code==409
    with db.tx() as c:c.execute("UPDATE sessions SET created_at=now() WHERE id='track-session'")
    assert client.post(endpoint,json=batch_body(60)).status_code==409
    with db.tx() as c:c.execute("UPDATE sessions SET revoked_at=now() WHERE id='track-session'")
    assert client.post(endpoint,json=batch_body()).status_code==401
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_tracking_points').fetchone()['n']==0


@pytest.mark.postgres
def test_batch_close_and_branch_revocation_rejected(tracking_data):
    from app import db
    client,user=tracking_data
    endpoint='/api/operations/driver-location-batch'
    user['branch_ids']=[]
    assert client.post(endpoint,json=batch_body()).status_code==403
    user['branch_ids']=['b-track']
    with db.tx() as c:c.execute("UPDATE shifts SET status='closed' WHERE id='track-shift'")
    assert client.post(endpoint,json=batch_body()).status_code==409


@pytest.mark.postgres
def test_history_permission_and_coverage(tracking_data):
    client,user=tracking_data
    assert client.post('/api/operations/driver-location-batch',json=batch_body(300)).status_code==200
    endpoint='/api/operations/map-services/history?shift_id=track-shift'
    assert client.get(endpoint).status_code==403
    user.update(role='owner')
    report=client.get(endpoint)
    assert report.status_code==200,report.text
    assert len(report.json()['points'])==1
    assert report.json()['tracking_coverage_percent']==0
    assert report.json()['history_complete'] is False
    user.update(role='delivery_manager',branch_ids=[])
    assert client.get(endpoint).status_code==403


def test_station_timing_uses_first_kind_specific_prediction_and_unions_wait():
    from app.tracking_ingest import station_timing
    def forecast(calc,kind,arrival):
        return {'calculated_at':NOW+timedelta(seconds=calc),'stops':[{'task_id':'t','shift_id':'s','brand_id':'kps','kind':kind,'arrival_at':(NOW+timedelta(seconds=arrival)).isoformat()}]}
    forecasts=[forecast(0,'pickup',10),forecast(0,'dropoff',100),forecast(80,'dropoff',130)]
    stops=[{'id':'p','task_ids':['t'],'kind':'pickup','arrived_at':NOW+timedelta(seconds=15),'departed_at':NOW+timedelta(seconds=35)},
           {'id':'d','task_ids':['t'],'kind':'delivery','arrived_at':NOW+timedelta(seconds=120),'departed_at':NOW+timedelta(seconds=140)}]
    result=station_timing(stops,forecasts,'s','kps')
    assert [r['arrival_delta_seconds'] for r in result['arrivals']]==[5,20]
    assert result['branch_wait_seconds']==20
    assert result['customer_stop_seconds']==20
    assert station_timing(stops,forecasts,'other','kps')['arrivals']==[]


def test_history_legs_share_physical_day_only_with_current_brand_grants(monkeypatch):
    from app import tracking_ingest as tracking,central_workdays,multibrand
    from fastapi import HTTPException
    anchor={'id':'k','user_id':'d','kind':'driver','brand_id':'kps','branch_ids':['kb'],'opened_at':NOW}
    other={'id':'e','user_id':'d','kind':'driver','brand_id':'enbishan','branch_ids':['eb'],'opened_at':NOW}
    actor={'id':'m','brand_id':'enbishan','role':'delivery_manager','branch_ids':['eb'],'central_operations':{'brands':[{'id':'kps'},{'id':'enbishan'}]}}
    grants={'kps':['kb'],'enbishan':['eb']}
    def projected(c,user,brand):
        return dict(user,brand_id=brand,branch_ids=grants[brand]) if brand in grants else None
    def access(c,user,shift):
        if user['brand_id']!=shift['brand_id'] or not set(shift['branch_ids'])<=set(user['branch_ids']):
            raise HTTPException(403,'revoked')
    class Result:
        def __init__(self,rows):self.rows=rows
        def fetchall(self):return self.rows
        def fetchone(self):return self.rows[0] if self.rows else None
    class Connection:
        def execute(self,sql,args):
            if 'central_workday_legs' in sql:return Result([anchor,other])
            return Result([{'id':'d','role':'driver','active':True,'brand_id':'kps','branch_ids':['kb']}])
    monkeypatch.setattr(tracking,'shift_access',access)
    monkeypatch.setattr(central_workdays,'association',lambda c,sid:{'id':'day'})
    monkeypatch.setattr(multibrand,'project',projected)
    assert [s['id'] for s in tracking.history_legs(Connection(),actor,other)]==['k','e']
    del grants['kps']
    assert [s['id'] for s in tracking.history_legs(Connection(),actor,other)]==['e']
    actor['_morning_manager_context']={'limited':True}
    assert tracking.history_legs(Connection(),actor,other)==[other]


@pytest.mark.parametrize('method,path,payload,permission',[
    ('post','/api/operations/map-services/trip-stops',{'trip_id':'t','kind':'return','branch_id':'b'},'tasks_update'),
    ('post','/api/operations/map-services/trip-stops/s/depart',None,'tasks_update'),
    ('get','/api/operations/map-services/trip-stops/current',None,'tasks_view'),
])
def test_explicit_driver_denial_blocks_stations_before_database(monkeypatch,method,path,payload,permission):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import auth,db,tracking_ingest
    app=FastAPI();app.include_router(tracking_ingest.router)
    app.dependency_overrides[auth.current_user]=lambda:{'id':'d','role':'driver','brand_id':'kps','permission_denials':[permission]}
    def forbidden_db(*args,**kwargs):raise AssertionError('permission denial must precede database')
    monkeypatch.setattr(db,'tx',forbidden_db)
    response=getattr(TestClient(app),method)(path,**({'json':payload} if payload is not None else {}))
    assert response.status_code==403,response.text
    assert response.json()['detail']['code']=='TRACKING_TASK_PERMISSION'


@pytest.mark.postgres
def test_batch_is_atomic_and_new_session_cannot_rebind_saved_points(tracking_data):
    from app import db
    client,user=tracking_data
    endpoint='/api/operations/driver-location-batch'
    body=batch_body(30,'first')
    body['points'].append(batch_body(7200,'outside')['points'][0])
    assert client.post(endpoint,json=body).status_code==409
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_tracking_points').fetchone()['n']==0
        assert c.execute('SELECT count(*) n FROM driver_latest_locations').fetchone()['n']==0
    original=batch_body(30,'first')
    assert client.post(endpoint,json=original).status_code==200
    with db.tx() as c:
        c.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES('track-replacement','track-new-token','track-new-csrf','tracking-driver',now()+interval '1 hour')")
    user['session_id']='track-replacement'
    response=client.post(endpoint,json=original)
    assert response.status_code==409
    assert response.json()['detail']['code']=='TRACKING_POINT_CONFLICT'
    with db.tx() as c:
        assert c.execute("SELECT session_id FROM driver_tracking_points WHERE id='first'").fetchone()['session_id']=='track-session'


@pytest.mark.postgres
def test_exact_saved_replay_remains_acknowledged_across_history_age_boundary(tracking_data):
    from app import db
    client,user=tracking_data
    captured=datetime.now(timezone.utc)-timedelta(hours=26)
    body={'shift_id':'track-shift','points':[{'id':'accepted-old','captured_at':captured.isoformat(),'lat':28.4,'lng':45.9,'accuracy_m':10}]}
    with db.tx() as c:
        c.execute("UPDATE shifts SET opened_at=now()-interval '2 days' WHERE id='track-shift'")
        c.execute("UPDATE sessions SET created_at=now()-interval '2 days' WHERE id='track-session'")
        c.execute("INSERT INTO driver_tracking_points(id,driver_id,brand_id,shift_id,session_id,lat,lng,accuracy_m,captured_at,received_at) VALUES('accepted-old','tracking-driver','kps','track-shift','track-session',28.4,45.9,10,%s,%s)",
                  (captured,captured+timedelta(seconds=30)))
    response=client.post('/api/operations/driver-location-batch',json=body)
    assert response.status_code==200,response.text
    assert response.json()['accepted_ids']==['accepted-old']
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_tracking_points').fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM driver_latest_locations').fetchone()['n']==0


@pytest.mark.postgres
def test_batch_waits_for_fleet_user_before_locking_shift(tracking_data, database, monkeypatch):
    """The real fleet writer must acquire its shifts while GPS waits on users."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    import threading
    import time
    import psycopg
    from psycopg.rows import dict_row
    from app import db, driver_checks, tracking_ingest
    _, user = tracking_data
    body = tracking_ingest.Batch.model_validate(batch_body(1, 'fleet-race'))
    original_tx = db.tx
    reader_pid = []
    entered = threading.Event()

    @contextmanager
    def observed_tx(*args, **kwargs):
        with original_tx(*args, **kwargs) as conn:
            if threading.current_thread().name.startswith('tracking-reader'):
                reader_pid.append(conn.info.backend_pid)
                entered.set()
            yield conn

    monkeypatch.setattr(db, 'tx', observed_tx)
    with psycopg.connect(database, autocommit=True, row_factory=dict_row) as fleet:
        fleet.execute("SET statement_timeout='5000ms'")
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='tracking-reader') as workers:
            with fleet.transaction():
                # Same first lock as driver_checks.lock_fleet and fleet management.
                fleet.execute("SELECT id FROM users WHERE brand_id='kps' AND role='driver' ORDER BY id FOR UPDATE").fetchall()
                future = workers.submit(tracking_ingest.ingest, body, user)
                assert entered.wait(3)
                deadline = time.monotonic() + 3
                while not fleet.execute('SELECT pg_blocking_pids(%s) p', (reader_pid[0],)).fetchone()['p']:
                    assert time.monotonic() < deadline, 'GPS did not wait on the real user lock'
                    time.sleep(.01)
                # On the old ordering GPS already holds this shift and NOWAIT
                # fails. With users first, the actual fleet lock sequence succeeds.
                fleet.execute("SELECT id FROM shifts WHERE id='track-shift' FOR UPDATE NOWAIT")
                driver_checks.lock_fleet(fleet, {'brand_id': 'kps'})
            assert future.result(timeout=5)['accepted_ids'] == ['fleet-race']
    with original_tx() as c:
        assert c.execute("SELECT count(*) n FROM driver_tracking_points WHERE id='fleet-race'").fetchone()['n'] == 1


@pytest.mark.postgres
@pytest.mark.parametrize('sqlstate', ['40P01', '40001'])
def test_batch_retries_whole_transaction_without_partial_points(tracking_data, monkeypatch, sqlstate):
    from app import db, map_services, tracking_ingest
    client, _ = tracking_data
    original = map_services.record_sample
    attempts = []

    def transient_failure(c, *args, **kwargs):
        attempts.append(c.execute('SELECT txid_current() n').fetchone()['n'])
        # Fail after the point INSERT, through PostgreSQL itself, so rollback
        # must erase the partial write before retrying the identical point ID.
        if len(attempts) == 1:
            c.execute("DO $$ BEGIN RAISE EXCEPTION 'isolated retry test' USING ERRCODE = '" + sqlstate + "'; END $$")
        return original(c, *args, **kwargs)

    monkeypatch.setattr(map_services, 'record_sample', transient_failure)
    monkeypatch.setattr(tracking_ingest, 'sleep', lambda _: None)
    body = batch_body(1, 'retry-point')
    response = client.post('/api/operations/driver-location-batch', json=body)
    assert response.status_code == 200, response.text
    assert response.json()['accepted_ids'] == ['retry-point']
    assert len(attempts) == 2 and attempts[0] != attempts[1]
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_tracking_points').fetchone()['n'] == 1
        latest = c.execute('SELECT * FROM driver_latest_locations').fetchone()
        assert latest['updated_at'].isoformat() == body['points'][0]['captured_at']


def test_batch_retry_is_bounded_and_does_not_retry_other_errors(monkeypatch):
    import psycopg
    from app import tracking_ingest
    for error, expected in ((psycopg.errors.DeadlockDetected, 3),
                            (psycopg.errors.SerializationFailure, 3),
                            (psycopg.errors.UniqueViolation, 1),
                            (psycopg.OperationalError, 1),
                            (RuntimeError, 1)):
        calls = []
        def fail(body, user):
            calls.append(True)
            raise error('isolated failure')
        monkeypatch.setattr(tracking_ingest, '_ingest_once', fail)
        monkeypatch.setattr(tracking_ingest, 'sleep', lambda _: None)
        with pytest.raises(error):
            tracking_ingest.ingest(Batch(shift_id='s', points=[point()]), {})
        assert len(calls) == expected


@pytest.mark.postgres
@pytest.mark.parametrize('change, expected', [('session', 401), ('shift', 409), ('driver', 403)])
def test_batch_retry_rechecks_session_shift_and_driver(tracking_data, monkeypatch, change, expected):
    from app import db, map_services, tracking_ingest
    client, _ = tracking_data
    original = map_services.record_sample
    sleep_calls = []
    def fail(c, *args, **kwargs):
        c.execute("DO $$ BEGIN RAISE EXCEPTION 'retry scope test' USING ERRCODE='40P01'; END $$")
        return original(c, *args, **kwargs)
    def revoke(_):
        sleep_calls.append(True)
        with db.tx() as c:
            if change == 'session':
                c.execute("UPDATE sessions SET revoked_at=now() WHERE id='track-session'")
            elif change == 'shift':
                c.execute("UPDATE shifts SET status='closing' WHERE id='track-shift'")
            else:
                c.execute("UPDATE users SET active=false WHERE id='tracking-driver'")
    monkeypatch.setattr(map_services, 'record_sample', fail)
    monkeypatch.setattr(tracking_ingest, 'sleep', revoke)
    response = client.post('/api/operations/driver-location-batch', json=batch_body(1, 'revoked'))
    assert response.status_code == expected, response.text
    assert len(sleep_calls) == 1
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_tracking_points').fetchone()['n'] == 0
        assert c.execute('SELECT count(*) n FROM driver_latest_locations').fetchone()['n'] == 0
