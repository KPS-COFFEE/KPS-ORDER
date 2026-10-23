"""Manager authorization to begin early/unscheduled closing; never final closure."""
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from . import auth, db
from .operations import lock_shift, shift_access, mutate, safe, uid, roles

router = APIRouter(prefix='/api/driver-closure', tags=['driver-closure'])

class Reason(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=1000)
    @field_validator('reason')
    @classmethod
    def safe_reason(cls, value):
        if '\x00' in value: raise ValueError('Invalid text')
        return value

class Schedule(Reason):
    scheduled_end_at: datetime | None
    @field_validator('scheduled_end_at')
    @classmethod
    def aware(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError('Timezone required')
        return value

class Withdrawal(Reason):
    request_id: str

class Decision(Reason):
    decision: Literal['approved', 'rejected']
    request_id: str

def manager(c, user, shift, *, revalidate_morning=False):
    roles(user, 'owner', 'delivery_manager')
    if revalidate_morning and not auth.has_permission(user, 'shifts_approve') and shift and (shift.get('delivery_window_snapshot') or {}).get('period') == 'morning':
        # An approval is revalidated later by the driver's request with a fresh
        # database principal, not with the manager's prior HTTP context object.
        from .morning_manager_context import resolve, require_shift
        user = resolve(c, user, shift_id=shift.get('manager_shift_id'))
        require_shift(user, shift)
    if not auth.has_permission(user, 'shifts_approve'):
        raise db.error(403, 'CLOSURE_FORBIDDEN', 'غير مخول لاعتماد التقفيلة / Closure approval forbidden')
    shift_access(c, user, shift, manager=True)
    return shift

def driver_shift(c, user, sid):
    s = lock_shift(c, sid)
    shift_access(c, user, s)
    if s['kind'] != 'driver' or s['status'] != 'open':
        raise db.error(409, 'CLOSURE_SHIFT_NOT_OPEN', 'شفت السائق ليس مفتوحاً / Driver shift is not open')
    return s

def needs_approval(shift, now):
    return shift.get('scheduled_end_at') is None or now < shift['scheduled_end_at']

def guard(c, user, shift):
    """Called under shift lock, before any open -> closing transition."""
    if shift['kind'] != 'driver' or shift['status'] != 'open': return
    now = c.execute('SELECT now() n').fetchone()['n']
    if not needs_approval(shift, now): return
    approval = shift.get('closure_authorization') or {}
    schedule = safe(shift.get('scheduled_end_at'))
    if approval.get('status') == 'approved' and approval.get('scheduled_end_at') == schedule:
        principal = c.execute('SELECT * FROM users WHERE id=%s', (approval.get('decided_by'),)).fetchone()
        from . import multibrand
        if multibrand.enabled():principal=multibrand.project(c,principal,shift['brand_id'])
        if principal and principal['active'] and principal['role'] in ('owner', 'delivery_manager'):
            manager(c, principal, shift, revalidate_morning=True)
            return
    raise db.error(409, 'EARLY_CLOSURE_APPROVAL_REQUIRED',
                   'اطلب موافقة مدير التوصيل قبل بدء التقفيلة / Request delivery manager approval before closing')

def schedule_in_tx(c, user, sid, body):
    from .central_workdays import guard_component
    guard_component(c, sid)
    s = driver_shift(c, user, sid); manager(c, user, s)
    if body.scheduled_end_at is not None and body.scheduled_end_at <= s['opened_at']:
        raise db.error(422, 'CLOSURE_SCHEDULE_INVALID', 'وقت النهاية بعد بداية الشفت / End must follow shift start')
    c.execute('UPDATE shifts SET scheduled_end_at=%s,closure_authorization=NULL WHERE id=%s', (body.scheduled_end_at, sid))
    db.audit(c, user['id'], 'driver_closure.schedule', 'shift', sid, body.model_dump(mode='json'))
    return {'shift_id': sid, 'scheduled_end_at': body.scheduled_end_at}


@router.put('/{sid}/schedule')
def schedule(sid: str, body: Schedule, request: Request, user=Depends(auth.current_user)):
    with db.tx() as c:
        s = driver_shift(c, user, sid); manager(c, user, s)
        def perform():
            return schedule_in_tx(c, user, sid, body)
        return mutate(c, user, request, 'driver_closure.schedule:'+sid, body.model_dump(mode='json'), perform)

def request_authorization_in_tx(c, user, sid, body):
    from .central_workdays import guard_component
    guard_component(c, sid)
    roles(user, 'driver')
    if not auth.has_permission(user, 'shifts_close'):
        raise db.error(403, 'CLOSURE_FORBIDDEN', 'صلاحية التقفيلة غير متاحة.')
    s = driver_shift(c, user, sid)
    if s['user_id'] != user['id']:
        raise db.error(403, 'CLOSURE_FORBIDDEN', 'غير مصرح.')
    s = driver_shift(c, user, sid)
    now = c.execute('SELECT now() n').fetchone()['n']
    old = s.get('closure_authorization') or {}
    if old.get('status') in ('requested', 'approved'):
        return {'shift_id': sid, 'authorization': old, 'status': 'open'}
    approval = {'id': uid(), 'status': 'requested', 'reason': body.reason,
                'requested_at': now.isoformat(), 'scheduled_end_at': safe(s.get('scheduled_end_at'))}
    c.execute('UPDATE shifts SET closure_authorization=%s WHERE id=%s', (db.json(approval), sid))
    db.audit(c, user['id'], 'driver_closure.request', 'shift', sid, approval)
    return {'shift_id': sid, 'authorization': approval, 'status': 'open'}


@router.post('/{sid}/request')
def request_authorization(sid: str, body: Reason, request: Request, user=Depends(auth.current_user)):
    roles(user, 'driver')
    if not auth.has_permission(user, 'shifts_close'):
        raise db.error(403, 'CLOSURE_FORBIDDEN', 'التقفيلة غير مخولة / Closure forbidden')
    with db.tx() as c:
        s = driver_shift(c, user, sid)
        if s['user_id'] != user['id']: raise db.error(403, 'CLOSURE_FORBIDDEN', 'غير مصرح / Forbidden')
        def perform():
            return request_authorization_in_tx(c, user, sid, body)
        return mutate(c, user, request, 'driver_closure.request:'+sid, body.model_dump(), perform)

@router.get('')
def listing(user=Depends(auth.current_user)):
    roles(user, 'owner', 'delivery_manager', 'driver')
    if user['role']=='delivery_manager' and not auth.has_permission(user, 'shifts_approve'):
        raise db.error(403, 'CLOSURE_FORBIDDEN', 'غير مصرح / Forbidden')
    with db.tx() as c:
        rows = c.execute("SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.brand_id=%s AND s.kind='driver' AND s.status='open' AND s.closure_authorization IS NOT NULL ORDER BY s.opened_at", (user['brand_id'],)).fetchall()
        from .central_workdays import annotate
        annotate(c, rows)
        visible=[]
        from fastapi import HTTPException
        for s in rows:
            try: shift_access(c, user, s)
            except HTTPException: continue
            visible.append({key:s[key] for key in ('id','display_name','scheduled_end_at','closure_authorization','brand_id','central_workday_id','central_anchor_id') if key in s})
        return safe({'requests': visible})

def decide_in_tx(c, user, sid, body):
    from .central_workdays import guard_component
    guard_component(c, sid)
    s = driver_shift(c, user, sid); manager(c, user, s)
    old = s.get('closure_authorization') or {}
    if old.get('status') != 'requested' or old.get('id') != body.request_id or old.get('scheduled_end_at') != safe(s.get('scheduled_end_at')):
        raise db.error(409, 'CLOSURE_REQUEST_STALE', 'طلب تغير؛ اطلب مجدداً / Request changed; submit again')
    approval = {**old, 'status': body.decision, 'decision_reason': body.reason,
                'decided_by': user['id'], 'decided_at': c.execute('SELECT now() n').fetchone()['n'].isoformat()}
    c.execute('UPDATE shifts SET closure_authorization=%s WHERE id=%s', (db.json(approval), sid))
    db.audit(c, user['id'], 'driver_closure.'+body.decision, 'shift', sid, approval)
    return {'shift_id': sid, 'status': 'open', 'authorization': approval}


@router.post('/{sid}/decision')
def decide(sid: str, body: Decision, request: Request, user=Depends(auth.current_user)):
    with db.tx() as c:
        s = driver_shift(c, user, sid); manager(c, user, s)
        def perform():
            return decide_in_tx(c, user, sid, body)
        return mutate(c, user, request, 'driver_closure.decision:'+sid, body.model_dump(), perform)


def withdraw_in_tx(c, user, sid, body):
    from .central_workdays import guard_component
    guard_component(c, sid)
    roles(user, 'driver')
    if not auth.has_permission(user, 'shifts_close'):
        raise db.error(403, 'CLOSURE_FORBIDDEN', 'صلاحية التقفيلة غير متاحة.')
    s = driver_shift(c, user, sid)
    if s['user_id'] != user['id']:
        raise db.error(403, 'CLOSURE_FORBIDDEN', 'غير مصرح.')
    s = driver_shift(c, user, sid)
    old = s.get('closure_authorization') or {}
    if old.get('id') != body.request_id:
        raise db.error(409, 'CLOSURE_REQUEST_STALE', 'طلب تغير؛ حدّث الشاشة / Request changed; refresh')
    if old.get('status') == 'withdrawn':
        return {'shift_id': sid, 'status': 'open', 'authorization': old}
    if old.get('status') not in ('requested', 'approved'):
        raise db.error(409, 'CLOSURE_REQUEST_STALE', 'لا يوجد طلب قابل للسحب / No request to withdraw')
    approval = {**old, 'status': 'withdrawn', 'withdrawal_reason': body.reason,
                'withdrawn_by': user['id'], 'withdrawn_at': c.execute('SELECT now() n').fetchone()['n'].isoformat()}
    c.execute('UPDATE shifts SET closure_authorization=%s WHERE id=%s', (db.json(approval), sid))
    db.audit(c, user['id'], 'driver_closure.withdraw', 'shift', sid, approval)
    return {'shift_id': sid, 'status': 'open', 'authorization': approval}


@router.post('/{sid}/withdraw')
def withdraw(sid: str, body: Withdrawal, request: Request, user=Depends(auth.current_user)):
    roles(user, 'driver')
    if not auth.has_permission(user, 'shifts_close'):
        raise db.error(403, 'CLOSURE_FORBIDDEN', 'غير مصرح / Forbidden')
    with db.tx() as c:
        s = driver_shift(c, user, sid)
        if s['user_id'] != user['id']:
            raise db.error(403, 'CLOSURE_FORBIDDEN', 'غير مصرح / Forbidden')
        def perform():
            return withdraw_in_tx(c, user, sid, body)
        return mutate(c, user, request, 'driver_closure.withdraw:'+sid, body.model_dump(), perform)
