"""Optional staff map configuration; never changes customer map behavior."""
import os
import re
from fastapi import APIRouter, Depends
from . import auth

router = APIRouter(prefix='/api/operations', tags=['operations'])


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
            'style': 'mapbox://styles/mapbox/streets-v12'}


# One last observation per driver, never a route history. All clocks are server
# clocks; callers cannot refresh an old position by providing a timestamp.
from pydantic import BaseModel, ConfigDict, Field
from fastapi import HTTPException
from . import db
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
        rows = c.execute("SELECT u.id,u.display_name,u.branch_ids,s.id shift_id,s.branch_ids shift_branches,"
                         "g.lat,g.lng,g.accuracy_m,g.updated_at,"
                         "g.updated_at < now()-interval '120 seconds' AS stale "
                         "FROM driver_latest_locations g JOIN users u ON u.id=g.driver_id AND u.brand_id=g.brand_id "
                         "JOIN shifts s ON s.id=g.shift_id AND s.user_id=u.id AND s.brand_id=u.brand_id "
                         "WHERE g.brand_id=%s AND u.active AND u.role='driver' AND s.kind='driver' AND s.status='open' "
                         "AND g.updated_at > now()-interval '24 hours' ORDER BY u.display_name,u.id",
                         (user['brand_id'],)).fetchall()
        visible = []
        for row in rows:
            branches = [bid for bid in row['branch_ids'] if bid in row['shift_branches']]
            if user['role'] != 'owner':
                branches = [bid for bid in branches if bid in user.get('branch_ids', [])]
            if not branches:
                continue
            visible.append({'id': row['id'], 'display_name': row['display_name'], 'branch_ids': branches,
                            'shift_id': row['shift_id'], 'location': {'lat': row['lat'], 'lng': row['lng']},
                            'accuracy_m': row['accuracy_m'], 'updated_at': row['updated_at'], 'stale': row['stale']})
        return safe({'drivers': visible, 'stale_after_seconds': STALE_AFTER_SECONDS})
