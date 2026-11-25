"""Owner-approved Hafar morning delivery; disabled until explicitly configured.

The customer branch remains the commercial/coverage boundary. Execution routing
never expands employee branch access or changes existing operational-day rules.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from . import auth, db, service_hours

router = APIRouter(prefix='/api/morning-delivery', tags=['morning-delivery'])
HOURS = [{'days': list(range(7)), 'start': '04:00', 'end': '16:00'}]
ENBISHAN_HOURS = [{'days': [0, 1, 2, 3, 5, 6], 'start': '09:00', 'end': '12:00'}]


def hours_for(brand_id, *, manager=False):
    if brand_id == 'enbishan':
        return ENBISHAN_HOURS
    return MANAGER_SHIFT_HOURS if manager else HOURS


def scope_branch(branch, brand_id, *, manager=False):
    city = ''.join(str(branch.get('city', '')).split())
    if city != 'حفرالباطن':
        return False
    if brand_id == 'enbishan':
        return branch.get('branch_no') == 1
    return brand_id == 'kps' and (not manager or branch.get('branch_no') == 2)


def enbishan_evening_hours(hours):
    return [row for row in (hours if isinstance(hours, list) else [])
            if isinstance(row, dict) and (row.get('start'), row.get('end')) != ('09:00', '12:00')]



def scheduled_delivery_hours(branch, settings, now=None):
    configured = branch['settings'].get('delivery_hours')
    brand_id = branch.get('brand_id')
    if brand_id == 'enbishan' and scope_branch(branch, brand_id):
        # Legacy preparations could already include this exact window. Only the
        # approved policy now owns morning admission; preserve the other rows.
        normal = enbishan_evening_hours(configured)
        return normal + ENBISHAN_HOURS if enabled_for_branch(branch, settings) else normal
    return HOURS if active(branch, settings, now) else configured

# Manager staffing is independent from customer admission and driver shifts.
MANAGER_SHIFT_HOURS = [
    {'days': [0, 1, 2, 3, 5, 6], 'start': '04:00', 'end': '16:00'},
    {'days': [4], 'start': '12:30', 'end': '16:00'},
]


def manager_shift_status(now=None, *, brand_id='kps'):
    """The approved manager may prepare fifteen minutes before morning duty."""
    local = service_hours.local_time(now)
    hours = hours_for(brand_id, manager=True)
    status = service_hours.hours_status(hours, local)
    if not status['open'] and status['next_open_at']:
        opening = datetime.fromisoformat(status['next_open_at'])
        start = opening - timedelta(minutes=15)
        if start <= local < opening:
            status = service_hours.hours_status(hours, opening)
            status.update(preparation=True, preparation_start=start.isoformat())
    return status


def defaults():
    return {'version': 0, 'enabled': False, 'execution_branch_ids': [],
            'preparer_user_ids': [], 'manager_user_ids': []}


def policy(settings):
    return defaults() | deepcopy((settings or {}).get('morning_delivery') or {})


def in_window(now=None):
    local = service_hours.local_time(now)
    return 4 <= local.hour < 16


def enabled_for_branch(branch, settings):
    brand_id = branch.get('brand_id')
    configured = policy(settings)
    return (scope_branch(branch, brand_id) and configured['enabled'] is True
            and (brand_id == 'kps' or branch['id'] in configured['execution_branch_ids']))


def active(branch, settings, now=None):
    if not enabled_for_branch(branch, settings):
        return False
    return (service_hours.hours_open(ENBISHAN_HOURS, now) if branch['brand_id'] == 'enbishan'
            else in_window(now))


def target(rows, selected, settings, *, khaliya=False, now=None):
    """Return one approved execution branch, or fail closed; never split a basket."""
    from . import catalog
    if not active(selected, settings, now):
        return selected
    if selected['brand_id'] == 'enbishan':
        return selected
    by_id = {b['id']: b for b in rows}
    for bid in policy(settings)['execution_branch_ids']:
        branch = by_id.get(bid)
        if not branch or branch.get('brand_id') != selected['brand_id']:
            continue
        if catalog.normalize_city(branch['city']) != 'حفر الباطن':
            continue
        if khaliya and not catalog.is_khaliya_branch(branch):
            continue
        if catalog.branch_service(branch, 'delivery', now=now, brand_settings=settings)[0]:
            return branch
    raise db.error(409, 'MORNING_EXECUTION_UNAVAILABLE', 'خدمة التوصيل الصباحية غير متاحة لهذه السلة حاليًا.')


class MorningPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool = False
    execution_branch_ids: list[str] = Field(default_factory=list, max_length=100)
    preparer_user_ids: list[str] = Field(default_factory=list, max_length=200)
    manager_user_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def valid_ids(self):
        for values in (self.execution_branch_ids, self.preparer_user_ids, self.manager_user_ids):
            if len(values) != len(set(values)) or any(not v or len(v)>100 or '\x00' in v for v in values):
                raise ValueError('Unique, nonempty identifiers are required.')
        if self.enabled and (not self.execution_branch_ids or not self.manager_user_ids):
            raise ValueError('Enabled morning delivery requires branches and managers.')
        return self


class PolicyChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    policy: MorningPolicy
    expected_version: StrictInt = Field(ge=0)
    reason: str = Field(min_length=3, max_length=500)


@router.get('/policy')
def read_policy(user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        row=c.execute('SELECT settings FROM brands WHERE id=%s',(user['brand_id'],)).fetchone()
        branches=c.execute('SELECT id,branch_no,name,city FROM branches WHERE brand_id=%s ORDER BY branch_no',(user['brand_id'],)).fetchall()
        users=c.execute("SELECT id,display_name,phone,role,branch_ids FROM users WHERE brand_id=%s AND active "
                        "AND role IN ('branch_manager','delivery_manager') ORDER BY display_name",(user['brand_id'],)).fetchall()
        if user['brand_id'] == 'enbishan':
            from . import multibrand
            if multibrand.enabled():
                shared = c.execute(
                    "SELECT u.* FROM users u JOIN staff_brand_memberships m ON m.user_id=u.id "
                    "WHERE m.brand_id=%s AND m.active AND u.active AND u.brand_id<>%s AND u.role='delivery_manager' "
                    "ORDER BY u.display_name", (user['brand_id'], user['brand_id']),
                ).fetchall()
                for employee in shared:
                    projected = multibrand.project(c, employee, user['brand_id'])
                    if projected:
                        users.append({field: projected[field] for field in ('id', 'display_name', 'phone', 'role', 'branch_ids')})
        eligible = [b for b in branches if scope_branch(b, user['brand_id'])
                    and (not user.get('home_brand_id') or b['id'] in user.get('branch_ids', []))]
        return {'policy':policy((row or {}).get('settings')), 'hours':hours_for(user['brand_id']), 'timezone':'Asia/Riyadh',
                'branches':eligible, 'users':users}



@router.put('/policy')
def save_policy(body: PolicyChange, user=Depends(auth.require_roles('owner')),
                idem: str | None=Header(None,alias='Idempotency-Key')):
    if user['brand_id'] not in {'kps', 'enbishan'}:
        raise db.error(403,'MORNING_BRAND_SCOPE','التشغيل الصباحي غير معتمد لهذا البراند.')
    staff_roles = [('manager_user_ids','delivery_manager')]
    if user['brand_id'] == 'kps':
        staff_roles.insert(0, ('preparer_user_ids','branch_manager'))
    if body.policy.enabled and (not body.policy.execution_branch_ids
            or any(not getattr(body.policy, field) for field, _ in staff_roles)):
        raise db.error(422,'MORNING_BRANCH_UNSTAFFED','اختر فرع تنفيذ وموظفي الصباح المخولين.')
    if len(body.reason.strip())<3 or '\x00' in body.reason:
        raise db.error(422,'REASON_REQUIRED','أدخل سبب التعديل.')
    with db.tx() as c:
        if user['brand_id'] == 'enbishan':
            auth.require_owner_step_up(c, user)
        # An authenticated owner confirms this operational change in the UI.
        def perform():
            row=c.execute('SELECT settings FROM brands WHERE id=%s FOR UPDATE',(user['brand_id'],)).fetchone()
            previous=policy(row['settings'])
            if previous['version']!=body.expected_version:
                raise db.error(409,'POLICY_CHANGED','عُدلت الإعدادات؛ أعد فتحها قبل الحفظ.')
            candidate=body.policy.model_dump()
            # Pausing must remain possible even after a configured employee was
            # revoked. Enabling never creates users or widens their branch scope.
            if candidate['enabled']:
                for bid in candidate['execution_branch_ids']:
                    b=c.execute('SELECT * FROM branches WHERE id=%s AND brand_id=%s FOR SHARE',(bid,user['brand_id'])).fetchone()
                    if (not b or not scope_branch(b, user['brand_id'])
                            or user.get('home_brand_id') and bid not in user.get('branch_ids', [])):
                        raise db.error(422,'MORNING_BRANCH_SCOPE','اختر فرع التنفيذ الصباحي المعتمد لهذا البراند.')
                for field,role in staff_roles:
                    covered=set()
                    for uid in candidate[field]:
                        if user['brand_id'] == 'enbishan':
                            from . import multibrand
                            raw = c.execute(
                                "SELECT * FROM users WHERE id=%s AND active AND invitation_status='active' "
                                "AND role=%s FOR SHARE", (uid, role),
                            ).fetchone()
                            employee = multibrand.project(c, raw, user['brand_id'])
                        else:
                            employee = c.execute(
                                'SELECT * FROM users WHERE id=%s AND brand_id=%s AND active AND role=%s FOR SHARE',
                                (uid, user['brand_id'], role),
                            ).fetchone()
                        if not employee:
                            raise db.error(422,'MORNING_USER_SCOPE','اختر مستخدمًا فعالًا بالدور والبراند الصحيحين.')
                        covered.update(employee['branch_ids'])
                    if not set(candidate['execution_branch_ids'])<=covered:
                        raise db.error(422,'MORNING_BRANCH_UNSTAFFED','اختر موظفي صباح مخولين على كل فرع تنفيذ.')
            if user['brand_id'] == 'enbishan':
                candidate['preparer_user_ids'] = []
            saved=candidate|{'version':previous['version']+1}
            c.execute('UPDATE brands SET settings=%s WHERE id=%s',(db.json(row['settings']|{'morning_delivery':saved}),user['brand_id']))
            db.audit(c,user['id'],'morning.policy.updated','brand',user['brand_id'],{'before':previous,'after':saved,'reason':body.reason.strip()})
            return {'policy':saved,'existing_orders_preserved':True}
        return db.idempotent(c,user['id'],idem,'morning.policy.update',body.model_dump(),perform)


def authorize_order(c, user, order, *, preparation=False):
    if order.get('snapshot',{}).get('delivery_period')!='morning':
        return
    if not user or not user.get('active') or user.get('brand_id') != order['brand_id']:
        raise db.error(403,'MORNING_USER_NOT_APPROVED','المستخدم غير مخول لهذا الطلب.')
    if user['role']=='owner':
        return
    if not preparation and user['role']=='delivery_manager':
        from .manager_handover_scope import accepted_order
        if accepted_order(c, user, order):
            return
    row=c.execute('SELECT settings FROM brands WHERE id=%s',(order['brand_id'],)).fetchone()
    field='preparer_user_ids' if preparation else 'manager_user_ids'
    expected='branch_manager' if preparation else 'delivery_manager'
    if user['role']!=expected or user['id'] not in policy((row or {}).get('settings'))[field]:
        raise db.error(403,'MORNING_USER_NOT_APPROVED','هذا الإجراء مخصص لمستخدم الفترة الصباحية المعتمد.')
    if order['branch_id'] not in user.get('branch_ids',[]):
        raise db.error(403,'BRANCH_FORBIDDEN','فرع التنفيذ خارج صلاحياتك.')


def require_duty(c, brand_id, branch_id):
    row=c.execute('SELECT settings FROM brands WHERE id=%s',(brand_id,)).fetchone()
    p=policy((row or {}).get('settings'))
    if brand_id == 'enbishan':
        from . import multibrand
        duties = c.execute(
            "SELECT s.id AS duty_id, u.* FROM shifts s JOIN users u ON u.id=s.user_id "
            "WHERE s.brand_id=%s AND s.kind='manager' AND s.status='open' "
            "AND s.branch_ids=jsonb_build_array(%s::text) AND s.manager_admission_ended_at IS NULL "
            "AND s.delivery_operational_date=%s AND s.delivery_window_snapshot->>'period'='morning' "
            "AND u.active AND u.invitation_status='active' AND u.role='delivery_manager' AND u.id=ANY(%s) "
            "ORDER BY s.opened_at DESC,s.id DESC FOR SHARE OF s,u",
            (brand_id, branch_id, service_hours.delivery_operational_date(), p['manager_user_ids']),
        ).fetchall()
        for duty in duties:
            manager = multibrand.project(c, duty, brand_id)
            if manager and branch_id in manager['branch_ids']:
                return duty['duty_id']
        raise db.error(409,'MORNING_DUTY_UNAVAILABLE','التوصيل الصباحي مغلق حاليًا لعدم وجود مناوبة مستلمة.')
    duty=c.execute("SELECT s.id FROM shifts s JOIN users u ON u.id=s.user_id "
        "WHERE s.brand_id=%s AND s.kind='manager' AND s.status='open' AND s.branch_ids ? %s "
        "AND s.delivery_window_snapshot->>'period'='morning' "
        "AND u.active AND u.brand_id=s.brand_id AND (u.role='owner' OR "
        "(u.role='delivery_manager' AND u.id=ANY(%s) AND u.branch_ids ? %s)) FOR SHARE OF s,u",
        (brand_id,branch_id,p['manager_user_ids'],branch_id)).fetchone()
    if not duty:
        raise db.error(409,'MORNING_DUTY_UNAVAILABLE','التوصيل الصباحي مغلق حاليًا لعدم وجود مناوبة مستلمة.')
