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
