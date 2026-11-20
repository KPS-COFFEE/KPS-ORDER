"""Owner interventions remain narrow, audited and subject to custody admission."""
from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict, Field, StrictBool
from . import auth, catalog, db, multibrand, operations as ops
from . import central_workdays as days

router = APIRouter(prefix='/api/owner-driver-interventions', tags=['owner-driver-interventions'])


class ExceptionalAssignment(ops.Assignment):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reason: str = Field(min_length=3, max_length=2000)
    resume_for_task: StrictBool = False


class Move(ops.OperationBody):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    branch_ids: list[str] = Field(min_length=1, max_length=500)
    target_manager_shift_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=2000)


def owner(user):
    ops.roles(user, 'owner')


@router.get('/orders/{oid}/candidates')
def candidates(oid: str, user=Depends(auth.current_user)):
    owner(user)
    with db.tx() as c:
        order = c.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
        ops.order_access(c, user, order)
        rows = c.execute("SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id "
                         "WHERE s.brand_id=%s AND s.kind='driver' AND s.status='open' AND u.active ORDER BY u.display_name,s.id",
                         (order['brand_id'],)).fetchall()
        allowed = []
        for row in rows:
            if not ops.unrestricted_owner(user) and not set(row['branch_ids']) <= set(user.get('branch_ids', [])):
                continue
            # These are candidates, never an authorization or eligibility promise.
            allowed.append({k: row[k] for k in ('id','user_id','display_name','brand_id','branch_ids','available','status','kind')})
        return {'shifts': allowed, 'eligibility_checked_on_commit': True}


@router.post('/orders/{oid}/assign')
def assign(oid: str, body: ExceptionalAssignment, request: Request, user=Depends(auth.current_user)):
    owner(user)
    with db.tx() as c:
        days.lock_lanes(c)
        order = c.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
        ops.order_access(c, user, order)
        target = c.execute('SELECT * FROM shifts WHERE id=%s', (body.driver_shift_id,)).fetchone()
        ops.shift_access(c, user, target)
        def perform():
            # Order first, followed by the canonical physical-shift locking order.
            c.execute('SELECT id FROM orders WHERE id=%s FOR UPDATE', (oid,))
            from . import dispatch_impact
            dispatch_impact.lock_physical_shifts(c, [body.driver_shift_id])
            s = ops.lock_shift(c, body.driver_shift_id)
            ops.shift_access(c, user, s)
            physical = days.physical_shift(c, s)
            ids = sorted({s['id'], physical['id']})
            rows = c.execute('SELECT id,available FROM shifts WHERE id=ANY(%s) ORDER BY id FOR UPDATE', (ids,)).fetchall()
            before = {r['id']: r['available'] for r in rows}
            if body.resume_for_task:
                c.execute('UPDATE shifts SET available=true WHERE id=ANY(%s)', (ids,))
            normal = ops.Assignment(**body.model_dump(exclude={'reason','resume_for_task'}))
            result = ops.assign_in_transaction(c, user, oid, normal)
            # Restore pause in the SAME transaction; no other task inherits this exception.
            for identifier, available in before.items():
                c.execute('UPDATE shifts SET available=%s WHERE id=%s', (available, identifier))
            details = {'reason': body.reason, 'resume_for_task': body.resume_for_task,
                       'task_id': result['task']['id'], 'driver_shift_id': s['id'],
                       'before_available': before, 'after_available': before, 'financial_changes': False}
            db.audit(c, user['id'], 'owner.driver.assignment_exception', 'order', oid, details)
            db.event(c, oid, user['id'], 'owner.driver.assignment_exception', details)
            return dict(result, exception_applies_to_task_only=True)
        return ops.mutate(c, user, request, 'order.owner_assignment:'+oid, body.model_dump(), perform)


def move_in_tx(c, user, sid, body):
    initial = ops.lock_shift(c, sid)
    ops.shift_access(c, user, initial)
    day = days.association(c, sid)
    if day:
        day, legs = days.authorized(c, user, sid)
        if sid != day['anchor_shift_id']:
            raise db.error(409, 'CENTRAL_VEHICLE_ANCHOR_REQUIRED', 'انقل وردية السائق من شفت السيارة الأساسي.')
    else:
        legs = [(user, initial)]
    identifiers = [s['id'] for _,s in legs]
    if any(s['kind'] != 'driver' or s['status'] != 'open' for _,s in legs):
        ops.fail(409, 'النقل يخص شفت سائق مفتوح فقط.')
    if c.execute('SELECT 1 FROM driver_tasks WHERE shift_id=ANY(%s) LIMIT 1', (identifiers,)).fetchone():
        ops.fail(409, 'للشفت سجل مهام؛ استخدم تسليم المهام ثم إنهاء الدوام وفتح النطاق الجديد.')
    if c.execute("SELECT 1 FROM custody_transfers WHERE status='pending' AND (from_shift_id=ANY(%s) OR to_shift_id=ANY(%s)) LIMIT 1", (identifiers,identifiers)).fetchone():
        ops.fail(409, 'أكد تسليم العهدة المعلق قبل نقل نطاق الشفت.')
    # Manager/branch keys are financial report attribution, even on an empty
    # task ledger. Funded ledgers must keep those keys and use actual handover.
    if (any(s.get('opening_halalas', 0) for _,s in legs)
            or any(c.execute('SELECT 1 FROM '+table+' WHERE shift_id=ANY(%s) LIMIT 1', (identifiers,)).fetchone()
                   for table in ('custody_events','collections','refunds'))
            or c.execute('SELECT 1 FROM custody_transfers WHERE from_shift_id=ANY(%s) OR to_shift_id=ANY(%s) LIMIT 1',
                         (identifiers,identifiers)).fetchone()):
        ops.fail(409, 'للشفت سجل عهدة مالي؛ سلّم العهدة وأنهِ الدوام ثم افتح النطاق الجديد دون انتظار جرد المحاسبة.')
    target = ops.lock_shift(c, body.target_manager_shift_id)
    ops.shift_access(c, user, target)
    parent = days.association(c, target['id'])
    targets = {target['brand_id']: target}
    if parent:
        _, parent_legs = days.authorized(c, user, target['id'])
        targets = {s['brand_id']: s for _,s in parent_legs}
    requested = set(body.branch_ids)
    branches = c.execute('SELECT * FROM branches WHERE id=ANY(%s) ORDER BY id', (sorted(requested),)).fetchall()
    if len(branches) != len(requested):
        ops.fail(403, 'أحد الفروع غير مخول.')
    cities = {catalog.normalize_city(b.get('city') or '') for b in branches}
    if len(cities) != 1 or not next(iter(cities)):
        ops.fail(409, 'حدد فروع مدينة واحدة للنقل.')
    chosen = next(iter(cities))
    existing_brands = {s['brand_id'] for _,s in legs}
    if any(b['brand_id'] not in existing_brands for b in branches):
        ops.fail(409, 'النقل لا ينشئ دفتر براند جديد؛ افتح وردية جديدة بالنطاق المعتمد.')
    planned = []
    manager_users = set()
    for principal,s in legs:
        manager = targets.get(s['brand_id'])
        if not manager or manager['kind'] != 'manager' or manager['status'] != 'open':
            ops.fail(409, 'يلزم شفت مدير مفتوح لكل دفتر براند في الوردية.')
        ops.shift_access(c, principal, manager)
        if ((s.get('delivery_window_snapshot') or {}).get('period', 'evening') !=
                (manager.get('delivery_window_snapshot') or {}).get('period', 'evening')):
            ops.fail(409, 'أنهِ فترة الدوام الحالية قبل النقل بين الصباحي والمسائي.')
        if c.execute("SELECT 1 FROM manager_handovers WHERE status='pending' AND (from_shift_id=ANY(%s) OR to_shift_id=ANY(%s)) LIMIT 1",
                     ([s['manager_shift_id'], manager['id']], [s['manager_shift_id'], manager['id']])).fetchone():
            ops.fail(409, 'أكمل تسليم مناوبة المدير المعلق قبل النقل.')
        driver = c.execute('SELECT * FROM users WHERE id=%s', (s['user_id'],)).fetchone()
        manager_user = c.execute('SELECT * FROM users WHERE id=%s', (manager['user_id'],)).fetchone()
        if multibrand.enabled():
            driver = multibrand.project(c, driver, s['brand_id'])
            manager_user = multibrand.project(c, manager_user, s['brand_id'])
        if not driver or not driver['active'] or driver['role'] != 'driver' or not manager_user or not manager_user['active'] or manager_user['role'] not in ('owner','delivery_manager'):
            ops.fail(403, 'صلاحية السائق أو مدير الاستلام غير سارية.')
        manager_users.add(manager['user_id'])
        selected = [b for b in branches if b['brand_id'] == s['brand_id']]
        bids = [b['id'] for b in selected]
        retained = bids or s['branch_ids']
        if not set(retained) <= set(driver['branch_ids']) or not set(retained) <= set(manager['branch_ids']) or (not ops.unrestricted_owner(manager_user) and not set(retained) <= set(manager_user['branch_ids'])):
            ops.fail(403, 'اعتمد فروع السائق والمدير أولًا؛ النقل لا يمنح صلاحيات فروع ضمنيًا.')
        if not ops.unrestricted_owner(principal) and not set(retained) <= set(principal.get('branch_ids', [])):
            ops.fail(403, 'فروع النقل خارج نطاق المالك الحالي.')
        window = s.get('delivery_window_snapshot')
        if bids:
            _, window = ops.driver_shift_window(c, s['brand_id'], selected, manager, manager_user)
        planned.append((s, retained, manager, window, bool(bids)))
    if len(manager_users) != 1 or not next(p[4] for p in planned if p[0]['id'] == sid):
        ops.fail(409, 'شفت السيارة الأساسي يجب أن يخدم المدينة المختارة تحت مدير واحد.')
    for s, retained, manager, window, active in planned:
        c.execute('UPDATE shifts SET branch_ids=%s,manager_shift_id=%s,delivery_window_snapshot=%s,available=%s WHERE id=%s',
                  (db.json(retained),manager['id'],db.json(window),s['available'] if active else False,s['id']))
        db.audit(c,user['id'],'owner.driver.shift_moved','shift',s['id'],
                 {'reason':body.reason,'before_branch_ids':s['branch_ids'],'branch_ids':retained,
                  'before_manager_shift_id':s['manager_shift_id'],'manager_shift_id':manager['id'],
                  'before_available':s['available'],'available':s['available'] if active else False,
                  'city':chosen,'financial_changes':False})
    from .driver_shift_city import remember
    remember(c,user,initial['user_id'],chosen,body.reason)
    return {'shift_id':sid,'city':chosen,'branch_ids':body.branch_ids,'financial_changes':False}


@router.post('/shifts/{sid}/move')
def move(sid: str, body: Move, request: Request, user=Depends(auth.current_user)):
    owner(user)
    with db.tx() as c:
        days.lock_lanes(c)
        initial = c.execute('SELECT * FROM shifts WHERE id=%s', (sid,)).fetchone()
        ops.shift_access(c,user,initial)
        return ops.mutate(c,user,request,'shift.owner_move:'+sid,body.model_dump(),
                          lambda: move_in_tx(c,user,sid,body))
