"""Prospective work missions and conservative, server-observed branch returns.

GPS is evidence only while an authorized live session advances the latest point.
An offline upload cannot manufacture elapsed server observation time. Historical
orders are deliberately never converted into completed work missions here.
"""
from datetime import datetime, timedelta, timezone
from math import asin, cos, isfinite, radians, sin, sqrt
from uuid import uuid4
from fastapi import HTTPException
from . import auth, db

RADIUS_M = 100
MAX_ACCURACY_M = 35
MAX_AGE_SECONDS = 120
MAX_GAP_SECONDS = 45
DWELL_SECONDS = 60
MIN_POINTS = 3
TERMINAL = ('delivered', 'cancelled', 'transferred', 'reassigned')


def _stamp(value):
    if isinstance(value, str):
        try: value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError: return None
    return value if isinstance(value, datetime) and value.tzinfo is not None else None


def _location(branch):
    from .catalog import location_verified
    if not location_verified(branch): return None
    pin = (branch.get('settings') or {}).get('location') or {}
    try:
        lat, lng = float(pin['lat']), float(pin['lng'])
        if not all(map(isfinite, (lat, lng))) or not (-90 <= lat <= 90 and -180 <= lng <= 180): return None
        return lat, lng
    except (ValueError, TypeError, KeyError): return None


def _distance(lat, lng, target):
    a, b, c, d = map(radians, (lat, lng, *target))
    return 6371000 * 2 * asin(min(1, sqrt(sin((a-c)/2)**2 + cos(a)*cos(c)*sin((b-d)/2)**2)))


def evaluate_point(state, point, branch, now, eligible_after, started_at):
    """Return new JSON state and evidenced first arrival, never estimated return."""
    state = dict(state or {})
    target, captured = _location(branch), _stamp(point.get('captured_at'))
    previous = _stamp(state.get('last_captured_at'))
    def reset(reason):
        for key in ('inside_since', 'received_since', 'inside_count'):
            state.pop(key, None)
        state['status'] = reason
        return state, None
    def clear_geofence():
        # Session identity and observed terminal tasks do not depend on the
        # branch pin. Only location/departure/dwell evidence must be discarded
        # when initializing, changing or losing the verified geofence.
        independent={key:state[key] for key in ('session_id','terminal_signature','terminal_observed_at') if key in state}
        state.clear()
        state.update(independent)
    try:
        lat, lng, accuracy = (float(point[k]) for k in ('lat','lng','accuracy_m'))
    except (ValueError, TypeError, KeyError): return reset('invalid_point')
    if target is None:
        clear_geofence()
        return reset('branch_location_missing')
    target_key=','.join(format(value,'.8f') for value in target)
    if state.get('target_key')!=target_key:
        clear_geofence()
        state['target_key']=target_key
        previous=None
    if (not captured or not started_at or captured < started_at or
            not 0 <= (now-captured).total_seconds() <= MAX_AGE_SECONDS or
            not all(map(isfinite, (lat,lng,accuracy))) or not -90<=lat<=90 or not -180<=lng<=180 or
            not 0<=accuracy<=MAX_ACCURACY_M):
        return reset('location_unreliable')
    if previous and captured <= previous: return reset('out_of_order')
    if previous and (captured-previous).total_seconds() > MAX_GAP_SECONDS:
        reset('location_gap')
    state.update(last_captured_at=captured.isoformat(), last_point_id=point.get('id'))
    distance = _distance(lat, lng, target)
    if distance-accuracy > RADIUS_M:
        state['outside_seen_at'] = captured.isoformat()
        return reset('away_from_return_branch')
    if distance+accuracy > RADIUS_M: return reset('boundary_uncertain')
    if not _stamp(state.get('outside_seen_at')): return reset('departure_not_observed')
    if eligible_after is None or captured <= eligible_after: return reset('orders_pending')
    inside = _stamp(state.get('inside_since'))
    received = _stamp(state.get('received_since'))
    if inside is None or received is None or inside <= eligible_after:
        inside, received = captured, now
        state.update(inside_since=inside.isoformat(), received_since=received.isoformat(), inside_count=0)
    state['inside_count'] = int(state.get('inside_count',0))+1
    state['status'] = 'confirming_arrival'
    if (state['inside_count'] >= MIN_POINTS and (captured-inside).total_seconds() >= DWELL_SECONDS
            and (now-received).total_seconds() >= DWELL_SECONDS):
        state['status'] = 'returned'
        return state, inside
    return state, None


def physical_scope(c, user, shift):
    """Read current grants for every leg; grouping itself grants no permission."""
    from .central_workdays import association, authorized
    from .operations import shift_access
    if shift['user_id'] != user['id'] or user['role'] != 'driver' or not user.get('active'):
        raise db.error(403,'AUTO_RETURN_SCOPE','الرحلة خارج نطاق السائق.')
    if association(c, shift['id']):
        _, pairs = authorized(c,user,shift['id'],lock=False)
    else:
        shift_access(c,user,shift)
        pairs = [(user,shift)]
    if any(s['user_id']!=user['id'] or s['kind']!='driver' or s['status']!='open' or
           not auth.has_permission(p,'tasks_update') for p,s in pairs):
        raise db.error(403,'AUTO_RETURN_SCOPE','الوردية غير متاحة لتسجيل العودة.')
    return {s['id']:(p,s) for p,s in pairs}


def _optional_scope(c,user,shift):
    # An incomplete shared scope stops certification without blocking a
    # separately authorized acceptance or storing the driver's GPS evidence.
    try: return physical_scope(c,user,shift)
    except HTTPException as exc:
        if exc.status_code in (403,404,409): return None
        raise


def choose_initial_target(c,user,scope,task):
    """A live authorized published route may select another return branch."""
    from .route_eta import published_rows, validated_publication
    for saved in published_rows(c,user['id']):
        bid=saved.get('return_branch_id')
        shifts=set(saved.get('published_shift_ids') or [])
        if not bid or task['shift_id'] not in shifts or not shifts<=set(scope): continue
        task_ids={tid for stop in saved.get('published_stops') or []
                  for tid in (stop.get('task_ids') or [stop.get('task_id')]) if tid}
        if task['id'] not in task_ids: continue
        branch=c.execute('SELECT * FROM branches WHERE id=%s',(bid,)).fetchone()
        if not branch or not _location(branch): continue
        if not any(branch['brand_id']==s['brand_id'] and bid in s['branch_ids'] and bid in p.get('branch_ids',[]) for p,s in scope.values()): continue
        if validated_publication(c,saved,user['id']) is not None: return bid
    return task['branch_id']


def ensure_mission(c, user, shift, task, now=None):
    """Called inside a successful genuine acceptance transaction, never on reads."""
    if user.get('role')!='driver' or task.get('driver_id')!=user['id']: return None
    session=c.execute('SELECT id FROM sessions WHERE id=%s AND user_id=%s AND revoked_at IS NULL AND expires_at>now() FOR SHARE',
                      (user.get('session_id'),user['id'])).fetchone()
    if not session: return None
    now = now or c.execute('SELECT now() n').fetchone()['n']
    scope = _optional_scope(c,user,shift)
    if not scope: return None
    actual=c.execute('SELECT t.*,o.brand_id AS order_brand_id,o.branch_id,o.test_void FROM driver_tasks t JOIN orders o ON o.id=t.order_id WHERE t.id=%s FOR UPDATE OF t',(task['id'],)).fetchone()
    accepted = actual.get('accepted_at') if actual else None
    if (not actual or actual['driver_id']!=user['id'] or actual['shift_id']!=shift['id'] or
            actual['status'] not in ('accepted','picked_up') or not accepted or not 0 <= (now-accepted).total_seconds() <= 30 or
            actual['order_brand_id']!=shift['brand_id'] or actual['branch_id'] not in shift['branch_ids'] or actual['test_void'] is not None):
        return None
    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('automatic-driver-mission:'+user['id'],))
    trip=c.execute('SELECT * FROM driver_work_trips WHERE driver_id=%s AND returned_at IS NULL FOR UPDATE',(user['id'],)).fetchone()
    if trip and trip['shift_id'] not in scope: return None
    linked=c.execute('SELECT * FROM driver_work_trips WHERE driver_id=%s AND shift_id=ANY(%s) AND auto_task_ids ? %s',(user['id'],list(scope),actual['id'])).fetchone()
    if linked: return linked
    if not trip:
        evidence={'source':'automatic_task_acceptance','task_id':actual['id'],'accepted_at':accepted.isoformat()}
        target=choose_initial_target(c,user,scope,actual)
        trip=c.execute('INSERT INTO driver_work_trips(id,brand_id,shift_id,driver_id,departure_branch_id,departed_at,departure_evidence,planned_return_branch_id,auto_task_ids) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
            (str(uuid4()),shift['brand_id'],shift['id'],user['id'],actual['branch_id'],accepted,db.json(evidence),target,db.json([actual['id']]))).fetchone()
        from .driver_work_hours import record_event
        record_event(c,user,trip['id'],'depart',actual['branch_id'],evidence)
    elif trip.get('auto_task_ids'):
        ids=list(trip['auto_task_ids'])+[actual['id']]
        trip=c.execute('UPDATE driver_work_trips SET auto_task_ids=%s WHERE id=%s RETURNING *',(db.json(ids),trip['id'])).fetchone()
    c.execute('INSERT INTO driver_work_trip_orders(trip_id,order_id) VALUES(%s,%s) ON CONFLICT DO NOTHING',(trip['id'],actual['order_id']))
    return trip


def set_return_branch(c,user,trip_id,branch_id):
    """Caller authorizes manager plan; this also checks the driver's live grants."""
    trip=c.execute('SELECT * FROM driver_work_trips WHERE id=%s AND returned_at IS NULL FOR UPDATE',(trip_id,)).fetchone()
    if not trip: return None
    driver=c.execute('SELECT * FROM users WHERE id=%s AND active',(trip['driver_id'],)).fetchone()
    shift=c.execute('SELECT * FROM shifts WHERE id=%s',(trip['shift_id'],)).fetchone()
    if not driver or not shift: return None
    from . import multibrand
    principal=multibrand.project(c,driver,shift['brand_id'])
    if not principal: return None
    scope=physical_scope(c,principal,shift)
    valid=any(branch_id in s['branch_ids'] and branch_id in p.get('branch_ids',[]) for p,s in scope.values())
    if not valid: raise db.error(403,'AUTO_RETURN_BRANCH_SCOPE','فرع العودة خارج الوردية المخولة.')
    if trip.get('planned_return_branch_id')!=branch_id:
        c.execute("UPDATE driver_work_trips SET planned_return_branch_id=%s,auto_return_state='{}' WHERE id=%s",(branch_id,trip_id))
        db.audit(c,user['id'],'driver.trip.return_target','driver_work_trip',trip_id,{'branch_id':branch_id})
    return branch_id


def consider_point(c,user,shift,point,now):
    """Called only for an inserted point that advances authorized live location."""
    scope=_optional_scope(c,user,shift)
    if not scope: return None
    trip=c.execute('SELECT * FROM driver_work_trips WHERE driver_id=%s AND returned_at IS NULL FOR UPDATE',(user['id'],)).fetchone()
    if not trip or trip['shift_id'] not in scope: return None
    bid=trip.get('planned_return_branch_id')
    if not bid: return None
    branch=c.execute('SELECT * FROM branches WHERE id=%s',(bid,)).fetchone()
    if not branch or not any(branch['brand_id']==s['brand_id'] and bid in s['branch_ids'] and bid in p.get('branch_ids',[]) for p,s in scope.values()): return None
    tasks=c.execute('SELECT t.*,x.order_id AS linked_order_id,o.brand_id AS order_brand_id,o.branch_id,o.test_void FROM driver_work_trip_orders x JOIN orders o ON o.id=x.order_id LEFT JOIN driver_tasks t ON t.order_id=x.order_id AND t.driver_id=%s AND t.shift_id=ANY(%s) WHERE x.trip_id=%s', (user['id'],list(scope),trip['id'])).fetchall()
    ids=trip.get('auto_task_ids') or []
    if ids:
        linked_orders={t['linked_order_id'] for t in tasks}
        tasks=[t for t in tasks if t.get('id') in ids]
        if set(ids)!={t['id'] for t in tasks} or linked_orders!={t['order_id'] for t in tasks}: return None
    if not tasks: return None
    valid=all(t.get('id') and t['accepted_at'] and t['shift_id'] in scope and
              t['order_brand_id']==scope[t['shift_id']][1]['brand_id'] and
              t['branch_id'] in scope[t['shift_id']][1]['branch_ids'] and t['test_void'] is None for t in tasks)
    if not valid: return None
    terminal=all(t['status'] in TERMINAL for t in tasks)
    finished=[t['delivered_at'] or t['accepted_at'] for t in tasks]
    eligible_after=max(finished) if terminal else None
    state=trip.get('auto_return_state') or {}
    if state.get('session_id')!=user.get('session_id'):
        state={k:v for k,v in state.items() if k=='outside_seen_at'}
        state['session_id']=user.get('session_id')
    if terminal and any(t['status']!='delivered' for t in tasks):
        # Cancellation and handover have no dedicated terminal timestamp in the
        # task schema. Certify only an arrival after observing the terminal set.
        signature=sorted(t['id']+':'+t['status'] for t in tasks)
        if state.get('terminal_signature')!=signature:
            state['terminal_signature']=signature
            state['terminal_observed_at']=now.isoformat()
        eligible_after=max(eligible_after,_stamp(state['terminal_observed_at']))
    new,arrival=evaluate_point(state,point,branch,now,eligible_after,trip['departed_at'])
    c.execute('UPDATE driver_work_trips SET auto_return_state=%s WHERE id=%s',(db.json(new),trip['id']))
    if arrival is None: return None
    stop=c.execute('SELECT id,kind FROM driver_trip_stops WHERE trip_id=%s AND departed_at IS NULL FOR UPDATE',(trip['id'],)).fetchone()
    if stop and stop['kind']!='return':
        new['status']='stop_open'
        c.execute('UPDATE driver_work_trips SET auto_return_state=%s WHERE id=%s',(db.json(new),trip['id']))
        return None
    evidence={'source':'automatic_gps_dwell','arrival_at':arrival.isoformat(),'confirmed_at':now.isoformat(),
              'point_id':point.get('id'),'session_id':user.get('session_id'),'accuracy_m':point['accuracy_m'],
              'radius_m':RADIUS_M,'dwell_seconds':DWELL_SECONDS,'point_count':new['inside_count']}
    updated=c.execute('UPDATE driver_work_trips SET returned_at=%s,return_branch_id=%s,return_evidence=%s WHERE id=%s AND returned_at IS NULL RETURNING *',(arrival,bid,db.json(evidence),trip['id'])).fetchone()
    if not updated: return None
    if stop:c.execute('UPDATE driver_trip_stops SET departed_at=greatest(arrived_at,%s) WHERE id=%s',(arrival,stop['id']))
    from .driver_work_hours import record_event
    record_event(c,user,trip['id'],'return',bid,evidence)
    return updated


def describe_current(c,user,trip):
    shift=c.execute('SELECT * FROM shifts WHERE id=%s',(trip['shift_id'],)).fetchone()
    if not shift: return None
    scope=_optional_scope(c,user,shift)
    if not scope: return {'enabled':True,'status':'needs_review','reason':'physical_scope_incomplete'}
    bid=trip.get('planned_return_branch_id')
    if not bid and (trip.get('departure_evidence') or {}).get('source')!='automatic_task_acceptance':
        return {'enabled':False,'status':'needs_review','reason':'legacy_explicit_return'}
    branch=c.execute('SELECT * FROM branches WHERE id=%s',(bid,)).fetchone() if bid else None
    if not branch or not any(branch['brand_id']==s['brand_id'] and bid in s['branch_ids'] and bid in p.get('branch_ids',[]) for p,s in scope.values()):
        return {'enabled':True,'status':'needs_review','reason':'return_branch_missing'}
    pin=_location(branch)
    state=trip.get('auto_return_state') or {}
    raw=state.get('status','awaiting_location') if pin else 'branch_location_missing'
    if pin and raw not in ('departure_not_observed','stop_open'):
        server_now=c.execute('SELECT now() n').fetchone()['n']
        last_captured=_stamp(state.get('last_captured_at'))
        if last_captured and not 0 <= (server_now-last_captured).total_seconds() <= MAX_AGE_SECONDS:
            raw='location_stale'
        elif last_captured is None:
            raw='awaiting_location'
    status=('confirming_return' if raw=='confirming_arrival' else
            'awaiting_deliveries' if raw=='orders_pending' else
            'awaiting_return' if raw=='away_from_return_branch' else
            'needs_review' if raw in ('branch_location_missing','departure_not_observed','stop_open') else 'awaiting_location')
    return {'enabled':True,'status':status,
            'return_branch_id':bid,'return_branch_name':branch['name'],
            'return_location':{'lat':pin[0],'lng':pin[1]} if pin else None,
            'reason':raw if pin else 'verified_branch_location_required'}
