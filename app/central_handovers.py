"""Atomic manager succession for every ledger of a shared workday.

Canonical handovers remain the per-brand operational record. The group only
binds their request and acceptance; it never moves cash between ledgers.
"""
from fastapi import APIRouter, Depends, Request

from . import auth, db, multibrand, central_workdays as days
from . import operations as ops

router = APIRouter(tags=['central-handovers'])


def unavailable():
    raise db.error(404, 'CENTRAL_HANDOVER_UNAVAILABLE', 'تسليم المناوبة غير متاح.')


def integrity():
    raise db.error(409, 'CENTRAL_HANDOVER_INTEGRITY', 'تغير نطاق المناوبة؛ راجع تسليم جميع ورديات السائقين.')


def workday(c, sid):
    day = days.association(c, sid)
    if not day or day['kind'] != 'manager':
        unavailable()
    shifts = c.execute('SELECT s.* FROM central_workday_legs l JOIN shifts s ON s.id=l.shift_id '
                       'WHERE l.workday_id=%s ORDER BY s.brand_id FOR UPDATE OF s', (day['id'],)).fetchall()
    if not shifts or any(s['user_id'] != day['user_id'] or s['kind'] != 'manager' for s in shifts):
        integrity()
    return day, {s['brand_id']: s for s in shifts}


def principal(c, user, shift):
    projected = multibrand.project(c, user, shift['brand_id'])
    if not projected or ((projected['role'] != 'owner' or projected.get('home_brand_id'))
                         and not set(shift['branch_ids']) <= set(projected['branch_ids'])):
        unavailable()
    return projected


def children(c, sources, expected=None):
    """A shared driver must leave all source parents together, never one leg."""
    rows = c.execute("SELECT * FROM shifts WHERE manager_shift_id=ANY(%s) AND status<>'closed' "
                     'ORDER BY id FOR UPDATE', ([s['id'] for s in sources.values()],)).fetchall()
    ids = {s['id'] for s in rows}
    if expected is not None:
        active_expected = {r['id'] for r in c.execute(
            "SELECT id FROM shifts WHERE id=ANY(%s) AND status<>'closed'", (expected,)).fetchall()}
        if ids != active_expected:
            integrity()
    for child in rows:
        group = days.association(c, child['id'])
        if not group:
            continue
        legs = c.execute('SELECT s.* FROM central_workday_legs l JOIN shifts s ON s.id=l.shift_id '
                         'WHERE l.workday_id=%s ORDER BY s.brand_id FOR UPDATE OF s', (group['id'],)).fetchall()
        if any(s['status'] == 'closed' or s['id'] not in ids or s['brand_id'] not in sources
               or s['manager_shift_id'] != sources[s['brand_id']]['id'] for s in legs):
            integrity()
    return rows


def load(c, hid, user):
    group = c.execute('SELECT * FROM central_manager_handovers WHERE id=%s FOR UPDATE', (hid,)).fetchone()
    if not group:
        unavailable()
    source = c.execute('SELECT anchor_shift_id FROM central_workdays WHERE id=%s', (group['from_workday_id'],)).fetchone()
    target = c.execute('SELECT anchor_shift_id FROM central_workdays WHERE id=%s', (group['to_workday_id'],)).fetchone()
    if not source or not target:
        integrity()
    source_day, sources = workday(c, source['anchor_shift_id'])
    target_day, targets = workday(c, target['anchor_shift_id'])
    records = c.execute('SELECT h.*,l.brand_id FROM central_manager_handover_legs l '
                        'JOIN manager_handovers h ON h.id=l.handover_id WHERE l.handover_group_id=%s '
                        'ORDER BY l.brand_id FOR UPDATE OF h', (hid,)).fetchall()
    if len(records) != len(sources) or {h['brand_id'] for h in records} != set(sources):
        integrity()
    for h in records:
        if h['brand_id'] not in targets or h['from_shift_id'] != sources[h['brand_id']]['id'] or h['to_shift_id'] != targets[h['brand_id']]['id']:
            integrity()
        p = principal(c, user, sources[h['brand_id']])
        if user['id'] not in {source_day['user_id'], target_day['user_id']} and p['role'] != 'owner' and not ops.delegated_shift(p, 'shifts_view'):
            unavailable()
    if len({h['status'] for h in records}) != 1:
        integrity()
    response = dict(group, handover_id=group['id'], from_shift_id=source_day['anchor_shift_id'],
                    to_shift_id=target_day['anchor_shift_id'], status=records[0]['status'],
                    reason=records[0]['reason'], requested_by=records[0]['requested_by'],
                    from_name=c.execute('SELECT display_name FROM users WHERE id=%s', (source_day['user_id'],)).fetchone()['display_name'],
                    to_name=c.execute('SELECT display_name FROM users WHERE id=%s', (target_day['user_id'],)).fetchone()['display_name'],
                    driver_shift_ids=sorted({sid for h in records for sid in h['driver_shift_ids']}),
                    legs=records, cash_transferred=False, cash_handover_requires_separate_confirmation=True)
    return response, source_day, sources, target_day, targets


@router.post('/workdays/{sid}/handover-manager')
def request_handover(sid: str, body: ops.ManagerHandover, request: Request, user=Depends(auth.current_user)):
    days.require_enabled(user, 'shifts_handover', existing_sid=sid)
    ops.shift_roles(user, 'shifts_handover', 'owner', 'delivery_manager')
    with db.tx() as c:
        days.lock_lanes(c)
        source_day, sources = workday(c, sid)
        target_day, targets = workday(c, body.target_manager_shift_id)
        if source_day['id'] == target_day['id'] or source_day['user_id'] == target_day['user_id'] or not set(sources) <= set(targets):
            integrity()
        for brand, shift in sources.items():
            ops.shift_access(c, principal(c, user, shift), shift, capability='shifts_handover')
        def perform():
            children(c, sources)
            hid = ops.uid()
            c.execute('INSERT INTO central_manager_handovers(id,from_workday_id,to_workday_id,created_by) VALUES(%s,%s,%s,%s)',
                      (hid, source_day['id'], target_day['id'], user['id']))
            with days.action():
                for brand, shift in sources.items():
                    result = ops.manager_handover_in_tx(c, principal(c, user, shift), shift['id'],
                        body.model_copy(update={'target_manager_shift_id': targets[brand]['id']}))
                    c.execute('INSERT INTO central_manager_handover_legs(handover_group_id,brand_id,handover_id) VALUES(%s,%s,%s)',
                              (hid, brand, result['handover_id']))
            db.audit(c, user['id'], 'central.manager.handover.request', 'central_manager_handover', hid,
                     {'from_workday_id':source_day['id'], 'to_workday_id':target_day['id']})
            return ops.safe(load(c, hid, user)[0])
        result = db.idempotent(c, user['id'], request.headers.get('Idempotency-Key',''),
                              'central.manager.handover:'+source_day['id'], body.model_dump(), perform)
        load(c, result['handover_id'], user)  # Grants are mandatory even for replay.
        return result


@router.get('/manager-handovers')
def list_handovers(user=Depends(auth.current_user)):
    if not multibrand.enabled():
        unavailable()
    with db.tx() as c:
        existing = c.execute('SELECT anchor_shift_id FROM central_workdays WHERE user_id=%s OR %s LIMIT 1',
                             (user['id'],user['role']=='owner' or ops.delegated_shift(user,'shifts_view'))).fetchone()
        days.require_enabled(user, 'shifts_view', existing_sid=existing['anchor_shift_id'] if existing else None)
        ops.shift_roles(user, 'shifts_view', 'owner', 'delivery_manager')
        days.lock_lanes(c)
        groups = c.execute('SELECT h.id FROM central_manager_handovers h '
                           'JOIN central_workdays s ON s.id=h.from_workday_id '
                           'JOIN central_workdays t ON t.id=h.to_workday_id '
                           'WHERE s.user_id=%s OR t.user_id=%s OR %s ORDER BY h.created_at DESC LIMIT 100',
                           (user['id'],user['id'],user['role']=='owner' or ops.delegated_shift(user,'shifts_view'))).fetchall()
        from fastapi import HTTPException
        visible=[]
        for group in groups:
            try:
                visible.append(load(c, group['id'], user)[0])
            except HTTPException as exc:
                if exc.status_code not in (403,404):
                    raise
        return ops.safe({'handovers':visible})


@router.post('/manager-handovers/{hid}/accept')
def accept_handover(hid: str, request: Request, user=Depends(auth.current_user)):
    if not multibrand.enabled():
        unavailable()
    with db.tx() as c:
        existing = c.execute('SELECT w.anchor_shift_id FROM central_manager_handovers h '
                             'JOIN central_workdays w ON w.id=h.from_workday_id WHERE h.id=%s', (hid,)).fetchone()
        days.require_enabled(user, 'shifts_handover', existing_sid=existing['anchor_shift_id'] if existing else None)
        ops.roles(user, 'owner', 'delivery_manager')
        days.lock_lanes(c)
        group, _, sources, target_day, targets = load(c, hid, user)
        if target_day['user_id'] != user['id']:
            raise db.error(403, 'CENTRAL_HANDOVER_RECIPIENT', 'المستلم الحقيقي فقط يقبل المناوبة.')
        for shift in targets.values():
            ops.shift_access(c, principal(c, user, shift), shift)
        def perform():
            if group['status'] != 'pending':
                unavailable()
            children(c, sources, group['driver_shift_ids'])
            # Fresh receiving scope must still cover each original brand leg.
            for h in group['legs']:
                target = targets[h['brand_id']]
                if not set(h['branch_ids']) <= set(target['branch_ids']):
                    integrity()
            with days.action():
                for h in group['legs']:
                    ops.accept_manager_handover_in_tx(c, principal(c, user, targets[h['brand_id']]), h['id'])
            db.audit(c,user['id'],'central.manager.handover.accept','central_manager_handover',hid,{'cash_transferred':False})
            return ops.safe(load(c,hid,user)[0])
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),
                             'central.manager.handover.accept:'+hid,{},perform)
