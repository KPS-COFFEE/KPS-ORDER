"""Authorized road-time planning. Customer tracking remains an independent opt-in.

Mapbox Matrix charges elements, not requests: reserve n*n from the shared budget.
No straight-line speed fallback, traffic guarantee, or inferred arrival is used.
"""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import math
import os
import re
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from . import auth, db, dispatch_map
from .operations import safe
from . import route_corridor

router = APIRouter(prefix='/api/operations', tags=['operations'])
TTL_SECONDS = 60
MAX_STOPS = 8
STAFF = ('owner', 'delivery_manager', 'delivery_staff')
DWELL_SECONDS = {'pickup': 180, 'dropoff': 120, 'delivery': 120, 'return': 0}


class EtaRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    driver_id: str = Field(min_length=1, max_length=100)
    publish: bool = False
    automatic_preview: bool = False
    return_branch_id: str | None = Field(default=None, min_length=1, max_length=100)
    ordered_stop_ids: list[str] | None = Field(default=None, max_length=MAX_STOPS)

    @model_validator(mode='after')
    def preview_has_no_mutating_fields(self):
        if self.automatic_preview and self.model_fields_set & {'publish','ordered_stop_ids','return_branch_id'}:
            raise ValueError('Automatic preview cannot change publication, order, or return selection.')
        return self


def context_for(c, user, driver_id):
    if user.get('central_operations') and not user.get('_morning_manager_context'):
        return dispatch_map.central_route_context(c, user, driver_id)
    return dispatch_map.route_context(c, user, driver_id)


def complete_itinerary(c, user, context):
    """Do not describe a visible subset of a driver's work as the entire trip.

    Only a boolean escapes this check; hidden tasks/branches are never exposed.
    """
    central = bool(user.get('central_operations') and not user.get('_morning_manager_context'))
    rows = c.execute("""SELECT t.id FROM driver_tasks t
      JOIN shifts s ON s.id=t.shift_id AND s.user_id=t.driver_id AND s.status='open'
      JOIN orders o ON o.id=t.order_id AND o.brand_id=s.brand_id
      WHERE t.driver_id=%s AND (%s OR o.brand_id=%s)
      AND t.status IN ('assigned','accepted','picked_up') AND o.service='delivery'
      AND o.completed_at IS NULL AND o.preparation_status NOT IN ('cancelled','rejected')
      AND o.delivery_status NOT IN ('delivered','cancelled')""",
      (context['driver_id'],central,user['brand_id'])).fetchall()
    visible = {s.get('task_id') for s in context.get('stops',[])}
    if not context.get('stops'):
        visible = {t['id'] for t in context['tasks']}
    return {r['id'] for r in rows} <= visible


def prepare_stops(context, return_branch_id=None):
    source = context.get('stops')
    if source is None:
        source = [dict(t, id='dropoff:'+t['id'], task_id=t['id'], kind='dropoff', prerequisite_ids=[])
                  for t in context['tasks']]
    stops = [dict(s) for s in source]
    # A branch visit shared by several orders is one physical pickup stop.
    # Otherwise adding another order would charge branch waiting twice.
    pickups, aliases = {}, {}
    for stop in stops:
        if stop.get('kind') == 'pickup' and stop.get('branch_id'):
            key = (stop.get('brand_id'), stop['branch_id'], stop.get('shift_id'))
            if key not in pickups:
                pickups[key] = dict(stop, id='pickup:'+str(key[0])+':'+str(key[1]), task_ids=[], order_ids=[])
            grouped = pickups[key]
            aliases[stop['id']] = grouped['id']
            if stop.get('task_id'):
                grouped['task_ids'].append(stop['task_id'])
            if stop.get('order_id'):
                grouped['order_ids'].append(stop['order_id'])
    if aliases:
        stops = [s for s in stops if s['id'] not in aliases] + list(pickups.values())
        for stop in stops:
            stop['prerequisite_ids'] = list(dict.fromkeys(aliases.get(i,i) for i in stop.get('prerequisite_ids',[])))
    if len(stops) > MAX_STOPS:
        raise HTTPException(409, 'يتجاوز المسار حد ثماني محطات؛ قسّم الرحلة.')
    ids = [s['id'] for s in stops]
    if len(set(ids)) != len(ids) or any(s.get('kind') not in DWELL_SECONDS for s in stops):
        raise HTTPException(409, 'بيانات محطات الرحلة غير متاحة.')
    if any(dispatch_map.route_point(s.get('location')) is None for s in stops):
        raise HTTPException(409, 'أكمل مواقع جميع محطات الرحلة قبل حساب الوقت.')
    if return_branch_id:
        branch = next((b for b in context['return_branches'] if b['id'] == return_branch_id), None)
        if branch is None:
            raise HTTPException(409, 'فرع العودة غير متاح ضمن الصلاحيات الحالية.')
        stops.append(dict(branch, id='return:'+branch['id'], branch_id=branch['id'], kind='return',
                          prerequisite_ids=ids))
    return stops


def validate_order(stops, ordered_ids):
    normal = [s for s in stops if s['kind'] != 'return']
    if len(ordered_ids) != len(normal) or len(set(ordered_ids)) != len(ordered_ids) or set(ordered_ids) != {s['id'] for s in normal}:
        raise HTTPException(409, 'يجب أن يشمل الترتيب جميع المحطات مرة واحدة.')
    by_id = {s['id']: s for s in normal}
    visited = set()
    for identifier in ordered_ids:
        if not set(by_id[identifier].get('prerequisite_ids', [])) <= visited:
            raise HTTPException(409, 'يلزم استلام الطلب قبل محطة تسليمه.')
        visited.add(identifier)
    return [by_id[i] for i in ordered_ids]+[s for s in stops if s['kind']=='return']


def valid_number(value):
    return type(value) in (float, int) and math.isfinite(value) and 0 <= value <= 31_536_000


def validate_matrix(value, count):
    if not isinstance(value, list) or len(value) != count:
        raise ValueError('matrix_shape')
    if any(not isinstance(row, list) or len(row) != count or
           any(x is not None and not valid_number(x) for x in row) for row in value):
        raise ValueError('matrix_values')
    return value


def road_order(stops, matrix):
    """Exact bounded dynamic programming with directed roads and pickup precedence."""
    n = len(stops)
    index = {s['id']: i for i,s in enumerate(stops)}
    prerequisites = []
    for stop in stops:
        required = stop.get('prerequisite_ids', [])
        if any(i not in index for i in required):
            raise ValueError('missing_prerequisite')
        prerequisites.append(sum(1 << index[i] for i in required))
    @lru_cache(maxsize=None)
    def best(mask, previous):
        if mask == (1 << n)-1:
            return 0, ()
        winner = (math.inf, ())
        for i in range(n):
            if mask & (1 << i) or prerequisites[i] & mask != prerequisites[i]:
                continue
            duration = matrix[previous][i+1]
            if duration is None:
                continue
            cost, rest = best(mask | (1 << i), i+1)
            candidate = (duration+cost, (i,)+rest)
            if candidate < winner:
                winner = candidate
        return winner
    cost, ordered = best(0, 0)
    if not math.isfinite(cost):
        raise ValueError('no_road_route')
    return [stops[i] for i in ordered]


def timeline(stops, ordered, matrix, calculated_at, now=None):
    now = now or datetime.now(timezone.utc)
    index = {s['id']: i+1 for i,s in enumerate(stops)}
    previous, elapsed = 0, 0
    rows = []
    delivery_at = return_at = None
    for stop in ordered:
        i = index[stop['id']]
        duration = matrix[previous][i]
        if duration is None:
            raise ValueError('no_road_route')
        elapsed += math.ceil(duration)
        arrival = calculated_at+timedelta(seconds=elapsed)
        dwell = DWELL_SECONDS[stop['kind']]
        elapsed += dwell
        departure = calculated_at+timedelta(seconds=elapsed)
        rows.append({**stop, 'drive_seconds': math.ceil(duration), 'dwell_seconds': dwell,
                     'arrival_at': arrival, 'departure_at': departure,
                     'eta_seconds': max(0, math.ceil((arrival-now).total_seconds()))})
        if stop['kind'] in ('dropoff','delivery'):
            delivery_at = departure
        if stop['kind'] == 'return':
            return_at = arrival
        previous = i
    return {'stops': rows, 'next_stop': rows[0] if rows else None,
            'delivery_complete_at': delivery_at,
            'delivery_complete_seconds': max(0,math.ceil((delivery_at-now).total_seconds())) if delivery_at else None,
            'return_at': return_at,
            'return_seconds': max(0,math.ceil((return_at-now).total_seconds())) if return_at else None}


def fetch_matrix(points, token, transport=None):
    coords = ';'.join(f"{p['lng']},{p['lat']}" for p in points)
    with httpx.Client(timeout=httpx.Timeout(4.0, connect=2.0), follow_redirects=False,
                      trust_env=False, transport=transport) as client:
        response = client.get('https://api.mapbox.com/directions-matrix/v1/mapbox/driving/'+coords,
                              params={'annotations':'duration','access_token':token})
        if response.status_code != 200 or len(response.content) > 262144:
            raise ValueError('provider_unavailable')
        value = response.json()
        if not isinstance(value,dict) or value.get('code') != 'Ok':
            raise ValueError('provider_unavailable')
        return validate_matrix(value.get('durations'),len(points))


def corridor_current(corridor, signature, first, now):
    try:
        age = (now-datetime.fromisoformat(corridor['calculated_at'])).total_seconds()
        return bool(first and corridor['stop_id']==first['id'] and
                    corridor['fingerprint']==signature and 0 <= age <= 86400 and
                    route_corridor.validate_geometry(corridor['geometry']))
    except (ValueError,TypeError,KeyError):
        return False


def fingerprint(stops):
    return hashlib.sha256(json.dumps(stops, sort_keys=True, separators=(',',':'), default=str).encode()).hexdigest()


def unavailable(driver_id, reason, status='unavailable'):
    return {'driver_id':driver_id,'status':status,'reason':reason,'stops':[], 'next_stop':None,
            'delivery_complete_at':None,'delivery_complete_seconds':None,
            'return_at':None,'return_seconds':None,'refresh_after_seconds':TTL_SECONDS}


def refreshed_caller(c, user, *, assignment=False):
    """Provider I/O releases our transaction; revoke stale caller authority after it."""
    from . import multibrand, central_operations
    actor = c.execute("SELECT u.* FROM users u JOIN brands b ON b.id=u.brand_id AND b.active WHERE u.id=%s AND u.active",
                      (user['id'],)).fetchone()
    session = c.execute("SELECT id FROM sessions WHERE id=%s AND user_id=%s AND revoked_at IS NULL AND expires_at>now() FOR SHARE",
                        (user.get('session_id'),user['id'])).fetchone()
    if not session:
        raise HTTPException(401,'انتهت الجلسة أثناء حساب المسار. سجل الدخول مجددًا.')
    if not actor or actor['role'] not in STAFF:
        raise HTTPException(403,'تغيرت صلاحيات حساب التشغيل. أعد فتح الشاشة.')
    actor = multibrand.project(c,actor,user['brand_id'])
    if not actor:
        raise HTTPException(403,'تم سحب صلاحية البراند أثناء حساب المسار.')
    if user.get('_morning_manager_context'):
        from .morning_manager_context import resolve
        actor = resolve(c,actor,shift_id=user['_morning_manager_context']['shift_id'])
    if not auth.has_permission(actor,'drivers_view') or assignment and not auth.has_permission(actor,'delivery_assign'):
        raise HTTPException(403,'تم سحب صلاحية المسار أثناء الحساب.')
    if user.get('central_operations') and not user.get('_morning_manager_context'):
        actor['central_operations'] = central_operations.context(c,actor)
        if not actor['central_operations']:
            raise HTTPException(403,'نطاق الإدارة الموحدة لم يعد متاحًا.')
    actor['session_id'] = user['session_id']
    return actor


def location_fresh(stamp, now=None):
    """A future or unzoned capture cannot make an estimate look current."""
    try:
        return 0 <= ((now or datetime.now(timezone.utc))-stamp).total_seconds() <= 120
    except (TypeError,AttributeError,OverflowError):
        return False


def automatic_intent(c, user, context, saved):
    """Follow authorized current publication; never copy another actor's grants."""
    published = published_plan_for(c,user,context['driver_id'])
    if published:
        returns = [s for s in published['stops'] if s['kind']=='return']
        return_id = returns[0]['branch_id'] if returns else None
        ordered = [s['id'] for s in published['stops'] if s['kind']!='return']
        return return_id, ordered, 'published'
    return_id = saved.get('return_branch_id') if saved else None
    stops = prepare_stops(context,return_id)
    ordered = saved.get('ordered_stop_ids') or None if saved else None
    if ordered is not None:
        try:
            validate_order(stops,ordered)
        except HTTPException:
            ordered = None  # Changed tasks cannot silently inherit an old order.
    basis = 'saved_preferences' if return_id or ordered is not None else 'road_suggestion'
    return return_id,ordered,basis


@router.post('/route-eta')
def route_eta(body: EtaRequest, user=Depends(auth.require_roles(*STAFF))):
    if not auth.has_permission(user,'drivers_view'):
        raise HTTPException(403,'صلاحية عرض السائقين غير متاحة.')
    if (body.ordered_stop_ids is not None or body.publish) and not auth.has_permission(user,'delivery_assign'):
        raise HTTPException(403,'تعديل الترتيب يتطلب صلاحية الإسناد.')
    now = datetime.now(timezone.utc)
    with db.tx() as c:
        # Authentication/scope is re-evaluated even on a cache hit.
        try:
            context = context_for(c,user,body.driver_id)
        except HTTPException as exc:
            if exc.status_code == 409:
                return unavailable(body.driver_id,'location_or_shift_unavailable','stale')
            raise
        if not complete_itinerary(c,user,context):
            return unavailable(body.driver_id,'itinerary_scope_incomplete')
        stamp = context.get('updated_at')
        if not location_fresh(stamp,now):
            return unavailable(body.driver_id,'location_stale','stale')
        saved = c.execute('SELECT * FROM driver_route_eta_state WHERE driver_id=%s AND manager_id=%s FOR UPDATE',
                          (body.driver_id,user['id'])).fetchone()
        intent_basis = None
        if body.automatic_preview:
            return_id, preview_order, intent_basis = automatic_intent(c,user,context,saved)
        else:
            return_id = body.return_branch_id if 'return_branch_id' in body.model_fields_set else saved.get('return_branch_id') if saved else None
        stops = prepare_stops(context,return_id)
        if not stops:
            return unavailable(body.driver_id,'no_stops')
        signature = fingerprint(stops)
        ordered_ids = preview_order if body.automatic_preview else body.ordered_stop_ids
        remaining_saved = remaining_publication(c,saved,stops,body.driver_id) if saved and saved.get('published_at') else None
        if not body.automatic_preview and ordered_ids is None and remaining_saved:
            ordered_ids = [s['id'] for s in remaining_saved[1] if s['kind']!='return']
        if not body.automatic_preview and ordered_ids is None and saved and saved['fingerprint']==signature:
            ordered_ids = saved['ordered_stop_ids'] or None
        if ordered_ids is not None:
            validate_order(stops,ordered_ids)
        plan_changed = (body.ordered_stop_ids is not None or 'return_branch_id' in body.model_fields_set) and (
            not saved or saved['ordered_stop_ids'] != (ordered_ids or []) or saved['return_branch_id'] != return_id)
        cached = bool(saved and saved['fingerprint']==signature and saved['matrix'] is not None and
                      saved['calculated_at'] and 0 <= (now-saved['calculated_at']).total_seconds()<TTL_SECONDS)
        matrix = saved['matrix'] if cached else None
        calculated = saved['calculated_at'] if cached else now
        origin_stamp = saved['origin_updated_at'] if cached else stamp
        if not cached:
            # Reuse only bounded provider data after this caller's full itinerary
            # authorization. Never inherit another manager's route intent/grants.
            shared = c.execute('''SELECT matrix,calculated_at,origin_updated_at
              FROM driver_route_eta_state WHERE driver_id=%s AND manager_id<>%s
              AND fingerprint=%s AND matrix IS NOT NULL AND calculated_at<=%s
              AND calculated_at>%s ORDER BY calculated_at DESC,manager_id LIMIT 1''',
              (body.driver_id,user['id'],signature,now,now-timedelta(seconds=TTL_SECONDS))).fetchone()
            if shared:
                matrix = shared['matrix']
                calculated = shared['calculated_at']
                origin_stamp = shared['origin_updated_at']
                cached = True
        # Preserve the reference corridor across minute ETA refreshes. Rebase only
        # on a changed plan not at every GPS sample. A valid reference stays fixed for the leg.
        corridor = None if body.automatic_preview else (saved or {}).get('road_corridor')
        first = (validate_order(stops,ordered_ids) if ordered_ids is not None else
                 road_order(stops,matrix) if matrix is not None else [None])[0]
        if not corridor_current(corridor,signature,first,now):
            corridor = None
        wants_publication = not body.automatic_preview and (body.publish or body.ordered_stop_ids is not None or bool(remaining_saved))
        fetch_corridor = False
        # Browser-restricted public tokens must never be reused by the server.
        token = os.getenv('MAPBOX_ROUTING_TOKEN','')
        token_valid = bool(re.fullmatch(r'(?:pk|sk)\.[A-Za-z0-9_.-]{20,2048}',token))
        if not cached:
            if not token_valid:
                return unavailable(body.driver_id,'routing_token_unconfigured')
            try:
                dispatch_map.consume_route_budget(c,body.driver_id,user['id'],units=(len(stops)+1)**2,
                                                  purpose='automatic_preview' if body.automatic_preview else 'route')
            except HTTPException as exc:
                if exc.status_code==429:
                    return unavailable(body.driver_id,getattr(exc,'reason','route_budget_or_cooldown'))
                raise
        if wants_publication and corridor is None and token_valid:
            # Optional geometry has its own cooldown, but charges the SAME locked
            # monthly budget. A geometry limit must not discard a valid matrix.
            try:
                dispatch_map.consume_route_budget(c,body.driver_id,user['id'],units=1,purpose='corridor')
                fetch_corridor = True
            except HTTPException as exc:
                if exc.status_code!=429:
                    raise
    # The shared budget row is committed before network I/O, even if provider fails.
    if matrix is None:
        try:
            matrix = fetch_matrix([context['origin']]+[s['location'] for s in stops],token)
        except (ValueError, httpx.HTTPError):
            return unavailable(body.driver_id,'routing_provider_unavailable')
    try:
        matrix = validate_matrix(matrix,len(stops)+1)
        ordered = validate_order(stops,ordered_ids) if ordered_ids is not None else road_order(stops,matrix)
        output = timeline(stops,ordered,matrix,calculated,now)
    except ValueError:
        return unavailable(body.driver_id,'road_route_unavailable')
    if fetch_corridor:
        try:
            geometry = route_corridor.fetch_geometry(context['origin'],ordered[0]['location'],token)
            corridor = {'geometry':geometry,'calculated_at':now.isoformat(),
                        'fingerprint':signature,'stop_id':ordered[0]['id']}
        except (ValueError,httpx.HTTPError):
            corridor = None  # ETA remains usable; never invent a straight-line corridor.
    if not corridor_current(corridor,signature,ordered[0],now):
        corridor = None
    with db.tx() as c:
        user = refreshed_caller(c,user,assignment=body.publish or body.ordered_stop_ids is not None)
        # A task can be completed/reassigned while the provider responds.
        try:
            current = context_for(c,user,body.driver_id)
        except HTTPException as exc:
            if exc.status_code == 409:
                return unavailable(body.driver_id,'location_or_shift_unavailable','stale')
            raise
        checked_at = datetime.now(timezone.utc)
        if not location_fresh(current.get('updated_at'),checked_at):
            return unavailable(body.driver_id,'location_stale','stale')
        if not location_fresh(origin_stamp,checked_at):
            return unavailable(body.driver_id,'route_origin_stale','stale')
        if not complete_itinerary(c,user,current):
            return unavailable(body.driver_id,'itinerary_scope_incomplete')
        if fingerprint(prepare_stops(current,return_id)) != signature:
            return unavailable(body.driver_id,'itinerary_changed')
        if body.automatic_preview:
            latest = c.execute('SELECT * FROM driver_route_eta_state WHERE driver_id=%s AND manager_id=%s FOR UPDATE',
                               (body.driver_id,user['id'])).fetchone()
            fresh_return,fresh_order,_ = automatic_intent(c,user,current,latest)
            if (fresh_return,fresh_order)!=(return_id,preview_order):
                return unavailable(body.driver_id,'route_intent_changed')
            # Only bounded provider data is cached. The actor's chosen route,
            # return, published guidance, corridor and expectations are untouched.
            c.execute('''INSERT INTO driver_route_eta_state(driver_id,manager_id,fingerprint,matrix,calculated_at,origin_updated_at)
              VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(driver_id,manager_id) DO UPDATE SET
              fingerprint=EXCLUDED.fingerprint,matrix=EXCLUDED.matrix,
              calculated_at=EXCLUDED.calculated_at,origin_updated_at=EXCLUDED.origin_updated_at''',
              (body.driver_id,user['id'],signature,db.json(matrix),calculated,origin_stamp))
        else:
            c.execute('''INSERT INTO driver_route_eta_state(driver_id,manager_id,fingerprint,matrix,calculated_at,
              origin_updated_at,ordered_stop_ids,return_branch_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT(driver_id,manager_id) DO UPDATE SET fingerprint=EXCLUDED.fingerprint,matrix=EXCLUDED.matrix,
              calculated_at=EXCLUDED.calculated_at,origin_updated_at=EXCLUDED.origin_updated_at,
              ordered_stop_ids=EXCLUDED.ordered_stop_ids,return_branch_id=EXCLUDED.return_branch_id''',
              (body.driver_id,user['id'],signature,db.json(matrix),calculated,origin_stamp,db.json(ordered_ids or []),return_id))
        requested_publication = body.publish or body.ordered_stop_ids is not None
        refreshing_publication = bool(not body.automatic_preview and remaining_saved and (not cached or calculated!=saved.get('published_calculated_at'))
                                      and saved['source_session_id']==user.get('session_id')
                                      and auth.has_permission(user,'delivery_assign'))
        publishing = requested_publication or refreshing_publication
        if publishing:
            session = c.execute("SELECT id FROM sessions WHERE id=%s AND user_id=%s AND revoked_at IS NULL AND expires_at>now() FOR SHARE",
                                (user.get('session_id'),user['id'])).fetchone()
            if not session:
                raise HTTPException(401,'يلزم تسجيل دخول إداري صالح لاعتماد المسار.')
            shift_ids = sorted({s['shift_id'] for s in stops if s.get('shift_id')})
            c.execute('''UPDATE driver_route_eta_state SET published_at=now(),published_stop_ids=%s,
              published_shift_ids=%s,source_session_id=%s,manager_brand_id=%s,central_scope=%s,morning_shift_id=%s,
              published_stops=%s,published_matrix=%s,published_calculated_at=%s,road_corridor=%s
              WHERE driver_id=%s AND manager_id=%s''',
              (db.json([s['id'] for s in ordered]),db.json(shift_ids),user['session_id'],user['brand_id'],
               bool(user.get('central_operations') and not user.get('_morning_manager_context')),
               (user.get('_morning_manager_context') or {}).get('shift_id'),
               db.json(stops),db.json(matrix),calculated,db.json(corridor),body.driver_id,user['id']))
            db.audit(c,user['id'],'driver.route_published' if requested_publication else 'driver.route_refreshed','driver',body.driver_id,
                     {'stop_ids':[s['id'] for s in ordered],'return_branch_id':return_id,'shift_ids':shift_ids})
        if plan_changed:
            db.audit(c,user['id'],'driver.route_plan','driver',body.driver_id,
                     {'ordered_stop_ids':ordered_ids,'return_branch_id':return_id})
        if not body.automatic_preview and (not cached or plan_changed or refreshing_publication):
            expected = [{'id':r['id'],'task_id':r.get('task_id'),'order_id':r.get('order_id'),
                         'shift_id':r.get('shift_id'),'brand_id':r.get('brand_id'),'task_ids':r.get('task_ids',[]),
                         'kind':r['kind'],'arrival_at':r['arrival_at'].isoformat(),
                         'departure_at':r['departure_at'].isoformat(),'dwell_seconds':r['dwell_seconds']}
                        for r in output['stops']]
            c.execute('''INSERT INTO driver_route_eta_expectations(id,driver_id,manager_id,calculated_at,stops,duration_basis)
              VALUES(%s,%s,%s,%s,%s,'road_time_plus_explicit_stop_estimates')''',
              (uuid4().hex,body.driver_id,user['id'],calculated,db.json(expected)))
    return safe({'driver_id':body.driver_id,'status':'ready','calculated_at':calculated,
                 'location_updated_at':current['updated_at'],'route_origin_updated_at':origin_stamp,
                 'refresh_after_seconds':TTL_SECONDS,'duration_basis':'road_time_plus_explicit_stop_estimates',
                 'traffic_included':False,'ordering':'manager' if ordered_ids is not None else 'road_duration',
                 'corridor_status':'not_requested' if body.automatic_preview else 'ready' if corridor else 'unavailable',
                 'automatic_preview':body.automatic_preview,'intent_basis':intent_basis,
                 'cached':cached,'published':publishing,**output})


def published_manager(c, saved):
    """Recreate the original least-privilege manager scope from live policy."""
    from . import multibrand, central_operations
    manager = c.execute('SELECT * FROM users WHERE id=%s AND active',(saved['manager_id'],)).fetchone()
    if not manager or manager['role'] not in STAFF:
        return None
    manager = multibrand.project(c,manager,saved['manager_brand_id'])
    if not manager:
        return None
    if saved.get('morning_shift_id'):
        from .morning_manager_context import resolve
        try:
            manager = resolve(c,manager,shift_id=saved['morning_shift_id'])
        except HTTPException:
            return None
    if not auth.has_permission(manager,'delivery_assign') or not auth.has_permission(manager,'drivers_view'):
        return None
    if saved['central_scope']:
        manager['central_operations'] = central_operations.context(c,manager)
        if not manager['central_operations']:
            return None
    return manager


def published_rows(c, driver_id):
    return c.execute('''SELECT p.* FROM driver_route_eta_state p JOIN users u ON u.id=p.manager_id
      JOIN sessions s ON s.id=p.source_session_id AND s.user_id=p.manager_id
      WHERE p.driver_id=%s AND p.published_at IS NOT NULL AND u.active
      AND s.revoked_at IS NULL AND s.expires_at>now()
      ORDER BY p.published_at DESC,p.manager_id LIMIT 10''',(driver_id,)).fetchall()


def published_return_completed(c, saved, current, driver_id):
    """A completed canonical trip consumes its old return guidance, not its preference.

    No active tasks is insufficient evidence: an open trip may still be returning.
    Original order links bind the confirmation to this publication, including
    trips anchored on another leg of the same physical workday.
    """
    if not current or any(stop.get('kind')!='return' for stop in current) or not saved.get('published_at'):
        return False
    order_ids = {oid for stop in saved.get('published_stops') or []
                 for oid in (stop.get('order_ids') or [stop.get('order_id')]) if oid}
    if not order_ids:
        return False
    return bool(c.execute('''SELECT 1 FROM driver_work_trips trip
      WHERE trip.driver_id=%s AND trip.returned_at>=%s
      AND EXISTS (SELECT 1 FROM driver_work_trip_orders linked
                  WHERE linked.trip_id=trip.id AND linked.order_id=ANY(%s))
      AND NOT EXISTS (SELECT 1 FROM driver_work_trips active
                      WHERE active.driver_id=trip.driver_id AND active.returned_at IS NULL)
      LIMIT 1''',(driver_id,saved['published_at'],sorted(order_ids))).fetchone())


def remaining_publication(c, saved, current, driver_id):
    """Consume completed stations while rejecting reassignment and new work."""
    original = saved.get('published_stops') or []
    by_id = {s['id']:s for s in original}
    ids = saved['published_stop_ids']
    current_by_id = {s['id']:s for s in current}
    if (not original or len(set(ids))!=len(ids) or set(ids)!=set(by_id)
            or not set(current_by_id) <= set(by_id)):
        return None
    task_ids = {tid for s in original for tid in (s.get('task_ids') or [s.get('task_id')]) if tid}
    tasks = {t['id']:t for t in c.execute(
        'SELECT id,status,driver_id,shift_id FROM driver_tasks WHERE id=ANY(%s)',(sorted(task_ids),)).fetchall()}
    for stop in original:
        live = current_by_id.get(stop['id'])
        original_tasks = set(stop.get('task_ids') or ([stop['task_id']] if stop.get('task_id') else []))
        if live:
            if any(live.get(k)!=stop.get(k) for k in ('location','brand_id','shift_id','kind','branch_id')):
                return None
            live_tasks = set(live.get('task_ids') or ([live['task_id']] if live.get('task_id') else []))
            if not live_tasks <= original_tasks:
                return None
            removed = original_tasks-live_tasks
        else:
            if stop['kind']=='return':
                return None
            removed = original_tasks
        for tid in removed:
            task = tasks.get(tid)
            allowed = {'picked_up','delivered'} if stop['kind']=='pickup' else {'delivered'}
            if (not task or task['driver_id']!=driver_id or task['shift_id']!=stop.get('shift_id')
                    or task['status'] not in allowed):
                return None
    ordered = [current_by_id[i] for i in ids if i in current_by_id]
    if not ordered or published_return_completed(c,saved,current,driver_id):
        return None
    normalized = validate_order(current,[s['id'] for s in ordered if s['kind']!='return'])
    if [s['id'] for s in normalized] != [s['id'] for s in ordered]:
        return None
    return original, ordered


def validated_publication(c, saved, driver_id):
    manager = published_manager(c,saved)
    if not manager:
        return None
    try:
        context = context_for(c,manager,driver_id)
        if not complete_itinerary(c,manager,context):
            return None
        stops = prepare_stops(context,saved['return_branch_id'])
        remaining = remaining_publication(c,saved,stops,driver_id)
        if remaining is None:
            return None
        original,ordered = remaining
        calculated = saved['published_calculated_at']
        view = timeline(original,ordered,validate_matrix(saved['published_matrix'],len(original)+1),calculated)
        changed = fingerprint(stops)!=fingerprint(original)
        age = (datetime.now(timezone.utc)-calculated).total_seconds()
        view['eta_status'] = 'ready' if not changed and 0 <= age <= 120 else 'stale'
        if view['eta_status'] == 'stale':
            for stop in view['stops']:
                stop.update(eta_seconds=None,arrival_at=None,departure_at=None)
            view.update(delivery_complete_at=None,delivery_complete_seconds=None,return_at=None,return_seconds=None)
        return context,stops,ordered,view
    except (HTTPException,ValueError,TypeError,KeyError):
        return None


def published_plan_for(c, user, driver_id):
    """Staff-only internal helper for progress alerts; never widens viewer scope."""
    if user['role'] not in STAFF or not auth.has_permission(user,'drivers_view'):
        return None
    try:
        context = context_for(c,user,driver_id)
        if not complete_itinerary(c,user,context):
            return None
        for saved in published_rows(c,driver_id):
            stops = prepare_stops(context,saved['return_branch_id'])
            if remaining_publication(c,saved,stops,driver_id) is None:
                continue
            validated = validated_publication(c,saved,driver_id)
            if validated:
                view = validated[3]
                corridor = saved.get('road_corridor')
                if corridor_current(corridor,fingerprint(validated[1]),view.get('next_stop'),datetime.now(timezone.utc)):
                    view['road_corridor'] = corridor
                return {**view,'calculated_at':saved['published_calculated_at'],'published_at':saved['published_at']}
    except (HTTPException,ValueError,TypeError,KeyError):
        pass
    return None


def own_published_plan(c, user):
    """Own live shift, both sessions, live source policy, and every task rechecked."""
    from . import multibrand, driver_destination
    session = c.execute("SELECT id FROM sessions WHERE id=%s AND user_id=%s AND revoked_at IS NULL AND expires_at>now() FOR SHARE",
                        (user.get('session_id'),user['id'])).fetchone()
    if not session:
        raise HTTPException(401,'انتهت جلسة السائق. سجل الدخول مجددًا.')
    shift = driver_destination.own_shift(c,user)
    for saved in published_rows(c,user['id']):
        if shift['id'] not in saved['published_shift_ids']:
            continue
        validated = validated_publication(c,saved,user['id'])
        if not validated:
            continue
        _,stops,ordered,view = validated
        try:
            # Driver grants independently cover every station, regardless of
            # the source manager's broader permissions.
            for stop in ordered:
                projected = multibrand.project(c,user,stop.get('brand_id',user['brand_id']))
                if not projected:
                    raise ValueError('driver_grant_revoked')
                if stop.get('branch_id') and stop['branch_id'] not in projected.get('branch_ids',[]):
                    raise ValueError('driver_branch_revoked')
                if stop.get('task_id'):
                    task = c.execute('''SELECT o.branch_id FROM driver_tasks t JOIN orders o ON o.id=t.order_id
                      WHERE t.id=%s AND t.driver_id=%s AND t.shift_id=%s AND o.brand_id=%s
                      AND t.status IN ('assigned','accepted','picked_up')''',
                      (stop['task_id'],user['id'],stop.get('shift_id'),stop.get('brand_id'))).fetchone()
                    if not task or task['branch_id'] not in projected.get('branch_ids',[]):
                        raise ValueError('driver_task_revoked')
        except ValueError:
            continue
        return safe({'driver_id':user['id'],'status':'ready','published_at':saved['published_at'],
                     'calculated_at':saved['published_calculated_at'],'ordering':'manager',
                     'refresh_after_seconds':30,**view})
    return unavailable(user['id'],'published_plan_unavailable')


@router.get('/driver-route-plan')
def driver_route_plan(user=Depends(auth.require_roles('driver'))):
    if not auth.has_permission(user,'tasks_view'):
        raise HTTPException(403,'صلاحية عرض مهام التوصيل غير متاحة.')
    with db.tx() as c:
        return own_published_plan(c,user)
