"""Compatibility for existing preparation routes over the verified prior app.

Creation is disabled. Existing source and target scopes, branch preparation,
printing and driver pickup continue without rewriting the original sale.
"""
from copy import deepcopy
from typing import Literal
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from . import auth, catalog, db, invoice_profiles
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


def guard_amendment(conn, order, *, allow_routed=False):
    """A revised basket cannot silently invalidate a preparation assignment."""
    route = current_route(conn, order)
    if route and route['target_branch_id'] != order['branch_id']:
        if allow_routed:
            pickup_branch_id(conn, order)
            return
        raise db.error(409, 'PREPARATION_ROUTE_AMENDMENT', 'أعد التحضير لفرع الطلب قبل تعديل السلة؛ التحويل بعد الطباعة له معالجة مستقلة.')



def amendment_route(conn, order, actor, amendment_id, proposal):
    """Retain the exact preparation destination while refreshing its revision.

    A routed basket is revalidated at its physical branch, without repricing or
    changing the original seller. The matching immutable revision must already
    exist; DB guards independently require this new route during order update.
    """
    route = current_route(conn, order)
    if not route or route['target_branch_id'] == order['branch_id']:
        return order.get('preparation_route_id')
    pickup_branch_id(conn, order)
    revision = conn.execute('SELECT * FROM order_revisions WHERE order_id=%s AND amendment_id=%s AND revision=%s',
        (order['id'],amendment_id,order.get('amendment_revision',0)+1)).fetchone()
    if (not revision or revision['after_snapshot'] != proposal['snapshot']
            or revision['after_customer'] != proposal['customer']):
        raise db.error(409,'PREPARATION_ROUTE_AMENDMENT','يلزم اعتماد نفس تعديل الطلب قبل تحديث فرع التحضير.')
    from .collected_order_authority import current_actor
    reviewer = conn.execute('SELECT * FROM users WHERE id=%s', (revision['approved_by'],)).fetchone()
    reviewer = current_actor(conn,reviewer,order['brand_id'],lock=True) if reviewer else None
    target = route['target_branch_id']
    if not reviewer or not _scope(reviewer,order['brand_id'],target):
        raise db.error(403,'PREPARATION_PICKUP_SCOPE','معتمد التعديل لا يغطي فرع التحضير الحالي.')
    branches=conn.execute('SELECT * FROM branches WHERE brand_id=%s AND id=ANY(%s) ORDER BY id FOR SHARE',
        (order['brand_id'],sorted({order['branch_id'],target}))).fetchall()
    by_id={b['id']:b for b in branches}
    brand=conn.execute('SELECT * FROM brands WHERE id=%s AND active FOR SHARE',(order['brand_id'],)).fetchone()
    if not brand or set(by_id)!={order['branch_id'],target}:
        raise db.error(404,'PREPARATION_BRANCH_NOT_FOUND','فرع التحضير غير متاح.')
    revised={**order,'snapshot':proposal['snapshot'],'customer':proposal['customer'],
        **{key:proposal['snapshot'][key] for key in ('subtotal_halalas','delivery_fee_halalas','total_halalas')}}
    options=_candidates(conn,revised,reviewer,by_id[order['branch_id']],by_id[target],brand['settings'],lock=True)
    selected=next((b for b in options if b['id']==target),None)
    if not selected or not selected['available']:
        raise db.error(409,selected['code'] if selected else 'PREPARATION_BRANCH_NOT_FOUND',
            selected['reason'] if selected else 'فرع التحضير غير متاح.')
    identifier=db.new_id('prep_route_')
    evidence={'amendment_id':amendment_id,'previous_route_id':route['id'],
        'after_snapshot':proposal['snapshot'],'after_customer':proposal['customer'],
        'order_snapshot_digest':db.digest(proposal['snapshot']),
        'subtotal_halalas':revised['subtotal_halalas'],'delivery_fee_halalas':revised['delivery_fee_halalas'],
        'total_halalas':revised['total_halalas'],'preserves_preparation_destination':True}
    reason='تحديث نسخة الطلب المعتمدة لفرع التحضير الحالي'
    conn.execute('INSERT INTO order_preparation_routes(id,order_id,brand_id,source_branch_id,previous_branch_id,target_branch_id,'
        'order_version,amendment_revision,requested_by,reason,evidence) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (identifier,order['id'],order['brand_id'],order['branch_id'],target,target,
         order['version']+1,revision['revision'],reviewer['id'],reason,db.json(evidence)))
    _mark_runtime(conn,identifier)
    details={'route_id':identifier,'previous_route_id':route['id'],'amendment_id':amendment_id,
        'preparation_branch_id':target,'source_branch_id':order['branch_id'],
        'revision':revision['revision'],'action_required':'review_approved_revision', 'financial_change':False}
    db.event(conn,order['id'],actor['id'],'preparation.amendment_routed',details)
    db.audit(conn,actor['id'],'order.preparation_amendment_routed','order',order['id'],details)
    return identifier



def _catalog_items(conn, order, *, lock=False):
    items = (order.get('snapshot') or {}).get('items') or []
    requested = {item.get('product_id') for item in items if isinstance(item, dict)}
    if not items or None in requested or len(requested) > 50:
        return [], {}, {}, 'PREPARATION_ITEMS_UNVERIFIED'
    products = conn.execute('SELECT * FROM products WHERE brand_id=%s AND id=ANY(%s)' + (' ORDER BY id FOR SHARE' if lock else ''),
                            (order['brand_id'], sorted(requested))).fetchall()
    if requested != {p['id'] for p in products}:
        return [], {}, {}, 'PREPARATION_ITEMS_UNVERIFIED'
    by_id = {p['id']: p for p in products}
    selected = {}
    for item in items:
        units = item.get('units') or []
        product = by_id[item['product_id']]
        groups = product['settings'].get('option_groups') or []
        if groups and (not units or len(units) != item.get('quantity')):
            return [], {}, {}, 'PREPARATION_VARIANTS_UNVERIFIED'
        for unit in units:
            options = unit.get('options') if isinstance(unit, dict) else None
            if not isinstance(options, dict):
                return [], {}, {}, 'PREPARATION_VARIANTS_UNVERIFIED'
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get('values'), list):
                    return [], {}, {}, 'PREPARATION_VARIANTS_UNVERIFIED'
                code = str(group.get('code') or group.get('id'))
                choices = options.get(code, [])
                choices = [choices] if isinstance(choices, str) else choices
                available_codes = {str(v.get('code') or v.get('id')) for v in group['values'] if isinstance(v, dict)}
                minimum = group.get('min_select', 1 if group.get('required', True) else 0)
                if (not isinstance(choices, list) or type(minimum) is not int or len(choices) < minimum
                        or any(choice not in available_codes for choice in choices)):
                    return [], {}, {}, 'PREPARATION_VARIANTS_UNVERIFIED'
        selected.setdefault(item['product_id'], []).extend(deepcopy(units))
    sources = set()
    for product in products:
        if product['settings'].get('unified_khaliya'):
            sources.update(catalog.KHALIYA_SOURCES.values())
        for group in product['settings'].get('option_groups') or []:
            if isinstance(group, dict):
                sources.update(v['source_product_id'] for v in group.get('values') or []
                               if isinstance(v, dict) and isinstance(v.get('source_product_id'), str))
    if sources - set(by_id):
        extra = conn.execute('SELECT * FROM products WHERE brand_id=%s AND id=ANY(%s)' + (' ORDER BY id FOR SHARE' if lock else ''),
                             (order['brand_id'], sorted(sources - set(by_id)))).fetchall()
        by_id.update({p['id']: p for p in extra})
    # No quoting/pricing function runs: frozen unit and delivery prices survive.
    return products, by_id, selected, None



def _commercial_block(order, target, profiles):
    snapshot = order.get('snapshot') or {}
    frozen = snapshot.get('receipt_profile') or {}
    commercial = snapshot.get('channel_commercial') or {}
    target_entity = profiles.get('assignments', {}).get(target['id'])
    if not target_entity or target_entity not in profiles.get('entities', {}):
        return 'PREPARATION_SELLER_UNKNOWN', 'مالك الفرع وبيانات البائع غير معتمدين.'
    target_seller = profiles['entities'][target_entity]
    if commercial:
        if (commercial.get('settlement_beneficiary') != target_entity or
                commercial.get('branch_owner') != target_entity):
            return 'PREPARATION_SETTLEMENT_MISMATCH', 'الفرع يتبع مستفيد تسوية مختلفًا؛ يلزم إجراء محاسبي مستقل.'
        seller_entity = commercial.get('customer_facing_seller_legal_entity')
        target_seller = profiles.get('entities', {}).get(seller_entity, {})
        if seller_entity != frozen.get('entity_id'):
            return 'PREPARATION_SELLER_MISMATCH', 'هوية بائع الطلب تحتاج مراجعة قبل تحويل التحضير.'
    elif target_entity != frozen.get('entity_id'):
        return 'PREPARATION_SELLER_MISMATCH', 'الفرع يتبع كيان بيع مختلفًا.'
    if (not frozen.get('entity_id') or not frozen.get('legal_name')
            or type(frozen.get('vat_enabled')) is not bool
            or any(frozen.get(k) != target_seller.get(k) for k in ('legal_name', 'vat_enabled', 'vat_number'))):
        return 'PREPARATION_SELLER_MISMATCH', 'بيانات بائع الطلب المحفوظة لا تطابق كيان الفرع الحالي.'
    return None, None



def _candidates(conn, order, user, source, current, brand_settings, *, lock=False):
    rows = conn.execute('SELECT * FROM branches WHERE brand_id=%s ORDER BY branch_no', (order['brand_id'],)).fetchall()
    products, by_id, selected, item_error = _catalog_items(conn, order, lock=lock)
    if lock:
        conn.execute('SELECT brand_id FROM app_settings WHERE brand_id=%s FOR SHARE', (order['brand_id'],)).fetchone()
    profiles = invoice_profiles.settings(conn, order['brand_id'])
    devices = conn.execute('SELECT id,branch_id FROM branch_print_devices WHERE brand_id=%s '
                           'AND revoked_at IS NULL AND token_hash IS NOT NULL AND approved_at IS NOT NULL ORDER BY id' + (' FOR SHARE' if lock else ''),
                           (order['brand_id'],)).fetchall()
    printers = {}
    for device in devices:
        printers[device['branch_id']] = printers.get(device['branch_id'], 0) + 1
    point = (order.get('customer') or {}).get('location')
    result = []
    for branch in rows:
        if not _scope(user, order['brand_id'], branch['id']):
            continue
        code, reason = None, None
        if not source.get('city') or catalog.normalize_city(source['city']) != catalog.normalize_city(branch.get('city')):
            code, reason = 'PREPARATION_CITY_MISMATCH', 'اختر فرعًا داخل محافظة الطلب.'
        elif not catalog.location_verified(branch):
            code, reason = 'BRANCH_LOCATION_UNVERIFIED', 'دبوس الفرع غير معتمد.'
        elif not catalog.valid_location(point):
            code, reason = 'CUSTOMER_LOCATION_UNVERIFIED', 'موقع العميل غير متاح للتحقق من التغطية.'
        else:
            allowed, code, reason = catalog.branch_service(branch, 'delivery', brand_settings=brand_settings)
            if allowed and not catalog.covered(branch['settings'].get('coverage'), point):
                code, reason = 'OUTSIDE_COVERAGE', 'موقع العميل خارج نطاق توصيل الفرع.'
        if not code:
            code, reason = _commercial_block(order, branch, profiles)
        if not code and item_error:
            code, reason = item_error, 'تفاصيل الأصناف أو خياراتها المحفوظة تحتاج مراجعة.'
        if not code:
            if any(catalog.is_khaliya(p) or p['settings'].get('unified_khaliya') for p in products):
                code, reason = 'PREPARATION_ROUTE_FIXED', 'هذه الأصناف لها فرع تنفيذ معتمد مستقل.'
            elif catalog._nearest_unavailable_products(products, by_id, selected, branch, 'delivery'):
                code, reason = 'PRODUCT_UNAVAILABLE', 'بعض أصناف الطلب أو خياراته غير متاحة في هذا الفرع.'
        if not code and not printers.get(branch['id']):
            code, reason = 'PREPARATION_PRINTER_REQUIRED', 'الفرع يحتاج طابعة مرتبطة ومعتمدة.'
        location = branch['settings'].get('location')
        current_location = (current.get('settings') or {}).get('location')
        def distance(origin):
            return round(catalog.direct_distance(origin, location), 1) if catalog.valid_location(origin) and catalog.location_verified(branch) else None
        result.append({**_branch_view(branch), 'available': code is None, 'code': code, 'reason': reason,
            'current': branch['id'] == current['id'], 'approved_printer_count': printers.get(branch['id'], 0),
            'distance_from_branch_meters': distance(current_location) if catalog.location_verified(current) else None,
            'distance_to_customer_meters': distance(point), 'distance_label': catalog.DISTANCE_LABEL})
    result.sort(key=lambda r: (not r['available'], r['distance_from_branch_meters'] is None,
                             r['distance_from_branch_meters'] or 0, r['branch_no']))
    return result



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
    result = {key: order.get(key) for key in ('id', 'brand_id', 'order_no', 'version', 'created_at', 'preparation_status', 'delivery_status', 'amendment_revision')}
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
            revision=order.get('amendment_revision',0)
            if revision and not conn.execute("SELECT 1 FROM order_revision_receipts WHERE order_id=%s AND revision=%s AND kind='branch'",(oid,revision)).fetchone():
                # This existing preparation action confirms the basket currently
                # displayed in the target-only queue. The locked version above
                # prevents an older card from acknowledging a later revision.
                conn.execute("INSERT INTO order_revision_receipts(order_id,revision,actor_id,kind) VALUES(%s,%s,%s,'branch') ON CONFLICT DO NOTHING",(oid,revision,user['id']))
                receipt_details={'kind':'branch','revision':revision,'version':body.version,
                    'route_id':route['id'],'branch_id':route['target_branch_id'],
                    'source':'target_preparation_confirmation','note':'Confirmed displayed approved basket before preparation action'}
                db.event(conn,oid,user['id'],'amendment.received',receipt_details)
                db.audit(conn,user['id'],'amendment.received','order',oid,receipt_details)
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
