"""Server-timed branch-to-branch trips and interval-union operational reports.

GPS absence is evidence absence, never a break. Historical missing trip evidence
is explicitly unclassified; a delivered task never proves a branch return.
"""
from datetime import date, datetime, timedelta, time
from zoneinfo import ZoneInfo
from math import asin, cos, radians, sin, sqrt
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from . import auth, db
from .operations import day_start, lock_shift, shift_access, roles, safe, uid, mutate

router = APIRouter(prefix='/api/driver-work-hours', tags=['driver-work-hours'])
RIYADH = ZoneInfo('Asia/Riyadh')


def union(intervals):
    out = []
    for lo, hi in sorted((a,b) for a,b in intervals if a is not None and b is not None and b>a):
        if out and lo <= out[-1][1]: out[-1] = (out[-1][0], max(hi,out[-1][1]))
        else: out.append((lo,hi))
    return out


def subtract(intervals, exclusions):
    result = union(intervals)
    for a,b in union(exclusions):
        result = [(x,y) for lo,hi in result for x,y in ((lo,min(hi,a)),(max(lo,b),hi)) if y>x]
    return union(result)


def clipped(intervals, lo, hi):
    return union((max(a,lo),min(b,hi)) for a,b in intervals if a is not None and b is not None)


def seconds(intervals):
    return round(sum((b-a).total_seconds() for a,b in union(intervals)), 3)


def metrics(shifts, breaks, trips, lo, hi, now, stops=(), unknown=()):
    spans = clipped([(s['opened_at'],min(s.get('submitted_at') or s.get('closed_at') or now,now)) for s in shifts],lo,hi)
    nonwork = clipped([*breaks,*stops],lo,hi)
    actual = subtract(spans,nonwork)
    # Intersect with actual work so overlap/breaks never inflate delivery totals.
    confirmed = clipped([(t['departed_at'],t['returned_at']) for t in trips if t.get('returned_at')],lo,hi)
    delivery = subtract(actual,subtract(actual,confirmed))
    pending = clipped([(t['departed_at'],now) for t in trips if not t.get('returned_at')],lo,hi)
    unclassified = subtract(actual,subtract(actual,[*unknown,*pending]))
    unclassified = subtract(unclassified,delivery)
    waiting = subtract(actual,[*delivery,*unclassified])
    return dict(shift_seconds=seconds(spans),actual_seconds=seconds(actual),delivery_seconds=seconds(delivery),
                waiting_seconds=seconds(waiting),break_seconds=seconds(subtract(spans,actual)),
                other_work_seconds=0,unclassified_seconds=seconds(unclassified))


def order_activity(shifts, tasks, breaks, lo, hi, now, stops=()):
    """Completed pickup-to-delivery time, unioned per driver and clipped to work.

    Branch returns remain a separate measure. Missing history never becomes an
    invented zero-duration delivery or assumed waiting; live tasks are separate.
    """
    spans = clipped([(s['opened_at'], min(s.get('submitted_at') or s.get('closed_at') or now, now)) for s in shifts], lo, hi)
    work = subtract(spans, clipped([*breaks, *stops], lo, hi))
    completed, ongoing = [], []
    missing = set()
    for task in tasks:
        pickup, delivered = task.get('picked_up_at'), task.get('delivered_at')
        if task['status'] == 'delivered':
            if pickup is not None and delivered is not None and pickup <= delivered <= now:
                completed.append((pickup, delivered))
            elif delivered is not None and lo <= delivered < hi:
                missing.add(task['order_id'])
        elif task['status'] in ('picked_up', 'problem') and pickup is not None:
            ongoing.append((pickup, now))
    completed = subtract(work, subtract(work, clipped(completed, lo, hi)))
    ongoing = subtract(work, subtract(work, clipped(ongoing, lo, hi)))
    ongoing = subtract(ongoing, completed)
    total, done, active = seconds(spans), seconds(completed), seconds(ongoing)
    return {'order_activity_seconds': done, 'active_delivery_seconds': active,
            'remaining_shift_seconds': round(max(0, total-done-active), 3),
            'order_utilization_percent': round(done / total * 100, 2) if total else 0,
            'missing_order_timing_count': len(missing)}


def completed_order_evidence(tasks, lo, hi, now):
    """Evidence only: intent is not arrival, and no promise means no lateness score."""
    orders = {}
    for task in sorted(tasks, key=lambda t: t.get('delivered_at') or lo):
        delivered, pickup = task.get('delivered_at'), task.get('picked_up_at')
        if task.get('status') != 'delivered' or delivered is None or not lo <= delivered < hi or delivered > now:
            continue
        valid = pickup is not None and pickup <= delivered
        returned = task.get('returned_at')
        if returned is not None and (returned < delivered or returned > now):
            returned = None
        destination = task.get('next_destination')
        orders[task['order_id']] = {
            'order_id': task['order_id'], 'picked_up_at': pickup, 'delivered_at': delivered,
            'delivery_seconds': (delivered-pickup).total_seconds() if valid else None,
            'trip_id': task.get('trip_id'), 'departed_at': task.get('departed_at'),
            'returned_at': returned, 'return_branch_id': task.get('return_branch_id') if returned else None,
            'next_destination': destination,
            'timing_evidence': 'recorded' if valid else 'missing_or_invalid_pickup',
        }
    rows = list(orders.values())
    timed = sum(row['delivery_seconds'] is not None for row in rows)
    return {'completed_orders': rows, 'performance': {
        'completed_order_count': len(rows), 'timed_order_count': timed,
        'missing_pickup_count': len(rows)-timed,
        'punctuality_available': False,
        'punctuality_unavailable_reason': 'no_recorded_delivery_promise',
        'on_time_count': None, 'late_count': None,
        'gps_gaps_affect_performance': False,
    }}


def report_completed_orders(c, user, driver_id, shift_ids, allowed, lo, hi, now):
    # Explicit brand/branch/driver/shift predicates also defend inconsistent old rows.
    rows = c.execute("""
        SELECT t.order_id,t.status,t.picked_up_at,t.delivered_at,
               w.id trip_id,w.departed_at,w.returned_at,w.return_branch_id,
               CASE WHEN d.created_at IS NOT NULL THEN
                 jsonb_build_object('kind',d.details->>'kind','branch_id',d.details->>'branch_id',
                   'declared_at',d.created_at,'source','driver_declared_intent') END next_destination
        FROM effective_driver_tasks t JOIN orders o ON o.id=t.order_id
        LEFT JOIN LATERAL (
          SELECT w.* FROM driver_work_trips w JOIN driver_work_trip_orders x ON x.trip_id=w.id
          WHERE x.order_id=t.order_id AND w.shift_id=t.shift_id AND w.driver_id=t.driver_id
            AND w.brand_id=o.brand_id ORDER BY w.departed_at DESC LIMIT 1
        ) w ON true
        LEFT JOIN LATERAL (
          SELECT a.created_at,a.details FROM audit_events a
          WHERE a.entity_type='shift' AND a.entity_id=t.shift_id AND a.actor_id=t.driver_id
            AND a.action='driver.next_destination' AND a.created_at>=t.delivered_at AND a.created_at<=%s
            AND (a.details->>'kind'='external_enbishan' OR
                 (a.details->>'kind'='branch' AND a.details->>'branch_id'=ANY(%s)))
            AND NOT EXISTS(SELECT 1 FROM driver_tasks newer WHERE newer.shift_id=t.shift_id
              AND newer.driver_id=t.driver_id AND newer.created_at>t.delivered_at AND newer.created_at<=a.created_at)
          ORDER BY a.created_at,a.id LIMIT 1
        ) d ON true
        WHERE t.shift_id=ANY(%s) AND t.driver_id=%s AND o.brand_id=%s AND o.branch_id=ANY(%s)
          AND o.test_void IS NULL AND t.status='delivered' AND t.delivered_at>=%s AND t.delivered_at<%s
        ORDER BY t.delivered_at,t.id
    """, (now,list(allowed),shift_ids,driver_id,user['brand_id'],list(allowed),lo,hi)).fetchall()
    return completed_order_evidence(rows,lo,hi,now)


class Movement(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    branch_id: str = Field(min_length=1,max_length=100)
    lat: float | None = Field(None,ge=-90,le=90)
    lng: float | None = Field(None,ge=-180,le=180)


class Departure(Movement):
    shift_id: str = Field(min_length=1,max_length=100)
    order_ids: list[str] = Field(min_length=1,max_length=30)


def branch_evidence(c,user,s,body):
    if body.branch_id not in s['branch_ids'] or body.branch_id not in user.get('branch_ids',[]):
        raise db.error(403,'TRIP_BRANCH_SCOPE','الفرع خارج نطاق السائق أو الشفت.')
    branch=c.execute('SELECT * FROM branches WHERE id=%s AND brand_id=%s',(body.branch_id,user['brand_id'])).fetchone()
    if not branch: raise db.error(404,'TRIP_BRANCH_SCOPE','الفرع غير متاح.')
    if (body.lat is None)!=(body.lng is None): raise db.error(422,'TRIP_LOCATION','أرسل إحداثيات الموقع كاملة.')
    pin=branch['settings'].get('location') or {}
    evidence={'source':'driver_explicit_confirmation','lat':body.lat,'lng':body.lng,'geofence':'unavailable'}
    if body.lat is not None and pin.get('lat') is not None and pin.get('lng') is not None:
        a,b,c1,d=map(radians,[body.lat,body.lng,float(pin['lat']),float(pin['lng'])])
        distance=6371000*2*asin(min(1,sqrt(sin((a-c1)/2)**2+cos(a)*cos(c1)*sin((b-d)/2)**2)))
        if distance>500: raise db.error(409,'TRIP_GEOFENCE','أكد الحركة عند الفرع المحدد.')
        evidence.update(geofence='verified',distance_m=round(distance))
    return evidence


def driver_shift(c,user,sid,lock=True):
    roles(user,'driver')
    s=lock_shift(c,sid) if lock else c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone()
    shift_access(c,user,s)
    if s['user_id']!=user['id'] or s['kind']!='driver' or s['status'] not in ('open','closing'):
        raise db.error(409,'TRIP_SHIFT','شفت السائق غير متاح.')
    return s


@router.post('/depart')
def depart(body:Departure,request:Request,user=Depends(auth.current_user)):
    roles(user,'driver')
    if not auth.has_permission(user,'tasks_update'):raise db.error(403,'TRIP_PERMISSION','صلاحية تحديث المهام غير متاحة.')
    with db.tx() as c:
        s=driver_shift(c,user,body.shift_id,lock=False)
        evidence=branch_evidence(c,user,s,body)
        def perform():
            s=driver_shift(c,user,body.shift_id)
            evidence=branch_evidence(c,user,s,body)
            if s['status']!='open' or not s.get('available',True):raise db.error(409,'TRIP_NOT_READY','السائق غير جاهز للخروج.')
            if c.execute("SELECT 1 FROM driver_breaks WHERE shift_id=%s AND status='active'",(s['id'],)).fetchone():raise db.error(409,'TRIP_BREAK','أنهِ الاستراحة أولاً.')
            if c.execute('SELECT 1 FROM driver_work_trips WHERE driver_id=%s AND returned_at IS NULL',(user['id'],)).fetchone():raise db.error(409,'TRIP_OVERLAP','أكد العودة من الرحلة السابقة أولاً.')
            if len(set(body.order_ids))!=len(body.order_ids):raise db.error(422,'TRIP_DUPLICATE_ORDER','الطلب مكرر.')
            for oid in body.order_ids:
                task=c.execute("SELECT t.id FROM driver_tasks t JOIN orders o ON o.id=t.order_id WHERE t.shift_id=%s AND t.driver_id=%s AND t.order_id=%s AND t.status='picked_up' AND o.brand_id=%s AND o.branch_id=%s",(s['id'],user['id'],oid,user['brand_id'],body.branch_id)).fetchone()
                if not task:raise db.error(403,'TRIP_ORDER_SCOPE','استلم الطلبات المخصصة لك من الفرع أولاً.')
                if c.execute('SELECT 1 FROM driver_work_trip_orders x JOIN driver_work_trips w ON w.id=x.trip_id WHERE x.order_id=%s AND w.driver_id=%s AND w.shift_id=%s',(oid,user['id'],s['id'])).fetchone():raise db.error(409,'TRIP_ORDER_LINKED','الطلب مسجل في رحلة سابقة.')
            tid=uid()
            trip=c.execute('INSERT INTO driver_work_trips(id,brand_id,shift_id,driver_id,departure_branch_id,departure_evidence) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *',(tid,user['brand_id'],s['id'],user['id'],body.branch_id,db.json(evidence))).fetchone()
            for oid in body.order_ids:c.execute('INSERT INTO driver_work_trip_orders(trip_id,order_id) VALUES(%s,%s)',(tid,oid))
            record_event(c,user,tid,'depart',body.branch_id,evidence)
            return {'trip':trip,'shift_id':s['id']}
        return mutate(c,user,request,'driver.work.depart',body.model_dump(),perform)


def record_event(c,user,tid,action,bid,evidence):
    c.execute('INSERT INTO driver_work_trip_events(id,trip_id,actor_id,action,branch_id,evidence) VALUES(%s,%s,%s,%s,%s,%s)',(uid(),tid,user['id'],action,bid,db.json(evidence)))
    db.audit(c,user['id'],'driver.trip.'+action,'driver_work_trip',tid,{'branch_id':bid,**evidence})


def return_branch_evidence(c,user,shift,body):
    if body.branch_id in shift['branch_ids'] and body.branch_id in user.get('branch_ids',[]):
        return branch_evidence(c,user,shift,body)
    from .central_workdays import association
    from . import multibrand
    group=association(c,shift['id'])
    if group:
        legs=c.execute("SELECT s.* FROM shifts s JOIN central_workday_legs l ON l.shift_id=s.id WHERE l.workday_id=%s AND s.user_id=%s AND s.status='open' ORDER BY s.id",(group['id'],user['id'])).fetchall()
        for leg in legs:
            projected=multibrand.project(c,user,leg['brand_id'])
            if projected and body.branch_id in leg['branch_ids'] and body.branch_id in projected.get('branch_ids',[]):
                shift_access(c,projected,leg)
                return {**branch_evidence(c,projected,leg,body),'return_brand_id':leg['brand_id']}
    raise db.error(403,'TRIP_BRANCH_SCOPE','الفرع خارج نطاق السائق أو الشفت.')


def lock_return_legs(c,user,shift_id):
    from .central_workdays import association
    group=association(c,shift_id)
    if group:
        c.execute('SELECT s.id FROM shifts s JOIN central_workday_legs l ON l.shift_id=s.id WHERE l.workday_id=%s AND s.user_id=%s ORDER BY s.id FOR UPDATE OF s',(group['id'],user['id'])).fetchall()
    else:
        c.execute('SELECT id FROM shifts WHERE id=%s FOR UPDATE',(shift_id,)).fetchall()


@router.post('/{trip_id}/return')
def return_trip(trip_id:str,body:Movement,request:Request,user=Depends(auth.current_user)):
    roles(user,'driver')
    if not auth.has_permission(user,'tasks_update'):raise db.error(403,'TRIP_PERMISSION','صلاحية تحديث المهام غير متاحة.')
    with db.tx() as c:
        t=c.execute('SELECT * FROM driver_work_trips WHERE id=%s AND driver_id=%s AND brand_id=%s',(trip_id,user['id'],user['brand_id'])).fetchone()
        if not t:raise db.error(404,'TRIP_NOT_FOUND','الرحلة غير متاحة.')
        s=driver_shift(c,user,t['shift_id'],lock=False);evidence=return_branch_evidence(c,user,s,body)
        def perform():
            lock_return_legs(c,user,t['shift_id'])
            s=driver_shift(c,user,t['shift_id'])
            evidence=return_branch_evidence(c,user,s,body)
            current=c.execute('SELECT * FROM driver_work_trips WHERE id=%s FOR UPDATE',(trip_id,)).fetchone()
            if current['returned_at']:raise db.error(409,'TRIP_RETURNED','تم تأكيد العودة مسبقًا.')
            pending=c.execute("SELECT 1 FROM driver_work_trip_orders x JOIN driver_tasks t ON t.order_id=x.order_id WHERE x.trip_id=%s AND t.driver_id=%s AND t.status NOT IN ('delivered','cancelled','transferred','reassigned')",(trip_id,user['id'])).fetchone()
            if pending:raise db.error(409,'TRIP_PENDING_ORDERS','أكمل الطلبات أو سلّمها وفق الإجراء المعتمد أولاً.')
            stop=c.execute('SELECT id,kind FROM driver_trip_stops WHERE trip_id=%s AND departed_at IS NULL FOR UPDATE',(trip_id,)).fetchone()
            if stop and stop['kind']!='return':
                raise db.error(409,'TRIP_STOP_OPEN','أكد مغادرة المحطة الحالية قبل العودة.')
            trip=c.execute('UPDATE driver_work_trips SET returned_at=now(),return_branch_id=%s,return_evidence=%s WHERE id=%s RETURNING *',(body.branch_id,db.json(evidence),trip_id)).fetchone()
            if stop:c.execute('UPDATE driver_trip_stops SET departed_at=%s WHERE id=%s',(trip['returned_at'],stop['id']))
            record_event(c,user,trip_id,'return',body.branch_id,evidence)
            return {'trip':trip,'shift_id':s['id']}
        return mutate(c,user,request,'driver.work.return:'+trip_id,body.model_dump(),perform)


def require_report_access(user):
    if user['role']!='owner' and not auth.has_permission(user,'driver_hours_reports'):
        raise db.error(403,'DRIVER_HOURS_PERMISSION','تقرير الساعات غير مصرح به.')


def report_scope_contains(branch_ids, allowed):
    # Empty historical scopes provide no evidence of branch authorization.
    assigned=set(branch_ids or [])
    return bool(assigned) and assigned <= allowed


def build_report(c,user,start=None,end=None,driver_id=None,branch_id=None):
    require_report_access(user)
    brand=c.execute('SELECT settings FROM brands WHERE id=%s',(user['brand_id'],)).fetchone()
    setting=c.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(user['brand_id'],)).fetchone()
    config={**brand['settings'],**(setting['settings'] if setting else {})}
    minutes,configured=day_start(config)
    now=c.execute('SELECT now() AS n').fetchone()['n']
    today=(now.astimezone(RIYADH)-timedelta(minutes=minutes)).date()
    try:
        first=date.fromisoformat(str(start)) if start else today
        last=date.fromisoformat(str(end)) if end else first
    except ValueError:raise db.error(422,'DRIVER_HOURS_DATE','التاريخ غير صحيح.')
    if last<first or (last-first).days>366:raise db.error(422,'DRIVER_HOURS_RANGE','اختر فترة لا تتجاوز 367 يومًا.')
    lo=datetime.combine(first,time(),RIYADH)+timedelta(minutes=minutes)
    hi=datetime.combine(last+timedelta(days=1),time(),RIYADH)+timedelta(minutes=minutes)
    branches=c.execute('SELECT id,name FROM branches WHERE brand_id=%s ORDER BY branch_no',(user['brand_id'],)).fetchall()
    brand_branches={b['id'] for b in branches}
    allowed=brand_branches if user['role']=='owner' else set(user.get('branch_ids') or []) & brand_branches
    if branch_id and branch_id not in allowed:raise db.error(403,'DRIVER_HOURS_BRANCH','الفرع خارج الصلاحية.')
    people=c.execute("SELECT id,display_name,branch_ids FROM users WHERE brand_id=%s AND role='driver' ORDER BY display_name,id",(user['brand_id'],)).fetchall()
    if driver_id and not any(p['id']==driver_id and (user['role']=='owner' or report_scope_contains(p['branch_ids'],allowed)) for p in people):raise db.error(404,'DRIVER_HOURS_DRIVER','السائق خارج الصلاحية.')
    drivers=[]
    for p in people:
        if driver_id and p['id']!=driver_id:continue
        ss=c.execute("SELECT * FROM shifts WHERE user_id=%s AND brand_id=%s AND kind='driver' AND opened_at<%s AND COALESCE(submitted_at,closed_at,%s)>%s ORDER BY opened_at",(p['id'],user['brand_id'],hi,now,lo)).fetchall()
        # Multi-branch shifts cannot be divided into invented per-branch hours.
        ss=[s for s in ss if (user['role']=='owner' or report_scope_contains(s['branch_ids'],allowed)) and (not branch_id or branch_id in s['branch_ids'])]
        if user.get('_morning_manager_context'):
            from .morning_manager_context import shift_allowed
            ss=[s for s in ss if shift_allowed(user,s)]
        if not ss:continue
        sids=[s['id'] for s in ss]
        bs=c.execute('SELECT started_at,ended_at FROM driver_breaks WHERE shift_id=ANY(%s) AND started_at IS NOT NULL',(sids,)).fetchall()
        ts=c.execute('SELECT t.*,(SELECT count(*) FROM driver_work_trip_orders x WHERE x.trip_id=t.id) AS order_count FROM driver_work_trips t WHERE t.shift_id=ANY(%s) AND t.departed_at<%s AND COALESCE(t.returned_at,%s)>%s ORDER BY departed_at',(sids,hi,now,lo)).fetchall()
        missing=c.execute('SELECT DISTINCT t.shift_id FROM driver_tasks t WHERE t.shift_id=ANY(%s) AND t.picked_up_at IS NOT NULL AND NOT EXISTS(SELECT 1 FROM driver_work_trip_orders x JOIN driver_work_trips w ON w.id=x.trip_id WHERE x.order_id=t.order_id AND w.shift_id=t.shift_id)',(sids,)).fetchall()
        missing_ids={r['shift_id'] for r in missing}
        # No historical return evidence: keep residual time unknown, never invent waiting.
        unknown=[(s['opened_at'],s.get('submitted_at') or s.get('closed_at') or now) for s in ss if s['id'] in missing_ids]
        stops=[]
        for s in ss:
            events=c.execute("SELECT created_at,details FROM audit_events WHERE entity_type='shift' AND entity_id=%s AND action='shift.availability' ORDER BY created_at,id",(s['id'],)).fetchall()
            paused=None
            for e in events:
                if e['details'].get('available') is False and e['details'].get('nonworking') is True and paused is None:paused=e['created_at']
                elif e['details'].get('available') is True and paused is not None:stops.append((paused,e['created_at']));paused=None
            if paused:stops.append((paused,s.get('submitted_at') or s.get('closed_at') or now))
        m=metrics(ss,[(b['started_at'],b['ended_at'] or now) for b in bs],ts,lo,hi,now,stops,unknown)
        count=c.execute("SELECT count(DISTINCT order_id) n,avg(extract(epoch FROM(delivered_at-picked_up_at))) FILTER(WHERE delivered_at>=picked_up_at) average FROM effective_driver_tasks WHERE shift_id=ANY(%s) AND status='delivered' AND delivered_at>=%s AND delivered_at<%s",(sids,lo,hi)).fetchone()
        activity_tasks=c.execute("SELECT order_id,status,picked_up_at,delivered_at FROM driver_tasks WHERE shift_id=ANY(%s) AND driver_id=%s AND (delivered_at>=%s OR (status IN ('picked_up','problem') AND picked_up_at<%s))",(sids,p['id'],lo,hi)).fetchall()
        activity=order_activity(ss,activity_tasks,[(b['started_at'],b['ended_at'] or now) for b in bs],lo,hi,now,stops)
        evidence=report_completed_orders(c,user,p['id'],sids,allowed,lo,hi,now)
        confirmed=[t for t in ts if t['returned_at']]
        for t in ts:
            t['status']='returned' if t['returned_at'] else 'awaiting_return'
            t['duration_seconds']=(t['returned_at']-t['departed_at']).total_seconds() if t['returned_at'] else None
            t['period_seconds']=seconds(clipped([(t['departed_at'],t['returned_at'])],lo,hi)) if t['returned_at'] else None
        drivers.append({'driver_id':p['id'],'name':p['display_name'],'shift_status':'open' if any(s['status']=='open' for s in ss) else ss[-1]['status'],**m,**activity,
                        'trip_count':len(confirmed),'pending_trip_count':len(ts)-len(confirmed),'order_count':count['n'],
                        'average_trip_seconds':sum(t['duration_seconds'] for t in confirmed)/len(confirmed) if confirmed else None,
                        'average_order_delivery_seconds':float(count['average']) if count['average'] is not None else None,
                        'missing_trip_evidence':bool(missing_ids),'trips':ts,**evidence})
    keys=('shift_seconds','actual_seconds','delivery_seconds','waiting_seconds','break_seconds','other_work_seconds','unclassified_seconds','trip_count','pending_trip_count','order_count','order_activity_seconds','active_delivery_seconds','remaining_shift_seconds','missing_order_timing_count')
    totals={key:sum(d[key] for d in drivers) for key in keys}
    totals['order_utilization_percent']=round(totals['order_activity_seconds']/totals['shift_seconds']*100,2) if totals['shift_seconds'] else 0
    return safe({'start':first,'end':last,'timezone':'Asia/Riyadh','operational_day_start':f'{minutes//60:02d}:{minutes%60:02d}',
                 'operational_day_configured':configured,'warning':None if configured else 'لم يحدد بدء اليوم التشغيلي؛ عرض يوم تقويمي بتوقيت الرياض',
                 'branch_filter_basis':'shift_scope_membership; multi-branch hours are not additive across branch filters',
                 'operational_date':today,'options':{'drivers':[{'id':p['id'],'name':p['display_name']} for p in people if user['role']=='owner' or report_scope_contains(p['branch_ids'],allowed)]},'totals':totals,'drivers':drivers,'branches':[b for b in branches if b['id'] in allowed]})


@router.get('/report')
def report(start:str|None=None,end:str|None=None,driver_id:str|None=None,branch_id:str|None=None,user=Depends(auth.current_user)):
    with db.tx() as c:return build_report(c,user,start,end,driver_id,branch_id)


@router.get('/current')
def current_trip(user=Depends(auth.current_user)):
    roles(user,'driver')
    if not auth.has_permission(user,'tasks_view'):raise db.error(403,'TRIP_PERMISSION','صلاحية عرض المهام غير متاحة.')
    with db.tx() as c:
        t=c.execute('SELECT * FROM driver_work_trips WHERE driver_id=%s AND brand_id=%s AND returned_at IS NULL',(user['id'],user['brand_id'])).fetchone()
        if t:
            shift_access(c,user,c.execute('SELECT * FROM shifts WHERE id=%s',(t['shift_id'],)).fetchone())
        return safe({'trip':t})


def guard_shift_completion(c,shift):
    """Caller holds the shift row lock; no close can strand a pending return."""
    if shift['kind']=='driver' and c.execute(
            'SELECT 1 FROM driver_work_trips WHERE shift_id=%s AND returned_at IS NULL',
            (shift['id'],)).fetchone():
        raise db.error(409,'TRIP_RETURN_REQUIRED','أكد العودة إلى الفرع قبل تقفيلة الشفت.')
