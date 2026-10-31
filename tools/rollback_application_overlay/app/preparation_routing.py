"""Compatibility for existing preparation routes over the verified prior app.

Creation is disabled. Existing source and target scopes, branch preparation,
printing and driver pickup continue without rewriting the original sale.
"""
from copy import deepcopy
from typing import Literal
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from . import auth, catalog, db
router=APIRouter(prefix='/api/preparation-routing',tags=['preparation-routing-compatibility'])
PREPARATION_ROLES=('owner','branch_manager')

def _scope(user, brand_id, branch_id):
    return bool(user.get('active', True) and user.get('brand_id') == brand_id and
                ((user.get('role') == 'owner' and not user.get('home_brand_id'))
                 or branch_id in user.get('branch_ids', [])))


def current_route(conn, order):
    # Canonical rows carry the nullable pointer; synthetic roster candidates
    # and ordinary orders must not query or invent a routing assignment.
    if not order.get('preparation_route_id'):
        return None
    oid = order.get('order_id') or order['id']
    route = conn.execute('SELECT * FROM order_preparation_routes WHERE id=%s AND order_id=%s',
                         (order['preparation_route_id'], oid)).fetchone()
    if not route:
        raise db.error(409, 'PREPARATION_ROUTE_MISSING', 'سجل فرع التحضير غير متاح؛ راجع الطلب.')
    return route


def _mark_runtime(conn, route_id):
    # Transaction-local, per verified route. This is version compatibility
    # evidence for DB guards, never an authorization grant or global bypass.
    conn.execute("SELECT set_config('kps.preparation_route_ids', "
                 "(coalesce(nullif(current_setting('kps.preparation_route_ids',true),''),'[]')::jsonb "
                 "|| jsonb_build_array(%s::text))::text,true)", (route_id,)).fetchone()


def pickup_branch_id(conn, order, *, user=None, shift=None):
    """The physical pickup only; callers retain original sale/task authorization."""
    route = current_route(conn, order)
    bid = route['target_branch_id'] if route else order['branch_id']
    if route:
        if (route['source_branch_id'] != order['branch_id'] or
                ('brand_id' in order and route['brand_id'] != order['brand_id'])):
            raise db.error(409, 'PREPARATION_ROUTE_SCOPE', 'بيانات فرع التحضير تحتاج مراجعة.')
        if (bid != order['branch_id'] and 'amendment_revision' in order and
                route['amendment_revision'] != order['amendment_revision']):
            raise db.error(409, 'PREPARATION_ROUTE_REVISION', 'تغيرت نسخة الطلب؛ راجع فرع التحضير قبل التنفيذ.')
        if user is not None and not _scope(user, route['brand_id'], bid):
            raise db.error(403, 'PREPARATION_PICKUP_SCOPE', 'فرع التحضير خارج نطاق الموظف الحالي.')
        if shift is not None and (shift.get('brand_id') != route['brand_id'] or bid not in shift.get('branch_ids', [])):
            raise db.error(409, 'PREPARATION_PICKUP_SHIFT', 'فرع التحضير خارج نطاق الشفت الحالي.')
        _mark_runtime(conn, route['id'])
    return bid


def _branch_view(branch):
    return {**{key: branch[key] for key in ('id', 'name', 'branch_no', 'city')},
            'location': (branch.get('settings') or {}).get('location'),
            'directions_url': catalog.directions_url(branch)}


def enrich_orders(conn, rows):
    if not rows:
        return rows
    ids = [r.get('order_id') or r['id'] for r in rows]
    routes = conn.execute('SELECT r.*,b.name,b.branch_no,b.city,b.settings FROM latest_order_preparation_routes r '
                          'JOIN branches b ON b.id=r.target_branch_id AND b.brand_id=r.brand_id '
                          'WHERE r.order_id=ANY(%s)', (ids,)).fetchall()
    by_id = {r['order_id']: r for r in routes}
    for row in rows:
        route = by_id.get(row.get('order_id') or row['id'])
        if route:
            branch = {**route, 'id': route['target_branch_id']}
            row['preparation_branch'] = {**_branch_view(branch), 'route_id': route['id'],
                'source_branch_id': route['source_branch_id'], 'assigned_at': route['created_at'],
                'routed': route['target_branch_id'] != route['source_branch_id']}
    return rows


def guard_source_preparation(conn, order, status):
    if status == 'cancelled':
        return  # Existing source scope and cancellation/financial guards own this.
    route = current_route(conn, order)
    if route and route['target_branch_id'] != order['branch_id']:
        raise db.error(409, 'PREPARATION_TARGET_QUEUE', 'التجهيز مسند لفرع آخر؛ استخدم قائمة تحضير الفرع المكلف.')


def guard_amendment(conn, order):
    """A revised basket cannot silently invalidate a preparation assignment."""
    route = current_route(conn, order)
    if route and route['target_branch_id'] != order['branch_id']:
        raise db.error(409, 'PREPARATION_ROUTE_AMENDMENT', 'أعد التحضير لفرع الطلب قبل تعديل السلة؛ التحويل بعد الطباعة له معالجة مستقلة.')


def _prep_principal(user, *, write=False):
    if user.get('role') not in PREPARATION_ROLES or not auth.has_permission(user, 'orders_prepare' if write else 'orders_view'):
        raise db.error(403, 'PREPARATION_QUEUE_PERMISSION', 'قائمة التحضير مخصصة لمسؤول الفرع المكلف.')


def _prep_order(conn, oid, user, *, lock=False):
    order = conn.execute('SELECT * FROM orders WHERE id=%s' + (' FOR UPDATE' if lock else ''), (oid,)).fetchone()
    route = current_route(conn, order) if order else None
    if (not order or not route or route['target_branch_id'] == order['branch_id']
            or not _scope(user, order['brand_id'], route['target_branch_id'])):
        raise db.error(404, 'PREPARATION_TASK_NOT_FOUND', 'التحضير غير متاح ضمن فرعك.')
    pickup_branch_id(conn, order, user=user)
    return order, route


def _preparation_view(conn, order):
    result = {key: order.get(key) for key in ('id', 'brand_id', 'order_no', 'version', 'created_at', 'preparation_status', 'delivery_status')}
    snapshot = order.get('snapshot') or {}
    def options(item):
        return [{str(code): {'name': group.get('name', ''),
                            'choices': [choice.get('name', '') for choice in group.get('choices') or [] if isinstance(choice, dict)]}
                 for code, group in (unit.get('option_snapshot') or {}).items() if isinstance(group, dict)}
                for unit in item.get('units') or [] if isinstance(unit, dict)]
    result.update(order_reference=snapshot.get('order_reference'), notes=snapshot.get('notes', ''),
                  items=[{key: deepcopy(item[key]) for key in ('name', 'quantity', 'note') if key in item}
                         | {'options': options(item)}
                         for item in snapshot.get('items') or []],
                  workflow=snapshot.get('delivery_workflow', 'branch_fulfillment'))
    enrich_orders(conn, [result])
    return result


@router.get('/tasks')
def queue(user=Depends(auth.current_user)):
    _prep_principal(user)
    with db.tx() as conn:
        rows = conn.execute("SELECT o.* FROM orders o JOIN latest_order_preparation_routes r ON r.order_id=o.id "
            "WHERE o.brand_id=%s AND r.brand_id=o.brand_id AND r.target_branch_id<>o.branch_id "
            "AND (%s OR r.target_branch_id=ANY(%s)) AND o.completed_at IS NULL "
            "AND o.preparation_status NOT IN ('cancelled','rejected','completed','handed_over') "
            "AND o.delivery_status NOT IN ('cancelled','delivered') ORDER BY o.created_at,o.id LIMIT 200",
            (user['brand_id'], user['role'] == 'owner' and not user.get('home_brand_id'), user.get('branch_ids', []))).fetchall()
        return {'preparation_tasks': [_preparation_view(conn, row) for row in rows]}


@router.get('/tasks/{oid}')
def task(oid: str, user=Depends(auth.current_user)):
    _prep_principal(user)
    with db.tx() as conn:
        order, _ = _prep_order(conn, oid, user)
        return {'preparation_task': _preparation_view(conn, order)}


class PreparationUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    version: StrictInt = Field(ge=1)
    status: Literal['accepted', 'preparing', 'ready', 'handed_over']


@router.post('/tasks/{oid}/prepare')
def prepare(oid: str, body: PreparationUpdate, user=Depends(auth.current_user),
            idem: str | None = Header(None, alias='Idempotency-Key')):
    _prep_principal(user, write=True)
    with db.tx() as conn:
        order, route = _prep_order(conn, oid, user, lock=True)
        from .collected_order_authority import current_actor
        user = current_actor(conn, user, order['brand_id'], lock=True)
        _prep_principal(user, write=True)
        order, route = _prep_order(conn, oid, user)
        def perform():
            if (order['service'] != 'delivery' or order.get('completed_at')
                    or order['delivery_status'] in ('delivered', 'cancelled')):
                raise db.error(409, 'PREPARATION_CLOSED', 'الطلب منتهٍ ولا يقبل تحديث التحضير.')
            if order['version'] != body.version:
                raise db.error(409, 'ORDER_VERSION_CONFLICT', 'تغير الطلب؛ حدّث قائمة التحضير.')
            transitions = {'new': 'accepted', 'accepted': 'preparing', 'preparing': 'ready', 'ready': 'handed_over'}
            if transitions.get(order['preparation_status']) != body.status:
                raise db.error(409, 'PREPARATION_TRANSITION_INVALID', 'انتقال حالة التحضير غير مسموح.')
            if order['snapshot'].get('delivery_workflow') == 'manager_dispatch':
                raise db.error(409, 'PREPARATION_MANAGER_DISPATCH', 'هذا الطلب يعمل بتجهيز مدير التوصيل واستلام السائق.')
            from .order_amendments import guard
            guard(conn, order)
            if body.status in ('preparing', 'ready'):
                from .branch_payments import require_verified
                require_verified(conn, order)
            if body.status in ('accepted', 'preparing') and order['customer'].get('requires_operational_confirmation'):
                if not conn.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='order.operational_confirmation' "
                                    "AND details->>'result'='reached'", (oid,)).fetchone():
                    raise db.error(409, 'OPERATIONAL_CONTACT_CONFIRMATION_REQUIRED', 'انتظر تأكيد مدير التوصيل للتواصل مع العميل.')
            if body.status == 'handed_over':
                assigned = conn.execute("SELECT * FROM driver_tasks WHERE order_id=%s AND status IN ('accepted','picked_up') ORDER BY created_at DESC LIMIT 1", (oid,)).fetchone()
                if not assigned:
                    raise db.error(409, 'DRIVER_ACCEPTANCE_REQUIRED', 'انتظر قبول السائق المكلف قبل التسليم.')
                driver = conn.execute('SELECT * FROM users WHERE id=%s', (assigned['driver_id'],)).fetchone()
                shift = conn.execute('SELECT * FROM shifts WHERE id=%s', (assigned['shift_id'],)).fetchone()
                from . import multibrand
                if driver and multibrand.enabled():
                    driver = multibrand.project(conn, driver, order['brand_id'])
                if not driver or not shift or shift['status'] != 'open' or not auth.can_access_order(driver, order, conn):
                    raise db.error(409, 'DRIVER_ACCEPTANCE_REQUIRED', 'راجع صلاحية السائق وشفت المهمة قبل التسليم.')
                pickup_branch_id(conn, order, user=driver, shift=shift)
            conn.execute('UPDATE orders SET preparation_status=%s,version=version+1 WHERE id=%s', (body.status, oid))
            details = {'previous': order['preparation_status'], 'status': body.status,
                       'branch_id': route['target_branch_id'], 'source_branch_id': order['branch_id'],
                       'route_id': route['id'], 'actor_name': user.get('display_name', ''), 'actor_role': user['role']}
            db.event(conn, oid, user['id'], 'preparation.' + body.status, details)
            db.audit(conn, user['id'], 'order.routed_preparation', 'order', oid, details)
            updated = {**order, 'version': order['version'] + 1, 'preparation_status': body.status}
            return {'preparation_task': _preparation_view(conn, updated)}
        return db.idempotent(conn, user['id'], idem, 'preparation.target:' + oid, body.model_dump(), perform)



@router.post('/orders/{oid}')
def disable_new(oid: str, user=Depends(auth.current_user)):
    raise db.error(409,'PREPARATION_ROUTE_PRIOR_READ_ONLY','نسخة التوافق تكمل التحضير المسند فقط؛ إنشاء تحويل جديد يتطلب النسخة المعتمدة الحالية.')
