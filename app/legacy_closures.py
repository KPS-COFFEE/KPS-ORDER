"""Owner attestation of deliveries completed in Sabad with unknown actual time.

This only finishes operational order/task status. It does not close a shift,
assign a driver, invent payment, confirm receipt or manufacture physical evidence.
Original task/order state is preserved in an immutable linked attestation.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Request
from pydantic import Field, StrictBool, StrictInt
from . import auth, db, operations as ops, central_workdays as central, multibrand
from .prior_deliveries import Body, day_of

router = APIRouter(prefix='/api/legacy-deliveries', tags=['legacy deliveries'])


class OrderVersion(Body):
    order_id: str
    task_id: str
    expected_version: StrictInt = Field(ge=1)


class Complete(Body):
    operational_date: date
    orders: list[OrderVersion] = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=5, max_length=2000)
    confirmed: StrictBool


def fail(message, code='LEGACY_DELIVERY_BLOCKED', status=409):
    raise db.error(status, code, message)


def owner(user=Depends(auth.current_user)):
    ops.roles(user, 'owner')
    return user


def scope(c, user, sid):
    if central.association(c, sid):
        day, legs = central.authorized(c, user, sid, capability='shifts_view')
        anchor = day['anchor_shift_id']
    else:
        s = ops.lock_shift(c, sid)
        if not s:
            fail('الوردية غير متاحة.', status=404)
        p = multibrand.project(c, user, s['brand_id'])
        if not p:
            fail('الوردية خارج نطاق حسابك.', status=404)
        ops.shift_access(c, p, s)
        legs, anchor = [(p, s)], sid
    if any(s['kind'] != 'driver' for _, s in legs):
        fail('الاستثناء لطلبات وردية سائق فقط.')
    return anchor, legs


def pending(c, sids):
    return c.execute("SELECT t.id task_id,t.order_id,t.status task_status,t.shift_id,t.transfer_shift_id,"
        "o.version expected_version,o.total_halalas,o.brand_id,o.branch_id,o.snapshot,o.service,"
        "o.payment_status,o.preparation_status,o.delivery_status,o.completed_at,o.test_void "
        "FROM driver_tasks t JOIN orders o ON o.id=t.order_id WHERE t.shift_id=ANY(%s) "
        "AND t.status NOT IN ('delivered','cancelled','transferred') ORDER BY t.id FOR UPDATE OF t,o", (sids,)).fetchall()


def eligible(c, rows):
    from .order_amendments import active
    for r in rows:
        if (r['brand_id'] != 'enbishan' or r['service'] != 'delivery' or
                r['snapshot'].get('order_source') != 'delivery_center' or
                r['payment_status'] != 'unpaid' or r['completed_at'] or r['test_void'] or r['transfer_shift_id'] or
                r['preparation_status'] in ('cancelled', 'rejected', 'completed') or
                r['delivery_status'] in ('delivered', 'cancelled') or active(c, r['order_id'])):
            fail('يوجد طلب لا تنطبق عليه شروط طلب ساباد السابق غير المحصل؛ عالجه مستقلًا.')
        oid = r['order_id']
        if (c.execute('SELECT 1 FROM collections WHERE order_id=%s', (oid,)).fetchone() or
                c.execute('SELECT 1 FROM refunds WHERE order_id=%s', (oid,)).fetchone() or
                c.execute('SELECT 1 FROM fiscal_documents WHERE order_id=%s', (oid,)).fetchone() or
                c.execute('SELECT 1 FROM order_driver_attribution_corrections WHERE order_id=%s', (oid,)).fetchone()):
            fail('الطلب مرتبط بتحصيل أو مستند مالي؛ يلزم معالجة منفصلة.')


def preview_data(c, user, sid):
    anchor, legs = scope(c, user, sid)
    shifts = [s for _, s in legs]
    sids = [s['id'] for s in shifts]
    days = {day_of(s) for s in shifts}
    if (len(days) != 1 or any(s['status'] not in ('open', 'closing') or s.get('supplement_of_shift_id') or
            (s.get('delivery_window_snapshot') or {}).get('period', 'evening') != 'evening' for s in shifts)):
        fail('اختر وردية مسائية مفتوحة أو قيد الإقفال لنفس يوم التشغيل.')
    if next(iter(days)) >= datetime.now(ZoneInfo('Asia/Riyadh')).date():
        fail('الاستثناء مخصص ليوم سابق حسب تاريخ السعودية؛ لا يشمل طلبات اليوم أو المستقبل.')
    if c.execute('SELECT 1 FROM closure_reports WHERE shift_id=ANY(%s)', (sids,)).fetchone():
        fail('صدرت تقفيلة سابقة؛ استخدم التصحيح المحاسبي.')
    rows = pending(c, sids)
    if not rows:
        fail('لا توجد طلبات سابقة معلقة لهذه الوردية.')
    eligible(c, rows)
    by_shift={s['id']:s for s in shifts}
    if any(r['brand_id']!=by_shift[r['shift_id']]['brand_id'] or r['branch_id'] not in by_shift[r['shift_id']]['branch_ids'] or (r['snapshot'].get('manager_shift_id') and r['snapshot']['manager_shift_id']!=by_shift[r['shift_id']]['manager_shift_id']) for r in rows):
        fail('يوجد طلب خارج فروع وردية السائق.')
    orders = [dict(order_id=r['order_id'],task_id=r['task_id'],expected_version=r['expected_version'],
                   total_halalas=r['total_halalas'],brand_id=r['brand_id'],shift_id=r['shift_id'],
                   order_reference=r['snapshot'].get('order_reference') or r['order_id']) for r in rows]
    name = c.execute('SELECT display_name FROM users WHERE id=%s', (shifts[0]['user_id'],)).fetchone()['display_name']
    return dict(anchor_shift_id=anchor,operational_date=next(iter(days)),driver_name=name,
                orders=orders,system_total_halalas=sum(r['total_halalas'] for r in orders))


@router.get('/shifts/{sid}/preview')
def preview(sid: str, user=Depends(owner)):
    with db.tx() as c:
        central.lock_lanes(c)
        return ops.safe(preview_data(c, user, sid))


@router.post('/shifts/{sid}/complete')
def complete(sid: str, body: Complete, request: Request, user=Depends(owner)):
    with db.tx() as c:
        central.lock_lanes(c)
        # Re-authorize even idempotent retries after branch membership changes.
        scope(c,user,sid)
        def perform():
            if not body.confirmed:
                fail('أكد أن الطلبات سُلّمت بالفعل في ساباد.', status=422)
            data = preview_data(c, user, sid)
            if body.operational_date != data['operational_date']:
                fail('يوم التشغيل تغير؛ راجع المعاينة.')
            expected = {(r['order_id'],r['task_id'],r['expected_version']) for r in data['orders']}
            supplied = {(r.order_id,r.task_id,r.expected_version) for r in body.orders}
            if expected != supplied or len(supplied) != len(body.orders):
                fail('تغيرت الطلبات المعلقة؛ حدّث المعاينة قبل الاعتماد.')
            ids=[]
            for r in data['orders']:
                original = {'task':c.execute('SELECT * FROM driver_tasks WHERE id=%s',(r['task_id'],)).fetchone(),
                            'order':c.execute('SELECT * FROM orders WHERE id=%s',(r['order_id'],)).fetchone(),
                            'source':'Sabad','actual_delivery_time':None,'payment_recorded':False}
                aid=db.new_id('legacy_delivery_')
                c.execute('INSERT INTO legacy_delivery_attestations(id,order_id,task_id,shift_id,brand_id,operational_date,actor_id,reason,original) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (aid,r['order_id'],r['task_id'],r['shift_id'],r['brand_id'],body.operational_date,user['id'],body.reason,db.json(ops.safe(original))))
                c.execute("UPDATE driver_tasks SET status='delivered',reason=%s WHERE id=%s",('Legacy delivery attested: '+aid,r['task_id']))
                c.execute("UPDATE orders SET preparation_status='completed',delivery_status='delivered',version=version+1 WHERE id=%s",(r['order_id'],))
                evidence={'attestation_id':aid,'reason':body.reason,'actual_delivery_time':None,'payment_recorded':False,'source':'Sabad','operational_date':body.operational_date.isoformat()}
                db.event(c,r['order_id'],user['id'],'order.legacy_delivery_attested',evidence)
                db.audit(c,user['id'],'legacy.delivery_attested','order',r['order_id'],evidence)
                ids.append(aid)
            return {'attestation_ids':ids,'anchor_shift_id':data['anchor_shift_id'],'completed_count':len(ids),'shift_closed':False,'payments_changed':False}
        return ops.safe(db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'legacy.delivery:'+sid,body.model_dump(mode='json'),perform))
