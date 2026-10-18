"""Reviewed, customer-consented order revisions. No destructive order/ledger edits."""
from __future__ import annotations

from typing import Literal
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from . import auth, catalog, db
from .orders import scoped_order, customer_order_view

router = APIRouter(prefix='/api/order-amendments')
ACTIVE = ('requested', 'branch_confirmed', 'offered')


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: StrictInt = Field(ge=1)
    items: list[catalog.OrderItem] = Field(min_length=1, max_length=50)
    customer: catalog.CustomerInfo
    notes: str = Field(default='', max_length=1000)
    reason: str = Field(min_length=1, max_length=500)


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['confirm_branch', 'offer', 'reject', 'withdraw', 'accept']
    channel: Literal['customer', 'phone', 'whatsapp'] = 'customer'
    note: str = Field(default='', max_length=500)


def active(c, oid):
    return c.execute("SELECT * FROM order_amendments WHERE order_id=%s AND status IN ('requested','branch_confirmed','offered') AND expires_at>now() ORDER BY created_at DESC LIMIT 1", (oid,)).fetchone()


def balance(c, o):
    row = c.execute('SELECT (SELECT coalesce(sum(amount_halalas),0) FROM collections WHERE order_id=%s) - (SELECT coalesce(sum(amount_halalas),0) FROM refunds WHERE order_id=%s) AS net', (o['id'], o['id'])).fetchone()
    net = int(row['net'])
    from .branch_payments import online_order
    if online_order(o):
        net += int(c.execute('SELECT coalesce(sum(amount_halalas),0) AS net FROM branch_online_receipts WHERE order_id=%s',(o['id'],)).fetchone()['net'])
    return net


def editable(o, c):
    from .branch_payments import online_order
    if online_order(o) and c.execute('SELECT 1 FROM branch_payment_attempts WHERE order_id=%s',(o['id'],)).fetchone():
        raise db.error(409,'PAYMENT_RECONCILIATION_REQUIRED','يلزم تسوية محاولة الدفع قبل تعديل مبلغ الطلب.')
    from .fiscal_issuance import original
    if original(c,o['id']):
        raise db.error(409, 'FISCAL_ISSUED_ADJUSTMENT', 'صدرت فاتورة لهذا الطلب؛ ألغِ الطلب أو استخدم المرتجع بدل تغيير الفاتورة المحفوظة.')
    if o.get('completed_at') or o['preparation_status'] in ('completed', 'cancelled', 'rejected') or o['delivery_status'] in ('delivered', 'cancelled'):
        raise db.error(409, 'AMENDMENT_CLOSED', 'الطلب منتهٍ؛ استخدم الشكوى أو الاسترداد بدل التعديل.')


def reviewer(o, u):
    return u['role'] == 'owner' or u['role'] == ('delivery_manager' if o['service'] == 'delivery' else 'branch_manager')


def check_staff(u):
    if u['role'] != 'customer' and not auth.has_permission(u, 'orders_amend'):
        raise db.error(403, 'AMENDMENT_PERMISSION', 'صلاحية تعديل الطلبات غير متاحة.')


def original_items(o):
    return [{'product_id': x['product_id'], 'quantity': x['quantity'],
             'units': [{'options': v.get('options', {})} for v in x.get('units', [])] or None,
             'note': x.get('note', '')} for x in o['snapshot']['items']]


def canonical_items(items):
    return [catalog.OrderItem.model_validate(x).model_dump() for x in items]


def proposal_quote(c, o, p):
    # Identity, payment method, fulfillment branch and receipt ownership are never client editable.
    if p.customer.name != o['customer'].get('name') or (p.customer.phone or '') != (o['customer'].get('phone') or ''):
        raise db.error(422, 'AMENDMENT_IDENTITY', 'التعديل لا يغير هوية صاحب الطلب أو رقم تواصله.')
    items_changed = canonical_items(original_items(o)) != [x.model_dump() for x in p.items]
    departed = o['preparation_status'] == 'handed_over' or c.execute("SELECT 1 FROM driver_tasks WHERE order_id=%s AND status IN ('picked_up','delivered')", (o['id'],)).fetchone()
    if departed and (items_changed or p.notes != o['snapshot'].get('notes', '')):
        raise db.error(409, 'AMENDMENT_DEPARTED', 'السائق استلم الطلب؛ الأصناف الجديدة تحتاج طلبًا مستقلًا. يمكن طلب تعديل العنوان فقط.')
    quote = catalog.quote_order(c, catalog.QuoteBody(brand_id=o['brand_id'], branch_id=o['branch_id'], service=o['service'], payment_method=o['payment_method'], items=p.items, customer=p.customer, notes=p.notes), for_update=True)
    if quote['branch_id'] != o['branch_id']:
        raise db.error(409, 'AMENDMENT_BRANCH_CHANGE', 'التعديل يتطلب فرع تجهيز مختلفًا؛ أنشئ طلبًا مستقلًا.')
    if not items_changed:
        # Address-only changes retain the already agreed item prices.
        quote['items'] = o['snapshot']['items']
        quote['subtotal_halalas'] = o['subtotal_halalas']
        quote['total_halalas'] = quote['subtotal_halalas'] + quote['delivery_fee_halalas']
    for key in ('receipt_occasion_style', 'receipt_occasion_text', 'receipt_profile', 'order_source', 'order_reference', 'internal_routing', 'performance_snapshot', 'manager_shift_id', 'created_by_staff_id', 'delivery_workflow', 'branch_payment_mode', 'branch_payment_seller'):
        if key in o['snapshot']: quote[key] = o['snapshot'][key]
    if 'channel_commercial' in o['snapshot']:
        from .channel_commercial import amounts
        quote['channel_commercial'] = amounts(o['snapshot']['channel_commercial'], quote)
    customer = dict(o['customer'])
    for key,value in p.customer.model_dump().items():
        if key in ('address','neighborhood','location','car') and (key in customer or value not in (None,'')):
            customer[key] = value
    return {'snapshot': quote, 'customer': customer, 'items_changed': items_changed or p.notes!=o['snapshot'].get('notes','')}


def public_amendment(a, customer=False):
    result = {k: a[k] for k in ('id', 'status', 'reason', 'created_at', 'expires_at', 'base_revision', 'consent_channel')}
    result['quote'] = catalog.customer_quote_view(a['proposal']['snapshot']) if customer else a['proposal']['snapshot']
    result['customer'] = {k: a['proposal']['customer'].get(k) for k in ('name', 'phone', 'address', 'neighborhood', 'location', 'car')}
    result['items_changed'] = a['proposal']['items_changed']
    return result


def summary(c, o, u):
    from .order_requests import rows as request_rows
    amendments = c.execute('SELECT * FROM order_amendments WHERE order_id=%s ORDER BY created_at DESC LIMIT 30', (o['id'],)).fetchall()
    receipts = c.execute('SELECT kind,actor_id FROM order_revision_receipts WHERE order_id=%s AND revision=%s', (o['id'], o.get('amendment_revision', 0))).fetchall()
    net = balance(c, o)
    return {'requests': request_rows(c, o['id']), 'amendments': [public_amendment(a, u['role'] == 'customer') for a in amendments],
            'active_id': next((a['id'] for a in amendments if a['status'] in ACTIVE), None), 'revision': o.get('amendment_revision', 0),
            'due_halalas': max(0, o['total_halalas']-net), 'refund_due_halalas': max(0, net-o['total_halalas']),
            'receipts': receipts if u['role'] != 'customer' else []}


@router.get('/inbox')
def inbox(user=Depends(auth.require_roles('owner','delivery_manager','branch_manager'))):
    with db.tx() as c:
        if not auth.has_permission(user, 'orders_view'):
            raise db.error(403, 'REQUEST_PERMISSION', 'عرض الطلبات غير مصرح.')
        rows=c.execute("""SELECT o.id,o.order_no,o.service,o.amendment_revision,a.id amendment_id,a.status
            FROM orders o LEFT JOIN LATERAL (SELECT id,status FROM order_amendments
              WHERE order_id=o.id AND status IN ('requested','branch_confirmed','offered')
              ORDER BY created_at DESC LIMIT 1) a ON true
            WHERE o.brand_id=%s AND (%s OR o.branch_id=ANY(%s))
              AND (%s='owner' OR (%s='delivery_manager' AND o.service='delivery')
                   OR (%s='branch_manager' AND o.service IN ('pickup','car')))
              AND (a.id IS NOT NULL OR (o.completed_at IS NULL
                AND o.preparation_status NOT IN ('cancelled','rejected') AND o.amendment_revision>0
                AND NOT EXISTS(SELECT 1 FROM order_revision_receipts r WHERE r.order_id=o.id
                  AND r.revision=o.amendment_revision AND r.kind='branch')))
            ORDER BY o.created_at LIMIT 100""",(user['brand_id'],user['role']=='owner',user.get('branch_ids',[]),
            user['role'],user['role'],user['role'])).fetchall()
        # Apply operational routing on the server, including expired requests:
        # opening a request or its conversation cannot remove it from this inbox.
        rows=[r for r in rows if reviewer(r,user)]
        from .order_requests import pending_inbox
        return {'orders':rows, 'requests':pending_inbox(c,user)}


@router.post('/orders/{oid}/preview')
def preview(oid: str, body: Proposal, user=Depends(auth.require_roles('customer','owner','delivery_manager','branch_manager'))):
    check_staff(user)
    with db.tx() as c:
        o=scoped_order(c,oid,user,lock=True)
        editable(o, c)
        if user['role']!='customer' and not reviewer(o,user):raise db.error(403,'AMENDMENT_REVIEWER','راجع مسؤول الطلب المختص.')
        q=proposal_quote(c,o,body)
        return {'quote':catalog.customer_quote_view(q['snapshot']) if user['role']=='customer' else q['snapshot']}


@router.get('/orders/{oid}')
def detail(oid: str, user=Depends(auth.current_user)):
    with db.tx() as c:
        o = scoped_order(c, oid, user)
        return summary(c, o, user)


@router.post('/orders/{oid}')
def request_amendment(oid: str, body: Proposal, user=Depends(auth.require_roles('customer','owner','delivery_manager','branch_manager')), idempotency_key: str | None=Header(default=None)):
    check_staff(user)
    with db.tx() as c:
        scoped_order(c, oid, user)
        def perform():
            o = scoped_order(c, oid, user, lock=True)
            editable(o, c)
            if user['role'] != 'customer' and not reviewer(o, user):
                raise db.error(403, 'AMENDMENT_REVIEWER', 'طلب التوصيل يتبع مدير التوصيل، والاستلام يتبع الفرع.')
            if o['version'] != body.version or c.execute("SELECT 1 FROM order_amendments WHERE order_id=%s AND status IN ('requested','branch_confirmed','offered')", (oid,)).fetchone():
                raise db.error(409, 'AMENDMENT_CONFLICT', 'تغير الطلب أو يوجد تعديل قيد المراجعة؛ حدّث التفاصيل.')
            if not body.reason.strip():raise db.error(422,'AMENDMENT_REASON','اكتب سبب التعديل.')
            if not db.rate_limit(c,'amendment:'+user['id'],10,900):raise db.error(429,'AMENDMENT_RATE','طلبات تعديل كثيرة؛ حاول لاحقًا.')
            if o['payment_status'] in ('refunded','partially_refunded') or balance(c,o)>o['total_halalas']:
                raise db.error(409, 'AMENDMENT_SETTLEMENT', 'أكمل تسوية الاسترداد القائم قبل طلب تعديل جديد.')
            quote = proposal_quote(c, o, body)
            if quote['snapshot']['items'] == o['snapshot']['items'] and quote['customer'] == o['customer'] and body.notes == o['snapshot'].get('notes',''):
                raise db.error(422, 'AMENDMENT_EMPTY', 'لم يتغير شيء في الطلب.')
            aid = db.new_id('amend_')
            c.execute("INSERT INTO order_amendments(id,order_id,requested_by,base_revision,proposal,status,reason) VALUES(%s,%s,%s,%s,%s,'requested',%s)", (aid,oid,user['id'],o['amendment_revision'],db.json(quote),body.reason.strip()))
            db.event(c,oid,user['id'],'amendment.requested',{'amendment_id':aid})
            db.audit(c,user['id'],'amendment.requested','order',oid,{'amendment_id':aid,'proposal':quote,'reason':body.reason})
            return {'amendment_id':aid}
        return db.idempotent(c,user['id'],idempotency_key,'amendment.request:'+oid,body.model_dump(),perform)


@router.post('/{aid}/decision')
def decide(aid: str, body: Decision, user=Depends(auth.require_roles('customer','owner','delivery_manager','branch_manager')), idempotency_key: str | None=Header(default=None)):
    check_staff(user)
    with db.tx() as c:
        a = c.execute('SELECT * FROM order_amendments WHERE id=%s',(aid,)).fetchone()
        if not a: raise db.error(404,'AMENDMENT_NOT_FOUND','طلب التعديل غير موجود.')
        scoped_order(c,a['order_id'],user)
        def perform():
            o = scoped_order(c,a['order_id'],user,lock=True)
            row = (c.execute("SELECT * FROM order_amendments WHERE id=%s AND order_id=%s AND status IN ('requested','branch_confirmed','offered')", (aid,o['id'])).fetchone()
                   if body.action in ('reject','withdraw') else active(c,o['id']))
            if not row or row['id'] != aid or row['base_revision'] != o['amendment_revision']:
                raise db.error(409,'AMENDMENT_EXPIRED','انتهى أو تغير طلب التعديل؛ حدّث الصفحة.')
            if body.action not in ('reject','withdraw'):
                editable(o, c)
            action = body.action
            staff = reviewer(o,user)
            if action == 'withdraw':
                if user['id'] != row['requested_by'] and not staff: raise db.error(403,'AMENDMENT_FORBIDDEN','لا يمكنك سحب هذا الطلب.')
                status = 'withdrawn'
            elif action == 'reject':
                if not staff and user['role'] != 'branch_manager': raise db.error(403,'AMENDMENT_FORBIDDEN','رفض التعديل من المسؤول فقط.')
                if not body.note.strip(): raise db.error(422,'AMENDMENT_REASON','اكتب سبب الرفض.')
                status = 'rejected'
            elif action == 'confirm_branch':
                delegated=user['role']=='delivery_manager' and o['snapshot'].get('delivery_workflow')=='manager_dispatch' and body.note.strip()
                if (user['role'] not in ('branch_manager','owner') and not delegated) or row['status'] != 'requested': raise db.error(403,'AMENDMENT_BRANCH','يلزم تأكيد الفرع؛ في التشغيل المباشر يوثق المدير تأكيده بملاحظة.')
                c.execute('UPDATE order_amendments SET branch_confirmed_by=%s WHERE id=%s',(user['id'],aid))
                status = 'branch_confirmed'
            elif action == 'offer':
                if not staff or row['status'] not in ('requested','branch_confirmed'): raise db.error(403,'AMENDMENT_REVIEWER','مراجعة التعديل من المسؤول المختص فقط.')
                if row['proposal']['items_changed'] and o['preparation_status'] in ('preparing','ready') and not row['branch_confirmed_by']:
                    raise db.error(409,'AMENDMENT_BRANCH_REQUIRED','يلزم تأكيد الفرع أن تعديل التجهيز ممكن أولًا.')
                c.execute('UPDATE order_amendments SET reviewed_by=%s WHERE id=%s',(user['id'],aid))
                status = 'offered'
            else:
                if row['status'] != 'offered': raise db.error(409,'AMENDMENT_NOT_OFFERED','انتظر مراجعة المسؤول أولًا.')
                if user['role'] == 'customer':
                    if body.channel != 'customer': raise db.error(422,'AMENDMENT_CONSENT','موافقة العميل هنا تكون داخل النظام.')
                elif not staff or body.channel == 'customer' or not body.note.strip():
                    raise db.error(422,'AMENDMENT_CONSENT','وثّق موافقة العميل عبر الهاتف أو الواتساب مع ملاحظة؛ لا تسجلها كموافقة داخل النظام.')
                # Revalidate availability/coverage without silently changing the accepted price.
                q = row['proposal']; customer = catalog.CustomerInfo.model_validate({k:v for k,v in q['customer'].items() if k in catalog.CustomerInfo.model_fields})
                p = Proposal(version=o['version'],items=original_items({'snapshot':q['snapshot']}),customer=customer,notes=q['snapshot'].get('notes',''),reason=row['reason'])
                fresh = proposal_quote(c,o,p)
                if any(fresh['snapshot'][key] != q['snapshot'][key] for key in ('items','subtotal_halalas','delivery_fee_halalas','total_halalas')):
                    raise db.error(409,'AMENDMENT_PRICE_CHANGED','تغير السعر أو التوفر؛ اسحب التعديل واطلب عرضًا جديدًا.')
                rev = o['amendment_revision']+1
                c.execute("UPDATE order_amendments SET status='applied',consent_by=%s,consent_channel=%s,consent_note=%s WHERE id=%s",(user['id'],body.channel,body.note,aid))
                c.execute('INSERT INTO order_revisions(order_id,revision,amendment_id,before_snapshot,before_customer,after_snapshot,after_customer,before_total,after_total,approved_by,consent_by,consent_channel) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(o['id'],rev,aid,db.json(o['snapshot']),db.json(o['customer']),db.json(q['snapshot']),db.json(q['customer']),o['total_halalas'],q['snapshot']['total_halalas'],row['reviewed_by'],user['id'],body.channel))
                paid = balance(c,o)
                payment = 'paid' if paid == q['snapshot']['total_halalas'] else 'unpaid'
                c.execute('UPDATE orders SET snapshot=%s,customer=%s,subtotal_halalas=%s,delivery_fee_halalas=%s,total_halalas=%s,amendment_revision=%s,payment_status=%s,version=version+1 WHERE id=%s',(db.json(q['snapshot']),db.json(q['customer']),q['snapshot']['subtotal_halalas'],q['snapshot']['delivery_fee_halalas'],q['snapshot']['total_halalas'],rev,payment,o['id']))
                c.execute("UPDATE branch_print_jobs SET status='cancelled',finished_at=now() WHERE order_id=%s AND status='queued'",(o['id'],))
                status = 'applied'
            c.execute('UPDATE order_amendments SET status=%s,updated_at=now() WHERE id=%s',(status,aid))
            db.event(c,o['id'],user['id'],'amendment.'+status,{'amendment_id':aid,'revision':o['amendment_revision']+(status=='applied'),'note':body.note if status=='rejected' else ''})
            db.audit(c,user['id'],'amendment.'+status,'order',o['id'],{'amendment_id':aid,**body.model_dump()})
            return {'status':status,'order_id':o['id']}
        return db.idempotent(c,user['id'],idempotency_key,'amendment.decision:'+aid,body.model_dump(),perform)


class Receipt(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: StrictInt = Field(gt=0)
    kind: Literal['branch','driver']
    note: str = Field(default='',max_length=500)


@router.post('/orders/{oid}/receipt')
def acknowledge(oid: str, body: Receipt, user=Depends(auth.require_roles('branch_manager','driver','owner','delivery_manager')), idempotency_key: str | None=Header(default=None)):
    with db.tx() as c:
        scoped_order(c,oid,user)
        def perform():
            o=scoped_order(c,oid,user,lock=True)
            if body.revision != o['amendment_revision']: raise db.error(409,'AMENDMENT_STALE_RECEIPT','راجع آخر نسخة من الطلب.')
            delegated=user['role']=='delivery_manager' and o['snapshot'].get('delivery_workflow')=='manager_dispatch' and body.note.strip()
            if body.kind == 'branch' and user['role'] not in ('branch_manager','owner') and not delegated or body.kind == 'driver' and user['role'] != 'driver':
                raise db.error(403,'AMENDMENT_RECEIPT_ROLE','التأكيد من الفرع أو السائق المعني فقط.')
            c.execute('INSERT INTO order_revision_receipts(order_id,revision,actor_id,kind) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',(oid,body.revision,user['id'],body.kind))
            db.event(c,oid,user['id'],'amendment.received',body.model_dump())
            db.audit(c,user['id'],'amendment.received','order',oid,body.model_dump())
            return {'received':True}
        return db.idempotent(c,user['id'],idempotency_key,'amendment.receipt:'+oid,body.model_dump(),perform)


def guard(c, o, *, driver_id=None, require_branch=True):
    """Called with the order lock, before preparation/collection/driver transitions."""
    if active(c,o['id']): raise db.error(409,'AMENDMENT_PENDING','يوجد تعديل قيد المراجعة؛ وافق عليه أو ارفضه أو اسحبه قبل متابعة التنفيذ.')
    rev=o.get('amendment_revision',0)
    if not rev:return
    if require_branch and not c.execute("SELECT 1 FROM order_revision_receipts WHERE order_id=%s AND revision=%s AND kind='branch'",(o['id'],rev)).fetchone():
        raise db.error(409,'AMENDMENT_BRANCH_RECEIPT','على الفرع مراجعة النسخة المعدلة وإلغاء العمل بالنسخة القديمة ثم تأكيد الاستلام.')
    if driver_id and not c.execute("SELECT 1 FROM order_revision_receipts WHERE order_id=%s AND revision=%s AND kind='driver' AND actor_id=%s",(o['id'],rev,driver_id)).fetchone():
        raise db.error(409,'AMENDMENT_DRIVER_RECEIPT','راجع النسخة المعدلة وعنوانها وأكد استلامها قبل متابعة المهمة.')
