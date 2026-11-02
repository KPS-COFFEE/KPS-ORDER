"""A driver joins a scoped duty after an explicitly delegated approver decides."""
from fastapi import APIRouter, Depends, Request
from pydantic import Field
from . import auth, db, operations as ops
from .manager_handover_scope import authorized, snapshot, live_principal

router = APIRouter(prefix='/api', tags=['driver-support'])


class Support(ops.OperationBody):
    driver_shift_id: str
    target_manager_shift_id: str
    reason: str = Field(min_length=1, max_length=2000)


def approver(c, user, brand, branches):
    """A freshly read explicit owner grant, never a display name or source role."""
    from . import multibrand
    principal = c.execute('SELECT * FROM users WHERE id=%s FOR SHARE', (user['id'],)).fetchone()
    if principal and multibrand.enabled():
        principal = multibrand.project(c, principal, brand)
    return bool(principal and principal['active'] and principal['brand_id'] == brand
                and principal['role'] in ('owner', 'admin', 'delivery_manager')
                and auth.has_permission(principal, 'driver_support_approve')
                and (ops.unrestricted_owner(principal) or set(branches) <= set(principal.get('branch_ids', []))))


def supported(c, driver, target_id):
    return bool(c.execute("SELECT 1 FROM driver_support_requests WHERE driver_shift_id=%s "
        "AND to_shift_id=%s AND status='accepted'", (driver['id'], target_id)).fetchone())


def validate(c, driver, source, target):
    from .central_workdays import guard_component
    for s in (driver, source, target):
        if not s:
            ops.fail(404, 'الشفت غير موجود')
        guard_component(c, s['id'])
    if (driver['kind'] != 'driver' or driver['status'] != 'open'
            or source['kind'] != 'manager' or source['status'] != 'open'
            or target['kind'] != 'manager' or target['status'] != 'open'
            or source['id'] == target['id'] or driver['manager_shift_id'] != source['id']
            or driver['brand_id'] != target['brand_id'] or source['brand_id'] != target['brand_id']):
        ops.fail(409, 'حالة الشفت لا تسمح بطلب الدعم؛ راجع مدير السائق الحالي')
    driver_user = live_principal(c, driver)
    if (not driver_user or not driver_user['active'] or driver_user['role'] != 'driver'
            or not set(driver['branch_ids']) <= set(driver_user.get('branch_ids', []))):
        ops.fail(403, 'صلاحية السائق غير سارية')
    if not authorized(c, source, driver['branch_ids']) or not authorized(c, target, driver['branch_ids']):
        ops.fail(403, 'يلزم أن يغطي المديران جميع فروع شفت السائق بصلاحيات سارية')
    if c.execute("SELECT 1 FROM manager_handovers WHERE status='pending' AND "
                 "(from_shift_id=ANY(%s) OR to_shift_id=ANY(%s))", ([source['id'], target['id']], [source['id'], target['id']])).fetchone():
        ops.fail(409, 'يوجد تسليم مناوبة معلق؛ أكمله قبل طلب دعم منفرد')
    if (target.get('delivery_window_snapshot') or {}).get('period') == 'morning':
        incompatible = c.execute("SELECT 1 FROM driver_tasks t JOIN orders o ON o.id=t.order_id "
            "WHERE t.shift_id=%s AND (o.brand_id<>%s OR NOT(o.branch_id=ANY(%s)) "
            "OR o.service<>'delivery' OR COALESCE(o.snapshot->>'delivery_period','evening')<>'morning') LIMIT 1",
            (driver['id'], target['brand_id'], target['branch_ids'])).fetchone()
        if incompatible:
            ops.fail(409, 'شفت السائق يحتوي طلبات خارج نطاق الصباح؛ يقفل شفته الحالي ثم يفتح شفت دعم جديد')


def driver_snapshot(c, source, driver):
    observed = snapshot(c, source, [driver])
    # Support transfers only this driver's orders; unassigned manager work stays.
    observed['order_ids'] = [r['order_id'] for r in c.execute(
        "SELECT order_id FROM driver_tasks WHERE shift_id=%s AND status NOT IN "
        "('delivered','cancelled','reassigned','transferred') ORDER BY order_id", (driver['id'],)).fetchall()]
    return observed


def components(c, driver, target, user, *, approval=False):
    from . import central_workdays as days
    from .central_handovers import workday, principal
    driver_day = days.association(c, driver['id'])
    target_day = days.association(c, target['id'])
    drivers = c.execute('SELECT s.* FROM central_workday_legs l JOIN shifts s ON s.id=l.shift_id WHERE l.workday_id=%s ORDER BY s.brand_id FOR UPDATE OF s', (driver_day['id'],)).fetchall() if driver_day else [driver]
    targets = workday(c, target['id'])[1] if target_day else {target['brand_id']: target}
    if driver_day or target_day or days.association(c, driver['manager_shift_id']):
        days.require_enabled(user, 'driver_support_approve' if approval else 'shifts_handover', existing_sid=driver['id'] if driver_day else target['id'])
    legs = []
    for d in drivers:
        t = targets.get(d['brand_id'])
        if not t:
            ops.fail(409, 'مناوبة المدير المستفيد لا تغطي جميع براندات شفت السائق')
        source = ops.lock_shift(c, d['manager_shift_id'])
        if source['user_id'] != ops.lock_shift(c, driver['manager_shift_id'])['user_id'] or t['user_id'] != target['user_id']:
            ops.fail(409, 'يلزم تسليم جميع أجزاء وردية السائق بين نفس المديرين')
        principal(c, user, dict(t, branch_ids=d['branch_ids']))  # Fresh membership in each driver ledger.
        with days.action():
            validate(c, d, source, t)
        legs.append((d, source, t))
    return legs


@router.post('/driver-support')
def request_support(body: Support, request: Request, user=Depends(auth.current_user)):
    ops.roles(user, 'owner', 'delivery_manager')
    from . import central_workdays as days
    with db.tx() as c:
        days.lock_lanes(c)
        target = ops.lock_shift(c, body.target_manager_shift_id)
        if not target or target['user_id'] != user['id'] or target['brand_id'] != user['brand_id']:
            ops.fail(403, 'مدير المناوبة المستفيدة فقط يطلب الدعم')
        ops.shift_access(c, user, target)
        if not authorized(c, target, target['branch_ids']):
            ops.fail(403, 'صلاحية المدير المستفيد غير سارية')
        driver = ops.lock_shift(c, body.driver_shift_id)
        if not driver or driver['brand_id'] != user['brand_id']:
            ops.fail(404, 'شفت السائق غير موجود')
        def perform():
            legs = components(c, driver, target, user)
            group_id = ops.uid()
            records = []
            for d, source, t in legs:
                if c.execute("SELECT 1 FROM driver_support_requests WHERE driver_shift_id=%s AND status='pending'", (d['id'],)).fetchone():
                    ops.fail(409, 'يوجد طلب دعم معلق لهذا السائق')
                rid = ops.uid()
                observed = driver_snapshot(c, source, d)
                c.execute('INSERT INTO driver_support_requests(id,group_id,driver_shift_id,from_shift_id,to_shift_id,brand_id,branch_ids,reason,requested_by,operational_snapshot) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (rid, group_id, d['id'], source['id'], t['id'], t['brand_id'], db.json(d['branch_ids']), body.reason, user['id'], db.json(observed)))
                db.audit(c, user['id'], 'driver.support.request', 'shift', d['id'], {'request_id': rid, 'group_id': group_id, 'from_shift_id': source['id'], 'to_shift_id': t['id'], 'reason': body.reason})
                for oid in observed['order_ids']:
                    db.event(c,oid,user['id'],'driver.support.requested',{'request_id':rid,'driver_shift_id':d['id'],'from_shift_id':source['id'],'to_shift_id':t['id'],'cash_transferred':False,'reason':body.reason})
                records.append({'request_id': rid, 'brand_id': d['brand_id'], 'driver_shift_id': d['id']})
            primary = next(r for r in records if r['driver_shift_id'] == driver['id'])
            return dict(primary, group_id=group_id, status='pending', legs=records, cash_transferred=False)
        result = ops.mutate(c, user, request, 'driver.support.request:'+body.driver_shift_id, body.model_dump(), perform)
        load_group(c, result['request_id'], user)
        return result


@router.get('/driver-support')
def list_requests(user=Depends(auth.current_user)):
    ops.roles(user, 'owner', 'delivery_manager', 'admin')
    with db.tx() as c:
        can_review = approver(c,user,user['brand_id'],[])
        rows = c.execute("SELECT r.*,s.user_id AS from_user_id,t.user_id AS to_user_id,u.display_name AS driver_name "
            "FROM driver_support_requests r JOIN shifts s ON s.id=r.from_shift_id "
            "JOIN shifts t ON t.id=r.to_shift_id JOIN shifts d ON d.id=r.driver_shift_id JOIN users u ON u.id=d.user_id "
            "WHERE r.brand_id=%s AND (s.user_id=%s OR t.user_id=%s OR %s) "
            "ORDER BY r.created_at DESC LIMIT 100", (user['brand_id'], user['id'], user['id'], can_review)).fetchall()
        rows = [r for r in rows if ops.unrestricted_owner(user) or set(r['branch_ids']) <= set(user['branch_ids'])]
        for row in rows:
            row['can_approve'] = approver(c,user,row['brand_id'],row['branch_ids'])
            row['can_withdraw'] = row['to_user_id'] == user['id']
        return ops.safe({'requests': rows})


def load_group(c, rid, user, *, approval=False):
    from .central_handovers import principal
    r = c.execute('SELECT * FROM driver_support_requests WHERE id=%s FOR UPDATE', (rid,)).fetchone()
    if not r or r['brand_id'] != user['brand_id']:
        ops.fail(404, 'طلب الدعم غير موجود')
    records = c.execute('SELECT * FROM driver_support_requests WHERE group_id=%s ORDER BY brand_id FOR UPDATE', (r['group_id'],)).fetchall() if r['group_id'] else [r]
    result = []
    for record in records:
        shifts = {sid: ops.lock_shift(c, sid) for sid in sorted([record['from_shift_id'], record['to_shift_id'], record['driver_shift_id']])}
        source, target, driver = (shifts[record[k]] for k in ('from_shift_id', 'to_shift_id', 'driver_shift_id'))
        if approval:
            if not approver(c,user,record['brand_id'],record['branch_ids']):
                ops.fail(403, 'يلزم تفويض صريح لاعتماد دعم السائق على جميع البراندات والفروع')
            result.append((record, driver, source, target))
            continue
        if user['id'] not in (source['user_id'], target['user_id']):
            ops.fail(403, 'الإجراء مخصص لمديري طلب الدعم')
        own = source if source['user_id'] == user['id'] else target
        principal(c, user, own)
        if not authorized(c, own, record['branch_ids']):
            ops.fail(403, 'صلاحية المدير لم تعد سارية')
        result.append((record, driver, source, target))
    return result


@router.post('/driver-support/{rid}/accept')
def accept_support(rid: str, request: Request, user=Depends(auth.current_user)):
    ops.roles(user, 'owner', 'delivery_manager', 'admin')
    from . import central_workdays as days
    with db.tx() as c:
        days.lock_lanes(c)
        records = load_group(c, rid, user, approval=True)
        for r, driver, source, target in records:
            if not authorized(c, target, r['branch_ids']):
                ops.fail(403, 'صلاحية المدير المستفيد لم تعد سارية')
        def perform():
            # Reconstruct live group membership to prevent partial/shared transfers.
            expected = {(d['id'], src['id'], dst['id']) for _, d, src, dst in records}
            r, driver, source, target = next(row for row in records if row[0]['id'] == rid)
            current = components(c, driver, target, user, approval=True)
            if expected != {(d['id'], src['id'], dst['id']) for d, src, dst in current}:
                ops.fail(409, 'تغيرت أجزاء وردية السائق؛ اسحب الطلب وأعد طلب الدعم')
            for r, driver, source, target in records:
                if r['status'] != 'pending':
                    ops.fail(409, 'طلب الدعم غير معلق')
                with days.action():
                    validate(c, driver, source, target)
                observed = driver_snapshot(c, source, driver)
                observed['requested_snapshot'] = r['operational_snapshot']
                c.execute('UPDATE shifts SET manager_shift_id=%s WHERE id=%s', (target['id'], driver['id']))
                c.execute("UPDATE driver_support_requests SET status='accepted',accepted_by=%s,accepted_at=now(),operational_snapshot=%s WHERE id=%s", (user['id'], db.json(observed), r['id']))
                db.audit(c, user['id'], 'driver.support.accept', 'shift', driver['id'], {'request_id': r['id'], 'from_shift_id': source['id'], 'to_shift_id': target['id'], 'cash_transferred': False})
                for oid in observed['order_ids']:
                    db.event(c, oid, user['id'], 'driver.support.accepted', {'request_id': r['id'], 'driver_shift_id': driver['id'], 'from_shift_id': source['id'], 'to_shift_id': target['id'], 'cash_transferred': False})
            return {'request_id': rid, 'status': 'accepted', 'cash_transferred': False, 'driver_shift_ids': [d['id'] for _, d, _, _ in records]}
        return ops.mutate(c, user, request, 'driver.support.accept:'+rid, {}, perform)


class Resolve(ops.OperationBody):
    action: str = Field(pattern='^(reject|withdraw)$')
    reason: str = Field(min_length=1, max_length=2000)


@router.post('/driver-support/{rid}/resolve')
def resolve_request(rid: str, body: Resolve, request: Request, user=Depends(auth.current_user)):
    ops.roles(user, 'owner', 'delivery_manager', 'admin')
    from .central_workdays import lock_lanes
    with db.tx() as c:
        lock_lanes(c)
        records = load_group(c, rid, user, approval=body.action == 'reject')
        for r, driver, source, target in records:
            if body.action == 'withdraw' and target['user_id'] != user['id']:
                ops.fail(403, 'مدير الطلب يسحب طلبه فقط؛ الرفض يحتاج مخول اعتماد الدعم')
        def perform():
            for r, driver, source, target in records:
                if r['status'] != 'pending':
                    ops.fail(409, 'طلب الدعم غير معلق')
                c.execute("UPDATE driver_support_requests SET status='rejected',resolution=%s,resolved_by=%s,resolved_at=now(),resolution_reason=%s WHERE id=%s", (body.action, user['id'], body.reason, r['id']))
                db.audit(c, user['id'], 'driver.support.'+body.action, 'shift', driver['id'], {'request_id': r['id'], 'reason': body.reason})
            return {'request_id': rid, 'status': 'rejected', 'resolution': body.action, 'cash_transferred': False}
        return ops.mutate(c, user, request, 'driver.support.resolve:'+rid, body.model_dump(), perform)
