"""Isolated dual-brand development. No implicit cross-brand authority.

The switch is intentionally unusable with a remote or operational database.
Production integration requires a separately reviewed release.
"""
import os
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.conninfo import conninfo_to_dict

from . import auth, db

router = APIRouter(prefix='/api/multibrand', tags=['isolated-multibrand'])
BRANDS = frozenset({'kps', 'enbishan'})
operation_brand = ContextVar('operations_brand', default=None)


def replay_operation(conn, user_id, operation):
    if not enabled():
        return operation
    isolation_guard()
    row = conn.execute('SELECT brand_id FROM users WHERE id=%s', (user_id,)).fetchone()
    brand = operation_brand.get() or (row['brand_id'] if row else None)
    if brand:
        return 'brand:' + brand + ':' + operation
    return operation


def enabled():
    if os.getenv('ENBISHAN_ISOLATED_ENABLED') == 'true':
        if os.getenv('KPS_MULTIBRAND_ENABLED') == 'true':
            raise RuntimeError('Choose isolated development or an approved runtime, never both.')
        return True
    from . import activation
    return activation.enabled('multibrand')


def isolation_guard():
    if not enabled():
        raise db.error(404, 'MULTIBRAND_DISABLED', 'إدارة البراندات غير مفعلة.')
    try:
        parts = conninfo_to_dict(os.environ.get('DATABASE_URL', ''))
    except Exception:
        parts = {}
    name = parts.get('dbname', '')
    if os.getenv('ENBISHAN_ISOLATED_ENABLED') != 'true':
        # A distinct, exact-release approved runtime path. The original local
        # test-database/credential restrictions below remain unchanged.
        if (os.getenv('APP_ENV') not in {'production','staging','testing'} or parts.get('service')
                or not name or name != os.getenv('KPS_MULTIBRAND_DATABASE_NAME')
                or not parts.get('host') or parts['host'] != os.getenv('KPS_MULTIBRAND_DATABASE_HOST')
                or parts.get('hostaddr') not in (None,parts.get('host'))):
            raise RuntimeError('Approved multibrand runtime requires its explicit database host/name binding.')
        if os.getenv('APP_ENV')=='testing' and not name.startswith('test_'):
            raise RuntimeError('Runtime tests require a named test database.')
        return
    if (os.getenv('APP_ENV') != 'testing' or parts.get('host') not in {'127.0.0.1', 'localhost', '::1'}
            or parts.get('hostaddr', '127.0.0.1') not in {'127.0.0.1', '::1'}
            or parts.get('service')
            or not name.startswith('test_enbishan_')):
        raise RuntimeError('Enbishan development requires its own local test_enbishan_* database.')
    # Credentials must never be inherited by the isolated application.
    if any(os.getenv(key) for key in ('TAWKED_API_KEY', 'ENBISHAN_TAWKED_API_KEY', 'TWILIO_AUTH_TOKEN', 'RESEND_API_KEY', 'RENDER_API_KEY')):
        raise RuntimeError('Provider credentials are prohibited in isolated Enbishan development.')
    if any(key.startswith('KPS_PAYMENTS_') and value for key, value in os.environ.items()):
        raise RuntimeError('Live payment configuration is prohibited in isolated Enbishan development.')


def migrate(conn):
    if not enabled():
        return
    isolation_guard()
    import hashlib
    for path in sorted(Path(__file__).with_name('multibrand_migrations').glob('*.sql')):
        contents = path.read_text()
        checksum = hashlib.sha256(contents.encode()).hexdigest()
        previous = conn.execute('SELECT sha256 FROM schema_migrations WHERE version=%s', (path.name,)).fetchone()
        if previous:
            if previous['sha256'] != checksum:
                raise RuntimeError('Applied isolated migration changed.')
            continue
        conn.execute(contents)
        conn.execute('INSERT INTO schema_migrations(version,sha256) VALUES(%s,%s)', (path.name, checksum))


def project(conn, user, brand):
    """Same user ID/role; narrow branch scope from an explicit current grant."""
    if not user or not user.get('active'):
        return None
    if user.get('home_brand_id'):
        original = conn.execute('SELECT brand_id,branch_ids FROM users WHERE id=%s AND active', (user['id'],)).fetchone()
        if not original:
            return None
        user = dict(user, **original)
    if user['brand_id'] == brand:
        return user
    if not enabled() or user['role'] not in {'owner', 'admin', 'delivery_manager', 'delivery_staff', 'driver'}:
        return None
    isolation_guard()
    grant = conn.execute('SELECT m.branch_ids FROM staff_brand_memberships m '
        'JOIN brands b ON b.id=m.brand_id WHERE m.user_id=%s AND m.brand_id=%s AND m.active AND b.active',
        (user['id'], brand)).fetchone()
    if not grant:
        return None
    # No permissions or role can be elevated by membership.
    return dict(user, brand_id=brand, branch_ids=grant['branch_ids'], home_brand_id=user['brand_id'])


def request_user(conn, request, user):
    values = request.headers.getlist('x-operations-brand')
    if not values:
        return user
    if not enabled() or len(values) != 1 or ',' in values[0] or user['role'] == 'customer':
        raise db.error(403, 'OPERATIONS_BRAND_FORBIDDEN', 'البراند غير مخول لحسابك.')
    target = project(conn, user, values[0])
    if not target:
        raise db.error(403, 'OPERATIONS_BRAND_FORBIDDEN', 'البراند غير مخول لحسابك.')
    return target


def available(conn, user):
    if not enabled() or user['role'] == 'customer':
        return []
    isolation_guard()
    rows = conn.execute('SELECT b.id,b.name FROM brands b WHERE b.active AND '
        '(b.id=%s OR EXISTS(SELECT 1 FROM staff_brand_memberships m WHERE m.user_id=%s AND m.brand_id=b.id AND m.active)) '
        'ORDER BY b.id', (user.get('home_brand_id', user['brand_id']), user['id'])).fetchall()
    return rows


def lock_driver(conn, driver_id):
    conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('multibrand-driver:' + driver_id,))


def dedication_guard(conn, driver_id, brand):
    if not enabled():
        return
    isolation_guard()
    lock_driver(conn, driver_id)
    row = conn.execute('SELECT brand_id FROM driver_brand_dedications WHERE driver_id=%s '
        'AND cancelled_at IS NULL AND starts_at<=now() AND ends_at>now()', (driver_id,)).fetchone()
    if row and row['brand_id'] != brand:
        raise db.error(409, 'DRIVER_BRAND_DEDICATED', 'السائق مخصص لبراند آخر في هذه الفترة.')


@router.get('/context')
def context(user=Depends(auth.current_user)):
    isolation_guard()
    with db.tx() as conn:
        brands = available(conn, user)
    return {'brand_id': user['brand_id'], 'brands': brands}


@router.get('/inbox')
def inbox(user=Depends(auth.require_roles('owner', 'delivery_manager', 'delivery_staff', 'driver'))):
    isolation_guard()
    permission = 'tasks_view' if user['role']=='driver' else 'orders_view'
    if not auth.has_permission(user, permission):
        raise db.error(403, 'STAFF_PERMISSION_FORBIDDEN', 'صلاحية عرض الطلبات متوقفة لحسابك.')
    with db.tx() as conn:
        result = []
        for brand in available(conn, user):
            principal = project(conn, user, brand['id'])
            rows = conn.execute("SELECT o.* FROM orders o WHERE o.brand_id=%s AND o.service='delivery' "
                "AND o.completed_at IS NULL AND o.preparation_status NOT IN ('cancelled','rejected') "
                "ORDER BY o.created_at DESC LIMIT 100", (brand['id'],)).fetchall()
            # Reuse canonical per-order authorization, including task ownership.
            result.append({'brand_id': brand['id'], 'brand_name': brand['name'], 'orders': [
                {'id': row['id'], 'order_no': row.get('order_no'), 'branch_id': row['branch_id'],
                 'preparation_status': row['preparation_status'], 'delivery_status': row['delivery_status']}
                for row in rows if principal and auth.can_access_order(principal, row, conn)]})
    return {'brands': result}


@router.get('/pool')
def pool(user=Depends(auth.require_roles('owner', 'delivery_manager'))):
    isolation_guard()
    if not auth.has_permission(user, 'drivers_view'):
        raise db.error(403, 'STAFF_PERMISSION_FORBIDDEN', 'صلاحية عرض السائقين متوقفة لحسابك.')
    with db.tx() as conn:
        brands = available(conn, user)
        rows = conn.execute("SELECT * FROM users WHERE active AND role='driver' ORDER BY display_name,id").fetchall()
        drivers = []
        for row in rows:
            scopes = []
            for brand in brands:
                actor = project(conn, user, brand['id'])
                target = project(conn, row, brand['id'])
                if actor and target and (actor['role']=='owner' or set(target['branch_ids']) <= set(actor['branch_ids'])):
                    scopes.append(brand['id'])
            if scopes:
                dedications = conn.execute('SELECT id,brand_id,starts_at,ends_at,reason FROM driver_brand_dedications '
                    'WHERE driver_id=%s AND brand_id=ANY(%s) AND cancelled_at IS NULL AND ends_at>now() ORDER BY starts_at',
                    (row['id'], scopes)).fetchall()
                drivers.append({'id': row['id'], 'display_name': row['display_name'], 'brands': scopes, 'dedications': dedications})
    return {'drivers': drivers, 'brands': brands}


@router.get('/driver-tasks')
def driver_tasks(user=Depends(auth.require_roles('driver'))):
    isolation_guard()
    if not auth.has_permission(user, 'tasks_view'):
        raise db.error(403, 'STAFF_PERMISSION_FORBIDDEN', 'صلاحية عرض المهام متوقفة لحسابك.')
    from . import operations
    with db.tx() as conn:
        principals = [(brand, project(conn, user, brand['id'])) for brand in available(conn, user)]
    result = []
    for brand, principal in principals:
        if principal:
            for task in operations.tasks(order_id=None, user=principal)['tasks']:
                result.append(dict(task, brand_id=brand['id'], brand_name=brand['name']))
    return {'tasks': result}


class Membership(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str = Field(min_length=1, max_length=200)
    brand_id: str = Field(pattern='^(kps|enbishan)$')
    branch_ids: list[str] = Field(min_length=1, max_length=100)
    active: bool = True


@router.put('/memberships')
def membership(body: Membership, request: Request, user=Depends(auth.require_roles('owner'))):
    isolation_guard()
    with db.tx() as conn:
        auth.require_owner_step_up(conn, user)
        authorized = {b['id'] for b in available(conn, user)}
        target = conn.execute('SELECT * FROM users WHERE id=%s AND active', (body.user_id,)).fetchone()
        if (not target or target['role'] not in {'owner', 'admin', 'delivery_manager', 'delivery_staff', 'driver'}
                or body.brand_id not in authorized or target['brand_id'] not in authorized):
            raise db.error(403, 'MEMBERSHIP_FORBIDDEN', 'الموظف أو البراند خارج صلاحيتك.')
        branches = conn.execute('SELECT id FROM branches WHERE brand_id=%s AND id=ANY(%s)',
                               (body.brand_id, body.branch_ids)).fetchall()
        if {b['id'] for b in branches} != set(body.branch_ids):
            raise db.error(422, 'MEMBERSHIP_BRANCH_MISMATCH', 'الفروع لا تتبع البراند المحدد.')
        def perform():
            conn.execute('INSERT INTO staff_brand_memberships(user_id,brand_id,branch_ids,active,granted_by) '
                'VALUES(%s,%s,%s,%s,%s) ON CONFLICT(user_id,brand_id) DO UPDATE '
                'SET branch_ids=EXCLUDED.branch_ids,active=EXCLUDED.active,granted_by=EXCLUDED.granted_by',
                (body.user_id, body.brand_id, db.json(sorted(set(body.branch_ids))), body.active, user['id']))
            db.audit(conn, user['id'], 'multibrand.membership', 'user', body.user_id, body.model_dump())
            return {'updated': True, 'brand_id': body.brand_id}
        return db.idempotent(conn, user['id'], request.headers.get('Idempotency-Key', ''),
                             'multibrand.membership:' + user['brand_id'], body.model_dump(), perform)


class Dedication(BaseModel):
    model_config = ConfigDict(extra='forbid')
    driver_id: str = Field(min_length=1, max_length=200)
    brand_id: str = Field(pattern='^(kps|enbishan)$')
    starts_at: datetime
    ends_at: datetime
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('starts_at', 'ends_at')
    @classmethod
    def aware(cls, value):
        if value.utcoffset() is None:
            raise ValueError('Timezone is required.')
        return value.astimezone(timezone.utc)


@router.post('/dedications')
def dedicate(body: Dedication, request: Request, user=Depends(auth.require_roles('owner', 'delivery_manager'))):
    isolation_guard()
    if not auth.has_permission(user, 'delivery_assign'):
        raise db.error(403, 'STAFF_PERMISSION_FORBIDDEN', 'صلاحية تخصيص السائقين متوقفة لحسابك.')
    if body.ends_at <= body.starts_at:
        raise db.error(422, 'DEDICATION_PERIOD_INVALID', 'نهاية الفترة يجب أن تكون بعد بدايتها.')
    with db.tx() as conn:
        actor = project(conn, user, body.brand_id)
        target = project(conn, conn.execute('SELECT * FROM users WHERE id=%s', (body.driver_id,)).fetchone(), body.brand_id)
        if (not actor or not target or target['role'] != 'driver'
                or (actor['role'] != 'owner' and not set(target['branch_ids']) <= set(actor['branch_ids']))):
            raise db.error(403, 'DEDICATION_FORBIDDEN', 'السائق خارج نطاق الإدارة.')
        lock_driver(conn, body.driver_id)
        def perform():
            if conn.execute('SELECT 1 FROM driver_brand_dedications WHERE driver_id=%s AND cancelled_at IS NULL '
                    'AND starts_at<%s AND ends_at>%s', (body.driver_id, body.ends_at, body.starts_at)).fetchone():
                raise db.error(409, 'DEDICATION_OVERLAP', 'توجد فترة تخصيص متداخلة.')
            # A new dedication cannot strand an existing task or offer.
            if conn.execute("SELECT 1 FROM driver_tasks t JOIN orders o ON o.id=t.order_id WHERE t.driver_id=%s "
                    "AND o.brand_id<>%s AND t.status NOT IN ('delivered','cancelled','transferred','reassigned')",
                    (body.driver_id, body.brand_id)).fetchone():
                raise db.error(409, 'DEDICATION_ACTIVE_TASKS', 'أكمل المهام الأخرى قبل تخصيص السائق.')
            identifier = db.new_id('dedication_')
            conn.execute('INSERT INTO driver_brand_dedications(id,driver_id,brand_id,starts_at,ends_at,reason,created_by) '
                'VALUES(%s,%s,%s,%s,%s,%s,%s)', (identifier, body.driver_id, body.brand_id, body.starts_at, body.ends_at, body.reason, user['id']))
            db.audit(conn, user['id'], 'multibrand.dedication', 'user', body.driver_id,
                     body.model_dump(mode='json'))
            return {'id': identifier, 'brand_id': body.brand_id}
        return db.idempotent(conn, user['id'], request.headers.get('Idempotency-Key', ''),
                             'multibrand.dedication:' + user['brand_id'], body.model_dump(mode='json'), perform)
