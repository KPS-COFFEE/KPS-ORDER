"""A single durable order shared by customer, executing branch and delivery staff."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from . import auth, catalog, db, performance, service_hours, order_numbering, print_translations, invoice_profiles

router = APIRouter(prefix='/api/orders')


def scoped_order(conn, order_id, user, lock=False):
    row = conn.execute('SELECT * FROM orders WHERE id=%s' + (' FOR UPDATE' if lock else ''), (order_id,)).fetchone()
    # Identical not-found responses avoid confirming identifiers belonging to another scope.
    if not row or not auth.can_access_order(user, row, conn):
        raise db.error(404, 'ORDER_NOT_FOUND', 'الطلب غير موجود ضمن صلاحياتك.')
    return row


def customer_order_view(row):
    result=dict(row)
    result.pop('performance_snapshot',None)
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
    if user['brand_id'] != body.brand_id:
        raise db.error(403, 'BRAND_FORBIDDEN', 'حساب العميل لا يتبع هذا البراند.')
    with db.tx() as conn:
        auth.assert_customer_order_eligible(conn,user)
        def perform():
            if not db.rate_limit(conn,'order-principal:'+user['id'],10,600):
                raise db.error(429,'ORDER_RATE_LIMIT','أرسلت طلبات كثيرة. انتظر قليلًا قبل طلب جديد.')
            if request and not db.rate_limit(conn,'order-ip:'+(request.client.host if request.client else 'unknown'),200,600):
                raise db.error(429,'ORDER_RATE_LIMIT','طلبات كثيرة مؤقتًا. حاول بعد قليل.')
            quote = catalog.quote_order(conn, body, for_update=True)
            if body.expected_total_halalas is not None and quote['total_halalas'] != body.expected_total_halalas:
                raise db.error(409, 'PRICE_CHANGED', 'تغير سعر الطلب. راجع الإجمالي الجديد وأكد موافقتك.')
            if body.service == 'delivery':
                _ensure_manager_duty(conn, body.brand_id, body.branch_id)
            customer = body.customer.model_dump(exclude_none=True)
            if body.service == 'delivery':
                customer['neighborhood'] = customer['neighborhood'].strip()
            # Contact is not ownership proof; only the opaque authenticated customer ID owns the order.
            supplied_phone = customer.pop('phone',None)
            customer['phone'] = auth.normalize_phone(supplied_phone) if supplied_phone else user.get('phone')
            if not customer['phone']:
                raise db.error(422,'CUSTOMER_PHONE_REQUIRED','أدخل رقم اتصال صحيحًا قبل الطلب.')
            customer.update(auth.identity_snapshot(user))
            if supplied_phone and customer['phone'] != user.get('phone'):
                for flag in ('phone_verified','phone_sms_verified','phone_whatsapp_verified'):customer[flag]=False
            if user.get('identity_kind') == 'guest':
                recent=conn.execute("SELECT 1 FROM orders WHERE brand_id=%s AND branch_id=%s AND customer->>'phone'=%s AND snapshot->'items'=%s AND created_at>now()-interval '15 minutes' LIMIT 1",
                                    (body.brand_id,body.branch_id,customer['phone'],db.json(quote['items']))).fetchone()
                config=conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(body.brand_id,)).fetchone()
                threshold=(config['settings'] if config else {}).get('guest_operational_confirmation_above_halalas')
                customer['risk_alert']=bool(recent)
                customer['risk_reasons']=['similar_recent_unverified_contact_order'] if recent else []
                customer['requires_operational_confirmation']=type(threshold) is int and quote['total_halalas']>=threshold
            config = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s', (body.brand_id,)).fetchone()
            quote['delivery_workflow'] = (config or {}).get('settings', {}).get('delivery_workflow', 'branch_fulfillment')
            quote['order_reference'] = order_numbering.allocate(conn, body.branch_id)
            quote['receipt_profile'] = invoice_profiles.capture(conn, body.brand_id, body.branch_id)
            order_id = db.new_id('order_')
            delivery_day = service_hours.delivery_operational_date() if body.service == 'delivery' else None
            if delivery_day:
                quote['delivery_operating_day'] = {'date': delivery_day.isoformat(), 'start': '16:00',
                                                    'timezone': 'Asia/Riyadh'}
            row = conn.execute('INSERT INTO orders(id,brand_id,branch_id,customer_id,service,delivery_status,payment_method,'
                               'subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer,performance_snapshot,delivery_operational_date) '
                               'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                               (order_id, body.brand_id, body.branch_id, user['id'], body.service,
                                'pending' if body.service == 'delivery' else 'not_applicable', body.payment_method,
                                quote['subtotal_halalas'], quote['delivery_fee_halalas'], quote['total_halalas'],
                                db.json(quote), db.json(customer), db.json(performance.capture_policy(conn,body.brand_id,body.branch_id)), delivery_day)).fetchone()
            db.event(conn, order_id, user['id'], 'order.created', {'service': body.service})
            db.audit(conn, user['id'], 'order.created', 'order', order_id, {'branch_id': body.branch_id, 'total_halalas': quote['total_halalas']})
            return {'order': customer_order_view(row)}
        return db.idempotent(conn, user['id'], idempotency_key, 'orders.create', body.model_dump(), perform)


@router.get('')
def list_orders(user=Depends(auth.current_user), limit: int = Query(default=30, ge=1, le=100),
                offset: int = Query(default=0, ge=0, le=100000), branch_id: str | None = Query(default=None, max_length=80),
                service: str | None = Query(default=None, max_length=20), status: str | None = Query(default=None, max_length=30),
                period: str | None = Query(default=None, max_length=20), start: str | None = Query(default=None, max_length=50),
                end: str | None = Query(default=None, max_length=50),
                sort: str | None = Query(default=None, max_length=20),
                neighborhood: str | None = Query(default=None, max_length=120),
                driver_id: str | None = Query(default=None, max_length=80),
                search: str | None = Query(default=None, max_length=80)):
    conditions, params = ['o.brand_id=%s'], [user['brand_id']]
    if user['role'] == 'customer':
        conditions.append('o.customer_id=%s')
        params.append(user['id'])
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
        if user['role']!='customer':
            performance.enrich_orders(conn,rows[:limit])
        else:
            rows=[customer_order_view(row) for row in rows]
    return {'sort':sort, 'orders': rows[:limit], 'next_offset': offset + limit if len(rows) > limit else None, 'period': period_info}


@router.get('/{order_id}')
def get_order(order_id: str, user=Depends(auth.current_user)):
    with db.tx() as conn:
        row = scoped_order(conn, order_id, user)
        events = conn.execute('SELECT id,event_type,details,created_at FROM order_events WHERE order_id=%s ORDER BY created_at,id', (order_id,)).fetchall()
        if user['role']!='customer':
            performance.enrich_orders(conn,[row])
            row['print_labels_en']=print_translations.for_order(conn,row)
            row['receipt_qr']=invoice_profiles.ordinary_qr(row)
        else:
            row=customer_order_view(row)
    return {'order': row, 'events': events}


class PreparationBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: str = Field(min_length=1, max_length=30)
    version: StrictInt = Field(ge=1)
    reason: str = Field(default='', max_length=500)


@router.post('/{order_id}/preparation')
def preparation(order_id: str, body: PreparationBody, user=Depends(auth.require_roles('owner', 'branch_manager')),
                idempotency_key: str | None = Header(default=None)):
    with db.tx() as conn:
        # Replay returns a saved response, so fresh scope authorization must precede replay lookup.
        scoped_order(conn, order_id, user)
        def perform():
            order = scoped_order(conn, order_id, user, lock=True)
            if order['version'] != body.version:
                raise db.error(409, 'ORDER_VERSION_CONFLICT', 'تغير الطلب. حدّث الصفحة قبل تنفيذ العملية.')
            current = order['preparation_status']
            transitions = {'new': {'accepted', 'cancelled'}, 'accepted': {'preparing', 'cancelled'},
                           'preparing': {'ready', 'cancelled'}, 'ready': {'handed_over', 'completed', 'cancelled'}}
            if body.status not in transitions.get(current, set()):
                raise db.error(409, 'PREPARATION_TRANSITION_INVALID', 'انتقال حالة التجهيز غير مسموح.')
            if body.status in {'accepted','preparing'} and order['customer'].get('requires_operational_confirmation'):
                if not conn.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='order.operational_confirmation' AND details->>'result'='reached'",(order_id,)).fetchone():
                    raise db.error(409,'OPERATIONAL_CONTACT_CONFIRMATION_REQUIRED','تتطلب سياسة الطلب اتصالًا تشغيليًا موثقًا قبل التجهيز. لا يعتبر ذلك توثيق جوال.')
            if body.status == 'cancelled':
                if not body.reason.strip():
                    raise db.error(422, 'CANCELLATION_REASON_REQUIRED', 'اكتب سبب رفض أو إلغاء الطلب.')
                if order['payment_status'] not in {'unpaid', 'refunded'}:
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
            row = conn.execute("UPDATE orders SET preparation_status=%s,version=version+1,completed_at=CASE WHEN %s='completed' THEN now() ELSE completed_at END "
                               "WHERE id=%s RETURNING *", (body.status, body.status, order_id)).fetchone()
            if body.status == 'cancelled' and order['service'] == 'delivery':
                conn.execute("UPDATE orders SET delivery_status='cancelled' WHERE id=%s", (order_id,))
                row['delivery_status'] = 'cancelled'
            db.event(conn, order_id, user['id'], 'preparation.' + body.status, {'previous': current, 'reason': body.reason})
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
