"""A single durable order shared by customer, executing branch and delivery staff."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
import hashlib
import json

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response
from .responses import DatabaseJSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from . import auth, catalog, db, performance, service_hours, order_numbering, print_translations, invoice_profiles

router = APIRouter(prefix='/api/orders')


def _response_timestamp_sql(column):
    # Match the existing json.dumps(default=str) contract, including six-digit
    # nonzero microseconds and colon-separated timezone offsets (not JSON's T).
    return (f"CASE WHEN {column} IS NULL THEN NULL ELSE "
            f"to_char({column},'YYYY-MM-DD HH24:MI:SS') || "
            f"CASE WHEN extract(microseconds FROM {column})::bigint %% 1000000 = 0 THEN '' "
            f"ELSE '.' || to_char({column},'US') END || to_char({column},'TZH:TZM') END")


def scoped_order(conn, order_id, user, lock=False):
    row = conn.execute('SELECT * FROM orders WHERE id=%s' + (' FOR UPDATE' if lock else ''), (order_id,)).fetchone()
    # Identical not-found responses avoid confirming identifiers belonging to another scope.
    if not row or not auth.can_access_order(user, row, conn):
        raise db.error(404, 'ORDER_NOT_FOUND', 'الطلب غير موجود ضمن صلاحياتك.')
    return row


def customer_order_view(row):
    result=dict(row)
    result.pop('performance_snapshot',None)
    result['snapshot'] = catalog.customer_quote_view(row['snapshot'])
    if row['snapshot'].get('internal_routing'):
        result['branch_id'] = result['snapshot']['branch_id']
        if 'branch_name' in result:
            result['branch_name'] = result['snapshot']['branch_name']
        if 'branch_no' in result:
            result['branch_no'] = result['snapshot']['branch_no']
    result['customer']={key:value for key,value in row['customer'].items()
                        if key not in {'risk_alert','risk_reasons','requires_operational_confirmation'}}
    return result


def _ensure_manager_duty(conn, brand_id, branch_id):
    # Operations router provides the canonical manager shift; absence is a launch blocker.
    table = conn.execute("SELECT to_regclass('shifts') AS name").fetchone()['name']
    if not table:
        raise db.error(409, 'DELIVERY_DUTY_UNAVAILABLE', 'لا توجد مناوبة توصيل مهيأة لاستقبال الطلبات.')
    # Exact operations schema is shared with the delivery implementation.
    exists = conn.execute("SELECT 1 FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.brand_id=%s AND s.kind='manager' AND s.status='open' "
                          "AND s.branch_ids ? %s AND u.brand_id=s.brand_id AND u.active AND u.role IN ('owner','delivery_manager') "
                          "AND (u.role='owner' OR u.branch_ids ? %s) FOR SHARE OF s,u",
                          (brand_id, branch_id, branch_id)).fetchone()
    if not exists:
        raise db.error(409, 'DELIVERY_DUTY_UNAVAILABLE', 'خدمة التوصيل مغلقة حاليًا لعدم وجود مناوبة مستلمة.')


@router.post('')
def create_order(body: catalog.QuoteBody, request: Request = None, user=Depends(auth.require_roles('customer')), idempotency_key: str | None = Header(default=None)):
    return _create_order(body, request, user, idempotency_key)


class DeliveryCenterCustomer(catalog.CustomerInfo):
    name: str = Field(default='عميل', max_length=100)

    @field_validator('name', mode='before')
    @classmethod
    def default_contact_name(cls, value):
        return value.strip() or 'عميل' if isinstance(value, str) else value


class DeliveryCenterOrder(catalog.QuoteBody):
    customer: DeliveryCenterCustomer


@router.post('/delivery-center')
def create_delivery_center_order(body: DeliveryCenterOrder, request: Request = None,
                                 user=Depends(auth.require_roles('owner','delivery_manager')),
                                 idempotency_key: str | None = Header(default=None)):
    return _create_order(body, request, user, idempotency_key, delivery_center=True)


def delivery_center_scope(conn, user, branch_id):
    if user['role'] not in {'owner','delivery_manager'} or (user['role'] != 'owner' and branch_id not in user.get('branch_ids', [])):
        raise db.error(403, 'BRANCH_FORBIDDEN', 'الفرع خارج نطاق مدير التوصيل.')
    shift = conn.execute("SELECT id FROM shifts WHERE brand_id=%s AND user_id=%s AND kind='manager' AND status='open' AND branch_ids ? %s FOR SHARE",
                         (user['brand_id'],user['id'],branch_id)).fetchone()
    if not shift:
        raise db.error(409,'MANAGER_SHIFT_REQUIRED','افتح شفت مدير توصيل يغطي فرع الطلب أولًا.')
    return shift['id']


def _create_order(body, request, user, idempotency_key, *, delivery_center=False):
    if user['brand_id'] != body.brand_id:
        raise db.error(403, 'BRAND_FORBIDDEN', 'حساب العميل لا يتبع هذا البراند.')
    with db.tx(pipeline=True) as conn:
        # The fresh server-session read carries this policy. Replays still
        # require identity proof without repeating the full settings read.
        phone_required=False if delivery_center else user.get('customer_phone_required')
        if phone_required is None:phone_required=auth.customer_phone_required(conn,body.brand_id)
        if delivery_center:
            if body.service != 'delivery':
                raise db.error(422,'DELIVERY_ONLY','طلب مركز التوصيل مخصص للتوصيل.')
            if not body.customer.phone:
                raise db.error(422,'CUSTOMER_PHONE_REQUIRED','أدخل رقم اتصال العميل.')
            # Replays must satisfy today's branch and duty scope before reading
            # a saved response. Never let idempotency retain revoked access.
            delivery_center_scope(conn,user,body.branch_id)
        else:
            auth.assert_customer_order_eligible(conn,user,phone_required=phone_required)
        def perform():
            config=conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(body.brand_id,)).fetchone()
            if not db.rate_limit(conn,'order-principal:'+user['id'],100 if delivery_center else 10,600):
                raise db.error(429,'ORDER_RATE_LIMIT','أرسلت طلبات كثيرة. انتظر قليلًا قبل طلب جديد.')
            if request and not db.rate_limit(conn,'order-ip:'+(request.client.host if request.client else 'unknown'),200,600):
                raise db.error(429,'ORDER_RATE_LIMIT','طلبات كثيرة مؤقتًا. حاول بعد قليل.')
            if delivery_center:delivery_center_scope(conn,user,body.branch_id)
            quote = catalog.quote_order(conn, body, for_update=True)
            execution_branch_id = quote['branch_id']
            if body.expected_total_halalas is not None and quote['total_halalas'] != body.expected_total_halalas:
                raise db.error(409, 'PRICE_CHANGED', 'تغير سعر الطلب. راجع الإجمالي الجديد وأكد موافقتك.')
            if body.service == 'delivery' and (config or {}).get('settings', {}).get('delivery_workflow') != 'branch_without_manager':
                _ensure_manager_duty(conn, body.brand_id, execution_branch_id)
            customer = body.customer.model_dump(exclude_none=True)
            if body.service == 'delivery':
                customer['neighborhood'] = customer['neighborhood'].strip()
            # Contact is not ownership proof; only the opaque authenticated customer ID owns the order.
            supplied_phone = customer.pop('phone',None)
            customer['phone'] = auth.normalize_phone(supplied_phone) if supplied_phone else user.get('phone')
            if not customer['phone']:
                raise db.error(422,'CUSTOMER_PHONE_REQUIRED','أدخل رقم اتصال صحيحًا قبل الطلب.')
            if phone_required and customer['phone'] != user.get('phone'):
                raise db.error(403,'CUSTOMER_PHONE_VERIFICATION_REQUIRED','تحقق من الرقم الجديد قبل الطلب.')
            if body.service=='delivery' and phone_required:
                address_key='addr_'+hashlib.sha256((user['id']+json.dumps({k:customer.get(k) for k in ('address','neighborhood','location')},sort_keys=True)).encode()).hexdigest()
                loc=customer.get('location') or {}
                conn.execute('INSERT INTO customer_addresses(id,customer_id,brand_id,label,address,neighborhood,lat,lng) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET updated_at=now()', (address_key,user['id'],user['brand_id'],customer['neighborhood'],customer['address'],customer['neighborhood'],loc.get('lat'),loc.get('lng')))
            customer_owner_id=user['id']
            if delivery_center:
                manager_shift_id=delivery_center_scope(conn,user,execution_branch_id)
                # An opaque contact principal owns this order. A typed phone number
                # never grants access to an existing customer's account or history.
                customer_owner_id=operational_contact(conn,body.brand_id,customer,user['id'])
                customer.update(identity_kind='guest',phone_verified=False,phone_sms_verified=False,phone_whatsapp_verified=False)
                quote.update(order_source='delivery_center',created_by_staff_id=user['id'],manager_shift_id=manager_shift_id,registration_source='delivery')
            else:
                customer.update(auth.identity_snapshot(user))
            if supplied_phone and customer['phone'] != user.get('phone'):
                for flag in ('phone_verified','phone_sms_verified','phone_whatsapp_verified'):customer[flag]=False
            if user.get('identity_kind') == 'guest':
                recent=conn.execute("SELECT 1 FROM orders WHERE brand_id=%s AND branch_id=%s AND customer->>'phone'=%s AND snapshot->'items'=%s AND created_at>now()-interval '15 minutes' LIMIT 1",
                                    (body.brand_id,execution_branch_id,customer['phone'],db.json(quote['items']))).fetchone()
                threshold=(config['settings'] if config else {}).get('guest_operational_confirmation_above_halalas')
                customer['risk_alert']=bool(recent)
                customer['risk_reasons']=['similar_recent_unverified_contact_order'] if recent else []
                customer['requires_operational_confirmation']=type(threshold) is int and quote['total_halalas']>=threshold
            quote['receipt_occasion_style'] = (config or {}).get('settings', {}).get('receipt_occasion_style', {})
            quote['receipt_occasion_text'] = (config or {}).get('settings', {}).get('receipt_occasion_text', '')
            quote['delivery_workflow'] = (config or {}).get('settings', {}).get('delivery_workflow', 'branch_fulfillment')
            # Read immutable receipt/policy snapshots before the per-branch
            # counter. The allocation remains in this same atomic transaction,
            # while its mandatory serial section contains only durable writes.
            quote['receipt_profile'] = invoice_profiles.capture(
                conn, body.brand_id, execution_branch_id, brand_settings=(config or {}).get('settings', {}),
                channel='kps_order' if body.service == 'delivery' else body.service)
            if body.brand_id == 'kps' and body.service == 'delivery':
                from . import channel_commercial
                profiles = (config or {}).get('settings', {}).get('invoice_profiles') or invoice_profiles.defaults('kps')
                quote['channel_commercial'] = channel_commercial.capture(profiles, execution_branch_id, quote, user['id'])
            performance_snapshot = performance.capture_policy(conn, body.brand_id, execution_branch_id)
            order_id = db.new_id('order_')
            delivery_day = service_hours.delivery_operational_date() if body.service == 'delivery' else None
            if delivery_day:
                quote['delivery_operating_day'] = {'date': delivery_day.isoformat(), 'start': '16:00',
                                                    'timezone': 'Asia/Riyadh'}
            # Allocate the canonical number and insert in one database command.
            # No Python round trip holds the counter before saving the order.
            # These branch fields come from the quote's authoritative SHARE read.
            prefix = order_numbering.reference_prefix({'city': quote['branch_city'], 'branch_no': quote['branch_no']})
            # Prepare the canonical privacy projection before acquiring the
            # branch counter; do not duplicate routing/privacy rules in SQL.
            public = customer_order_view({'snapshot': quote, 'customer': customer, 'branch_id': execution_branch_id})
            ctes = ('next_number AS (' + order_numbering.NEXT_NUMBER_SQL + '), created_order AS ('
                               'INSERT INTO orders(id,brand_id,branch_id,customer_id,service,delivery_status,payment_method,'
                               'subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer,performance_snapshot,delivery_operational_date) '
                               "SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,jsonb_set(%s::jsonb,'{order_reference}',"
                               "to_jsonb(%s::text || lpad(last_number::text,greatest(4,length(last_number::text)),'0'))),"
                               '%s,%s,%s FROM next_number RETURNING *), '
                    'created_event AS (INSERT INTO order_events(id,order_id,actor_id,event_type,details) '
                    "SELECT %s,id,%s,'order.created',%s FROM created_order RETURNING id), "
                    'created_audit AS (INSERT INTO audit_events(id,actor_id,action,entity_type,entity_id,details) '
                    "SELECT %s,%s,'order.created','order',id,%s FROM created_order RETURNING id), "
                    "operation_response AS (SELECT jsonb_build_object('order',"
                    "(to_jsonb(o)-'performance_snapshot') || jsonb_build_object("
                    "'branch_id',%s::text,'customer',%s::jsonb,"
                    "'snapshot',jsonb_set(%s::jsonb,'{order_reference}',o.snapshot->'order_reference'),"
                    "'created_at'," + _response_timestamp_sql('o.created_at') + ","
                    "'arrival_at'," + _response_timestamp_sql('o.arrival_at') + ","
                    "'completed_at'," + _response_timestamp_sql('o.completed_at') +
                    ')) AS response FROM created_order o)')
            return db.AtomicIdempotentWrite(ctes,
                               (execution_branch_id, order_id, body.brand_id, execution_branch_id, customer_owner_id, body.service,
                                'pending' if body.service == 'delivery' else 'not_applicable', body.payment_method,
                                quote['subtotal_halalas'], quote['delivery_fee_halalas'], quote['total_halalas'],
                                db.json(quote), prefix, db.json(customer), db.json(performance_snapshot), delivery_day,
                                db.new_id('evt_'), user['id'], db.json({'service': body.service}),
                                db.new_id('audit_'), user['id'], db.json({'branch_id': execution_branch_id,
                                    'selected_branch_id': body.branch_id, 'routing_reason': (quote.get('internal_routing') or {}).get('reason'),
                                    'total_halalas': quote['total_halalas']}),
                                public['branch_id'], db.json(public['customer']), db.json(public['snapshot'])))
        result = db.idempotent(conn, user['id'], idempotency_key, 'orders.delivery_center' if delivery_center else 'orders.create', body.model_dump(), perform,
                               pipeline_writes=True, defer_atomic_response=True,encoded_response=request is not None)
    # The database acknowledges COMMIT before the saved JSON reaches HTTP.
    # Replays use the same durable JSON, with no new counter/event/financial write.
    if isinstance(result, db.DeferredAtomicResponse):
        result = result.resolve()
    if delivery_center:
        # Saved responses use the customer-safe branch projection; inspect the
        # durable order to reauthorize its actual execution branch as well.
        saved=json.loads(result) if isinstance(result,str) else result
        if isinstance(saved,dict) and saved.get('order',{}).get('id'):
            with db.tx() as conn:
                persisted=scoped_order(conn,saved['order']['id'],user)
                delivery_center_scope(conn,user,persisted['branch_id'])
    return Response(result,media_type='application/json') if request is not None else result



@router.get('')
def list_orders(user=Depends(auth.current_user), limit: int = Query(default=30, ge=1, le=100),
                offset: int = Query(default=0, ge=0, le=100000), branch_id: str | None = Query(default=None, max_length=80),
                service: str | None = Query(default=None, max_length=20), status: str | None = Query(default=None, max_length=30),
                period: str | None = Query(default=None, max_length=20), start: str | None = Query(default=None, max_length=50),
                end: str | None = Query(default=None, max_length=50),
                sort: str | None = Query(default=None, max_length=20),
                neighborhood: str | None = Query(default=None, max_length=120),
                driver_id: str | None = Query(default=None, max_length=80),
                search: str | None = Query(default=None, max_length=80),
                manager_view: str | None = None,
                customer_view: Literal['active','history','cancelled'] | None = None):
    conditions, params = ['o.brand_id=%s'], [user['brand_id']]
    if user['role'] == 'customer':
        conditions.append('o.customer_id=ANY(%s)')
        params.append(auth.customer_ids(user))
    elif user['role'] == 'branch_manager':
        conditions.append('o.branch_id=ANY(%s)')
        params.append(user['branch_ids'])
    elif user['role'] in {'delivery_manager', 'delivery_staff'}:
        conditions.append("o.service='delivery'")
        conditions.append('o.branch_id=ANY(%s)')
        params.append(user['branch_ids'])
    elif user['role'] == 'driver':
        conditions.append("o.service='delivery'")
        conditions.append('o.branch_id=ANY(%s)')
        params.append(user['branch_ids'])
        conditions.append("EXISTS(SELECT 1 FROM driver_tasks t WHERE t.order_id=o.id AND t.driver_id=%s AND t.status NOT IN ('cancelled','reassigned','transferred','delivered'))")
        params.append(user['id'])
    elif user['role'] != 'owner':
        raise db.error(403, 'ROLE_FORBIDDEN', 'الوصول غير مسموح.')
    if customer_view:
        if user['role'] != 'customer':
            raise db.error(403, 'ROLE_FORBIDDEN', 'هذا العرض مخصص للعميل.')
        cancelled="(o.preparation_status='cancelled' OR o.delivery_status='cancelled')"
        completed="(o.preparation_status='completed' OR o.delivery_status='delivered')"
        conditions.append(cancelled if customer_view=='cancelled' else
                          f'NOT {cancelled} AND {completed}' if customer_view=='history' else
                          f'NOT {cancelled} AND NOT {completed}')
    if branch_id:
        conditions.append('o.branch_id=%s')
        params.append(branch_id)
    if service:
        if service not in {'delivery', 'pickup', 'car'}:
            raise db.error(422, 'SERVICE_INVALID', 'نوع الخدمة غير صحيح.')
        conditions.append('o.service=%s')
        params.append(service)
    if status:
        if status == 'active':
            conditions.append("o.preparation_status NOT IN ('completed','cancelled') AND o.delivery_status NOT IN ('delivered','cancelled')")
        else:
            conditions.append('(o.preparation_status=%s OR o.delivery_status=%s)')
            params.extend([status,status])
    if neighborhood:
        conditions.append("o.customer->>'neighborhood' ILIKE %s")
        params.append('%'+neighborhood.replace('%','\\%').replace('_','\\_')+'%')
    if driver_id:
        conditions.append("EXISTS(SELECT 1 FROM driver_tasks td WHERE td.order_id=o.id AND td.driver_id=%s AND td.status<>'transferred')")
        params.append(driver_id)
    if search:
        conditions.append("(o.order_no::text=%s OR o.id=%s OR o.snapshot->>'order_reference'=%s)")
        params.extend([search.strip(),search.strip(),search.strip()])
    sort = sort or ('newest' if user['role']=='customer' else 'active_oldest')
    if sort not in {'newest','oldest','active_oldest'}:
        raise db.error(422,'ORDER_SORT_INVALID','ترتيب الطلبات غير صحيح.')
    ordering = {'newest':'o.created_at DESC,o.order_no DESC', 'oldest':'o.created_at ASC,o.order_no ASC',
                'active_oldest':"CASE WHEN o.preparation_status NOT IN ('completed','cancelled') AND o.delivery_status NOT IN ('delivered','cancelled') THEN 0 ELSE 1 END,o.created_at ASC,o.order_no ASC"}[sort]
    with db.tx() as conn:
        if user['role'] == 'driver' and not conn.execute("SELECT to_regclass('driver_tasks') AS n").fetchone()['n']:
            return {'orders': [], 'next_offset': None}
        period_info = None
        workspace_shift = None
        if manager_view:
            if user['role']!='delivery_manager' or manager_view not in ('current','history'):
                raise db.error(403,'MANAGER_WORKSPACE_SCOPE','عرض شفت المدير غير متاح لهذا الحساب.')
            from . import manager_workday
            workspace_shift=manager_workday.current_shift(conn,user)
            predicate, workspace_params=manager_workday.current_orders(workspace_shift,user)
            if manager_view=='current':
                conditions.append(predicate)
                params.extend(workspace_params)
                # Current work must survive midnight and missing/old date filters.
                period=start=end=None
            else:
                conditions.append('NOT ('+predicate+')')
                params.extend(workspace_params)
                conditions.append('NOT ('+manager_workday.ACTIVE+')')
        if period or start or end:
            if period not in {None, 'today', 'yesterday', 'custom'}:
                raise db.error(422, 'PERIOD_INVALID', 'الفترة غير صحيحة.')
            config = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s', (user['brand_id'],)).fetchone()
            clock = (config['settings'] if config else {}).get('operational_day_start')
            configured = bool(clock)
            minutes = catalog._minute(clock) if configured else 0
            if service=='delivery' or user['role'] in {'delivery_manager','delivery_staff','driver'}:
                minutes,configured=service_hours.DELIVERY_DAY_START_MINUTES,True
            day = (service_hours.now_riyadh() - timedelta(minutes=minutes)).date()
            if period == 'yesterday':
                day -= timedelta(days=1)
            if period == 'custom' and (not start or not end):
                raise db.error(422, 'PERIOD_DATES_REQUIRED', 'حدد بداية ونهاية الفترة.')
            def parse(value):
                parsed = datetime.fromisoformat(value)
                return parsed.astimezone(catalog.RIYADH) if parsed.tzinfo else parsed.replace(tzinfo=catalog.RIYADH)
            try:
                low = parse(start) if start else datetime.combine(day, datetime.min.time(), catalog.RIYADH) + timedelta(minutes=minutes)
                high = parse(end) if end else low + timedelta(days=1)
            except ValueError:
                raise db.error(422, 'PERIOD_DATE_INVALID', 'صيغة التاريخ غير صحيحة.') from None
            if high <= low or high - low > timedelta(days=93):
                raise db.error(422, 'PERIOD_RANGE_INVALID', 'الفترة يجب أن تكون أكبر من صفر وألا تتجاوز 93 يومًا.')
            conditions.extend(['o.created_at>=%s', 'o.created_at<%s'])
            params.extend([low, high])
            period_info = {'start': low.isoformat(), 'end': high.isoformat(), 'operational_day_configured': configured}
        rows = conn.execute('SELECT o.*,b.name branch_name,b.branch_no FROM orders o JOIN branches b ON b.id=o.branch_id WHERE ' + ' AND '.join(conditions) + ' ORDER BY ' + ordering + ' LIMIT %s OFFSET %s',
                            (*params, limit + 1, offset)).fetchall()
        from .branch_payments import enrich_orders as enrich_payments
        enrich_payments(conn, rows[:limit])
        if user['role']!='customer':
            performance.enrich_orders(conn,rows[:limit])
        else:
            rows=[customer_order_view(row) for row in rows]
    return {'sort':sort, 'orders': rows[:limit], 'next_offset': offset + limit if len(rows) > limit else None, 'period': period_info}


def operational_contact(conn,brand_id,customer,actor_id):
    # Separate from authenticated identities: no sessions, links or proof are
    # created from staff-typed contact data. Concurrent orders reuse one opaque
    # operational principal; verified users are never selected or merged by phone.
    identifier='customer_contact_'+hashlib.sha256((brand_id+'\0'+customer['phone']).encode()).hexdigest()
    row=conn.execute("INSERT INTO users(id,display_name,phone,role,brand_id,identity_kind) VALUES(%s,%s,%s,'customer',%s,'guest') ON CONFLICT(id) DO UPDATE SET updated_at=users.updated_at RETURNING id,active,identity_kind,brand_id",
        (identifier,customer['name'],customer['phone'],brand_id)).fetchone()
    if not row or not row['active'] or row['identity_kind']!='guest' or row['brand_id']!=brand_id:
        raise db.error(409,'CONTACT_UNAVAILABLE','تعذر استخدام سجل الاتصال التشغيلي.')
    return row['id']



@router.get('/{order_id}')
def get_order(order_id: str, user=Depends(auth.current_user), request: Request = None):
    with db.tx(pipeline=user['role'] == 'customer') as conn:
        row = scoped_order(conn, order_id, user)
        if user['role'] == 'customer':
            events = conn.execute('SELECT id,event_type,details,created_at FROM order_events WHERE order_id=%s ORDER BY created_at,id', (order_id,)).fetchall()
        else:
            events = conn.execute('SELECT e.id,e.event_type,e.details,e.created_at,e.actor_id,u.display_name AS actor_name '
                                  'FROM order_events e LEFT JOIN users u ON u.id=e.actor_id '
                                  'WHERE e.order_id=%s ORDER BY e.created_at,e.id', (order_id,)).fetchall()
        from .branch_payments import enrich_orders as enrich_payments
        enrich_payments(conn, [row])
        if user['role']!='customer':
            performance.enrich_orders(conn,[row])
            row['print_labels_en']=print_translations.for_order(conn,row)
            row['receipt_qr']=invoice_profiles.ordinary_qr(row)
            from .fiscal_issuance import original
            from .fiscal import print_order
            doc=original(conn,order_id)
            if doc:
                row['issued_receipt']={**print_order(doc,row), 'payment_status':row['payment_status'], 'payment_method':row['payment_method'], 'preparation_status':row['preparation_status']}
        else:
            row=customer_order_view(row)
            for event in events:
                event['details'] = {key: value for key, value in (event.get('details') or {}).items()
                                    if key not in {'actor_name', 'actor_id', 'actor_role'}}
    result = {'order': row, 'events': events}
    return DatabaseJSONResponse(result) if request is not None else result



class PreparationBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: str = Field(min_length=1, max_length=30)
    version: StrictInt = Field(ge=1)
    reason: str = Field(default='', max_length=500)


@router.post('/{order_id}/preparation')
def preparation(order_id: str, body: PreparationBody, user=Depends(auth.require_roles('owner', 'branch_manager', 'delivery_manager')),
                idempotency_key: str | None = Header(default=None)):
    if body.status == 'cancelled' and not auth.has_permission(user, 'orders_cancel'):
        raise db.error(403, 'STAFF_PERMISSION_FORBIDDEN', 'صلاحية رفض وإلغاء الطلبات متوقفة لحسابك.')
    with db.tx() as conn:
        # Replay returns a saved response, so fresh scope authorization must precede replay lookup.
        visible = scoped_order(conn, order_id, user)
        if user['role']=='delivery_manager' and (body.status!='cancelled' or visible['service']!='delivery'):
            raise db.error(403,'DELIVERY_DECISION_ONLY','مدير التوصيل يرفض طلب التوصيل أو يقبله مع إسناد سائق؛ تجهيز الفرع له إجراء مستقل.')
        def perform():
            order = scoped_order(conn, order_id, user, lock=True)
            if order['version'] != body.version:
                raise db.error(409, 'ORDER_VERSION_CONFLICT', 'تغير الطلب. حدّث الصفحة قبل تنفيذ العملية.')
            current = order['preparation_status']
            if body.status != 'cancelled':
                from .order_amendments import guard
                guard(conn,order)
            transitions = {'new': {'accepted', 'cancelled'}, 'accepted': {'preparing', 'cancelled'},
                           'preparing': {'ready', 'cancelled'}, 'ready': {'handed_over', 'completed', 'cancelled'}}
            if body.status not in transitions.get(current, set()):
                raise db.error(409, 'PREPARATION_TRANSITION_INVALID', 'انتقال حالة التجهيز غير مسموح.')
            if body.status in {'preparing','ready','completed'}:
                from .branch_payments import require_verified
                require_verified(conn, order)
            if body.status in {'accepted','preparing'} and order['customer'].get('requires_operational_confirmation'):
                if not conn.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='order.operational_confirmation' AND details->>'result'='reached'",(order_id,)).fetchone():
                    raise db.error(409,'OPERATIONAL_CONTACT_CONFIRMATION_REQUIRED','تتطلب سياسة الطلب اتصالًا تشغيليًا موثقًا قبل التجهيز. لا يعتبر ذلك توثيق جوال.')
            if body.status == 'cancelled':
                if not body.reason.strip():
                    raise db.error(422, 'CANCELLATION_REASON_REQUIRED', 'اكتب سبب رفض أو إلغاء الطلب.')
                from .order_amendments import balance as paid_balance
                if order['payment_status'] not in {'unpaid', 'refunded'} or paid_balance(conn,order)>0:
                    raise db.error(409, 'REFUND_WORKFLOW_REQUIRED', 'الطلب محصل. يلزم مسار الاسترداد المعتمد قبل الإلغاء.')
                if conn.execute("SELECT to_regclass('driver_tasks') AS n").fetchone()['n']:
                    if conn.execute("SELECT 1 FROM driver_tasks WHERE order_id=%s AND status NOT IN ('cancelled','reassigned','delivered','transferred')", (order_id,)).fetchone():
                        raise db.error(409, 'DELIVERY_TASK_ACTIVE', 'نسّق إلغاء المهمة القائمة مع فريق التوصيل قبل الإلغاء.')
            if body.status == 'completed':
                if order['service'] == 'delivery':
                    raise db.error(409, 'DRIVER_DELIVERY_REQUIRED', 'تأكيد تسليم طلب التوصيل من مهمة السائق.')
                if order['payment_status'] != 'paid':
                    raise db.error(409, 'PAYMENT_NOT_COLLECTED', 'سجل التحصيل الفعلي قبل تسليم الطلب.')
            if body.status == 'handed_over':
                if order['service'] != 'delivery':
                    raise db.error(409, 'HANDOVER_NOT_APPLICABLE', 'التسليم للسائق يخص طلبات التوصيل فقط.')
                if not conn.execute("SELECT to_regclass('driver_tasks') AS n").fetchone()['n'] or not conn.execute(
                        "SELECT 1 FROM driver_tasks WHERE order_id=%s AND status IN ('accepted','picked_up')", (order_id,)).fetchone():
                    raise db.error(409, 'DRIVER_ACCEPTANCE_REQUIRED', 'يلزم سائق مسند وقابل للمهمة قبل التسليم.')
            if body.status == 'cancelled':
                from .fiscal_issuance import credit_on_cancellation
                credit_on_cancellation(conn,order,user,body.reason)
            row = conn.execute("UPDATE orders SET preparation_status=%s,version=version+1,completed_at=CASE WHEN %s='completed' THEN now() ELSE completed_at END "
                               "WHERE id=%s RETURNING *", (body.status, body.status, order_id)).fetchone()
            if body.status == 'cancelled' and order['service'] == 'delivery':
                conn.execute("UPDATE orders SET delivery_status='cancelled' WHERE id=%s", (order_id,))
                row['delivery_status'] = 'cancelled'
            db.event(conn, order_id, user['id'], 'preparation.' + body.status,
                     {'previous': current, 'status': body.status, 'reason': body.reason.strip(),
                      'branch_id': order['branch_id'], 'actor_id': user['id'],
                      'actor_name': user.get('display_name', ''), 'actor_role': user['role']})
            db.audit(conn, user['id'], 'order.preparation', 'order', order_id, {'from': current, 'to': body.status, 'reason': body.reason})
            return {'order': row}
        return db.idempotent(conn, user['id'], idempotency_key, 'order.preparation:' + order_id, body.model_dump(), perform)



class ArrivalBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: StrictInt = Field(ge=1)


@router.post('/{order_id}/arrival')
def arrival(order_id: str, body: ArrivalBody, user=Depends(auth.require_roles('customer')), idempotency_key: str | None = Header(default=None)):
    with db.tx() as conn:
        scoped_order(conn, order_id, user)
        def perform():
            order = scoped_order(conn, order_id, user, lock=True)
            if order['service'] != 'car' or order['preparation_status'] in {'completed', 'cancelled'}:
                raise db.error(409, 'ARRIVAL_NOT_APPLICABLE', 'الوصول يخص طلب سيارة غير منتهٍ.')
            if order['version'] != body.version:
                raise db.error(409, 'ORDER_VERSION_CONFLICT', 'تغير الطلب. حدّث الصفحة.')
            if order['arrival_at']:
                return {'order': customer_order_view(order)}
            row = conn.execute('UPDATE orders SET arrival_at=now(),version=version+1 WHERE id=%s RETURNING *', (order_id,)).fetchone()
            db.event(conn, order_id, user['id'], 'customer.arrived')
            return {'order': customer_order_view(row)}
        return db.idempotent(conn, user['id'], idempotency_key, 'order.arrival:' + order_id, body.model_dump(), perform)


class ContactConfirmationBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    result: str = Field(pattern='^(reached|unreachable)$')
    reason: str = Field(min_length=1,max_length=500)


@router.post('/{order_id}/operational-confirmation')
def operational_confirmation(order_id:str,body:ContactConfirmationBody,
                             user=Depends(auth.require_roles('owner','branch_manager','delivery_manager')),
                             idempotency_key:str|None=Header(default=None)):
    if not body.reason.strip():
        raise db.error(422,'CONTACT_CONFIRMATION_REASON_REQUIRED','اكتب نتيجة الاتصال التشغيلي.')
    with db.tx() as conn:
        scoped_order(conn,order_id,user)
        def perform():
            order=scoped_order(conn,order_id,user,lock=True)
            if order['preparation_status'] in {'completed','cancelled'} or order['delivery_status'] in {'delivered','cancelled'}:
                raise db.error(409,'ORDER_COMPLETED','الطلب منتهٍ.')
            db.event(conn,order_id,user['id'],'order.operational_confirmation',body.model_dump())
            db.audit(conn,user['id'],'order.operational_confirmation','order',order_id,body.model_dump())
            return {'order_id':order_id,'result':body.result,'phone_verified':bool(order['customer'].get('phone_verified')),
                    'operational_confirmation_only':True}
        return db.idempotent(conn,user['id'],idempotency_key,'order.operational_confirmation:'+order_id,body.model_dump(),perform)
