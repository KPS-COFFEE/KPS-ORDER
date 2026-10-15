"""Vehicle faults and maintenance, using canonical sessions, scope and replay guards.

Reports never mutate delivery tasks, shifts, availability, custody or staff.
Only drivers submit their own reports; owners and explicitly delegated admins
manage them. No unrelated personnel, order, customer or financial data is read.
"""
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from . import auth, db

router = APIRouter(prefix='/api/vehicle-maintenance', tags=['vehicle-maintenance'])
PERMISSION = 'vehicle_maintenance_manage'
CATEGORIES = (
    ('order_problem','مشكلة طلب','Order problem'),
    ('delay','تأخير','Delay'),
    ('customer_problem','مشكلة عميل','Customer problem'),
    ('payment_problem','مشكلة دفع','Payment problem'),
    ('branch_problem','مشكلة في الفرع','Branch problem'),
    ('general','أخرى','Other'),
    ('engine', 'المحرك', 'Engine'),
    ('transmission', 'ناقل الحركة', 'Transmission'),
    ('brakes', 'الفرامل', 'Brakes'),
    ('tyres', 'الإطارات', 'Tyres'),
    ('battery', 'البطارية', 'Battery'),
    ('electrical', 'الكهرباء والإنارة', 'Electrical and lights'),
    ('cooling', 'التبريد وارتفاع الحرارة', 'Cooling and overheating'),
    ('air_conditioning', 'المكيف', 'Air conditioning'),
    ('oil_leak', 'تسريب الزيت أو السوائل', 'Oil or fluid leak'),
    ('accident', 'حادث أو تلف بالمركبة', 'Accident or vehicle damage'),
    ('scheduled_service', 'الصيانة الدورية', 'Scheduled maintenance'),
    ('other', 'عطل آخر', 'Other fault'),
)
DELIVERY_CATEGORIES={'order_problem','delay','customer_problem','payment_problem','branch_problem','general'}
CATEGORY_CODES = {row[0] for row in CATEGORIES}
Status = Literal['open', 'in_progress', 'resolved']


def participant(user=Depends(auth.current_user)):
    if user['role'] not in ('driver','delivery_manager') and not auth.has_permission(user, PERMISSION):
        raise db.error(403, 'ROLE_FORBIDDEN', 'ليس لديك صلاحية بلاغات المركبات والصيانة.')
    return user


def scope(user):
    clauses, params = ['r.brand_id=%s'], [user['brand_id']]
    if user['role'] != 'owner':
        clauses.append('r.branch_id=ANY(%s)')
        params.append(user.get('branch_ids') or [])
    if user['role'] == 'driver':
        clauses.append('r.driver_id=%s')
        params.append(user['id'])
    if user['role']=='delivery_manager':clauses.append("r.department='delivery'")
    return clauses, params


def report(conn, identifier, user, *, lock=False):
    clauses, params = scope(user)
    row = conn.execute(
        'SELECT r.* FROM vehicle_reports r WHERE r.id=%s AND ' + ' AND '.join(clauses)
        + (' FOR UPDATE' if lock else ''), [identifier, *params]).fetchone()
    if not row:
        raise db.error(404, 'VEHICLE_REPORT_NOT_FOUND', 'البلاغ غير موجود ضمن نطاقك.')
    return row


def record_event(conn, row, user, event_type, details):
    conn.execute('INSERT INTO vehicle_report_events(id,report_id,actor_id,event_type,details) VALUES(%s,%s,%s,%s,%s)',
                 (db.new_id('vre_'), row['id'], user['id'], event_type, db.json(details)))
    db.audit(conn, user['id'], 'vehicle_report.' + event_type, 'vehicle_report', row['id'], details)


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    shift_id:str|None=None
    order_id:str|None=None
    evidence_id:str|None=None
    branch_id: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=40)
    vehicle_description: str = Field(min_length=1, max_length=150)
    vehicle_plate: str = Field(default='', max_length=40)
    description: str = Field(min_length=1, max_length=2000)

    @field_validator('category')
    @classmethod
    def known_category(cls, value):
        if value not in CATEGORY_CODES:
            raise ValueError('Unknown vehicle fault category')
        return value

    @field_validator('*')
    @classmethod
    def safe_text(cls, value):
        if isinstance(value,str) and '\x00' in value:
            raise ValueError('Invalid text')
        return value


class ReportUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    status: Status
    version: StrictInt = Field(ge=1)
    note: str = Field(min_length=1, max_length=2000)

    @field_validator('note')
    @classmethod
    def safe_note(cls, value):
        if isinstance(value,str) and '\x00' in value:
            raise ValueError('Invalid text')
        return value


@router.get('/options')
def options(user=Depends(participant)):
    return {'categories': [{'code': code, 'ar': ar, 'en': en} for code, ar, en in CATEGORIES],
            'can_manage': user['role']=='delivery_manager' or auth.has_permission(user, PERMISSION), 'can_create': user['role'] == 'driver'}


@router.get('/reports')
def list_reports(status: Status | None = None, branch_id: str | None = None,
                 limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100000),
                 user=Depends(participant)):
    clauses, params = scope(user)
    if status:
        clauses.append('r.status=%s')
        params.append(status)
    if branch_id:
        clauses.append('r.branch_id=%s')
        params.append(branch_id)
    where = ' AND '.join(clauses)
    with db.tx() as conn:
        total = conn.execute('SELECT count(*) AS n FROM vehicle_reports r WHERE ' + where, params).fetchone()['n']
        rows = conn.execute(
            'SELECT r.*,u.display_name AS driver_name,b.name AS branch_name,b.branch_no '
            'FROM vehicle_reports r JOIN users u ON u.id=r.driver_id JOIN branches b ON b.id=r.branch_id '
            'WHERE ' + where + ' ORDER BY r.created_at DESC,r.id DESC LIMIT %s OFFSET %s',
            [*params, limit, offset]).fetchall()
    return {'reports': rows, 'total': total, 'limit': limit, 'offset': offset}


@router.get('/reports/{identifier}')
def report_detail(identifier: str, user=Depends(participant)):
    with db.tx() as conn:
        row = report(conn, identifier, user)
        events = conn.execute(
            'SELECT e.id,e.event_type,e.details,e.created_at,u.display_name AS actor_name '
            'FROM vehicle_report_events e JOIN users u ON u.id=e.actor_id WHERE e.report_id=%s '
            'ORDER BY e.created_at,e.id', (identifier,)).fetchall()
        return {'report': row, 'events': events}


@router.post('/reports')
def create_report(body: ReportCreate, request: Request, user=Depends(auth.require_roles('driver'))):
    with db.tx() as conn:
        # Revalidate before replay: an old idempotency key cannot restore withdrawn access.
        if body.branch_id not in (user.get('branch_ids') or []) or not conn.execute(
                'SELECT 1 FROM branches WHERE id=%s AND brand_id=%s', (body.branch_id, user['brand_id'])).fetchone():
            raise db.error(404, 'BRANCH_SCOPE_FORBIDDEN', 'الفرع خارج نطاقك المخول.')

        def perform():
            if body.shift_id:
                from .operations import shift_access
                shift=conn.execute('SELECT * FROM shifts WHERE id=%s',(body.shift_id,)).fetchone();shift_access(conn,user,shift)
            if body.evidence_id:
                from .driver_evidence import validate
                validate(conn,body.evidence_id,user,body.shift_id)
            if body.order_id:
                from .order_support import order_access
                order_access(conn,user,body.order_id)
            row = conn.execute(
                'INSERT INTO vehicle_reports(id,brand_id,branch_id,driver_id,category,vehicle_description,vehicle_plate,description) '
                'VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                (db.new_id('vr_'), user['brand_id'], body.branch_id, user['id'], body.category,
                 body.vehicle_description, body.vehicle_plate, body.description)).fetchone()
            department='delivery' if body.category in DELIVERY_CATEGORIES else 'maintenance'
            conn.execute('UPDATE vehicle_reports SET shift_id=%s,order_id=%s,evidence_id=%s,department=%s WHERE id=%s',(body.shift_id,body.order_id,body.evidence_id,department,row['id']))
            row.update(shift_id=body.shift_id,order_id=body.order_id,evidence_id=body.evidence_id,department=department)
            record_event(conn, row, user, 'created', {'category': body.category, 'status': 'open'})
            return {'report': row}

        return db.idempotent(conn, user['id'], request.headers.get('Idempotency-Key'),
                             'vehicle_report.create', body.model_dump(), perform)


@router.post('/reports/{identifier}/status')
def update_report(identifier: str, body: ReportUpdate, request: Request,
                  user=Depends(participant)):
    if user['role']!='delivery_manager' and not auth.has_permission(user,PERMISSION):raise db.error(403,'FORBIDDEN','تحديث البلاغ للإدارة فقط.')
    with db.tx() as conn:
        # Lock before replay and verify current scope. Only one version can win.
        row = report(conn, identifier, user, lock=True)

        def perform():
            if row['version'] != body.version:
                raise db.error(409, 'VEHICLE_REPORT_CHANGED', 'تم تحديث البلاغ. حدّث الصفحة قبل المحاولة.')
            allowed = {'open': {'in_progress', 'resolved'}, 'in_progress': {'resolved'}, 'resolved': {'open'}}
            if body.status not in allowed[row['status']]:
                raise db.error(409, 'VEHICLE_REPORT_TRANSITION', 'انتقال حالة البلاغ غير صالح.')
            updated = conn.execute(
                "UPDATE vehicle_reports SET status=%s,version=version+1,updated_at=now(),"
                "resolved_at=CASE WHEN %s='resolved' THEN now() ELSE NULL END,"
                "resolution_note=CASE WHEN %s='resolved' THEN %s ELSE '' END WHERE id=%s RETURNING *",
                (body.status, body.status, body.status, body.note, identifier)).fetchone()
            event_type = 'reopened' if body.status == 'open' else body.status
            record_event(conn, updated, user, event_type,
                         {'previous_status': row['status'], 'status': body.status, 'note': body.note,
                          'version': updated['version']})
            return {'report': updated}

        return db.idempotent(conn, user['id'], request.headers.get('Idempotency-Key'),
                             'vehicle_report.status:' + identifier, body.model_dump(), perform)
