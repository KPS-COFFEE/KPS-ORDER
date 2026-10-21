"""Owner-approved Hafar morning delivery; disabled until explicitly configured.

The customer branch remains the commercial/coverage boundary. Execution routing
never expands employee branch access or changes existing operational-day rules.
"""
from copy import deepcopy
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from . import auth, db, service_hours

router = APIRouter(prefix='/api/morning-delivery', tags=['morning-delivery'])
HOURS = [{'days': list(range(7)), 'start': '04:00', 'end': '16:00'}]


def defaults():
    return {'version': 0, 'enabled': False, 'execution_branch_ids': [],
            'preparer_user_ids': [], 'manager_user_ids': []}


def policy(settings):
    return defaults() | deepcopy((settings or {}).get('morning_delivery') or {})


def in_window(now=None):
    local = service_hours.local_time(now)
    return 4 <= local.hour < 16


def active(branch, settings, now=None):
    return (branch.get('brand_id') == 'kps'
            and ''.join(str(branch.get('city', '')).split()) == 'حفرالباطن'
            and policy(settings)['enabled'] is True and in_window(now))


def target(rows, selected, settings, *, khaliya=False, now=None):
    """Return one approved execution branch, or fail closed; never split a basket."""
    from . import catalog
    if not active(selected, settings, now):
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
            if self.enabled and not values:
                raise ValueError('Enabled morning delivery requires branches, preparers and managers.')
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
        return {'policy':policy((row or {}).get('settings')), 'hours':HOURS, 'timezone':'Asia/Riyadh',
                'branches':[b for b in branches if user['brand_id']=='kps' and ''.join(b['city'].split())=='حفرالباطن'],
                'users':users}


@router.put('/policy')
def save_policy(body: PolicyChange, user=Depends(auth.require_roles('owner')),
                idem: str | None=Header(None,alias='Idempotency-Key')):
    if user['brand_id']!='kps':
        raise db.error(403,'MORNING_BRAND_SCOPE','التشغيل الصباحي المعتمد خاص بكبز في حفر الباطن.')
    if len(body.reason.strip())<3 or '\x00' in body.reason:
        raise db.error(422,'REASON_REQUIRED','أدخل سبب التعديل.')
    with db.tx() as c:
        auth.require_owner_step_up(c,user)
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
                    if not b or ''.join(b['city'].split())!='حفرالباطن':
                        raise db.error(422,'MORNING_BRANCH_SCOPE','اختر فروع تنفيذ من حفر الباطن فقط.')
                for field,role in [('preparer_user_ids','branch_manager'),('manager_user_ids','delivery_manager')]:
                    covered=set()
                    for uid in candidate[field]:
                        employee=c.execute('SELECT * FROM users WHERE id=%s AND brand_id=%s AND active AND role=%s FOR SHARE',
                                           (uid,user['brand_id'],role)).fetchone()
                        if not employee:
                            raise db.error(422,'MORNING_USER_SCOPE','اختر مستخدمًا فعالًا بالدور والبراند الصحيحين.')
                        covered.update(employee['branch_ids'])
                    if not set(candidate['execution_branch_ids'])<=covered:
                        raise db.error(422,'MORNING_BRANCH_UNSTAFFED','كل فرع تنفيذ يحتاج محضّرًا ومديرًا مخولين عليه.')
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
    duty=c.execute("SELECT s.id FROM shifts s JOIN users u ON u.id=s.user_id "
        "WHERE s.brand_id=%s AND s.kind='manager' AND s.status='open' AND s.branch_ids ? %s "
        "AND s.delivery_window_snapshot->>'period'='morning' "
        "AND u.active AND u.brand_id=s.brand_id AND (u.role='owner' OR "
        "(u.role='delivery_manager' AND u.id=ANY(%s) AND u.branch_ids ? %s)) FOR SHARE OF s,u",
        (brand_id,branch_id,p['manager_user_ids'],branch_id)).fetchone()
    if not duty:
        raise db.error(409,'MORNING_DUTY_UNAVAILABLE','التوصيل الصباحي مغلق حاليًا لعدم وجود مناوبة مستلمة.')
