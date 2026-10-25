"""Owner-authorized recording of completed Enbishan deliveries.

The operator supplies the real time, source reference and payment split. This
does not dispatch or print a kitchen ticket, invent pickup evidence, confirm a
manager's cash receipt, or reopen a closed shift. Closed duties get a separate
closed accounting supplement with zero work duration and no vehicle/float.
"""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from . import auth, db, operations as ops, service_hours
from .orders import DeliveryCenterOrder

router = APIRouter(prefix='/api/prior-deliveries', tags=['prior deliveries'])


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Grant(Body):
    manager_shift_id: str
    closed_manager_supplement: StrictBool = False
    driver_shift_ids: list[str] = Field(min_length=1, max_length=30)
    minutes: StrictInt = Field(default=120, ge=15, le=240)
    reason: str = Field(min_length=5, max_length=1000)


class Revocation(Body):
    reason: str = Field(min_length=5, max_length=1000)


class Prior(Body):
    driver_shift_id: str
    actual_delivered_at: datetime
    source_reference: str = Field(min_length=3, max_length=150)
    cash_halalas: StrictInt = Field(ge=0, le=ops.MAX_HALALAS)
    card_halalas: StrictInt = Field(ge=0, le=ops.MAX_HALALAS)
    reason: str = Field(min_length=5, max_length=1000)
    confirmed: StrictBool


class Entry(Body):
    prior: Prior
    order: DeliveryCenterOrder | None = None
    order_id: str | None = None
    expected_version: StrictInt | None = None


def access(user=Depends(auth.current_user)):
    if (user['brand_id'] != 'enbishan' or user['role'] not in ('owner', 'delivery_manager')
            or user.get('_morning_manager_context')
            or not auth.has_permission(user, 'orders_create_customer')):
        raise db.error(403, 'PRIOR_DELIVERY_SCOPE', 'إدخال الطلبات السابقة غير متاح لحسابك.')
    return user


def shift_scope(c, user, sid):
    row = c.execute('SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.id=%s FOR UPDATE OF s', (sid,)).fetchone()
    ops.shift_access(c, user, row)
    if row.get('supplement_of_shift_id'):
        raise db.error(409, 'PRIOR_ORIGINAL_SHIFT_REQUIRED', 'اختر الوردية الأصلية، لا الملحق.')
    return row


def day_of(shift):
    return shift.get('delivery_operational_date') or service_hours.delivery_operational_date(shift['opened_at'])


def manager_scope(c, user, shift, *, closed_supplement=False):
    if closed_supplement and (user['role'] != 'owner' or shift['status'] != 'closed' or not shift.get('closed_at')):
        raise db.error(403, 'PRIOR_CLOSED_MANAGER_OWNER_ONLY', 'ملحق المناوبة المقفلة متاح للمالك فقط ولمناوبة مقفلة فعليًا.')
    if shift['kind'] != 'manager' or (not closed_supplement and shift['status'] != 'open'):
        raise db.error(409, 'PRIOR_MANAGER_CLOSED', 'يلزم شفت مدير توصيل مفتوح.')
    from .multibrand import project
    manager = c.execute('SELECT * FROM users WHERE id=%s AND active', (shift['user_id'],)).fetchone()
    principal = project(c, manager, shift['brand_id']) if manager else None
    if not principal or principal['role'] != 'delivery_manager' or not auth.has_permission(principal, 'orders_create_customer'):
        raise db.error(403, 'PRIOR_MANAGER_SCOPE', 'مدير التوصيل غير مخول حاليًا.')
    if user['role'] != 'owner' and user['id'] != shift['user_id']:
        raise db.error(403, 'PRIOR_MANAGER_SCOPE', 'الفتح مخصص لمدير التوصيل المحدد.')
    return principal


@router.get('/options')
def options(user=Depends(access)):
    with db.tx() as c:
        managers = c.execute("SELECT s.id,s.user_id,s.branch_ids,s.status,s.delivery_operational_date,s.opened_at,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.brand_id=%s AND s.kind='manager' AND (s.status='open' OR (%s AND s.status='closed' AND s.closed_at>=now()-interval '30 days')) AND (%s OR s.user_id=%s) ORDER BY s.opened_at DESC", (user['brand_id'],user['role']=='owner',user['role']=='owner',user['id'])).fetchall()
        managers = [s for s in managers if set(s['branch_ids']) <= set(user.get('branch_ids',[])) or ops.unrestricted_owner(user)]
        shifts = c.execute("SELECT s.id,s.user_id,s.manager_shift_id,s.branch_ids,s.status,s.shift_no,s.delivery_operational_date,s.opened_at,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.brand_id=%s AND s.kind='driver' AND s.manager_shift_id=ANY(%s) AND s.supplement_of_shift_id IS NULL ORDER BY u.display_name,s.opened_at DESC", (user['brand_id'],[s['id'] for s in managers])).fetchall()
        shifts = [s for s in shifts if set(s['branch_ids']) <= set(user.get('branch_ids',[])) or ops.unrestricted_owner(user)]
        grants = c.execute("SELECT g.* FROM prior_delivery_grants g WHERE g.brand_id=%s AND g.manager_shift_id=ANY(%s) AND g.expires_at>now() AND NOT EXISTS(SELECT 1 FROM prior_delivery_revocations r WHERE r.grant_id=g.id) ORDER BY g.created_at DESC", (user['brand_id'],[s['id'] for s in managers])).fetchall()
        return ops.safe({'managers':managers,'shifts':shifts,'grants':grants})


@router.post('/grants')
def grant(body: Grant, user=Depends(access), key: str | None=Header(None, alias='Idempotency-Key')):
    if user['role'] != 'owner':
        raise db.error(403, 'PRIOR_OWNER_REQUIRED', 'الفتح يتطلب اعتماد المالك.')
    with db.tx() as c:
        from .central_workdays import lock_lanes
        lock_lanes(c)
        manager = shift_scope(c,user,body.manager_shift_id)
        manager_scope(c,user,manager,closed_supplement=body.closed_manager_supplement)
        shifts = [shift_scope(c,user,sid) for sid in sorted(set(body.driver_shift_ids))]
        if body.closed_manager_supplement and any(s['status']!='closed' or not s.get('closed_at') for s in shifts):
            raise db.error(422, 'PRIOR_CLOSED_DRIVER_REQUIRED', 'اختر وردية سائق أصلية مقفلة لملحق المناوبة المقفلة.')
        operating_day=day_of(shifts[0])
        # A manager can open at 15:45 before the 16:00 operating-day boundary.
        # Keep its original ledger date, but authorize its attached driver day.
        manager_days={day_of(manager),manager['opened_at'].astimezone(timezone(timedelta(hours=3))).date()}
        if operating_day not in manager_days or any(s['kind']!='driver' or s['manager_shift_id']!=manager['id'] or day_of(s)!=operating_day for s in shifts):
            raise db.error(422, 'PRIOR_SHIFT_SCOPE', 'اختر سائقين تابعين لنفس المدير ويوم التشغيل.')
        def perform():
            gid=db.new_id('prior_grant_')
            row=c.execute('INSERT INTO prior_delivery_grants(id,brand_id,manager_shift_id,manager_id,driver_shift_ids,operational_date,authorized_by,reason,closed_manager_supplement,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,now()+%s) RETURNING *', (gid,user['brand_id'],manager['id'],manager['user_id'],db.json([s['id'] for s in shifts]),operating_day,user['id'],body.reason,body.closed_manager_supplement,timedelta(minutes=body.minutes))).fetchone()
            db.audit(c,user['id'],'prior_delivery.authorized','prior_delivery_grant',gid,body.model_dump())
            return ops.safe({'grant':row})
        return db.idempotent(c,user['id'],key,'prior_delivery.grant',body.model_dump(),perform)


@router.post('/grants/{gid}/revoke')
def revoke(gid:str, body:Revocation, user=Depends(access), key:str|None=Header(None,alias='Idempotency-Key')):
    if user['role']!='owner':raise db.error(403,'PRIOR_OWNER_REQUIRED','المالك فقط.')
    with db.tx() as c:
        from .central_workdays import lock_lanes
        lock_lanes(c)
        g=c.execute('SELECT * FROM prior_delivery_grants WHERE id=%s AND brand_id=%s FOR UPDATE',(gid,user['brand_id'])).fetchone()
        if not g:raise db.error(404,'PRIOR_GRANT_MISSING','الفتح غير موجود.')
        shift_scope(c,user,g['manager_shift_id'])
        def perform():
            c.execute('INSERT INTO prior_delivery_revocations(grant_id,actor_id,reason) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(gid,user['id'],body.reason))
            db.audit(c,user['id'],'prior_delivery.revoked','prior_delivery_grant',gid,body.model_dump())
            return {'revoked':True}
        return db.idempotent(c,user['id'],key,'prior_delivery.revoke:'+gid,body.model_dump(),perform)


def authorized(c,user,gid,sid):
    # Match canonical financial lock order and recheck scope before replay.
    from .central_workdays import lock_lanes
    lock_lanes(c)
    g=c.execute('SELECT * FROM prior_delivery_grants WHERE id=%s AND brand_id=%s AND expires_at>now() AND NOT EXISTS(SELECT 1 FROM prior_delivery_revocations r WHERE r.grant_id=prior_delivery_grants.id) FOR UPDATE', (gid,user['brand_id'])).fetchone()
    if not g or sid not in g['driver_shift_ids'] or (user['role']!='owner' and user['id']!=g['manager_id']):
        raise db.error(403,'PRIOR_GRANT_UNAVAILABLE','الفتح انتهى أو لا يشمل السائق وحسابك.')
    manager=shift_scope(c,user,g['manager_shift_id'])
    closed_supplement=bool(g.get('closed_manager_supplement'))
    principal=manager_scope(c,user,manager,closed_supplement=closed_supplement)
    shift=shift_scope(c,user,sid)
    if closed_supplement and (shift['status']!='closed' or not shift.get('closed_at')):
        raise db.error(409, 'PRIOR_CLOSED_DRIVER_REQUIRED', 'تغيرت حالة وردية السائق الأصلية؛ يلزم مراجعة المالك.')
    if shift['kind']!='driver' or shift['manager_shift_id']!=manager['id'] or day_of(shift)!=g['operational_date']:
        raise db.error(409,'PRIOR_SHIFT_CHANGED','تغير ارتباط الوردية؛ يلزم اعتماد جديد.')
    from .multibrand import project
    driver=c.execute('SELECT * FROM users WHERE id=%s AND active',(shift['user_id'],)).fetchone()
    driver=project(c,driver,user['brand_id']) if driver else None
    if not driver or driver['role']!='driver':raise db.error(403,'PRIOR_DRIVER_SCOPE','السائق لم يعد مخولًا.')
    allowed=set(shift['branch_ids']) & set(manager['branch_ids']) & set(principal['branch_ids']) & set(driver['branch_ids'])
    return g,shift,allowed


def validate(prior, grant, shift, now):
    if not prior.confirmed or prior.actual_delivered_at.tzinfo is None:
        raise db.error(422,'PRIOR_ATTESTATION_REQUIRED','أكد التسليم والتحصيل وحدد الوقت الفعلي.')
    if (prior.actual_delivered_at>now or prior.actual_delivered_at<shift['opened_at']
            or service_hours.delivery_operational_date(prior.actual_delivered_at)!=grant['operational_date']
            or (shift.get('closed_at') and prior.actual_delivered_at>shift['closed_at'])):
        raise db.error(422,'PRIOR_TIME_INVALID','وقت التسليم يجب أن يكون داخل الوردية ويوم التشغيل الأصلي ولا يتجاوز الوقت الحالي.')
    if not 0 < prior.cash_halalas+prior.card_halalas <= ops.MAX_HALALAS:
        raise db.error(422,'PRIOR_AMOUNT_REQUIRED','أدخل مبالغ التحصيل الفعلية.')


@router.get('/grants/{gid}/orders/{sid}')
def pending(gid:str,sid:str,user=Depends(access)):
    with db.tx() as c:
        g,s,bids=authorized(c,user,gid,sid)
        rows=c.execute("SELECT o.id,o.version,o.snapshot->>'order_reference' AS order_reference,o.branch_id,o.total_halalas,o.payment_status,o.delivery_status FROM orders o JOIN driver_tasks t ON t.order_id=o.id WHERE t.shift_id=%s AND o.brand_id=%s AND o.branch_id=ANY(%s) AND t.status IN ('assigned','accepted','picked_up','problem') AND o.payment_status='unpaid' AND o.preparation_status NOT IN ('cancelled','rejected') AND NOT EXISTS(SELECT 1 FROM collections p WHERE p.order_id=o.id) ORDER BY o.created_at",(sid,user['brand_id'],list(bids))).fetchall()
        return ops.safe({'orders':rows})


def quote(c,user,body,bids):
    from . import catalog
    order=body.order
    if not order or body.order_id or order.brand_id!=user['brand_id'] or order.service!='delivery' or order.branch_id not in bids:
        raise db.error(422,'PRIOR_ORDER_SCOPE','بيانات الطلب أو الفرع غير صالحة.')
    if order.gift or order.benefit_code or order.cashback_halalas:
        raise db.error(422,'PRIOR_BENEFITS_UNSUPPORTED','التسجيل السابق لا يمنح مزايا جديدة.')
    method = 'split' if body.prior.cash_halalas and body.prior.card_halalas else 'cash' if body.prior.cash_halalas else 'card'
    if order.payment_method != method:
        raise db.error(422,'PRIOR_PAYMENT_METHOD','طريقة الدفع يجب أن تطابق مبالغ التحصيل الفعلية.')
    priced=catalog.quote_order(c,order,user=user,for_update=True,delivery_center=True,prior_delivery=True)
    if priced['branch_id'] not in bids:raise db.error(403,'PRIOR_ORDER_SCOPE','الفرع خارج الاعتماد.')
    return priced


@router.post('/grants/{gid}/quote')
def price(gid:str,body:Entry,user=Depends(access)):
    with db.tx() as c:
        g,s,bids=authorized(c,user,gid,body.prior.driver_shift_id)
        validate(body.prior,g,s,datetime.now(timezone.utc))
        return {'quote':quote(c,user,body,bids)}


def create_order(c,user,body,priced,g):
    from . import orders, order_numbering, invoice_profiles
    order=body.order
    oid=db.new_id('order_')
    customer=order.customer.model_dump(mode='json')
    customer.update(identity_kind='guest',phone_verified=False,phone_sms_verified=False,phone_whatsapp_verified=False)
    # Contact lookup is canonical and does not authenticate a typed phone.
    customer['phone']=auth.normalize_phone(customer['phone'])
    contact=orders.operational_contact(c,user['brand_id'],customer,user['id'])
    priced.update(order_reference=order_numbering.allocate(c,order.branch_id,body.prior.actual_delivered_at),
                  order_source='delivery_center',created_by_staff_id=user['id'],manager_shift_id=g['manager_shift_id'],
                  registration_source='delivery',delivery_workflow='manager_dispatch',
                  receipt_profile=invoice_profiles.capture(c,user['brand_id'],order.branch_id,channel='delivery'))
    priced['prior_delivery']={**body.prior.model_dump(mode='json'),'grant_id':g['id'],
                             'recorded_by':user['id'],'cash_receipt_confirmed':False,'pickup_time_unknown':True}
    c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,payment_status,subtotal_halalas,delivery_fee_halalas,total_halalas,discount_halalas,snapshot,customer,delivery_operational_date) VALUES(%s,%s,%s,%s,'delivery','accepted','pending',%s,'unpaid',%s,%s,%s,%s,%s,%s,%s)", (oid,user['brand_id'],order.branch_id,contact,order.payment_method,priced['subtotal_halalas'],priced['delivery_fee_halalas'],priced['total_halalas'],priced['discount_halalas'],db.json(priced),db.json(customer),g['operational_date']))
    db.event(c,oid,user['id'],'order.created',{'prior_delivery':True,'grant_id':g['id']})
    return c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone()


def supplemental_shift(c,shift,g,prior):
    sid=db.new_id('supplement_')
    # A ledger supplement is not another physical workday. No new float,
    # vehicle inspection, work duration or manager cash receipt is asserted.
    sub={'expected_halalas':prior.cash_halalas,'expected_card_halalas':prior.card_halalas,
         'terminal_total_halalas':prior.card_halalas,'handover_halalas':0,'retained_halalas':0,
         'supplemental_cash_due_halalas':prior.cash_halalas,'notes':prior.reason,
         'prior_delivery_supplement':True,'closure_exception':{'reason':prior.reason,'grant_id':g['id']}}
    c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id,status,opened_at,closed_at,submission,approved_by,exception_reason,delivery_operational_date,supplement_of_shift_id,available) VALUES(%s,'driver',%s,%s,%s,%s,'closed',%s,%s,%s,%s,%s,%s,%s,false)", (sid,shift['user_id'],shift['brand_id'],db.json(shift['branch_ids']),shift['manager_shift_id'],prior.actual_delivered_at,prior.actual_delivered_at,db.json(sub),g['authorized_by'],prior.reason,g['operational_date'],shift['id']))
    return sid


@router.post('/grants/{gid}/record')
def record(gid:str,body:Entry,user=Depends(access),key:str|None=Header(None,alias='Idempotency-Key')):
    with db.tx() as c:
        g,s,bids=authorized(c,user,gid,body.prior.driver_shift_id)
        validate(body.prior,g,s,datetime.now(timezone.utc))
        def perform():
            p=body.prior
            if (c.execute('SELECT 1 FROM prior_delivery_records WHERE brand_id=%s AND driver_id=%s AND operational_date=%s AND source_reference=%s',(user['brand_id'],s['user_id'],g['operational_date'],p.source_reference)).fetchone() or c.execute('SELECT 1 FROM legacy_invoice_imports WHERE brand_id=%s AND operational_date=%s AND invoice_reference=%s',(user['brand_id'],g['operational_date'],p.source_reference)).fetchone()):
                raise db.error(409,'PRIOR_DUPLICATE_REFERENCE','هذا المرجع مسجل لهذا السائق واليوم؛ لا تضفه مرة أخرى.')
            if bool(body.order_id)==bool(body.order):raise db.error(422,'PRIOR_SELECT_ONE','اختر طلبًا موجودًا أو أنشئ طلبًا ناقصًا.')
            if body.order_id:
                o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(body.order_id,)).fetchone()
                ops.order_access(c,user,o)
                if o['branch_id'] not in bids or o['service']!='delivery' or o['version']!=body.expected_version:
                    raise db.error(409,'PRIOR_ORDER_CHANGED','الطلب تغير أو خارج نطاق الفتح؛ حدّث البيانات.')
                t=c.execute("SELECT * FROM driver_tasks WHERE order_id=%s AND status NOT IN ('cancelled','transferred','reassigned') FOR UPDATE",(o['id'],)).fetchone()
                if not t or t['shift_id']!=s['id'] or t['driver_id']!=s['user_id'] or t['status']=='delivered':
                    raise db.error(409,'PRIOR_TASK_SCOPE','الطلب ليس مهمة معلقة للسائق المحدد.')
                if o.get('delivery_operational_date') and o['delivery_operational_date']!=g['operational_date']:
                    raise db.error(409,'PRIOR_ORDER_DAY','الطلب يخص يوم تشغيل مختلف.')
            else:
                priced=quote(c,user,body,bids)
                if body.order.expected_total_halalas!=priced['total_halalas']:
                    raise db.error(409,'PRIOR_PRICE_CHANGED','راجع إجمالي الطلب قبل الحفظ.')
                duplicate=c.execute("SELECT 1 FROM orders o JOIN driver_tasks t ON t.order_id=o.id WHERE o.brand_id=%s AND o.branch_id=%s AND t.driver_id=%s AND t.shift_id=%s AND o.total_halalas=%s AND normalized_identity_phone(o.customer->>'phone')=normalized_identity_phone(%s) AND o.preparation_status NOT IN ('cancelled','rejected') LIMIT 1",(user['brand_id'],body.order.branch_id,s['user_id'],s['id'],priced['total_halalas'],body.order.customer.phone)).fetchone()
                if duplicate:raise db.error(409,'PRIOR_POSSIBLE_DUPLICATE','يوجد طلب مطابق للسائق والعميل والمبلغ؛ راجع الطلب الموجود بدل إضافة نسخة.')
                o=create_order(c,user,body,priced,g);t=None
            if (o['payment_status']!='unpaid' or o['preparation_status'] in ('cancelled','rejected','completed')
                    or o['delivery_status'] in ('delivered','cancelled') or o.get('completed_at')
                    or o['snapshot'].get('complimentary') or o.get('test_void')
                    or c.execute('SELECT 1 FROM collections WHERE order_id=%s',(o['id'],)).fetchone()):
                raise db.error(409,'PRIOR_ORDER_FINAL','الطلب محصل أو منتهٍ؛ لا يمكن تكرار تسجيله.')
            from .order_amendments import guard
            guard(c,o,driver_id=s['user_id'],require_branch=True)
            if p.cash_halalas+p.card_halalas!=o['total_halalas']:
                raise db.error(422,'PRIOR_PAYMENT_MISMATCH','الكاش والشبكة يجب أن يساويا قيمة الطلب. لا يسجل الفرق كخصم تلقائي.')
            supplement=s['status'] in ('closed','submitted')
            sid=supplemental_shift(c,s,g,p) if supplement else s['id']
            if not t:
                tid=db.new_id('task_')
                c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,delivered_at,proof,reason) VALUES(%s,%s,%s,%s,'delivered',%s,%s,%s,%s)",(tid,o['id'],s['user_id'],sid,user['id'],p.actual_delivered_at,p.source_reference,p.reason))
            else:
                tid=t['id']
                c.execute("UPDATE driver_tasks SET shift_id=%s,status='delivered',delivered_at=%s,proof=%s,reason=%s WHERE id=%s",(sid,p.actual_delivered_at,p.source_reference,p.reason,tid))
            method='split' if p.cash_halalas and p.card_halalas else 'cash' if p.cash_halalas else 'card'
            cids=[]
            for tender,amount in [('cash',p.cash_halalas),('card',p.card_halalas)]:
                if not amount:continue
                cid=db.new_id('collection_');cids.append(cid)
                c.execute('INSERT INTO collections(id,order_id,shift_id,collector_id,method,amount_halalas,tendered_halalas,change_halalas,reference,amendment_revision,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s)',(cid,o['id'],sid,s['user_id'],tender,amount,amount if tender=='cash' else None,p.source_reference,o.get('amendment_revision',0),p.actual_delivered_at))
                if tender=='cash':ops.event(c,sid,amount,'collection',cid,user['id'],'تسجيل تحصيل سابق: '+p.source_reference)
            details={**p.model_dump(mode='json'),'grant_id':gid,'authorized_by':g['authorized_by'],
                     'recorded_by':user['id'],'original_shift_id':s['id'],'ledger_shift_id':sid,
                     'collection_ids':cids,'pickup_time_unknown':True,'cash_receipt_confirmed':False}
            c.execute("UPDATE orders SET preparation_status='completed',delivery_status='delivered',payment_status='paid',payment_method=%s,completed_at=%s,delivery_operational_date=%s,version=version+1 WHERE id=%s",(method,p.actual_delivered_at,g['operational_date'],o['id']))
            rid=db.new_id('prior_record_')
            c.execute('INSERT INTO prior_delivery_records(id,grant_id,brand_id,order_id,original_shift_id,ledger_shift_id,driver_id,actor_id,operational_date,source_reference,actual_delivered_at,cash_halalas,card_halalas,reason,details) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(rid,gid,user['brand_id'],o['id'],s['id'],sid,s['user_id'],user['id'],g['operational_date'],p.source_reference,p.actual_delivered_at,p.cash_halalas,p.card_halalas,p.reason,db.json(details)))
            db.event(c,o['id'],user['id'],'order.prior_delivery_recorded',details)
            db.audit(c,user['id'],'order.prior_delivery_recorded','order',o['id'],details)
            # An original external invoice/reference is retained. Do not issue a
            # second fiscal invoice or send a fresh preparation ticket here.
            report_id=None
            if supplement:
                from .reporting import create_snapshot
                report_id=create_snapshot(c,sid,g['authorized_by'])
            return ops.safe({'order':{'id':o['id'],'order_reference':o['snapshot'].get('order_reference'),
                            'payment_status':'paid','delivery_status':'delivered'},'record_id':rid,
                            'supplement_report_id':report_id,'original_shift_id':s['id']})
        return db.idempotent(c,user['id'],key,'prior_delivery.record:'+gid,body.model_dump(mode='json'),perform)
