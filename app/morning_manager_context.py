"""Explicit, ephemeral morning manager capabilities; never stored on a user.

Normal requests retain the employee's exact existing permissions. The morning
context is a separate least-privilege view of one approved Branch 2 duty.
"""
from . import db
from .employee_permissions import eligible, ROUTES

HEADER = 'x-delivery-period'
# Capabilities remain bounded by the endpoint and fresh resource allowlists.
# Owner-approved parity with the delivery-manager role. The additional
# preparation capability retains the existing morning branch fallback. This
# never grants owner/accountant powers or changes stored employee permissions.
CAPABILITIES = frozenset(eligible('delivery_manager') | {'orders_prepare'})
BOOTSTRAP_CAPABILITIES = frozenset({'shifts_view', 'shifts_open'})
ENDPOINTS = {
    'auth': {'logout'},
    'admin': {'read_alert_policy'},
    'orders': {'list_orders', 'get_order', 'manager_accept', 'create_delivery_center_order', 'preparation'},
    'operations': {'shifts', 'open_shift', 'assign', 'tasks', 'task_handover'},
}
# These modules are reviewed below and at their aggregate read boundaries.
# Existing role, amount, recipient, immutable-report and approval checks still
# run after the fresh morning resource checks. Unmapped endpoints stay closed.
for _module in ('operations', 'orders', 'reporting', 'chat', 'order_support',
                'driver_incentives', 'driver_float', 'driver_breaks',
                'fuel_exceptions', 'branch_printing', 'order_amendments',
                'maintenance', 'accounting', 'catalog'):
    for _name, _permission in ROUTES.get(_module, {}).items():
        if isinstance(_permission, dict):
            _permission = _permission.get('delivery_manager')
        _permissions = {_permission} if isinstance(_permission, str) else set(_permission or ())
        if _permissions & CAPABILITIES:
            ENDPOINTS.setdefault(_module, set()).add(_name)
# Driver acceptance, pickup and delivery events remain driver-authored.
ENDPOINTS['operations'].discard('task_transition')
ENDPOINTS['orders'].update({'authorize_complimentary','revoke_complimentary_authorization','list_complimentary_authorizations','customer_lookup', 'customer_search', 'change_delivery_location'})
ENDPOINTS['order_requests'] = {'decide'}
ENDPOINTS['driver_support'] = {'list_requests', 'request_support', 'accept_support', 'resolve_request'}
ENDPOINTS['order_financial_cases'] = {'cancel','journey','inbox'}
ENDPOINTS['map_services'] = {'recommendations', 'health', 'alerts', 'zones', 'plan', 'trips'}
ENDPOINTS['driver_closure'] = {'listing', 'schedule', 'decide'}
ENDPOINTS['dispatch_map'] = {'map_config', 'driver_locations', 'discover_driver_routes', 'permit_driver_route'}
ENDPOINTS['driver_work_hours'] = {'report'}
ENDPOINTS['driver_hours_reports'] = {'dashboard', 'export_xlsx', 'export_pdf'}
ENDPOINTS['task_recovery'] = {'listing', 'detail', 'review_revision'}


def enabled(user):
    return user.get('_morning_manager_context')


def capabilities(user):
    if enabled(user).get('bootstrap'):
        return BOOTSTRAP_CAPABILITIES
    from . import auth
    base = dict(user)
    base.pop('_morning_manager_context', None)
    extra = {permission for permission in ('driver_support_approve','complimentary_approve') if auth.has_permission(base, permission)}
    return CAPABILITIES | extra


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
        'branch_ids': scoped['branch_ids'],
        'supported_shift_ids': [r['id'] for r in conn.execute(
            "SELECT s.id FROM shifts s WHERE s.manager_shift_id=%s AND s.brand_id=%s "
            "AND s.branch_ids <@ %s::jsonb AND EXISTS(SELECT 1 FROM driver_support_requests r "
            "WHERE r.driver_shift_id=s.id AND r.to_shift_id=s.manager_shift_id AND r.status='accepted')",
            (duty['id'], user['brand_id'], db.json(scoped['branch_ids']))).fetchall()]}
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
    if request.method=='GET' and ((module=='order_financial_cases' and name in {'journey','inbox'}) or (module=='orders' and name=='get_order')):
        scoped['_morning_history_read']=True
    if request.method=='POST' and module=='order_financial_cases' and name=='cancel':
        scoped['_morning_cancellation_replay']=True
    if request.method=='POST' and module=='driver_incentives' and name=='decide':
        scoped['_morning_cancelled_incentive_review']=True
    # Resource checks precede endpoint-specific idempotency replay. A saved
    # response must never escape a newly narrowed or revoked context.
    params = request.path_params
    if enabled(scoped).get('bootstrap') and (params or any(request.query_params.get(key)
            for key in ('order_id', 'task_id', 'owner_view'))):
        raise db.error(403, 'MORNING_MANAGER_DUTY', 'افتح مناوبتك الصباحية أولًا.')
    sid = params.get('sid')
    if sid:
        from .operations import shift_access
        shift_access(conn, scoped, conn.execute('SELECT * FROM shifts WHERE id=%s', (sid,)).fetchone())
    oid = params.get('oid') or params.get('order_id') or request.query_params.get('order_id')
    task_id = (params.get('tid') if module in ('operations', 'task_recovery') else None) or request.query_params.get('task_id')
    if task_id:
        task = conn.execute('SELECT * FROM driver_tasks WHERE id=%s', (task_id,)).fetchone()
        if not task:
            raise db.error(404, 'MORNING_TASK_NOT_FOUND', 'المهمة غير موجودة ضمن مناوبتك الصباحية.')
        require_shift(scoped, conn.execute('SELECT * FROM shifts WHERE id=%s', (task['shift_id'],)).fetchone())
        oid = task['order_id']
    if oid and not order_allowed(scoped, conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone(), conn):
        raise db.error(404, 'MORNING_ORDER_NOT_FOUND', 'الطلب غير موجود ضمن مناوبتك الصباحية.')
    recheck_resources(conn, scoped, request, module, name)
    return scoped


def order_filter(user, alias='o'):
    """SQL equivalent of order_allowed, for filtering BEFORE limits/aggregates."""
    context = enabled(user)
    if not context:
        return 'TRUE', []
    if alias not in {'o', 'mo'}:
        raise ValueError('Unsupported order alias')
    o = alias
    preferred = (f"COALESCE(NULLIF({o}.snapshot->>'manager_shift_id',''),"
        f"(SELECT NULLIF(ce.details->>'manager_shift_id','') FROM order_events ce WHERE ce.order_id={o}.id "
        "AND ce.event_type='preparation.accepted' AND ce.details ? 'manager_shift_id' "
        "ORDER BY ce.created_at DESC,ce.id DESC LIMIT 1))")
    clause = (f"{o}.brand_id=%s AND {o}.branch_id=ANY(%s) AND {o}.service='delivery' "
        f"AND {o}.snapshot->>'delivery_period'='morning' AND (COALESCE({preferred},%s)=%s "
        f"OR EXISTS(SELECT 1 FROM manager_handovers h WHERE h.status='accepted' AND h.to_shift_id=%s AND h.operational_snapshot->'order_ids' ? {o}.id) "
        f"OR EXISTS(SELECT 1 FROM driver_support_requests r JOIN shifts ds ON ds.id=r.driver_shift_id WHERE r.status='accepted' AND r.to_shift_id=%s AND ds.manager_shift_id=r.to_shift_id AND r.operational_snapshot->'order_ids' ? {o}.id)) "
        f"AND NOT EXISTS (SELECT 1 FROM driver_tasks ct JOIN shifts cs ON cs.id=ct.shift_id WHERE ct.order_id={o}.id "
        "AND ct.status NOT IN ('cancelled','transferred','reassigned') "
        f"AND (cs.manager_shift_id IS DISTINCT FROM %s OR cs.kind<>'driver' OR cs.brand_id IS DISTINCT FROM {o}.brand_id "
        f"OR (cs.delivery_window_snapshot->>'period' IS DISTINCT FROM 'morning' AND NOT EXISTS(SELECT 1 FROM driver_support_requests sr WHERE sr.driver_shift_id=cs.id AND sr.to_shift_id=cs.manager_shift_id AND sr.status='accepted')) OR cs.branch_ids<>jsonb_build_array({o}.branch_id))) "
        f"AND (EXISTS (SELECT 1 FROM driver_tasks ct WHERE ct.order_id={o}.id AND ct.status NOT IN ('cancelled','transferred','reassigned')) "
        f"OR ({o}.completed_at IS NULL AND {o}.preparation_status NOT IN ('completed','cancelled','rejected') "
        f"AND {o}.delivery_status NOT IN ('delivered','cancelled')))")
    return clause, [user['brand_id'], user['branch_ids'], *([context['shift_id']]*5)]


def require_order(conn, user, oid):
    if not order_allowed(user, conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone(), conn):
        raise db.error(404, 'MORNING_ORDER_NOT_FOUND', 'الطلب غير موجود ضمن مناوبتك الصباحية.')


def conversation_filter(user, alias='v'):
    if not enabled(user):
        return 'TRUE', []
    if alias != 'v':
        raise ValueError('Unsupported conversation alias')
    clause, args = order_filter(user, 'mo')
    return ("((v.order_id IS NOT NULL AND EXISTS (SELECT 1 FROM orders mo WHERE mo.id=v.order_id AND "
            + clause + ")) OR (v.order_id IS NULL AND v.brand_id=%s AND v.branch_id=ANY(%s) "
            "AND v.route='delivery' AND v.context_service='delivery' AND v.created_at>="
            "(SELECT opened_at FROM shifts WHERE id=%s)))"), [*args, user['brand_id'], user['branch_ids'], enabled(user)['shift_id']]


def recheck_resources(conn, user, request, module, name):
    """Check path, query AND body references before any idempotent response."""
    import json
    params = request.path_params
    # FastAPI has already read the JSON body before resolving dependencies.
    # This is the cached body only; never read a stream or change its contents.
    body = {}
    raw = getattr(request, '_body', b'')
    if raw and 'application/json' in request.headers.get('content-type', ''):
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, dict):
                body = decoded
        except (ValueError, UnicodeError):
            pass  # FastAPI's existing validation reports malformed JSON.
    for key in ('branch_id', 'receiving_branch_id', 'shift_id', 'from_shift_id', 'to_shift_id',
                'driver_shift_id', 'manager_shift_id', 'order_id'):
        if body.get(key) is not None and (not isinstance(body[key], str) or not 1 <= len(body[key]) <= 200):
            raise db.error(422, 'MORNING_REFERENCE_INVALID', 'مرجع الإجراء غير صالح.')
    for key in ('branch_id', 'receiving_branch_id'):
        if body.get(key) and body[key] not in user['branch_ids']:
            raise db.error(403, 'MORNING_MANAGER_BRANCH', 'الفرع خارج نطاق الصباح.')
    for key in ('shift_id', 'from_shift_id', 'to_shift_id', 'driver_shift_id', 'manager_shift_id'):
        sid = body.get(key)
        if sid:
            if module == 'driver_support' and name == 'request_support' and key == 'driver_shift_id':
                continue  # The endpoint validates the external driver and both live managers.
            require_shift(user, conn.execute('SELECT * FROM shifts WHERE id=%s', (sid,)).fetchone())
    if body.get('order_id'):
        require_order(conn, user, body['order_id'])
    for sid in (request.query_params.get('shift_id'),):
        if sid:
            require_shift(user,conn.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone())
    if module == 'chat':
        from .chat import conversation
        cid = params.get('cid')
        if params.get('aid'):
            row = conn.execute('SELECT conversation_id FROM chat_attachments WHERE id=%s', (params['aid'],)).fetchone()
            cid = (row or {}).get('conversation_id')
            if not cid:
                raise db.error(404, 'MORNING_CONVERSATION_NOT_FOUND', 'المحادثة غير موجودة ضمن مناوبتك.')
        if cid:
            conversation(conn, user, cid)
    if module == 'operations' and name == 'confirm_custody':
        row = conn.execute('SELECT * FROM custody_transfers WHERE id=%s', (params.get('cid'),)).fetchone()
        if not row:
            raise db.error(404, 'MORNING_TRANSFER_NOT_FOUND', 'التسليم غير موجود ضمن مناوبتك.')
        for key in ('from_shift_id', 'to_shift_id'):
            if row.get(key):
                require_shift(user, conn.execute('SELECT * FROM shifts WHERE id=%s', (row[key],)).fetchone())
    if module == 'reporting' and params.get('rid'):
        from .reporting import report
        report(conn, user, params['rid'])
    if module == 'order_support' and params.get('tid'):
        row = conn.execute('SELECT order_id FROM support_tickets WHERE id=%s', (params['tid'],)).fetchone()
        if not row:
            raise db.error(404, 'MORNING_TICKET_NOT_FOUND', 'التذكرة غير موجودة ضمن مناوبتك.')
        require_order(conn, user, row['order_id'])
    if module == 'order_amendments' and params.get('aid'):
        row = conn.execute('SELECT order_id FROM order_amendments WHERE id=%s', (params['aid'],)).fetchone()
        if not row:
            raise db.error(404, 'MORNING_AMENDMENT_NOT_FOUND', 'التعديل غير موجود ضمن مناوبتك.')
        require_order(conn, user, row['order_id'])
    if module == 'operations' and params.get('hid'):
        row = conn.execute('SELECT * FROM manager_handovers WHERE id=%s', (params['hid'],)).fetchone()
        if not row:
            raise db.error(404, 'MORNING_HANDOVER_NOT_FOUND', 'التسليم غير موجود ضمن مناوبتك.')
        target = conn.execute('SELECT * FROM shifts WHERE id=%s', (row['to_shift_id'],)).fetchone()
        require_shift(user, target)
        source = conn.execute('SELECT * FROM shifts WHERE id=%s', (row['from_shift_id'],)).fetchone()
        from .operations import morning_handover_scope
        if not source or source['brand_id'] != user['brand_id'] or set(source['branch_ids']) != set(user['branch_ids']):
            raise db.error(404, 'MORNING_HANDOVER_NOT_FOUND', 'التسليم غير موجود ضمن مناوبتك.')
        morning_handover_scope(conn, source, target)


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
    terminal = (order.get('preparation_status') in ('completed', 'cancelled', 'rejected')
                or order.get('delivery_status') in ('delivered', 'cancelled'))
    if terminal and (user.get('_morning_history_read') or user.get('_morning_cancellation_replay') or user.get('_morning_cancelled_incentive_review')):
        from .order_history_scope import allowed
        if allowed(conn,user,order):return True
    preferred = (order.get('snapshot') or {}).get('manager_shift_id')
    if not preferred:
        accepted = conn.execute("SELECT details->>'manager_shift_id' AS manager_shift_id FROM order_events "
            "WHERE order_id=%s AND event_type='preparation.accepted' AND details ? 'manager_shift_id' "
            "ORDER BY created_at DESC,id DESC LIMIT 1", (order['id'],)).fetchone()
        preferred = (accepted or {}).get('manager_shift_id')
    if preferred and preferred != context['shift_id']:
        from .manager_handover_scope import accepted_order
        if not accepted_order(conn, user, order, shift_id=context['shift_id']):
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
        and ((shift.get('delivery_window_snapshot') or {}).get('period') == 'morning'
             or shift.get('kind') == 'driver' and shift['id'] in context.get('supported_shift_ids', []))
        and (shift['id'] == context['shift_id'] or shift.get('manager_shift_id') == context['shift_id']))


def require_shift(user, shift):
    if not shift_allowed(user, shift):
        raise db.error(404, 'MORNING_SHIFT_NOT_FOUND', 'الشفت غير موجود ضمن مناوبتك الصباحية.')
