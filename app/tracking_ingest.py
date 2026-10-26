"""Authenticated offline GPS evidence and explicit existing-trip station events.

Historical points are replay evidence. Only a genuinely fresh captured point may
advance the latest position, whose updated_at remains the capture clock.
"""
from datetime import datetime, timezone, timedelta
from math import isfinite
from uuid import uuid4
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from . import auth, db, catalog
from .operations import safe, shift_access

router = APIRouter(prefix='/api/operations', tags=['operations'])
STAFF = ('owner', 'delivery_manager', 'delivery_staff')
MAX_CAPTURE_GAP_SECONDS = 180


class Point(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    id: str = Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9_-]+$')
    captured_at: datetime
    lat: float = Field(ge=-90,le=90)
    lng: float = Field(ge=-180,le=180)
    accuracy_m: float = Field(ge=0,le=10000)

    @field_validator('captured_at',mode='before')
    @classmethod
    def aware(cls,value):
        if isinstance(value,str):
            try:value=datetime.fromisoformat(value.replace('Z','+00:00'))
            except ValueError:raise ValueError('ISO timestamp required') from None
        if not isinstance(value,datetime):
            raise ValueError('ISO timestamp required')
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('Timezone is required')
        return value.astimezone(timezone.utc)


class Batch(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    shift_id: str = Field(min_length=1,max_length=100)
    points: list[Point] = Field(min_length=1,max_length=100)


def validate_capture(point, now, opened_at, session_at):
    if point.captured_at < max(opened_at,session_at,now-timedelta(hours=24)) or point.captured_at > now+timedelta(seconds=30):
        raise db.error(409,'TRACKING_CAPTURE_SCOPE','نقطة الموقع خارج فترة الشفت أو جلسة التتبع الحالية.')


def tracking_shift(c,user,shift_id):
    shift=c.execute('SELECT * FROM shifts WHERE id=%s FOR UPDATE',(shift_id,)).fetchone()
    if not shift or shift['user_id']!=user['id'] or shift['kind']!='driver' or shift['status']!='open' or shift['brand_id']!=user['brand_id']:
        raise db.error(409,'TRACKING_SHIFT_SCOPE','التتبع متاح للشفت المفتوح الحالي فقط.')
    shift_access(c,user,shift)
    session=c.execute('SELECT created_at FROM sessions WHERE id=%s AND user_id=%s AND revoked_at IS NULL AND expires_at>now() FOR SHARE',
                      (user.get('session_id'),user['id'])).fetchone()
    if not session:
        raise db.error(401,'TRACKING_SESSION_SCOPE','انتهت جلسة التتبع. سجل الدخول مجددًا.')
    return shift,session


@router.post('/driver-location-batch')
def ingest(body:Batch,user=Depends(auth.require_roles('driver'))):
    with db.tx() as c:
        shift,session=tracking_shift(c,user,body.shift_id)
        now=c.execute('SELECT now() n').fetchone()['n']
        stored=c.execute('SELECT count(*) n FROM driver_tracking_points WHERE shift_id=%s',(shift['id'],)).fetchone()['n']
        last_received=c.execute('SELECT max(received_at) stamp FROM driver_tracking_points WHERE shift_id=%s',(shift['id'],)).fetchone()['stamp']
        accepted=[]
        for point in body.points:
            existing=c.execute('SELECT * FROM driver_tracking_points WHERE driver_id=%s AND id=%s',(user['id'],point.id)).fetchone()
            if existing:
                if (existing['shift_id']!=shift['id'] or existing['session_id']!=user['session_id'] or
                    any(existing[k]!=getattr(point,k) for k in ('lat','lng','accuracy_m','captured_at'))):
                    raise db.error(409,'TRACKING_POINT_CONFLICT','معرّف نقطة التتبع مستخدم لبيانات أخرى.')
                accepted.append(point.id)
                continue
            validate_capture(point,now,shift['opened_at'],session['created_at'])
            if stored>=20000:
                raise db.error(429,'TRACKING_HISTORY_LIMIT','وصل سجل الشفت إلى حد النقاط. تواصل مع مدير التوصيل.')
            stored+=1
            c.execute('''INSERT INTO driver_tracking_points(id,driver_id,brand_id,shift_id,session_id,lat,lng,accuracy_m,captured_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(point.id,user['id'],user['brand_id'],shift['id'],user['session_id'],point.lat,point.lng,point.accuracy_m,point.captured_at))
            # Receipt cannot resurrect stale data. Never move backwards, including
            # updates arriving from another tab or an out-of-order offline batch.
            latest=None
            if timedelta(0)<=now-point.captured_at<=timedelta(seconds=120):
                latest=c.execute('''INSERT INTO driver_latest_locations(driver_id,brand_id,shift_id,lat,lng,accuracy_m,updated_at)
                  VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(driver_id) DO UPDATE SET
                  brand_id=EXCLUDED.brand_id,shift_id=EXCLUDED.shift_id,lat=EXCLUDED.lat,lng=EXCLUDED.lng,
                  accuracy_m=EXCLUDED.accuracy_m,updated_at=EXCLUDED.updated_at
                  WHERE driver_latest_locations.updated_at<EXCLUDED.updated_at RETURNING updated_at''',
                  (user['id'],user['brand_id'],shift['id'],point.lat,point.lng,point.accuracy_m,point.captured_at)).fetchone()
            from .map_services import record_sample
            previous={'shift_id':shift['id'],'updated_at':last_received} if last_received else None
            record_sample(c,user,shift,point,previous,now,near_customer=latest is not None)
            last_received=now
            accepted.append(point.id)
        return safe({'accepted_ids':accepted,'shift_id':shift['id'],'received_at':now})


def trajectory_metrics(points,opened_at,ended_at):
    """Sum plausible accurate segments once; missing intervals remain uncovered."""
    ordered=sorted(points,key=lambda p:(p['captured_at'],p['id']))
    distance=covered=0.0
    largest=max(0,(ended_at-opened_at).total_seconds()) if not ordered else max(0,(ordered[0]['captured_at']-opened_at).total_seconds(),(ended_at-ordered[-1]['captured_at']).total_seconds())
    for a,b in zip(ordered,ordered[1:]):
        gap=(b['captured_at']-a['captured_at']).total_seconds()
        largest=max(largest,gap)
        if gap<=0 or gap>MAX_CAPTURE_GAP_SECONDS or max(a['accuracy_m'],b['accuracy_m'])>100:
            continue
        segment=catalog.direct_distance(a,b)
        # 180 km/h excludes impossible fixes rather than inventing road distance.
        if segment is None or not isfinite(segment) or segment/gap>50:
            continue
        distance+=segment
        covered+=gap
    duration=max(0,(ended_at-opened_at).total_seconds())
    return {'gps_distance_m':round(distance,1),'measured_segment_seconds':round(covered,1),
            'tracking_coverage_percent':round(min(100,100*covered/duration),1) if duration else 0,
            'largest_capture_gap_seconds':round(largest,1),'distance_basis':'gps_measured_segments',
            'history_complete':False}


def station_timing(stops,forecasts,shift_id,brand_id):
    """Compare the first recorded promise, not a later moving ETA, with arrival."""
    from .driver_work_hours import seconds
    first={}
    for forecast in sorted(forecasts,key=lambda f:f['calculated_at']):
        for stop in forecast['stops']:
            if stop.get('shift_id')!=shift_id or stop.get('brand_id')!=brand_id or not (stop.get('task_ids') or stop.get('task_id')):
                continue
            try:expected=datetime.fromisoformat(stop['arrival_at'].replace('Z','+00:00'))
            except (KeyError,TypeError,ValueError):continue
            if expected.tzinfo is None:continue
            kind='delivery' if stop.get('kind')=='dropoff' else stop.get('kind')
            for tid in stop.get('task_ids') or [stop['task_id']]:
                first.setdefault((tid,kind),(forecast['calculated_at'],expected))
    arrivals=[];branch=[];customer=[]
    for stop in stops:
        if stop.get('departed_at'):
            interval=(stop['arrived_at'],stop['departed_at'])
            (customer if stop['kind']=='delivery' else branch).append(interval)
        for tid in stop['task_ids']:
            prediction=first.get((tid,stop['kind']))
            if prediction and prediction[0]<=stop['arrived_at']:
                arrivals.append({'task_id':tid,'stop_id':stop['id'],'expected_arrival_at':prediction[1],
                                 'actual_arrival_at':stop['arrived_at'],
                                 'arrival_delta_seconds':round((stop['arrived_at']-prediction[1]).total_seconds()),
                                 'expectation_basis':'first_recorded_road_eta'})
    return {'arrivals':arrivals,'branch_wait_seconds':seconds(branch),
            'customer_stop_seconds':seconds(customer),'timing_basis':'explicit_station_arrival_departure',
            'unrecorded_intervals':'unclassified_not_assumed_driving'}


def history_legs(c,user,shift):
    """Physical points may span ledger legs; station/order evidence stays scoped."""
    shift_access(c,user,shift)
    if not user.get('central_operations') or user.get('_morning_manager_context'):
        return [shift]
    from .central_workdays import association
    from . import multibrand
    day=association(c,shift['id'])
    if not day:return [shift]
    legs=c.execute("""SELECT s.* FROM central_workday_legs l JOIN shifts s ON s.id=l.shift_id
      WHERE l.workday_id=%s AND s.user_id=%s AND s.kind='driver' ORDER BY s.opened_at,s.id""",(day['id'],shift['user_id'])).fetchall()
    driver=c.execute("SELECT * FROM users WHERE id=%s AND role='driver' AND active",(shift['user_id'],)).fetchone()
    allowed=[]
    for leg in legs:
        principal=user if leg['brand_id']==user['brand_id'] else multibrand.project(c,user,leg['brand_id'])
        target=multibrand.project(c,driver,leg['brand_id']) if driver else None
        if not principal or not target or not set(leg['branch_ids'])<=set(target['branch_ids']):continue
        try:shift_access(c,principal,leg)
        except HTTPException as exc:
            if exc.status_code in (403,404):continue
            raise
        allowed.append(leg)
    return allowed or [shift]


@router.get('/map-services/history')
def history(shift_id:str=Query(min_length=1,max_length=100),user=Depends(auth.require_roles(*STAFF))):
    if not auth.has_permission(user,'drivers_view'):
        raise db.error(403,'TRACKING_HISTORY_SCOPE','صلاحية عرض السائقين غير متاحة.')
    with db.tx() as c:
        shift=c.execute('SELECT * FROM shifts WHERE id=%s',(shift_id,)).fetchone()
        if not shift or shift['kind']!='driver':
            raise db.error(404,'TRACKING_HISTORY_SHIFT','شفت السائق غير موجود.')
        legs=history_legs(c,user,shift)
        ids=[leg['id'] for leg in legs]
        rows=c.execute("""SELECT p.id,p.lat,p.lng,p.accuracy_m,p.captured_at,p.received_at FROM driver_tracking_points p
          JOIN shifts s ON s.id=p.shift_id AND s.brand_id=p.brand_id AND s.user_id=p.driver_id
          WHERE p.shift_id=ANY(%s) AND p.driver_id=%s ORDER BY p.captured_at,p.id LIMIT 20001""",(ids,shift['user_id'])).fetchall()
        if len(rows)>20000:
            raise db.error(409,'TRACKING_HISTORY_LIMIT','سجل التتبع أكبر من حد العرض. اختر رحلة أقصر.')
        now=c.execute('SELECT now() n').fetchone()['n']
        stops=c.execute('SELECT p.* FROM driver_trip_stops p JOIN shifts s ON s.id=p.shift_id AND s.brand_id=p.brand_id AND s.user_id=p.actor_id WHERE p.shift_id=ANY(%s) ORDER BY p.arrived_at,p.id',(ids,)).fetchall()
        opened=min(leg['opened_at'] for leg in legs)
        ended=max(leg.get('closed_at') or leg.get('submitted_at') or now for leg in legs)
        forecasts=c.execute("""SELECT calculated_at,stops FROM driver_route_eta_expectations
          WHERE driver_id=%s AND calculated_at>=%s AND calculated_at<=%s ORDER BY calculated_at,id LIMIT 2000""",
          (shift['user_id'],opened,ended)).fetchall()
        timings=[station_timing([stop for stop in stops if stop['shift_id']==leg['id']],forecasts,leg['id'],leg['brand_id']) for leg in legs]
        timing={'arrivals':[arrival for value in timings for arrival in value['arrivals']],
                'branch_wait_seconds':sum(value['branch_wait_seconds'] for value in timings),
                'customer_stop_seconds':sum(value['customer_stop_seconds'] for value in timings),
                'timing_basis':'explicit_station_arrival_departure','unrecorded_intervals':'unclassified_not_assumed_driving'}
        return safe({'shift_id':shift_id,'physical_shift_ids':ids,'points':rows,'trip_stops':stops,
                     'station_timing':timing,**trajectory_metrics(rows,opened,ended)})


class AlertResolution(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    shift_id:str=Field(min_length=1,max_length=100)
    kind:str=Field(min_length=1,max_length=80,pattern=r'^[a-z_]+$')
    reason:str=Field(min_length=3,max_length=500)
    action:str=Field(min_length=3,max_length=500)


@router.post('/map-services/alerts/resolve')
def resolve_alert(body:AlertResolution,user=Depends(auth.require_roles(*STAFF))):
    if not auth.has_permission(user,'drivers_view'):
        raise db.error(403,'TRACKING_ALERT_SCOPE','صلاحية عرض السائقين غير متاحة.')
    with db.tx() as c:
        shift=c.execute('SELECT * FROM shifts WHERE id=%s',(body.shift_id,)).fetchone()
        if not shift or shift['kind']!='driver':
            raise db.error(404,'TRACKING_ALERT_SHIFT','شفت السائق غير موجود.')
        shift_access(c,user,shift)
        db.audit(c,user['id'],'driver.tracking_alert.handled','shift',body.shift_id,
                 {'kind':body.kind,'reason':body.reason,'action':body.action,'brand_id':user['brand_id']})
        return {'recorded':True,'shift_id':body.shift_id,'kind':body.kind,'alert_condition_cleared':False}


class StopArrival(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    trip_id:str=Field(min_length=1,max_length=100)
    kind:str=Field(pattern=r'^(pickup|delivery|return)$')
    branch_id:str|None=Field(None,min_length=1,max_length=100)
    task_ids:list[str]=Field(default_factory=list,max_length=30)


def trip_scope(c,user,trip_id):
    trip=c.execute('SELECT * FROM driver_work_trips WHERE id=%s AND driver_id=%s',(trip_id,user['id'])).fetchone()
    if not trip or trip['returned_at']:
        raise db.error(409,'TRACKING_TRIP_SCOPE','الرحلة المفتوحة غير متاحة.')
    anchor=c.execute('SELECT * FROM shifts WHERE id=%s',(trip['shift_id'],)).fetchone()
    from . import multibrand
    projected=multibrand.project(c,user,trip['brand_id']) if trip['brand_id']!=user['brand_id'] else user
    if not projected:
        raise db.error(403,'TRACKING_TRIP_SCOPE','الرحلة خارج نطاقك.')
    shift_access(c,projected,anchor)
    target=c.execute("SELECT * FROM shifts WHERE user_id=%s AND brand_id=%s AND kind='driver' AND status='open'",(user['id'],user['brand_id'])).fetchone()
    if not target:
        raise db.error(409,'TRACKING_TRIP_SHIFT','الشفت المفتوح غير متاح.')
    shift_access(c,user,target)
    if target['id']!=anchor['id']:
        from .central_workdays import association
        left,right=association(c,anchor['id']),association(c,target['id'])
        if not left or not right or left['id']!=right['id']:
            raise db.error(403,'TRACKING_TRIP_DAY','المحطة خارج وردية الرحلة.')
    # All station actions lock shifts before the trip, matching return/depart.
    c.execute('SELECT id FROM shifts WHERE id=ANY(%s) ORDER BY id FOR UPDATE',([anchor['id'],target['id']],)).fetchall()
    target=c.execute('SELECT * FROM shifts WHERE id=%s',(target['id'],)).fetchone()
    trip=c.execute('SELECT * FROM driver_work_trips WHERE id=%s FOR UPDATE',(trip_id,)).fetchone()
    if target['status']!='open' or trip['returned_at']:
        raise db.error(409,'TRACKING_TRIP_CLOSED','أغلقت الرحلة أو الشفت أثناء الطلب.')
    return trip,target


@router.post('/map-services/trip-stops')
def arrive_stop(body:StopArrival,user=Depends(auth.require_roles('driver'))):
    if not auth.has_permission(user,'tasks_update'):
        raise db.error(403,'TRACKING_TASK_PERMISSION','صلاحية مهام التوصيل غير متاحة.')
    with db.tx() as c:
        trip,shift=trip_scope(c,user,body.trip_id)
        current=c.execute('SELECT * FROM driver_trip_stops WHERE trip_id=%s AND departed_at IS NULL',(trip['id'],)).fetchone()
        if current:
            if (current['actor_id']==user['id'] and current['shift_id']==shift['id'] and current['kind']==body.kind
                    and current['branch_id']==body.branch_id and current['task_ids']==body.task_ids):
                return safe({'stop':current,'confirms_order_handover':False,'replayed':True})
            raise db.error(409,'TRIP_STOP_OPEN','أكد مغادرة المحطة الحالية أولًا.')
        if len(set(body.task_ids))!=len(body.task_ids):
            raise db.error(422,'TRIP_STOP_DUPLICATE','الطلب مكرر في المحطة.')
        if body.kind in ('pickup','return'):
            if not body.branch_id or body.branch_id not in shift['branch_ids'] or body.branch_id not in user.get('branch_ids',[]):
                raise db.error(403,'TRIP_STOP_BRANCH','الفرع خارج نطاق الشفت.')
            branch=c.execute('SELECT id FROM branches WHERE id=%s AND brand_id=%s',(body.branch_id,user['brand_id'])).fetchone()
            if not branch:raise db.error(403,'TRIP_STOP_BRANCH','الفرع خارج نطاق البراند.')
        elif body.branch_id is not None:
            raise db.error(422,'TRIP_STOP_BRANCH','محطة العميل لا تستخدم فرعًا.')
        if body.kind!='return' and not body.task_ids:
            raise db.error(422,'TRIP_STOP_TASKS','حدد طلبات المحطة.')
        if body.kind=='return' and body.task_ids:
            raise db.error(422,'TRIP_STOP_TASKS','محطة العودة لا تربط طلبات جديدة.')
        for tid in body.task_ids:
            task=c.execute('''SELECT t.*,o.branch_id FROM driver_tasks t JOIN orders o ON o.id=t.order_id
              WHERE t.id=%s AND t.driver_id=%s AND t.shift_id=%s AND o.brand_id=%s''',
              (tid,user['id'],shift['id'],user['brand_id'])).fetchone()
            if not task or task['status'] not in ('assigned','accepted','picked_up'):
                raise db.error(403,'TRIP_STOP_TASK_SCOPE','الطلب غير متاح في الشفت الحالي.')
            if body.kind=='pickup':
                if task['branch_id']!=body.branch_id:
                    raise db.error(403,'TRIP_STOP_TASK_BRANCH','الطلب لا يتبع فرع الاستلام.')
                c.execute('INSERT INTO driver_work_trip_orders(trip_id,order_id) VALUES(%s,%s) ON CONFLICT DO NOTHING',(trip['id'],task['order_id']))
            elif not c.execute('SELECT 1 FROM driver_work_trip_orders WHERE trip_id=%s AND order_id=%s',(trip['id'],task['order_id'])).fetchone():
                raise db.error(403,'TRIP_STOP_ORDER_LINK','الطلب غير مرتبط بالرحلة.')
        stop=c.execute('''INSERT INTO driver_trip_stops(id,trip_id,brand_id,shift_id,kind,branch_id,task_ids,actor_id)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
          (uuid4().hex,trip['id'],user['brand_id'],shift['id'],body.kind,body.branch_id,db.json(body.task_ids),user['id'])).fetchone()
        db.audit(c,user['id'],'driver.trip.stop.arrive','driver_work_trip',trip['id'],{'stop_id':stop['id'],'kind':body.kind,'brand_id':user['brand_id']})
        return safe({'stop':stop,'confirms_order_handover':False})


@router.post('/map-services/trip-stops/{stop_id}/depart')
def depart_stop(stop_id:str,user=Depends(auth.require_roles('driver'))):
    if not auth.has_permission(user,'tasks_update'):
        raise db.error(403,'TRACKING_TASK_PERMISSION','صلاحية مهام التوصيل غير متاحة.')
    with db.tx() as c:
        existing=c.execute('SELECT * FROM driver_trip_stops WHERE id=%s AND actor_id=%s AND brand_id=%s',(stop_id,user['id'],user['brand_id'])).fetchone()
        if not existing:raise db.error(404,'TRIP_STOP_SCOPE','المحطة غير متاحة.')
        trip,shift=trip_scope(c,user,existing['trip_id'])
        if shift['id']!=existing['shift_id']:
            raise db.error(403,'TRIP_STOP_SCOPE','المحطة خارج الشفت الحالي.')
        stop=c.execute('UPDATE driver_trip_stops SET departed_at=COALESCE(departed_at,now()) WHERE id=%s RETURNING *',(stop_id,)).fetchone()
        if not existing['departed_at']:
            db.audit(c,user['id'],'driver.trip.stop.depart','driver_work_trip',trip['id'],{'stop_id':stop_id,'kind':stop['kind'],'brand_id':user['brand_id']})
        return safe({'stop':stop,'confirms_order_handover':False})


@router.get('/map-services/trip-stops/current')
def current_stop(user=Depends(auth.require_roles('driver'))):
    if not auth.has_permission(user,'tasks_view'):
        raise db.error(403,'TRACKING_TASK_PERMISSION','صلاحية مهام التوصيل غير متاحة.')
    with db.tx() as c:
        trip=c.execute('SELECT * FROM driver_work_trips WHERE driver_id=%s AND returned_at IS NULL',(user['id'],)).fetchone()
        if not trip:return {'trip':None,'stop':None}
        trip,_=trip_scope(c,user,trip['id'])
        stop=c.execute('SELECT * FROM driver_trip_stops WHERE trip_id=%s AND departed_at IS NULL',(trip['id'],)).fetchone()
        if stop and stop['brand_id']!=user['brand_id']:
            from .multibrand import project
            principal=project(c,user,stop['brand_id'])
            if not principal:raise db.error(403,'TRIP_STOP_SCOPE','المحطة خارج الصلاحيات الحالية.')
            shift_access(c,principal,c.execute('SELECT * FROM shifts WHERE id=%s',(stop['shift_id'],)).fetchone())
        return safe({'trip':trip,'stop':stop})
