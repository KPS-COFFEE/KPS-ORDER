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


def require_enabled(user, permission, *, existing_sid=None):
    from .central_operations import enabled
    available = enabled()
    if not available and existing_sid and multibrand.enabled():
        # A rollout rollback stops new groups but must not strand an existing
        # physical workday behind the canonical component protections.
        with db.tx() as c:
            available = association(c, existing_sid) is not None
    if not available or user['role'] == 'customer' or user.get('_morning_manager_context'):
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


def authorized(c, user, sid, *, manager=False, capability=None, lock=True):
    day = association(c, sid)
    if not day:
        raise db.error(404, 'CENTRAL_WORKDAY_UNAVAILABLE', 'الوردية غير متاحة.')
    legs = c.execute('SELECT s.* FROM central_workday_legs l JOIN shifts s ON s.id=l.shift_id '
                     'WHERE l.workday_id=%s ORDER BY s.brand_id' + (' FOR UPDATE OF s' if lock else ''), (day['id'],)).fetchall()
    if (not legs or day['anchor_shift_id'] not in {s['id'] for s in legs}
            or any(s['user_id'] != day['user_id'] or s['kind'] != day['kind'] for s in legs)):
        raise db.error(409, 'CENTRAL_WORKDAY_INTEGRITY', 'تحتاج سجلات الوردية مراجعة المالك.')
    result = []
    for shift in legs:
        principal = multibrand.project(c, user, shift['brand_id'])
        if not principal:
            raise db.error(404, 'CENTRAL_WORKDAY_UNAVAILABLE', 'الوردية غير متاحة ضمن صلاحياتك الحالية.')
        if (principal.get('home_brand_id') and principal['role']=='owner'
                and not set(shift['branch_ids']) <= set(principal.get('branch_ids', []))):
            raise db.error(404, 'CENTRAL_WORKDAY_UNAVAILABLE', 'الوردية غير متاحة ضمن صلاحياتك الحالية.')
        ops.shift_access(c, principal, shift, manager=manager, capability=capability(shift) if callable(capability) else capability)
        result.append((principal, shift))
    return day, result



def drain_metadata(c, user):
    """Expose active group identities for a safe rollout rollback, without cash."""
    if not multibrand.enabled() or user['role']=='customer' or user.get('_morning_manager_context'):
        return []
    candidates = c.execute("SELECT DISTINCT w.id,w.anchor_shift_id,w.kind FROM central_workdays w "
        "JOIN central_workday_legs l ON l.workday_id=w.id JOIN shifts s ON s.id=l.shift_id "
        "LEFT JOIN shifts parent ON parent.id=s.manager_shift_id WHERE s.status<>'closed' "
        "AND (s.user_id=%s OR parent.user_id=%s) ORDER BY w.id", (user['id'],user['id'])).fetchall()
    from fastapi import HTTPException
    output = []
    for candidate in candidates:
        try:
            day, legs = authorized(c, user, candidate['anchor_shift_id'], lock=False)
        except HTTPException:
            continue
        output.append({'id':day['id'],'anchor_shift_id':day['anchor_shift_id'],'kind':day['kind'],
            'legs':[{'brand_id':s['brand_id'],'shift_id':s['id'],'branch_ids':s['branch_ids']} for _,s in legs]})
    return output


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
        if body.kind == 'driver':
            parent_id=parent_legs.get(scope['id'])
            parent_shift=c.execute('SELECT branch_ids FROM shifts WHERE id=%s',(parent_id,)).fetchone() if parent_id else None
            bids=[bid for bid in bids if parent_shift and bid in parent_shift['branch_ids']]
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
        # Older clients send every driver opening here, including a driver
        # joining a standalone morning manager. Resolve that period from the
        # locked server record and retain the canonical single-brand contract.
        manager = ops.lock_shift(c, body.manager_shift_id) if body.kind == 'driver' and body.manager_shift_id else None
        if (manager and manager['kind'] == 'manager'
                and (manager.get('delivery_window_snapshot') or {}).get('period') == 'morning'
                and not association(c, manager['id'])):
            principal = multibrand.project(c, user, manager['brand_id'])
            if not principal:
                raise db.error(404, 'CENTRAL_WORKDAY_UNAVAILABLE', 'الوردية غير متاحة ضمن صلاحياتك الحالية.')
            ops.shift_roles(principal, 'shifts_open', 'owner', 'delivery_manager', 'driver')
            if (principal['role'] == 'driver' and body.branch_ids
                    and set(body.branch_ids) != set(principal.get('branch_ids', []))):
                ops.fail(403, 'فروع السائق يحددها مدير التوصيل؛ لا يمكن تغيير نطاق الشفت')
            token = multibrand.operation_brand.set(manager['brand_id'])
            try:
                # Do not enter action(): morning must not bypass grouped-shift
                # protections. mutate also rechecks current scope on replay.
                return ops.safe(ops.mutate(c, principal, request, 'shift.open', body.model_dump(),
                                          lambda: ops.open_shift_in_tx(c, principal, body)))
            finally:
                multibrand.operation_brand.reset(token)
        def perform():
            with action():
                return ops.safe(_open(c, user, body))
        # Replays must retain current access to every constituent branch.
        result = db.idempotent(c, user['id'], request.headers.get('Idempotency-Key',''),
                              'central.workday.open', body.model_dump(), perform)
        authorized(c, user, result['shift']['id'])
        return ops.safe(ops.blind_closure.driver_view(user, result, shift_money=True))


def physical_shift(c, shift):
    anchor = (shift.get('vehicle_log') or {}).get('central_vehicle_shift_id')
    if not anchor:
        return shift
    day = association(c, shift['id'])
    if not day or day['anchor_shift_id'] != anchor:
        raise db.error(409, 'CENTRAL_VEHICLE_SCOPE', 'مرجع السيارة غير مطابق للوردية.')
    physical = c.execute('SELECT * FROM shifts WHERE id=%s', (anchor,)).fetchone()
    if (not physical or physical['user_id'] != shift['user_id'] or physical['kind'] != 'driver'
            or (physical.get('vehicle_log') or {}).get('central_vehicle_shift_id')):
        raise db.error(409, 'CENTRAL_VEHICLE_SCOPE', 'مرجع السيارة غير مطابق للوردية.')
    if physical['status'] != 'open':
        raise db.error(409, 'CENTRAL_WORKDAY_CLOSED', 'الوردية لا تستقبل مهام جديدة.')
    return physical


def ordered(legs, anchor):
    return sorted(legs, key=lambda item: (item[1]['id'] != anchor, item[1]['brand_id']))


def result(c, day, legs):
    """One display row; the brand legs remain immutable canonical identities."""
    rows = [c.execute('SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id '
                     'WHERE s.id=%s', (s['id'],)).fetchone() for _, s in legs]
    annotate(c, rows)
    anchor = next(s for s in rows if s['id'] == day['anchor_shift_id'])
    display = dict(anchor, branch_ids=list(dict.fromkeys(b for r in rows for b in r['branch_ids'])),
                   central_legs=[{'id':r['id'],'brand_id':r['brand_id'],'branch_ids':r['branch_ids']} for r in rows])
    display['opening_halalas'] = sum(r['opening_halalas'] for r in rows)
    display['permanent_float'] = {'amount':sum((r.get('permanent_float') or {}).get('amount',0) for r in rows)}
    if all(r['submission'] is not None for r in rows):
        display['submission'] = {key:sum(r['submission'].get(key,0) or 0 for r in rows)
                                 for key in rows[0]['submission'] if key.endswith('_halalas')}
    return {'shift': display, 'shifts': rows, 'central_workday_id': day['id']}


@router.get('/{sid}')
def detail(sid: str, user=Depends(auth.current_user)):
    require_enabled(user, 'shifts_view', existing_sid=sid)
    with db.tx() as c:
        day, legs = authorized(c, user, sid, capability='shifts_view')
        output = result(c, day, legs)
        output['manager_user_id'] = None
        if day['kind']=='driver':
            managers = {c.execute('SELECT user_id FROM shifts WHERE id=%s',(shift['manager_shift_id'],)).fetchone()['user_id'] for _,shift in legs}
            if len(managers)!=1:
                raise db.error(409,'CENTRAL_MANAGER_SCOPE','وردية السائق لا تتبع مديرًا واحدًا.')
            output['manager_user_id'] = next(iter(managers))
        output['ledgers'] = []
        for name in ['tasks','collections','refunds','custody_events','transfers','driver_shifts']:
            output[name] = []
        for principal, shift in legs:
            item = {'shift_id': shift['id'], 'brand_id': shift['brand_id'],
                    'expected_halalas': ops.balance(c, shift), 'submission': shift['submission'],
                    'permanent_float': shift.get('permanent_float'), 'status':shift['status']}
            if user['role'] != 'driver':
                from .shift_reconciliation import closed_shift
                item['reconciliation'] = closed_shift(c,shift)
            output['ledgers'].append(item)
            sid = shift['id']
            queries = {
                'tasks': ('SELECT t.*,o.order_no,o.snapshot,o.test_void,o.total_halalas,o.preparation_status,o.payment_status,o.brand_id FROM effective_driver_tasks t JOIN orders o ON o.id=t.order_id WHERE shift_id=%s ORDER BY t.created_at', (sid,)),
                'collections': ('SELECT * FROM effective_collections WHERE shift_id=%s', (sid,)),
                'refunds': ('SELECT * FROM refunds WHERE shift_id=%s', (sid,)),
                'custody_events': ('SELECT * FROM custody_events WHERE shift_id=%s ORDER BY created_at', (sid,)),
                'transfers': ('SELECT * FROM custody_transfers WHERE from_shift_id=%s OR to_shift_id=%s', (sid,sid)),
                'driver_shifts': ('SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.manager_shift_id=%s ORDER BY s.opened_at', (sid,)),
            }
            for name, (query, params) in queries.items():
                values = c.execute(query, params).fetchall()
                if name == 'tasks' and user['role']=='driver':
                    values = [r for r in values if r['status'] not in ops.TERMINAL]
                output[name].extend(dict(r,brand_id=shift['brand_id']) for r in values)
        annotate(c, output['driver_shifts'])
        output['expected_halalas'] = sum(item['expected_halalas'] for item in output['ledgers'])
        output['vehicle_summary'] = ops.shift_vehicle.summary(output['shift'].get('vehicle_log'))
        output['external_enbishan_eligible'] = False
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
    require_enabled(user, 'shifts_delegate_close' if user['role']=='admin' and auth.has_permission(user, 'shifts_delegate_close') else 'shifts_close', existing_sid=sid)
    with db.tx() as c:
        def perform(day, legs):
            for principal, shift in legs:
                ops.request_close_in_tx(c, principal, shift['id'])
        return change(c, user, sid, 'closing', {}, request, perform, capability=ops.close_capability)


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
    require_enabled(user, 'shifts_delegate_close' if user['role']=='admin' and auth.has_permission(user, 'shifts_delegate_close') else 'shifts_close', existing_sid=sid)
    with db.tx() as c:
        def perform(day, legs):
            supplied = exact_legs(legs, body.legs)
            for principal, shift in legs:
                value = supplied[shift['id']]
                ops.submit_close_in_tx(c, principal, shift['id'], ops.ClosureSubmit(
                    **value.model_dump(exclude={'shift_id'}), vehicle_inspection=body.vehicle_inspection,
                    exception_reason=body.exception_reason))
        return change(c, user, sid, 'submit', body.model_dump(), request, perform, capability=ops.close_capability)


@router.post('/{sid}/approve')
def approve(sid: str, body: ops.Review, request: Request, user=Depends(auth.current_user)):
    require_enabled(user, 'shifts_approve', existing_sid=sid)
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
    require_enabled(user, 'shifts_delegate_close', existing_sid=sid)
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


from . import driver_closure as closure


def closure_change(c, user, sid, body, request, operation, helper, *, manager=False):
    def perform(day, legs):
        if day['kind'] != 'driver':
            raise db.error(409, 'CENTRAL_DRIVER_WORKDAY', 'اختر وردية سائق.')
        anchor = legs[0][1]
        if hasattr(body, 'request_id') and body.request_id != (anchor.get('closure_authorization') or {}).get('id'):
            raise db.error(409, 'CLOSURE_REQUEST_STALE', 'تغير طلب إنهاء الوردية؛ حدّث الشاشة.')
        for principal, shift in legs:
            current = body
            if hasattr(body, 'request_id'):
                current = body.model_copy(update={'request_id':(shift.get('closure_authorization') or {}).get('id','')})
            helper(c, principal, shift['id'], current)
    return change(c, user, sid, 'authorization.'+operation, body.model_dump(mode='json'), request,
                  perform, manager=manager)


@router.post('/{sid}/request')
def closure_request(sid:str, body:closure.Reason, request:Request, user=Depends(auth.current_user)):
    require_enabled(user, 'shifts_close', existing_sid=sid)
    with db.tx() as c:
        return closure_change(c,user,sid,body,request,'request',closure.request_authorization_in_tx)


@router.post('/{sid}/decision')
def closure_decision(sid:str, body:closure.Decision, request:Request, user=Depends(auth.current_user)):
    require_enabled(user, 'shifts_approve', existing_sid=sid)
    with db.tx() as c:
        return closure_change(c,user,sid,body,request,'decision',closure.decide_in_tx,manager=True)


@router.put('/{sid}/schedule')
def closure_schedule(sid:str, body:closure.Schedule, request:Request, user=Depends(auth.current_user)):
    require_enabled(user, 'shifts_approve', existing_sid=sid)
    with db.tx() as c:
        return closure_change(c,user,sid,body,request,'schedule',closure.schedule_in_tx,manager=True)


@router.post('/{sid}/withdraw')
def closure_withdraw(sid:str, body:closure.Withdrawal, request:Request, user=Depends(auth.current_user)):
    require_enabled(user, 'shifts_close', existing_sid=sid)
    with db.tx() as c:
        return closure_change(c,user,sid,body,request,'withdraw',closure.withdraw_in_tx)


@router.post('/{sid}/return')
def return_for_correction(sid:str, body:ops.Review, request:Request, user=Depends(auth.current_user)):
    require_enabled(user, 'shifts_approve', existing_sid=sid)
    ops.shift_roles(user, 'shifts_approve', 'owner', 'delivery_manager')
    with db.tx() as c:
        def perform(day, legs):
            for principal, shift in legs:
                ops.return_close_in_tx(c, principal, shift['id'], body)
        return change(c,user,sid,'return',body.model_dump(),request,perform,manager=True,capability='shifts_approve')


class CustodyLeg(ops.OperationBody):
    shift_id: str
    amount_halalas: ops.StrictInt = ops.Field(ge=0,le=ops.MAX_HALALAS)


class WorkdayCustody(ops.OperationBody):
    legs: list[CustodyLeg] = ops.Field(min_length=1,max_length=2)
    recipient_user_id: str
    to_shift_id: str | None = None
    reference: str = ops.Field(min_length=1,max_length=200)


@router.post('/{sid}/custody')
def custody(sid:str,body:WorkdayCustody,request:Request,user=Depends(auth.current_user)):
    require_enabled(user,'custody_transfer', existing_sid=sid)
    ops.roles(user,'owner','delivery_manager','driver')
    with db.tx() as c:
        def perform(day,legs):
            supplied=exact_legs(legs,body.legs)
            target={}
            if body.to_shift_id:
                receiving=association(c,body.to_shift_id)
                if not receiving:
                    raise db.error(409,'CENTRAL_CUSTODY_SCOPE','اختر وردية المستلم المشتركة.')
                target={r['brand_id']:r['shift_id'] for r in c.execute('SELECT brand_id,shift_id FROM central_workday_legs WHERE workday_id=%s',(receiving['id'],)).fetchall()}
            output=[]
            for principal,shift in legs:
                value=supplied[shift['id']]
                if not value.amount_halalas:continue
                if body.to_shift_id and shift['brand_id'] not in target:
                    raise db.error(409,'CENTRAL_CUSTODY_SCOPE','وردية المستلم لا تغطي سجل المبلغ.')
                output.append(ops.create_custody(c,principal,ops.Custody(from_shift_id=shift['id'],
                    to_shift_id=target.get(shift['brand_id']),recipient_user_id=body.recipient_user_id,
                    amount_halalas=value.amount_halalas,reference=body.reference))['transfer'])
            if not output:raise db.error(422,'CENTRAL_CUSTODY_AMOUNT','أدخل مبلغ التسليم الفعلي.')
            return {'transfers':output}
        return change(c,user,sid,'custody',body.model_dump(),request,perform)


@router.post('/{sid}/adopt')
def adopt(sid:str,request:Request,user=Depends(auth.current_user)):
    """Preserve an existing open shift/cash/vehicle; add only missing ledgers.

    Two pre-existing physical shifts are never merged. They must be settled by
    their existing canonical paths before a single shared workday can begin.
    """
    require_enabled(user,'shifts_open')
    ops.roles(user,'owner','delivery_manager','driver')
    with db.tx() as c:
        lock_lanes(c)
        existing=association(c,sid)
        if existing:
            day,legs=authorized(c,user,sid)
            return ops.safe(ops.blind_closure.driver_view(user,result(c,day,legs),shift_money=True))
        root=ops.lock_shift(c,sid)
        principal=multibrand.project(c,user,root['brand_id']) if root else None
        if not principal:raise db.error(404,'CENTRAL_WORKDAY_UNAVAILABLE','الوردية غير متاحة.')
        ops.shift_access(c,principal,root)
        if root['user_id']!=user['id']:
            raise db.error(403,'CENTRAL_ADOPT_SELF_ONLY','ضم الفروع إلى ورديتك يتم من حسابك.')
        def perform():
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('shift:'+user['id'],))
            rows=c.execute("SELECT * FROM shifts WHERE user_id=%s AND status<>'closed' FOR UPDATE",(user['id'],)).fetchall()
            if len(rows)!=1 or rows[0]['id']!=sid:
                raise db.error(409,'CENTRAL_LEGACY_SHIFTS','لديك شفتات سابقة متعددة؛ أكمل تقفيلها قبل فتح الوردية المشتركة.')
            if (root['status']!='open' or (root.get('delivery_window_snapshot') or {}).get('period')=='morning'
                    or (root.get('vehicle_log') or {}).get('receipt_trial')
                    or root['kind']=='driver' and not (root.get('vehicle_log') or {}).get('start')):
                raise db.error(409,'CENTRAL_LEGACY_SHIFT_END','أكمل الوردية السابقة ثم افتح وردية مشتركة جديدة.')
            target=c.execute('SELECT * FROM users WHERE id=%s AND active',(root['user_id'],)).fetchone()
            from .central_operations import context
            scopes=context(c,target)['brands']
            parents={}
            if root['kind']=='driver':
                parent=association(c,root['manager_shift_id'])
                if not parent:
                    raise db.error(409,'CENTRAL_MANAGER_ADOPTION_REQUIRED','يحتاج مديرك فتح شاشة التشغيل لتجهيز ورديته المشتركة أولًا.')
                parents={r['brand_id']:r['shift_id'] for r in c.execute('SELECT brand_id,shift_id FROM central_workday_legs WHERE workday_id=%s',(parent['id'],)).fetchall()}
            opened=[root]
            with action():
                for scope in scopes:
                    if scope['id']==root['brand_id'] or not scope['branch_ids']:continue
                    actor=multibrand.project(c,user,scope['id'])
                    if not actor:raise db.error(403,'CENTRAL_BRANCH_SCOPE','صلاحية الفرع لم تعد متاحة.')
                    if root['kind']=='driver' and scope['id'] not in parents:
                        raise db.error(409,'MANAGER_SHIFT_SCOPE','وردية المدير لا تغطي جميع الفروع المعتمدة.')
                    body=ops.ShiftOpen(kind=root['kind'],branch_ids=scope['branch_ids'],
                        manager_shift_id=parents.get(scope['id']) if root['kind']=='driver' else None,
                        delivery_period='evening')
                    opened.append(ops.open_shift_in_tx(c,actor,body,
                        vehicle_anchor=sid if root['kind']=='driver' else None, adoption_anchor=sid)['shift'])
            # A prior one-brand permission to end does not approve an expanded
            # workday. Preserve its audited history and request a fresh decision.
            for shift in opened:
                c.execute('UPDATE shifts SET scheduled_end_at=%s,closure_authorization=NULL WHERE id=%s',
                          (root.get('scheduled_end_at'),shift['id']))
            identifier=ops.uid()
            c.execute('INSERT INTO central_workdays(id,user_id,kind,anchor_shift_id,created_by) VALUES(%s,%s,%s,%s,%s)',
                      (identifier,user['id'],root['kind'],sid,user['id']))
            for shift in opened:
                c.execute('INSERT INTO central_workday_legs(workday_id,brand_id,shift_id) VALUES(%s,%s,%s)',
                          (identifier,shift['brand_id'],shift['id']))
            db.audit(c,user['id'],'central.workday.adopt','central_workday',identifier,
                     {'preserved_shift_id':sid,'new_legs':[s['id'] for s in opened[1:]]})
            day,legs=authorized(c,user,sid)
            return ops.safe(result(c,day,legs))
        output=db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),'central.workday.adopt:'+sid,{},perform)
        authorized(c,user,sid)
        return ops.safe(ops.blind_closure.driver_view(user,output,shift_money=True))
