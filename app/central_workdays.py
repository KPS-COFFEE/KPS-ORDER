"""One physical workday, canonical separate brand ledgers, atomic lifecycle.

All grants are re-read on every action. Grouping never authorizes a record or
changes a staff role. The rollout shares the central operations feature gate.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from fastapi import APIRouter, Depends, Request

from . import auth, db, multibrand
from . import operations as ops

router = APIRouter(prefix='/workdays', tags=['central-workdays'])
_action = ContextVar('central_workday_action', default=False)


@contextmanager
def action():
    token = _action.set(True)
    try:
        yield
    finally:
        _action.reset(token)


def require_action():
    if not _action.get():
        raise db.error(409, 'CENTRAL_WORKDAY_REQUIRED', 'أكمل الإجراء من الوردية المشتركة.')


def require_enabled(user, permission):
    from .central_operations import enabled
    if not enabled() or user['role'] == 'customer' or user.get('_morning_manager_context'):
        raise db.error(404, 'CENTRAL_WORKDAY_UNAVAILABLE', 'الوردية غير متاحة.')
    if not auth.has_permission(user, permission):
        raise db.error(403, 'STAFF_PERMISSION_FORBIDDEN', 'صلاحية الإجراء غير متاحة لحسابك.')


def lock_lanes(c):
    # Same lanes as canonical financial mutations, in one consistent order.
    for brand in sorted(multibrand.BRANDS):
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('operations:'+brand,))


def association(c, sid):
    if not multibrand.enabled():
        return None
    return c.execute('SELECT w.* FROM central_workdays w JOIN central_workday_legs l '
                     'ON l.workday_id=w.id WHERE l.shift_id=%s', (sid,)).fetchone()


def guard_component(c, sid):
    """Canonical endpoints cannot independently end one leg of a workday."""
    if association(c, sid):
        require_action()


def authorized(c, user, sid, *, manager=False, capability=None):
    day = association(c, sid)
    if not day:
        raise db.error(404, 'CENTRAL_WORKDAY_UNAVAILABLE', 'الوردية غير متاحة.')
    legs = c.execute('SELECT s.* FROM central_workday_legs l JOIN shifts s ON s.id=l.shift_id '
                     'WHERE l.workday_id=%s ORDER BY s.brand_id FOR UPDATE OF s', (day['id'],)).fetchall()
    result = []
    for shift in legs:
        principal = multibrand.project(c, user, shift['brand_id'])
        if not principal:
            raise db.error(404, 'CENTRAL_WORKDAY_UNAVAILABLE', 'الوردية غير متاحة ضمن صلاحياتك الحالية.')
        ops.shift_access(c, principal, shift, manager=manager, capability=capability)
        result.append((principal, shift))
    return day, result


def annotate(c, rows):
    """Attach only grouping metadata for rows already authorized by their API."""
    if not rows or not multibrand.enabled():
        return rows
    metadata = c.execute('SELECT l.shift_id,w.id AS central_workday_id,w.anchor_shift_id '
        'FROM central_workday_legs l JOIN central_workdays w ON w.id=l.workday_id '
        'WHERE l.shift_id=ANY(%s)', ([s['id'] for s in rows],)).fetchall()
    by_id = {r['shift_id']: r for r in metadata}
    for row in rows:
        item = by_id.get(row['id'])
        if item:
            row.update(central_workday_id=item['central_workday_id'], central_anchor_id=item['anchor_shift_id'])
    return rows


def _open(c, user, body):
    ops.shift_roles(user, 'shifts_open', 'owner', 'delivery_manager', 'driver')
    if body.receipt_trial or body.delivery_period == 'morning':
        raise db.error(409, 'CENTRAL_WORKDAY_PERIOD', 'مناوبة الصباح المحدودة لها مسارها المعتمد.')
    target = c.execute('SELECT * FROM users WHERE id=%s AND active', (body.user_id or user['id'],)).fetchone()
    if not target or target['role'] not in {'driver', 'delivery_manager', 'owner'}:
        raise db.error(404, 'CENTRAL_WORKDAY_UNAVAILABLE', 'الموظف غير متاح.')
    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('shift:'+target['id'],))
    if c.execute("SELECT 1 FROM shifts WHERE user_id=%s AND status<>'closed'", (target['id'],)).fetchone():
        raise db.error(409, 'SHIFT_ALREADY_OPEN', 'للموظف وردية مفتوحة؛ أكملها قبل فتح وردية جديدة.')
    from .central_operations import context
    scopes = context(c, target)['brands']
    if user['role'] == 'driver' and body.branch_ids:
        raise db.error(403, 'CENTRAL_BRANCH_SCOPE', 'فروع السائق تحددها صلاحياته المعتمدة.')
    parent_legs = {}
    if body.kind == 'driver':
        if not body.manager_shift_id:
            raise db.error(409, 'MANAGER_SHIFT_REQUIRED', 'اختر وردية مدير التوصيل المفتوحة.')
        parent = association(c, body.manager_shift_id)
        if not parent or parent['kind'] != 'manager':
            raise db.error(409, 'MANAGER_SHIFT_SCOPE', 'اختر وردية المدير المشتركة المفتوحة.')
        parent_legs = {r['brand_id']: r['shift_id'] for r in c.execute(
            'SELECT brand_id,shift_id FROM central_workday_legs WHERE workday_id=%s', (parent['id'],)).fetchall()}
    selected = set(body.branch_ids)
    available = {bid for scope in scopes for bid in scope['branch_ids']}
    if selected - available:
        raise db.error(403, 'CENTRAL_BRANCH_SCOPE', 'فرع غير مخول للموظف.')
    # A manual opening float belongs to one ledger once. Existing retained funds
    # and distinct accountant-approved floats retain their original brand.
    scopes.sort(key=lambda b: (b['id'] != target['brand_id'], b['id']))
    opened = []
    for scope in scopes:
        bids = [bid for bid in scope['branch_ids'] if not selected or bid in selected]
        if not bids:
            continue
        principal = multibrand.project(c, user, scope['id'])
        if not principal or (principal['role'] != 'owner' and not set(bids) <= set(principal['branch_ids'])):
            raise db.error(403, 'CENTRAL_BRANCH_SCOPE', 'فرع خارج صلاحياتك الحالية.')
        if body.kind == 'driver' and scope['id'] not in parent_legs:
            raise db.error(409, 'MANAGER_SHIFT_SCOPE', 'وردية المدير لا تغطي جميع فروع السائق المعتمدة.')
        leg_body = body.model_copy(update={'branch_ids': bids,
            'manager_shift_id': parent_legs.get(scope['id']) if body.kind == 'driver' else None,
            'opening_halalas': body.opening_halalas if not opened else 0})
        opened.append(ops.open_shift_in_tx(c, principal, leg_body,
            vehicle_anchor=opened[0]['id'] if opened and body.kind == 'driver' else None)['shift'])
    if not opened:
        raise db.error(403, 'CENTRAL_BRANCH_SCOPE', 'لا توجد فروع تشغيل مخولة.')
    identifier = ops.uid()
    c.execute('INSERT INTO central_workdays(id,user_id,kind,anchor_shift_id,created_by) VALUES(%s,%s,%s,%s,%s)',
              (identifier, target['id'], body.kind, opened[0]['id'], user['id']))
    for leg in opened:
        c.execute('INSERT INTO central_workday_legs(workday_id,brand_id,shift_id) VALUES(%s,%s,%s)',
                  (identifier, leg['brand_id'], leg['id']))
    db.audit(c, user['id'], 'central.workday.open', 'central_workday', identifier,
             {'anchor_shift_id': opened[0]['id'], 'legs': [r['id'] for r in opened]})
    annotate(c, opened)
    return {'shift': opened[0], 'shifts': opened}


@router.post('')
def open_workday(body: ops.ShiftOpen, request: Request, user=Depends(auth.current_user)):
    require_enabled(user, 'shifts_open')
    with db.tx() as c:
        lock_lanes(c)
        def perform():
            with action():
                return ops.safe(_open(c, user, body))
        # Replays must retain current access to every constituent branch.
        result = db.idempotent(c, user['id'], request.headers.get('Idempotency-Key',''),
                              'central.workday.open', body.model_dump(), perform)
        authorized(c, user, result['shift']['id'])
        return ops.safe(ops.blind_closure.driver_view(user, result, shift_money=True))


def ordered(legs, anchor):
    return sorted(legs, key=lambda item: (item[1]['id'] != anchor, item[1]['brand_id']))


def result(c, day, legs):
    """One display row; the brand legs remain immutable canonical identities."""
    rows = [c.execute('SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id '
                     'WHERE s.id=%s', (s['id'],)).fetchone() for _, s in legs]
    annotate(c, rows)
    anchor = next(s for s in rows if s['id'] == day['anchor_shift_id'])
    return {'shift': anchor, 'shifts': rows, 'central_workday_id': day['id']}


@router.get('/{sid}')
def detail(sid: str, user=Depends(auth.current_user)):
    require_enabled(user, 'shifts_view')
    with db.tx() as c:
        day, legs = authorized(c, user, sid, capability='shifts_view')
        output = result(c, day, legs)
        output['ledgers'] = []
        for principal, shift in legs:
            item = {'shift_id': shift['id'], 'brand_id': shift['brand_id'],
                    'expected_halalas': ops.balance(c, shift), 'submission': shift['submission'],
                    'permanent_float': shift.get('permanent_float')}
            output['ledgers'].append(item)
        return ops.safe(ops.blind_closure.driver_view(user, output, shift_money=True))


def change(c, user, sid, operation, body, request, perform, *, manager=False, capability=None):
    lock_lanes(c)
    day, legs = authorized(c, user, sid, manager=manager, capability=capability)
    def execute():
        with action():
            value = perform(day, ordered(legs, day['anchor_shift_id']))
            return ops.safe({**result(c, day, legs), **(value or {})})
    output = db.idempotent(c, user['id'], request.headers.get('Idempotency-Key',''),
        'central.workday.'+operation+':'+day['id'], body, execute)
    return ops.safe(ops.blind_closure.driver_view(user, output, shift_money=True))


@router.post('/{sid}/closing')
def closing(sid: str, request: Request, user=Depends(auth.current_user)):
    require_enabled(user, 'shifts_close')
    with db.tx() as c:
        def perform(day, legs):
            for principal, shift in legs:
                ops.request_close_in_tx(c, principal, shift['id'])
        return change(c, user, sid, 'closing', {}, request, perform)


class LegSubmission(ops.OperationBody):
    shift_id: str
    counted_halalas: ops.StrictInt = ops.Field(ge=0, le=ops.MAX_HALALAS)
    handover_halalas: ops.StrictInt = ops.Field(ge=0, le=ops.MAX_HALALAS)
    retained_halalas: ops.StrictInt = ops.Field(0, ge=0, le=ops.MAX_HALALAS)
    terminal_total_halalas: ops.StrictInt | None = ops.Field(None, ge=0, le=ops.MAX_HALALAS)
    handover: ops.ClosureHandover | None = None


class Submission(ops.OperationBody):
    legs: list[LegSubmission] = ops.Field(min_length=1, max_length=2)
    vehicle_inspection: ops.shift_vehicle.Inspection | None = None
    exception_reason: str = ops.Field('', max_length=2000)


def exact_legs(legs, supplied):
    ids = [row.shift_id for row in supplied]
    if len(ids) != len(set(ids)) or set(ids) != {s['id'] for _, s in legs}:
        raise db.error(422, 'CENTRAL_LEDGER_BREAKDOWN', 'أدخل تفاصيل جميع سجلات الوردية مرة واحدة.')
    return {row.shift_id: row for row in supplied}


@router.post('/{sid}/submit')
def submit(sid: str, body: Submission, request: Request, user=Depends(auth.current_user)):
    require_enabled(user, 'shifts_close' if user['role']=='driver' else 'shifts_delegate_close' if user['role']=='admin' else 'shifts_close')
    with db.tx() as c:
        def perform(day, legs):
            supplied = exact_legs(legs, body.legs)
            for principal, shift in legs:
                value = supplied[shift['id']]
                ops.submit_close_in_tx(c, principal, shift['id'], ops.ClosureSubmit(
                    **value.model_dump(exclude={'shift_id'}), vehicle_inspection=body.vehicle_inspection,
                    exception_reason=body.exception_reason))
        return change(c, user, sid, 'submit', body.model_dump(), request, perform)


@router.post('/{sid}/approve')
def approve(sid: str, body: ops.Review, request: Request, user=Depends(auth.current_user)):
    require_enabled(user, 'shifts_approve')
    with db.tx() as c:
        def perform(day, legs):
            reports = []
            for principal, shift in legs:
                reports.append(ops.approve_close_in_tx(c, principal, shift['id'], body)['report_id'])
            return {'report_ids': reports}
        output = change(c, user, sid, 'approve', body.model_dump(), request, perform, capability='shifts_approve')
    from .reporting import generate_files
    for identifier in output['report_ids']:
        generate_files(identifier)
    return output


class LegReceipt(ops.OperationBody):
    shift_id: str
    received_halalas: ops.StrictInt = ops.Field(ge=0, le=ops.MAX_HALALAS)
    counted_halalas: ops.StrictInt | None = ops.Field(None, ge=0, le=ops.MAX_HALALAS)
    terminal_total_halalas: ops.StrictInt | None = ops.Field(None, ge=0, le=ops.MAX_HALALAS)


class Receipt(ops.OperationBody):
    legs: list[LegReceipt] = ops.Field(min_length=1, max_length=2)
    confirmed: ops.StrictBool
    vehicle_inspection: ops.shift_vehicle.Inspection | None = None
    exception_reason: str = ops.Field('', max_length=2000)


@router.post('/{sid}/driver-close')
def driver_close(sid: str, body: Receipt, request: Request, user=Depends(auth.current_user)):
    require_enabled(user, 'shifts_delegate_close')
    ops.roles(user, 'delivery_manager')
    with db.tx() as c:
        def perform(day, legs):
            if day['kind'] != 'driver':
                raise db.error(409, 'CENTRAL_DRIVER_WORKDAY', 'اختر وردية سائق.')
            supplied = exact_legs(legs, body.legs)
            reports = []
            for principal, shift in legs:
                value = supplied[shift['id']]
                reports.append(ops.simple_driver_close_in_tx(c, principal, shift['id'], ops.DriverClose(
                    **value.model_dump(exclude={'shift_id'}), confirmed=body.confirmed,
                    vehicle_inspection=body.vehicle_inspection, exception_reason=body.exception_reason))['report_id'])
            return {'report_ids': reports}
        output = change(c, user, sid, 'driver-close', body.model_dump(), request, perform, manager=True)
    from .reporting import generate_files
    for identifier in output['report_ids']:
        generate_files(identifier)
    return output
