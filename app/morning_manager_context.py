"""Explicit, ephemeral morning manager capabilities; never stored on a user.

Normal requests retain the employee's exact existing permissions. The morning
context is a separate least-privilege view of one approved Branch 2 duty.
"""
from . import db

HEADER = 'x-delivery-period'
# Capabilities remain bounded by the endpoint and fresh resource allowlists.
CAPABILITIES = frozenset({'orders_view', 'orders_create_customer', 'delivery_assign', 'shifts_view',
                          'tasks_view', 'tasks_transfer', 'orders_prepare'})
BOOTSTRAP_CAPABILITIES = frozenset({'shifts_view', 'shifts_open'})
ENDPOINTS = {
    'auth': {'logout'},
    'orders': {'list_orders', 'get_order', 'manager_accept', 'create_delivery_center_order', 'preparation'},
    'operations': {'shifts', 'open_shift', 'assign', 'tasks', 'task_handover'},
}


def enabled(user):
    return user.get('_morning_manager_context')


def capabilities(user):
    return BOOTSTRAP_CAPABILITIES if enabled(user).get('bootstrap') else CAPABILITIES


def resolve(conn, user, *, shift_id=None, allow_bootstrap=False):
    """Fresh owner policy, existing employee scope and real duty are mandatory."""
    from . import morning_delivery, catalog
    if (not user.get('active') or user.get('invitation_status', 'active') != 'active'
            or user.get('role') != 'delivery_manager' or user.get('brand_id') != 'kps'):
        raise db.error(403, 'MORNING_MANAGER_SCOPE', 'هذا السياق مخصص لمدير الصباح المعتمد.')
    row = conn.execute('SELECT settings FROM brands WHERE id=%s', (user['brand_id'],)).fetchone()
    policy = morning_delivery.policy((row or {}).get('settings'))
    if user['id'] not in policy['manager_user_ids']:
        raise db.error(403, 'MORNING_MANAGER_REVOKED', 'التشغيل الصباحي أو اعتماد المدير غير ساري.')
    duty = conn.execute("SELECT * FROM shifts WHERE user_id=%s AND brand_id=%s AND kind='manager' "
        "AND status IN ('open','closing','submitted') AND delivery_window_snapshot->>'period'='morning' "
        "ORDER BY opened_at DESC,id DESC LIMIT 1", (user['id'], user['brand_id'])).fetchone()
    if not duty and allow_bootstrap and not shift_id:
        if not policy['enabled']:
            raise db.error(403, 'MORNING_MANAGER_REVOKED', 'التشغيل الصباحي غير مفعل لفتح مناوبة جديدة.')
        branches = conn.execute('SELECT id,branch_no,city FROM branches WHERE brand_id=%s AND id=ANY(%s)',
            (user['brand_id'], list(set(user.get('branch_ids') or []) & set(policy['execution_branch_ids'])))).fetchall()
        branches = [b for b in branches if b['branch_no'] == 2 and catalog.normalize_city(b['city']) == 'حفر الباطن']
        if len(branches) != 1:
            raise db.error(403, 'MORNING_MANAGER_BRANCH', 'يلزم اعتماد فرع 2 بحفر الباطن.')
        scoped = dict(user, branch_ids=[branches[0]['id']])
        scoped['_morning_manager_context'] = {'period': 'morning', 'shift_id': None,
            'branch_ids': scoped['branch_ids'], 'bootstrap': True}
        return scoped
    if not duty or shift_id and duty['id'] != shift_id:
        raise db.error(403, 'MORNING_MANAGER_DUTY', 'يلزم شفت صباحي فعلي تابع لحسابك.')
    branches = conn.execute('SELECT id,branch_no,city FROM branches WHERE brand_id=%s AND id=ANY(%s)',
        (user['brand_id'], duty['branch_ids'])).fetchall()
    permitted = set(user.get('branch_ids') or []) & set(policy['execution_branch_ids'])
    # This launch is intentionally one branch. A widened policy cannot widen it.
    if (not branches or len(branches) != len(duty['branch_ids'])
            or any(b['branch_no'] != 2 or catalog.normalize_city(b['city']) != 'حفر الباطن'
                   or b['id'] not in permitted for b in branches)):
        raise db.error(403, 'MORNING_MANAGER_BRANCH', 'نطاق الصباح المعتمد حاليًا فرع 2 بحفر الباطن فقط.')
    scoped = dict(user)
    scoped['branch_ids'] = [b['id'] for b in branches]
    scoped['_morning_manager_context'] = {'period': 'morning', 'shift_id': duty['id'],
        'branch_ids': scoped['branch_ids']}
    return scoped


def apply(conn, user, request, *, session=False):
    period = request.headers.get(HEADER)
    if period is None:
        return user
    if period != 'morning':
        raise db.error(400, 'DELIVERY_CONTEXT_INVALID', 'سياق فترة التوصيل غير صالح.')
    if not session:
        endpoint = request.scope.get('endpoint')
        module = getattr(endpoint, '__module__', '').rsplit('.', 1)[-1]
        if getattr(endpoint, '__name__', '') not in ENDPOINTS.get(module, set()):
            raise db.error(403, 'MORNING_CONTEXT_ENDPOINT', 'هذا الإجراء غير متاح في سياق الصباح.')
    endpoint = request.scope.get('endpoint')
    module = getattr(endpoint, '__module__', '').rsplit('.', 1)[-1]
    name = getattr(endpoint, '__name__', '')
    scoped = resolve(conn, user, allow_bootstrap=session or module == 'operations' and name in {'open_shift', 'shifts'})
    # Resource checks precede endpoint-specific idempotency replay. A saved
    # response must never escape a newly narrowed or revoked context.
    params = request.path_params
    if enabled(scoped).get('bootstrap') and (params or any(request.query_params.get(key)
            for key in ('order_id', 'task_id', 'owner_view'))):
        raise db.error(403, 'MORNING_MANAGER_DUTY', 'افتح مناوبتك الصباحية أولًا.')
    sid = params.get('sid')
    if sid:
        require_shift(scoped, conn.execute('SELECT * FROM shifts WHERE id=%s', (sid,)).fetchone())
    oid = params.get('oid') or params.get('order_id') or request.query_params.get('order_id')
    task_id = params.get('tid') or request.query_params.get('task_id')
    if task_id:
        task = conn.execute('SELECT * FROM driver_tasks WHERE id=%s', (task_id,)).fetchone()
        if not task:
            raise db.error(404, 'MORNING_TASK_NOT_FOUND', 'المهمة غير موجودة ضمن مناوبتك الصباحية.')
        require_shift(scoped, conn.execute('SELECT * FROM shifts WHERE id=%s', (task['shift_id'],)).fetchone())
        oid = task['order_id']
    if oid and not order_allowed(scoped, conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone(), conn):
        raise db.error(404, 'MORNING_ORDER_NOT_FOUND', 'الطلب غير موجود ضمن مناوبتك الصباحية.')
    return scoped


def order_allowed(user, order, conn=None):
    context = enabled(user)
    if not context:
        return True
    if not (order and order.get('brand_id') == user['brand_id']
            and order.get('branch_id') in user['branch_ids'] and order.get('service') == 'delivery'
            and (order.get('snapshot') or {}).get('delivery_period') == 'morning'):
        return False
    if conn is None:
        return True  # Pure scope checks; authenticated resource reads always pass conn.
    preferred = (order.get('snapshot') or {}).get('manager_shift_id')
    if not preferred:
        accepted = conn.execute("SELECT details->>'manager_shift_id' AS manager_shift_id FROM order_events "
            "WHERE order_id=%s AND event_type='preparation.accepted' AND details ? 'manager_shift_id' "
            "ORDER BY created_at DESC,id DESC LIMIT 1", (order['id'],)).fetchone()
        preferred = (accepted or {}).get('manager_shift_id')
    if preferred and preferred != context['shift_id']:
        return False
    bindings = conn.execute("SELECT s.* FROM driver_tasks t JOIN shifts s ON s.id=t.shift_id "
        "WHERE t.order_id=%s AND t.status NOT IN ('cancelled','transferred','reassigned')", (order['id'],)).fetchall()
    if bindings:
        return all(row['kind'] == 'driver' and row['manager_shift_id'] == context['shift_id']
                   and shift_allowed(user, row) for row in bindings)
    # Unassigned active morning orders form the preparation queue. History from
    # another duty is never made visible merely because its task was removed.
    return (order.get('completed_at') is None
        and order.get('preparation_status') not in ('completed','cancelled','rejected')
        and order.get('delivery_status') not in ('delivered','cancelled'))


def shift_allowed(user, shift):
    context = enabled(user)
    return not context or bool(not context.get('bootstrap') and shift and shift.get('brand_id') == user['brand_id']
        and shift.get('branch_ids') and set(shift['branch_ids']) <= set(user['branch_ids'])
        and (shift.get('delivery_window_snapshot') or {}).get('period') == 'morning'
        and (shift['id'] == context['shift_id'] or shift.get('manager_shift_id') == context['shift_id']))


def require_shift(user, shift):
    if not shift_allowed(user, shift):
        raise db.error(404, 'MORNING_SHIFT_NOT_FOUND', 'الشفت غير موجود ضمن مناوبتك الصباحية.')
