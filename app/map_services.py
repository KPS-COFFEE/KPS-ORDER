"""Scoped dispatch suggestions and connection evidence; never auto-assigns a task."""
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query
from . import auth, db, catalog, dispatch_map
from .operations import order_access, safe, shift_access

router = APIRouter(prefix='/api/operations/map-services', tags=['operations'])
STAFF = ('owner', 'delivery_manager', 'delivery_staff')


def require(user, permission='drivers_view'):
    if not auth.has_permission(user, permission):
        raise HTTPException(403, 'الصلاحية غير متاحة.')


def quality(row, now=None):
    now = now or datetime.now(timezone.utc)
    stamp = row.get('updated_at')
    age = max(0, (now - stamp).total_seconds()) if stamp else None
    accuracy = row.get('accuracy_m')
    state = 'missing' if age is None else 'stale' if age > 120 else 'approximate' if accuracy is None or accuracy > 100 else 'live'
    return {'state': state, 'age_seconds': round(age) if age is not None else None,
            'accuracy_m': accuracy, 'updated_at': stamp}


def record_sample(c, user, shift, body, previous, stamp, near_customer=True):
    """Minute buckets contain timings/accuracy only, never a position history.

    Gaps prove absence of received samples, not an app crash or its cause.
    """
    gap = max(0, (stamp - previous['updated_at']).total_seconds()) if previous and previous['shift_id'] == shift['id'] else 0
    c.execute('''INSERT INTO driver_location_health(brand_id,driver_id,shift_id,minute,received_count,max_gap_seconds,worst_accuracy_m)
      VALUES(%s,%s,%s,date_trunc('minute',%s::timestamptz),1,%s,%s)
      ON CONFLICT(shift_id,minute) DO UPDATE SET received_count=driver_location_health.received_count+1,
      max_gap_seconds=GREATEST(driver_location_health.max_gap_seconds,EXCLUDED.max_gap_seconds),
      worst_accuracy_m=GREATEST(driver_location_health.worst_accuracy_m,EXCLUDED.worst_accuracy_m)''',
      (user['brand_id'], user['id'], shift['id'], stamp, round(gap), body.accuracy_m))


    if near_customer and body.accuracy_m <= 50:
        tasks=c.execute("SELECT t.id,t.order_id,o.customer FROM driver_tasks t JOIN orders o ON o.id=t.order_id AND o.brand_id=%s WHERE t.shift_id=%s AND t.status='picked_up' AND o.completed_at IS NULL",(user['brand_id'],shift['id'])).fetchall()
        for task in tasks:
            destination=(task['customer'] or {}).get('location')
            distance=catalog.direct_distance({'lat':body.lat,'lng':body.lng},destination)
            if distance is not None and distance+body.accuracy_m<=150:
                c.execute("""INSERT INTO order_events(id,order_id,actor_id,event_type,details)
                  SELECT %s,%s,%s,'order.gps_near_customer',%s WHERE NOT EXISTS
                  (SELECT 1 FROM order_events WHERE order_id=%s AND event_type='order.gps_near_customer' AND details->>'task_id'=%s)""",
                  (uuid4().hex,task['order_id'],user['id'],db.json({'task_id':task['id'],'distance_m':round(distance),'accuracy_m':body.accuracy_m,'confirmed_delivery':False}),task['order_id'],task['id']))


@router.get('/recommendations')
def recommendations(order_id: str = Query(min_length=1, max_length=200), user=Depends(auth.require_roles(*STAFF))):
    require(user, 'delivery_assign')
    with db.tx() as c:
        order = c.execute('SELECT * FROM orders WHERE id=%s', (order_id,)).fetchone()
        order_access(c, user, order)
        if order['service'] != 'delivery' or order.get('completed_at') or order['preparation_status'] in ('cancelled','rejected','completed'):
            raise HTTPException(409, 'الطلب غير متاح للإسناد.')
        branch = c.execute('SELECT * FROM branches WHERE id=%s AND brand_id=%s', (order['branch_id'],user['brand_id'])).fetchone()
        origin = (branch['settings'] or {}).get('location') if branch and catalog.location_verified(branch) else None
        rows = c.execute('''SELECT s.*,u.display_name,g.lat,g.lng,g.accuracy_m,g.updated_at,
          (SELECT count(*) FROM driver_tasks t WHERE t.shift_id=s.id AND t.status NOT IN ('delivered','cancelled','transferred','reassigned')) active_tasks
          FROM shifts s JOIN users u ON u.id=s.user_id AND u.brand_id=s.brand_id
          LEFT JOIN driver_latest_locations g ON g.driver_id=u.id AND g.shift_id=s.id AND g.brand_id=s.brand_id
          WHERE s.brand_id=%s AND s.kind='driver' AND s.status='open' AND s.available AND u.active AND u.role='driver'
          AND s.branch_ids ? %s AND u.branch_ids ? %s ORDER BY s.opened_at,s.id''', (user['brand_id'], order['branch_id'],order['branch_id'])).fetchall()
        from . import smart_dispatch
        policy = smart_dispatch.policy(c, user)
        choices = []
        for row in rows:
            try:
                if user.get('_morning_manager_context'):shift_access(c,user,row)
                # Canonical checks preserve morning duty, parent manager and revocation rules.
                smart_dispatch.candidate(c,user,order,row,policy)
                from . import morning_delivery
                morning_delivery.authorize_order(c,user,order)
            except HTTPException:
                continue
            gps = quality(row)
            distance = round(catalog.direct_distance(origin, row), 0) if origin and gps['state']=='live' else None
            choices.append({'shift_id':row['id'], 'driver_id':row['user_id'], 'display_name':row['display_name'],
                            'active_tasks':row['active_tasks'], 'distance_m':distance, 'gps':gps})
        # Load first; among equally loaded drivers prefer a reliable nearby location.
        choices.sort(key=lambda r:(r['active_tasks'], r['distance_m'] is None, r['distance_m'] or 0, r['shift_id']))
        return safe({'drivers':choices,'distance_basis':'straight_line_to_pickup','assignment':'manager_confirmation',
                     'recommended_shift_id':next((r['shift_id'] for r in choices if r['distance_m'] is not None),None)})


@router.get('/health')
def health(shift_id: str = Query(min_length=1,max_length=200), user=Depends(auth.require_roles(*STAFF))):
    require(user)
    with db.tx() as c:
        shift = c.execute('SELECT * FROM shifts WHERE id=%s', (shift_id,)).fetchone()
        if not shift or shift['kind']!='driver':
            raise HTTPException(404,'الشفت غير موجود.')
        shift_access(c,user,shift)
        rows = c.execute('''SELECT minute,received_count,max_gap_seconds,worst_accuracy_m FROM driver_location_health
          WHERE shift_id=%s AND brand_id=%s ORDER BY minute LIMIT 1440''', (shift_id,user['brand_id'])).fetchall()
        return safe({'shift_id':shift_id,'buckets':rows,'received_count':sum(r['received_count'] for r in rows),
                     'largest_received_gap_seconds':max((r['max_gap_seconds'] for r in rows),default=None),
                     'cause':'unknown','evidence':'server_received_samples','history_complete':False})


def gps_join_predicate():
    # A single-brand schema deliberately has no central_workday_legs table.
    # Do not reference an optional relation even inside a false SQL OR branch:
    # PostgreSQL resolves every relation when it parses the statement.
    from .multibrand import enabled
    same_shift="g.shift_id=s.id AND g.brand_id=s.brand_id"
    same_day=(" OR EXISTS (SELECT 1 FROM central_workday_legs a "
              "JOIN central_workday_legs b ON b.workday_id=a.workday_id "
              "WHERE a.shift_id=g.shift_id AND b.shift_id=s.id)") if enabled() else ''
    return "g.driver_id=u.id AND ("+same_shift+same_day+")"


@router.get('/alerts')
def alerts(user=Depends(auth.require_roles(*STAFF))):
    require(user)
    if user.get('central_operations') and not user.get('_map_brand_scoped') and not user.get('_morning_manager_context'):
        from . import multibrand
        result=[]
        with db.tx() as c:
            principals=[(scope['id'],multibrand.project(c,user,scope['id'])) for scope in user['central_operations']['brands']]
        # Do not hold an outer pool slot while canonical reads acquire theirs.
        for brand_id,principal in principals:
            if principal and auth.has_permission(principal,'drivers_view'):
                value=alerts(dict(principal,_map_brand_scoped=True,_map_route_viewer=user))
                result.extend(dict(item,brand_id=brand_id) for item in value['alerts'])
        return safe({'alerts':result})
    with db.tx() as c:
        rows=c.execute('''SELECT s.id shift_id,u.id driver_id,u.display_name,s.branch_ids,u.brand_id driver_brand_id,u.branch_ids driver_branches,g.updated_at,g.accuracy_m,
          min(t.picked_up_at) departed_at,count(t.id) active_tasks
          FROM shifts s JOIN users u ON u.id=s.user_id
          JOIN driver_tasks t ON t.shift_id=s.id AND t.status='picked_up'
          JOIN orders o ON o.id=t.order_id AND o.brand_id=s.brand_id
          LEFT JOIN driver_latest_locations g ON _GPS_SCOPE_
          WHERE s.brand_id=%s AND s.kind='driver' AND s.status='open' AND u.active AND u.role='driver'
          AND (%s OR o.branch_id=ANY(%s)) AND o.completed_at IS NULL
          GROUP BY s.id,u.id,g.updated_at,g.accuracy_m'''.replace('_GPS_SCOPE_',gps_join_predicate()),(user['brand_id'],user['role']=='owner' and not user.get('home_brand_id'),user.get('branch_ids',[]))).fetchall()
        result=[]
        visible_driver_ids=set()
        now=datetime.now(timezone.utc)
        for row in rows:
            from .multibrand import project
            target=project(c,{'id':row['driver_id'],'role':'driver','active':True,'brand_id':row['driver_brand_id'],'branch_ids':row['driver_branches']},user['brand_id'])
            if not target or not set(row['branch_ids'])<=set(target['branch_ids']):continue
            visible_driver_ids.add(row['driver_id'])
            gps=quality(row,now)
            # Give first departure two minutes to acquire a fix.
            if gps['state']=='missing' and row['departed_at'] and (now-row['departed_at']).total_seconds()<120:
                continue
            if gps['state'] in ('missing','stale','approximate'):
                result.append({'driver_id':row['driver_id'],'display_name':row['display_name'],'shift_id':row['shift_id'],
                               'kind':'location_'+gps['state'],'gps':gps,'cause':'unknown'})
        near=c.execute('''SELECT DISTINCT u.id driver_id,u.display_name,s.id shift_id
          FROM order_events e JOIN driver_tasks t ON t.id=e.details->>'task_id' AND t.order_id=e.order_id AND t.status='picked_up'
          JOIN orders o ON o.id=t.order_id AND o.brand_id=%s AND o.completed_at IS NULL
          JOIN users u ON u.id=t.driver_id AND u.active AND u.role='driver'
          JOIN shifts s ON s.id=t.shift_id AND s.brand_id=o.brand_id AND s.status='open'
          JOIN driver_latest_locations g ON _GPS_SCOPE_
          WHERE e.event_type='order.gps_near_customer' AND e.created_at>now()-interval '2 minutes'
          AND g.updated_at>now()-interval '120 seconds' AND (%s OR o.branch_id=ANY(%s))'''.replace('_GPS_SCOPE_',gps_join_predicate()),
          (user['brand_id'],user['role']=='owner' and not user.get('home_brand_id'),user.get('branch_ids',[]))).fetchall()
        result.extend({**r,'kind':'near_customer','confirmed_delivery':False} for r in near if r['driver_id'] in visible_driver_ids)
        if user.get('_morning_manager_context'):
            from .morning_manager_context import shift_allowed
            result=[r for r in result if shift_allowed(user,c.execute('SELECT * FROM shifts WHERE id=%s',(r['shift_id'],)).fetchone())]
        from .tracking_alerts import for_scope
        result.extend(for_scope(c,user))
        return safe({'alerts':result})


@router.get('/zones')
def zones(user=Depends(auth.require_roles(*STAFF))):
    require(user,'orders_view')
    if user.get('central_operations') and not user.get('_map_brand_scoped') and not user.get('_morning_manager_context'):
        from . import multibrand
        result=[]
        with db.tx() as c:
            principals=[(scope['id'],multibrand.project(c,user,scope['id'])) for scope in user['central_operations']['brands']]
        # Do not hold an outer pool slot while canonical reads acquire theirs.
        for brand_id,principal in principals:
            if principal and auth.has_permission(principal,'orders_view'):
                value=zones(dict(principal,_map_brand_scoped=True))
                result.extend(dict(item,brand_id=brand_id) for item in value['zones'])
        return {'zones':result}
    with db.tx() as c:
        rows=c.execute('SELECT id,name,settings FROM branches WHERE brand_id=%s AND (%s OR id=ANY(%s)) ORDER BY branch_no',
                       (user['brand_id'],user['role']=='owner' and not user.get('home_brand_id'),user.get('branch_ids',[]))).fetchall()
        return {'zones':[{'branch_id':r['id'],'name':r['name'],'coverage':r['settings'].get('coverage'),
                          'delivery_fee_halalas':r['settings'].get('delivery_fee_halalas')}
                         for r in rows if catalog.coverage_configured(r['settings'].get('coverage'))]}


def ordered_stops(origin, tasks):
    """Nearest-neighbour suggestion, not a claim of road-optimal order."""
    remaining=list(tasks)
    result=[]
    while remaining:
        selected=min(remaining,key=lambda t:(catalog.direct_distance(origin,t['location']),t['id']))
        result.append(selected)
        remaining.remove(selected)
        origin=selected['location']
    return result


from pydantic import BaseModel, ConfigDict, Field


class PlanRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    driver_id:str=Field(min_length=1,max_length=100)


@router.post('/plan')
def plan(body:PlanRequest,user=Depends(auth.require_roles(*STAFF))):
    require(user)
    with db.tx() as c:
        context=(dispatch_map.central_route_context(c,user,body.driver_id)
                 if user.get('central_operations') and not user.get('_morning_manager_context')
                 else dispatch_map.route_context(c,user,body.driver_id))
        if not 2 <= len(context['tasks']) <= 8:
            raise HTTPException(409,'ترتيب المسار متاح من طلبين إلى ثمانية طلبات مستلمة.')
        gps=c.execute('SELECT accuracy_m FROM driver_latest_locations WHERE driver_id=%s',
                      (body.driver_id,)).fetchone()
        if not gps or gps['accuracy_m']>100:
            raise HTTPException(409,'يلزم موقع أدق لحساب المسار.')
        issued=dispatch_map.consume_route_budget(c,body.driver_id,user['id'])
        return safe({'driver_id':body.driver_id,'origin':context['origin'],
                     'stops':ordered_stops(context['origin'],context['tasks']),
                     'basis':'nearest_straight_line_suggestion','issued_at':issued,'changes_assignment':False})


@router.get('/trips')
def trips(shift_id:str=Query(min_length=1,max_length=200),user=Depends(auth.require_roles(*STAFF))):
    require(user)
    with db.tx() as c:
        shift=c.execute('SELECT * FROM shifts WHERE id=%s',(shift_id,)).fetchone()
        if not shift or shift['kind']!='driver':
            raise HTTPException(404,'الشفت غير موجود.')
        shift_access(c,user,shift)
        rows=c.execute('''SELECT t.id task_id,o.order_no,t.picked_up_at,t.delivered_at,
          CASE WHEN t.delivered_at>=t.picked_up_at THEN EXTRACT(EPOCH FROM t.delivered_at-t.picked_up_at)::integer END delivery_seconds,
          o.customer->'location' customer_location,b.settings branch_settings
          FROM driver_tasks t JOIN orders o ON o.id=t.order_id AND o.brand_id=%s
          JOIN branches b ON b.id=o.branch_id AND b.brand_id=o.brand_id
          WHERE t.shift_id=%s AND (%s OR o.branch_id=ANY(%s)) ORDER BY t.created_at,t.id LIMIT 500''',
          (user['brand_id'],shift_id,user['role']=='owner' and not user.get('home_brand_id'),user.get('branch_ids',[]))).fetchall()
        result=[]
        for row in rows:
            settings=row.pop('branch_settings')
            destination=row.pop('customer_location')
            distance=catalog.direct_distance(settings.get('location'),destination) if catalog.location_verified({'settings':settings}) else None
            result.append({**row,'direct_distance_m':round(distance) if distance is not None else None})
        return safe({'trips':result,'distance_basis':'straight_line_branch_to_customer_not_driven_distance',
                     'duration_basis':'recorded_pickup_to_delivery'})
