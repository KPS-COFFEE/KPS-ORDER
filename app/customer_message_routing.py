"""Owner-approved inbox recipients; never change employee roles or branch grants.

Unconfigured brands retain the existing policy. Every request reads live routing
and memberships. Customer care includes complaints even on delivery orders.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from . import auth, db, multibrand

router = APIRouter(prefix='/api/identity/customer-message-routing', tags=['customer-message-routing'])
KEY = 'customer_message_routing'
ROLES = {'admin', 'delivery_manager', 'delivery_staff'}
CAPABILITIES = {'chat_view', 'chat_reply', 'chat_manage'}
CARE_CATEGORIES = {'complaint', 'privacy', 'refund', 'suggestion', 'other'}
DELIVERY_SQL = "(v.route='delivery' AND coalesce(v.category,'help') NOT IN ('complaint','privacy','refund','suggestion','other'))"


def team(row):
    return 'delivery' if row['route'] == 'delivery' and row.get('category') not in CARE_CATEGORIES else 'care'


def config(c, brand):
    row = c.execute('SELECT settings FROM app_settings WHERE brand_id=%s', (brand,)).fetchone()
    return (row['settings'] or {}).get(KEY) if row else None


def brands(c, user):
    return multibrand.available(c, user) if multibrand.enabled() else [{'id': user['brand_id']}]


def capabilities(c, user):
    if user['role'] not in ROLES or not user.get('active'):
        return set()
    for brand in brands(c, user):
        value = config(c, brand['id'])
        principal = multibrand.project(c, user, brand['id'])
        if (value and principal and principal.get('branch_ids') and
                any(user['id'] in value.get(group, []) for group in ('delivery', 'care'))):
            return CAPABILITIES
    return set()


def access(c, user, row):
    """None means legacy policy; False means a configured explicit denial."""
    value = config(c, row['brand_id'])
    if value is None:
        return None
    if user['role'] not in ROLES or user['brand_id'] != row['brand_id']:
        return False
    group = team(row)
    return (user['id'] in value.get(group, []) and bool(user.get('branch_ids')) and
            (row['branch_id'] in user['branch_ids'] or (row['branch_id'] is None and group == 'care')))


def scope(c, user, *, alerts=False):
    """One SQL policy for inbox pagination and alerts; detail checks use access."""
    value = config(c, user['brand_id'])
    if value is None or user['role'] == 'customer':
        return None
    if user['role'] == 'owner':
        if not alerts:
            return None  # Owner retains supervision, including routed messages.
        # A deactivated employee, revoked membership, or denied reply must not
        # silently swallow an unanswered message. Uncovered messages reach owner.
        covered, args = [], []
        ids = sorted(set(value.get('delivery', []) + value.get('care', [])))
        rows = c.execute('SELECT * FROM users WHERE id=ANY(%s) AND active', (ids,)).fetchall() if ids else []
        for group in ('delivery', 'care'):
            branches = set()
            for row in rows:
                if row['id'] not in value.get(group, []) or row['role'] not in ROLES:
                    continue
                if set(row.get('permission_denials') or []) & {'chat_view', 'chat_reply'}:
                    continue
                principal = multibrand.project(c, row, user['brand_id'])
                if principal:
                    branches.update(principal.get('branch_ids') or [])
            if branches:
                predicate = DELIVERY_SQL if group == 'delivery' else 'NOT ' + DELIVERY_SQL
                branch_scope = 'v.branch_id=ANY(%s)' if group == 'delivery' else '(v.branch_id=ANY(%s) OR v.branch_id IS NULL)'
                covered.append('(' + predicate + ' AND ' + branch_scope + ')')
                args.append(sorted(branches))
        return ('NOT (' + ' OR '.join(covered) + ')', args) if covered else ('TRUE', [])
    groups = [group for group in ('delivery', 'care') if user['id'] in value.get(group, [])]
    if user['role'] not in ROLES or not user.get('branch_ids') or not groups:
        return 'FALSE', []
    clauses, args = [], [user['brand_id']]
    for group in groups:
        predicate = DELIVERY_SQL if group == 'delivery' else 'NOT ' + DELIVERY_SQL
        branch_scope = 'v.branch_id=ANY(%s)' if group == 'delivery' else '(v.branch_id=ANY(%s) OR v.branch_id IS NULL)'
        clauses.append('(' + predicate + ' AND ' + branch_scope + ')')
        args.append(user['branch_ids'])
    return 'v.brand_id=%s AND (' + ' OR '.join(clauses) + ')', args


class Recipients(BaseModel):
    model_config = ConfigDict(extra='forbid')
    delivery: list[str] = Field(min_length=1, max_length=50)
    care: list[str] = Field(min_length=1, max_length=50)


class RoutingUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    routing: dict[str, Recipients]
    expected: dict[str, dict | None]
    reason: str = Field(min_length=3, max_length=2000)


def owner_brands(c, user):
    result = []
    for brand in brands(c, user):
        principal = multibrand.project(c, user, brand['id'])
        branch_ids = {r['id'] for r in c.execute('SELECT id FROM branches WHERE brand_id=%s', (brand['id'],)).fetchall()}
        if principal and (not principal.get('home_brand_id') or branch_ids <= set(principal.get('branch_ids', []))):
            result.append(brand['id'])
    return result


@router.get('')
def settings(user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        allowed = owner_brands(c, user)
        return {'routing': {brand: config(c, brand) for brand in allowed}}


@router.put('')
def update(body: RoutingUpdate, user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        auth.require_owner_sms_step_up(c, user)
        allowed = set(owner_brands(c, user))
        if not body.routing or not set(body.routing) <= allowed or set(body.expected) != set(body.routing):
            raise db.error(403, 'MESSAGE_ROUTING_SCOPE', 'إعداد الرسائل خارج نطاقك.')
        saved = {}
        for brand, selection in sorted(body.routing.items()):
            row = c.execute('SELECT settings FROM app_settings WHERE brand_id=%s FOR UPDATE', (brand,)).fetchone()
            if row is None:
                raise db.error(409, 'MESSAGE_ROUTING_SETTINGS', 'إعدادات البراند غير جاهزة.')
            previous = (row['settings'] or {}).get(KEY)
            if previous != body.expected[brand]:
                raise db.error(409, 'MESSAGE_ROUTING_STALE', 'تغير توزيع الرسائل؛ حدّث الصفحة قبل الحفظ.')
            value = selection.model_dump()
            for group, ids in value.items():
                if len(set(ids)) != len(ids):
                    raise db.error(422, 'MESSAGE_ROUTING_DUPLICATE', 'الموظف مكرر.')
                for identifier in ids:
                    target = c.execute('SELECT * FROM users WHERE id=%s AND active', (identifier,)).fetchone()
                    principal = multibrand.project(c, target, brand) if target else None
                    if not principal or target['role'] not in ROLES or not principal.get('branch_ids'):
                        raise db.error(422, 'MESSAGE_ROUTING_RECIPIENT', 'اختر موظفًا نشطًا له فروع في البراند.')
                    if set(target.get('permission_denials') or []) & CAPABILITIES:
                        raise db.error(422, 'MESSAGE_ROUTING_DENIED', 'صلاحيات المحادثات موقوفة لأحد الموظفين؛ راجعها أولًا.')
                value[group] = sorted(ids)
            c.execute('UPDATE app_settings SET settings=settings||%s::jsonb WHERE brand_id=%s', (db.json({KEY: value}), brand))
            db.audit(c, user['id'], 'customer_message_routing.updated', 'brand', brand,
                     {'before': previous, 'after': value, 'reason': body.reason})
            saved[brand] = value
        return {'routing': saved}
