"""Optional staff map configuration; never changes customer map behavior."""
import os
import re
from fastapi import APIRouter, Depends
from . import auth

router = APIRouter(prefix='/api/operations', tags=['operations'])


def customer_tracking_enabled():
    # Explicit owner opt-in; never enabled by MAPBOX_ENABLED alone.
    return os.getenv('CUSTOMER_DELIVERY_TRACKING_ENABLED', '').lower() == 'true'


def enabled():
    return os.getenv('MAPBOX_ENABLED', '').lower() == 'true'


@router.get('/map-config')
def map_config(user=Depends(auth.require_roles('owner', 'delivery_manager', 'delivery_staff'))):
    # Scope comes only from the authenticated principal, never a query string.
    brand = user['brand_id']
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,59}', brand):
        return {'enabled': False}
    token = os.getenv('MAPBOX_PUBLIC_TOKEN_' + brand.upper().replace('-', '_'), '')
    if not enabled() or not re.fullmatch(r'pk\.[A-Za-z0-9_.-]{20,2048}', token):
        return {'enabled': False}
    return {'enabled': True, 'provider': 'mapbox', 'public_token': token,
            'style': 'mapbox://styles/mapbox/streets-v12',
            'customer_tracking_enabled': customer_tracking_enabled()}


# One last observation per driver, never a route history. All clocks are server
# clocks; callers cannot refresh an old position by providing a timestamp.
from pydantic import BaseModel, ConfigDict, Field
from fastapi import HTTPException
from . import db, catalog, driver_destination
from .operations import safe, shift_access

STALE_AFTER_SECONDS = 120


class DriverLocation(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: float = Field(ge=0, le=10000)


@router.post('/driver-location')
def update_driver_location(body: DriverLocation, user=Depends(auth.require_roles('driver'))):
    with db.tx() as c:
        shift = c.execute("SELECT * FROM shifts WHERE user_id=%s AND brand_id=%s "
                          "AND kind='driver' AND status='open' FOR UPDATE",
                          (user['id'], user['brand_id'])).fetchone()
        if not shift:
            raise HTTPException(409, 'مشاركة الموقع متاحة أثناء الشفت المفتوح فقط.')
        shift_access(c, user, shift)
        # The shift lock serializes updates with shift closure and with other
        # tabs. At most one write per ten seconds, including competing tabs.
        previous = c.execute('SELECT updated_at,shift_id,updated_at > now()-interval \'10 seconds\' AS throttled '
                             'FROM driver_latest_locations WHERE driver_id=%s', (user['id'],)).fetchone()
        if previous and previous['shift_id'] == shift['id'] and previous['throttled']:
            return safe({'updated_at': previous['updated_at'], 'shift_id': shift['id'], 'throttled': True})
        row = c.execute('INSERT INTO driver_latest_locations(driver_id,brand_id,shift_id,lat,lng,accuracy_m) '
                        'VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(driver_id) DO UPDATE SET '
                        'brand_id=EXCLUDED.brand_id,shift_id=EXCLUDED.shift_id,lat=EXCLUDED.lat,lng=EXCLUDED.lng,'
                        'accuracy_m=EXCLUDED.accuracy_m,updated_at=now() RETURNING updated_at',
                        (user['id'], user['brand_id'], shift['id'], body.lat, body.lng, body.accuracy_m)).fetchone()
        from .map_services import record_sample
        record_sample(c, user, shift, body, previous, row['updated_at'])
        return safe({'updated_at': row['updated_at'], 'shift_id': shift['id'], 'throttled': False})


@router.delete('/driver-location')
def stop_driver_location(user=Depends(auth.require_roles('driver'))):
    with db.tx() as c:
        # Match POST lock order so a stop cannot race an already-running update.
        c.execute("SELECT id FROM shifts WHERE user_id=%s AND brand_id=%s AND status='open' FOR UPDATE",
                  (user['id'], user['brand_id'])).fetchall()
        c.execute('DELETE FROM driver_latest_locations WHERE driver_id=%s AND brand_id=%s',
                  (user['id'], user['brand_id']))
    return {'stopped': True}


@router.get('/driver-locations')
def driver_locations(user=Depends(auth.require_roles('owner', 'delivery_manager', 'delivery_staff'))):
    if not auth.has_permission(user, 'drivers_view'):
        raise HTTPException(403, 'صلاحية عرض السائقين غير متاحة.')
    with db.tx() as c:
        rows = c.execute("SELECT u.id,u.display_name,u.branch_ids,s.id shift_id,s.branch_ids shift_branches,s.next_destination,"
                         "g.lat,g.lng,g.accuracy_m,g.updated_at,"
                         "g.updated_at < now()-interval '120 seconds' AS stale "
                         "FROM driver_latest_locations g JOIN users u ON u.id=g.driver_id AND u.brand_id=g.brand_id "
                         "JOIN shifts s ON s.id=g.shift_id AND s.user_id=u.id AND s.brand_id=u.brand_id "
                         "WHERE g.brand_id=%s AND u.active AND u.role='driver' AND s.kind='driver' AND s.status='open' "
                         "AND g.updated_at > now()-interval '24 hours' ORDER BY u.display_name,u.id",
                         (user['brand_id'],)).fetchall()
        if user.get('_morning_manager_context'):
            from .morning_manager_context import shift_allowed
            rows=[r for r in rows if shift_allowed(user,c.execute('SELECT * FROM shifts WHERE id=%s',(r['shift_id'],)).fetchone())]
        destinations = driver_destination.metadata(c, user, rows)
        visible = []
        for row in rows:
            branches = [bid for bid in row['branch_ids'] if bid in row['shift_branches']]
            if user['role'] != 'owner':
                branches = [bid for bid in branches if bid in user.get('branch_ids', [])]
            if not branches:
                continue
            visible.append({'id': row['id'], 'display_name': row['display_name'], 'branch_ids': branches,
                            'shift_id': row['shift_id'], 'location': {'lat': row['lat'], 'lng': row['lng']},
                            'accuracy_m': row['accuracy_m'], 'updated_at': row['updated_at'], 'stale': row['stale'],
                            **{k:destinations[row['id']][k] for k in ('active_order_brand','next_destination','destination_choice_required','movement_state','visible_active_task_count')}})
        return safe({'drivers': visible, 'stale_after_seconds': STALE_AFTER_SECONDS})


# This budget bounds permits issued by this application, not independent use of
# the public Mapbox token. Missing or malformed budget state fails closed.
from datetime import datetime, timezone
import math
from fastapi import Query
from pydantic import model_validator

ROUTE_MONTHLY_LIMIT = 20000
ROUTE_BUDGET_KEY = '_dispatch_route_budget_v1'


class DriverRouteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    driver_id: str = Field(min_length=1, max_length=100)
    task_id: str | None = Field(None, min_length=1, max_length=100)
    return_branch_id: str | None = Field(None, min_length=1, max_length=100)

    @model_validator(mode='after')
    def one_destination(self):
        if bool(self.task_id) == bool(self.return_branch_id):
            raise ValueError('Choose exactly one task or return branch.')
        return self


def route_point(value):
    if not isinstance(value, dict):
        return None
    lat, lng = value.get('lat'), value.get('lng')
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (lat, lng)):
        return None
    return {'lat': lat, 'lng': lng} if -90 <= lat <= 90 and -180 <= lng <= 180 else None


def route_context(c, user, driver_id):
    if not auth.has_permission(user, 'drivers_view'):
        raise HTTPException(403, 'صلاحية عرض السائقين غير متاحة.')
    if not map_config(user).get('enabled'):
        raise HTTPException(409, 'خدمة الخريطة غير مفعلة.')
    row = c.execute("SELECT u.id,u.branch_ids,s.branch_ids shift_branches,s.id shift_id,s.next_destination,g.lat,g.lng,g.updated_at "
                    "FROM users u JOIN shifts s ON s.user_id=u.id AND s.brand_id=u.brand_id "
                    "JOIN driver_latest_locations g ON g.driver_id=u.id AND g.shift_id=s.id AND g.brand_id=u.brand_id "
                    "WHERE u.id=%s AND u.brand_id=%s AND u.role='driver' AND u.active "
                    "AND s.kind='driver' AND s.status='open' AND g.updated_at>=now()-interval '120 seconds' AND g.accuracy_m<=100",
                    (driver_id, user['brand_id'])).fetchone()
    if not row:
        raise HTTPException(409, 'يلزم موقع حديث للسائق وشفت مفتوح.')
    if user.get('_morning_manager_context'):
        shift_access(c,user,c.execute('SELECT * FROM shifts WHERE id=%s',(row['shift_id'],)).fetchone())
    branches = [b for b in row['branch_ids'] if b in row['shift_branches'] and
                (user['role'] == 'owner' or b in user.get('branch_ids', []))]
    if not branches:
        raise HTTPException(403, 'السائق خارج نطاق الفروع المسموح.')
    tasks = c.execute("SELECT t.id,t.order_id,o.order_no,o.customer FROM driver_tasks t JOIN orders o ON o.id=t.order_id "
                      "WHERE t.driver_id=%s AND t.shift_id=%s AND t.status='picked_up' AND o.brand_id=%s "
                      "AND o.branch_id=ANY(%s) AND o.service='delivery' AND o.completed_at IS NULL "
                      "AND o.preparation_status NOT IN ('cancelled','rejected') AND o.delivery_status NOT IN ('delivered','cancelled') "
                      "ORDER BY t.picked_up_at,t.id", (driver_id, row['shift_id'], user['brand_id'], branches)).fetchall()
    destinations = [{'id': t['id'], 'order_id': t['order_id'], 'order_no': t['order_no'], 'location': p}
                    for t in tasks if (p := route_point((t['customer'] or {}).get('location')))]
    branch_rows = c.execute('SELECT id,name,settings FROM branches WHERE brand_id=%s AND id=ANY(%s)',
                            (user['brand_id'], branches)).fetchall()
    # These are explicit choices, not a claim that the driver plans to return
    # to the departure branch. Actual return_branch_id is only known on arrival.
    returns = [{'id': b['id'], 'name': b['name'], 'location': p} for b in branch_rows
               if catalog.location_verified(b) and (p := route_point((b['settings'] or {}).get('location')))]
    return {'driver_id': driver_id, 'origin': {'lat': row['lat'], 'lng': row['lng']},
            'updated_at': row['updated_at'], 'tasks': destinations, 'return_branches': returns,
            'next_destination': driver_destination.metadata(c, user, [row])[driver_id]['next_destination']}


def consume_route_budget(c, driver_id, manager_id):
    # A single existing row locks the budget across ALL brands and workers.
    row = c.execute("SELECT settings,clock_timestamp() AS budget_now FROM app_settings WHERE brand_id='kps' FOR UPDATE").fetchone()
    if not row or not isinstance(row['settings'], dict):
        raise HTTPException(503, 'ميزانية المسارات غير متاحة.')
    now = row['budget_now'].astimezone(timezone.utc)
    month = now.strftime('%Y-%m')
    settings = dict(row['settings'])
    budget = settings.get(ROUTE_BUDGET_KEY, {'month': month, 'count': 0, 'recent': {}})
    if (not isinstance(budget, dict) or not isinstance(budget.get('month'), str) or
            type(budget.get('count')) is not int or budget['count'] < 0 or not isinstance(budget.get('recent'), dict)):
        raise HTTPException(503, 'ميزانية المسارات غير صالحة.')
    if budget['month'] != month:
        budget = {'month': month, 'count': 0, 'recent': {}}
    recent = budget['recent']
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in recent.values()):
        raise HTTPException(503, 'ميزانية المسارات غير صالحة.')
    keys = ['driver:' + driver_id, 'manager:' + manager_id]
    if any(now.timestamp() - recent.get(key, 0) < 60 for key in keys):
        raise HTTPException(429, 'انتظر دقيقة قبل طلب مسار جديد.')
    if budget['count'] >= ROUTE_MONTHLY_LIMIT:
        raise HTTPException(429, 'تم بلوغ حد المسارات لهذا الشهر.')
    budget['recent'] = {key: value for key, value in recent.items() if now.timestamp() - value < 60}
    budget['recent'].update({key: now.timestamp() for key in keys})
    budget['count'] += 1
    settings[ROUTE_BUDGET_KEY] = budget
    c.execute("UPDATE app_settings SET settings=%s,updated_at=now() WHERE brand_id='kps'", (db.json(settings),))
    return now


@router.get('/driver-route-context')
def discover_driver_routes(driver_id: str = Query(min_length=1, max_length=100),
                           user=Depends(auth.require_roles('owner', 'delivery_manager', 'delivery_staff'))):
    with db.tx() as c:
        return safe(route_context(c, user, driver_id))


@router.post('/driver-route-context')
def permit_driver_route(body: DriverRouteRequest,
                        user=Depends(auth.require_roles('owner', 'delivery_manager', 'delivery_staff'))):
    with db.tx() as c:
        context = route_context(c, user, body.driver_id)
        kind = 'customer' if body.task_id else 'return_branch'
        selected = body.task_id or body.return_branch_id
        choices = context['tasks'] if body.task_id else context['return_branches']
        destination = next((item for item in choices if item['id'] == selected), None)
        if destination is None:
            raise HTTPException(409, 'الوجهة غير متاحة أو خارج نطاق الصلاحية.')
        issued_at = consume_route_budget(c, body.driver_id, user['id'])
        return safe({'driver_id': body.driver_id, 'origin': context['origin'], 'destination': destination['location'],
                     'kind': kind, 'target_id': selected, 'issued_at': issued_at})


@router.get('/driver-destination')
def own_driver_destination(user=Depends(auth.require_roles('driver'))):
    with db.tx() as c:
        return safe(driver_destination.own_view(c, user, driver_destination.own_shift(c, user)))


@router.put('/driver-destination')
def choose_driver_destination(body: driver_destination.DestinationChoice, user=Depends(auth.require_roles('driver'))):
    with db.tx() as c:
        return safe(driver_destination.save_choice(c, user, body))
