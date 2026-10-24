"""Append-only cancellation cases; operational closure never asserts a refund."""
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from . import auth, db

router = APIRouter(prefix='/api/order-financial-cases')


class Cancellation(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    physical_resolution: Literal['returned_to_branch','customer_kept'] | None = None
    version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=3, max_length=1000, pattern=r'^[^\x00]+$')


class Resolution(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reason: str = Field(min_length=3, max_length=1000, pattern=r'^[^\x00]+$')


def accounting(user):
    return user['role']=='owner' or (user['role']=='admin' and auth.has_permission(user,'accounting_review'))


def scoped(c, oid, user, lock=False):
    o=c.execute('SELECT * FROM orders WHERE id=%s'+(' FOR UPDATE' if lock else ''),(oid,)).fetchone()
    allowed=False
    if o and user.get('active',True) and o['brand_id']==user['brand_id']:
        if user['role']=='admin' and accounting(user):
            allowed=o['branch_id'] in user.get('branch_ids',[])
        elif user['role']=='driver':
            # Historical read access, never a new mutation permission.
            allowed=o['branch_id'] in user.get('branch_ids',[]) and bool(c.execute('SELECT 1 FROM driver_tasks WHERE order_id=%s AND driver_id=%s',(oid,user['id'])).fetchone())
        else:
            allowed=auth.can_access_order(user,o,c)
    if not allowed:raise db.error(404,'ORDER_NOT_FOUND','الطلب غير موجود ضمن صلاحياتك.')
    return o


def case(c, oid):
    row=c.execute("SELECT id,actor_id,created_at,details FROM order_events WHERE order_id=%s AND event_type='financial_case.opened' ORDER BY created_at,id LIMIT 1",(oid,)).fetchone()
    if not row:return None
    resolved=c.execute("SELECT actor_id,created_at,details FROM order_events WHERE order_id=%s AND event_type='financial_case.resolved' ORDER BY created_at,id LIMIT 1",(oid,)).fetchone()
    from .order_amendments import balance
    from .fiscal_issuance import original
    order=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
    invoice=original(c,oid)
    return {'pending_amount_halalas':balance(c,order),'original_amount_halalas':order['total_halalas'],'payment_status':order['payment_status'],'invoice_number':invoice['number'] if invoice else None,'id':row['id'],'opened_at':row['created_at'],'requester_id':row['actor_id'],
            'responsible_role':'accounting','status':'resolved' if resolved else 'pending',
            'reason':row['details']['reason'],'resolution':resolved,'operationally_blocking':False}


def needs_accounting(c,o):
    from .fiscal_issuance import original
    from .complimentary import is_free
    return bool(original(c,o['id']) or c.execute('SELECT 1 FROM collections WHERE order_id=%s LIMIT 1',(o['id'],)).fetchone() or (o['payment_status']!='unpaid' and not is_free(o)))


def invoice_guard(c,o):
    from .fiscal_issuance import original
    if original(c,o['id']):
        raise db.error(409,'ACCOUNTING_CANCELLATION_REQUIRED','للطلب فاتورة؛ استخدم إلغاء التشغيل مع إحالة المعالجة للمحاسبة.')


@router.post('/orders/{oid}/cancel')
def cancel(oid:str,body:Cancellation,user=Depends(auth.require_roles('owner','delivery_manager','branch_manager')),idempotency_key:str|None=Header(None)):
    if not auth.has_permission(user,'orders_cancel'):
        raise db.error(403,'STAFF_PERMISSION_FORBIDDEN','صلاحية إلغاء الطلبات متوقفة.')
    with db.tx() as c:
        visible=scoped(c,oid,user)
        from .order_amendments import reviewer
        if not reviewer(visible,user):raise db.error(403,'CANCELLATION_REVIEWER','الإلغاء من مسؤول الخدمة المختص.')
        from . import morning_delivery, morning_dispatch
        morning_delivery.authorize_order(c,user,visible,preparation=user['role']=='branch_manager')
        if morning_dispatch.actual_morning(visible):morning_dispatch.lock_operations(c)
        def perform():
            o=scoped(c,oid,user,True)
            if o['version']!=body.version:raise db.error(409,'ORDER_VERSION_CONFLICT','تغير الطلب؛ حدّث الصفحة.')
            if o['completed_at'] or o['delivery_status']=='delivered' or o['preparation_status'] in ('cancelled','rejected','completed'):
                raise db.error(409,'CANCELLATION_TERMINAL','الطلب منتهٍ؛ استخدم معالجة المرتجع المعتمدة.')
            tasks=c.execute("SELECT * FROM driver_tasks WHERE order_id=%s AND status NOT IN ('cancelled','reassigned','transferred','delivered') FOR UPDATE",(oid,)).fetchall()
            if tasks and user['role']=='branch_manager':
                raise db.error(403,'DELIVERY_CANCELLATION_MANAGER','إلغاء مهمة السائق من مدير التوصيل.')
            for sid in sorted({t['shift_id'] for t in tasks}):
                from .operations import lock_shift,shift_access
                shift=lock_shift(c,sid);shift_access(c,user,shift)
                if shift['status'] not in ('open','closing'):raise db.error(409,'CANCELLATION_SHIFT_CLOSED','المهمة ضمن تقفيلة محفوظة؛ تحتاج مراجعة مستقلة.')
            for t in tasks:
                if t.get('picked_up_at') and not body.physical_resolution:
                    raise db.error(409,'CANCELLATION_PHYSICAL_RESOLUTION_REQUIRED','الطلب مع السائق؛ وثّق معالجة الاستلام والموجودات عبر بلاغ مدير التوصيل أولًا.')
            from .branch_payments import online_order
            if online_order(o) and c.execute("SELECT 1 FROM branch_payment_attempts WHERE order_id=%s AND status IN ('pending','superseded') LIMIT 1",(oid,)).fetchone():
                raise db.error(409,'PAYMENT_RECONCILIATION_REQUIRED','سوّ محاولة الدفع المعلقة مع مزود الدفع قبل إلغاء التشغيل.')
            required=needs_accounting(c,o)
            requests=c.execute("SELECT id,actor_id FROM order_events e WHERE order_id=%s AND event_type='customer_request.opened' AND NOT EXISTS(SELECT 1 FROM order_events d WHERE d.order_id=e.order_id AND d.event_type='customer_request.decided' AND d.details->>'request_id'=e.id) ORDER BY created_at,id",(oid,)).fetchall()
            details={'manager_shift_id':(user.get('_morning_manager_context') or {}).get('shift_id'),'customer_requests':requests,'reason':body.reason,'requester_id':user['id'],'executor_id':user['id'],
                     'operational_approver_id':user['id'],'financial_approver_id':None,
                     'previous':{'preparation_status':o['preparation_status'],'delivery_status':o['delivery_status'],'payment_status':o['payment_status']},
                     'financial_status':'pending' if required else 'not_required','physical_resolution':body.physical_resolution}
            if required and not case(c,oid):db.event(c,oid,user['id'],'financial_case.opened',details)
            for t in tasks:
                c.execute("UPDATE driver_tasks SET status='cancelled',reason=%s WHERE id=%s",(body.reason,t['id']))
                db.event(c,oid,user['id'],'task.cancelled',{'task_id':t['id'],'driver_id':t['driver_id'],'shift_id':t['shift_id'],'previous_task_status':t['status'],**details})
            c.execute("UPDATE orders SET preparation_status='cancelled',delivery_status=CASE WHEN service='delivery' THEN 'cancelled' ELSE delivery_status END,version=version+1 WHERE id=%s",(oid,))
            db.event(c,oid,user['id'],'order.operationally_cancelled',details)
            db.audit(c,user['id'],'order.operationally_cancelled','order',oid,details)
            for request in requests:
                decision={'request_id':request['id'],'status':'resolved','note':body.reason,'financial_status':details['financial_status'],'resolution_scope':'operational_cancellation'}
                db.event(c,oid,user['id'],'customer_request.decided',decision)
                db.audit(c,user['id'],'customer_request.decided','order',oid,decision)
            followups=[{'type':'driver_incentive_review','status':'review_required','responsible_role':'delivery_manager','shift_id':t['shift_id'],'task_id':t['id'],'order_id':oid,'review_url':'/api/driver-incentives/shifts/'+t['shift_id']} for t in tasks]
            return {'operational_followups':followups,'order_id':oid,'operational_status':'cancelled','payment_status':o['payment_status'],'financial_case':case(c,oid)}
        return db.idempotent(c,user['id'],idempotency_key,'financial-case.cancel:'+oid,body.model_dump(),perform)


@router.post('/orders/{oid}/resolve')
def resolve(oid:str,body:Resolution,user=Depends(auth.current_user),idempotency_key:str|None=Header(None)):
    if not accounting(user):raise db.error(403,'ACCOUNTING_REVIEW_REQUIRED','المعالجة المالية للمحاسبة فقط.')
    with db.tx() as c:
        scoped(c,oid,user)
        def perform():
            o=scoped(c,oid,user,True); pending=case(c,oid)
            if not pending or pending['status']!='pending':raise db.error(409,'FINANCIAL_CASE_NOT_PENDING','لا توجد معالجة مالية معلقة.')
            from .order_amendments import balance
            if balance(c,o)!=0 or o['payment_status'] not in ('unpaid','refunded'):
                raise db.error(409,'ACTUAL_REFUND_REQUIRED','أكمل الاسترداد الفعلي بالمسار المعتمد أولًا؛ الإلغاء ليس استردادًا.')
            from .fiscal_issuance import credit_on_cancellation
            credit_on_cancellation(c,o,user,body.reason)
            details={'case_id':pending['id'],'reason':body.reason,'approver_id':user['id'],'executor_id':user['id']}
            db.event(c,oid,user['id'],'financial_case.resolved',details)
            db.audit(c,user['id'],'financial_case.resolved','order',oid,details)
            return {'financial_case':case(c,oid)}
        return db.idempotent(c,user['id'],idempotency_key,'financial-case.resolve:'+oid,body.model_dump(),perform)


@router.get('')
def inbox(user=Depends(auth.require_roles('owner','admin','delivery_manager','branch_manager'))):
    if user['role']=='admin' and not accounting(user):raise db.error(403,'ACCOUNTING_REVIEW_REQUIRED','صلاحية المحاسبة مطلوبة.')
    with db.tx() as c:
        candidates=c.execute("SELECT DISTINCT o.id,o.order_no,o.branch_id FROM orders o JOIN order_events e ON e.order_id=o.id WHERE o.brand_id=%s AND e.event_type='financial_case.opened' AND NOT EXISTS(SELECT 1 FROM order_events r WHERE r.order_id=o.id AND r.event_type='financial_case.resolved') ORDER BY o.id",(user['brand_id'],)).fetchall()
        result=[]
        for row in candidates:
            try:scoped(c,row['id'],user)
            except HTTPException as exc:
                if getattr(exc,'status_code',None)==404:continue
                raise
            result.append({**row,'financial_case':case(c,row['id'])})
        return {'cases':result}


@router.get('/orders/{oid}/journey')
def journey(oid:str,user=Depends(auth.require_roles('owner','admin','delivery_manager','branch_manager','driver'))):
    with db.tx() as c:
        o=scoped(c,oid,user)
        tasks=c.execute('SELECT t.id,t.driver_id,u.display_name driver_name,t.shift_id,t.status,t.reason,t.created_at,t.accepted_at,t.picked_up_at,t.delivered_at FROM driver_tasks t LEFT JOIN users u ON u.id=t.driver_id WHERE t.order_id=%s'+(' AND t.driver_id=%s' if user['role']=='driver' else '')+' ORDER BY t.created_at,t.id',(oid,user['id']) if user['role']=='driver' else (oid,)).fetchall()
        events=c.execute('SELECT e.id,e.event_type,e.actor_id,u.display_name actor_name,e.created_at,e.details FROM order_events e LEFT JOIN users u ON u.id=e.actor_id WHERE e.order_id=%s ORDER BY e.created_at,e.id',(oid,)).fetchall()
        if user['role']!='driver':
            for event in events:
                details=dict(event['details'] or {})
                for field in ('requester','approver','executor','operational_approver','financial_approver'):
                    uid=details.get(field+'_id')
                    if uid:
                        person=c.execute('SELECT display_name FROM users WHERE id=%s',(uid,)).fetchone()
                        details[field+'_name']=person['display_name'] if person else None
                event['details']=details
        # Drivers see their retained assignment, not administrative financial notes.
        if user['role']=='driver':events=[{k:v for k,v in e.items() if k!='details'} for e in events if e['event_type'].startswith(('task.','order.operationally_cancelled'))]
        collections=[] if user['role']=='driver' else c.execute('SELECT id,collector_id,method,amount_halalas,created_at FROM collections WHERE order_id=%s ORDER BY created_at,id',(oid,)).fetchall()
        refunds=[] if user['role']=='driver' else c.execute('SELECT id,authorized_by,method,amount_halalas,reason,created_at FROM refunds WHERE order_id=%s ORDER BY created_at,id',(oid,)).fetchall()
        online_collections=[] if user['role']=='driver' else c.execute('SELECT id,attempt_id,provider,amount_halalas,disposition,created_at FROM branch_online_receipts WHERE order_id=%s ORDER BY created_at,id',(oid,)).fetchall()
        online_refunds=[] if user['role']=='driver' else c.execute('SELECT id,attempt_id,authorized_by,amount_halalas,reason,status,created_at,verified_at FROM branch_online_refunds WHERE order_id=%s ORDER BY created_at,id',(oid,)).fetchall()
        return {'online_collections':online_collections,'online_refunds':online_refunds,'collections':collections,'refunds':refunds,'order_id':oid,'order_no':o['order_no'],'preparation_status':o['preparation_status'],'delivery_status':o['delivery_status'],
                'payment_status':o['payment_status'],'version':o['version'],'tasks':tasks,'timeline':events,
                'financial_case':None if user['role']=='driver' else case(c,oid)}


@router.get('/orders/{oid}/refund-context')
def refund_context(oid:str,user=Depends(auth.current_user)):
    if not accounting(user):raise db.error(403,'ACCOUNTING_REVIEW_REQUIRED','معالجة الاسترداد للمحاسبة فقط.')
    with db.tx() as c:
        o=scoped(c,oid,user)
        pending=case(c,oid)
        if not pending or pending['status']!='pending':raise db.error(409,'FINANCIAL_CASE_NOT_PENDING','لا توجد معالجة مالية معلقة.')
        from .operations import available_balance,shift_access
        shifts=[]
        for shift in c.execute("SELECT * FROM shifts WHERE brand_id=%s AND status IN ('open','closing') ORDER BY opened_at,id",(o['brand_id'],)).fetchall():
            try:shift_access(c,user,shift,capability='accounting_review')
            except HTTPException as exc:
                if exc.status_code in (403,404):continue
                raise
            shifts.append({**{k:shift[k] for k in ('id','kind','status','user_id','branch_ids')},'available_balance_halalas':available_balance(c,shift)})
        from .branch_payments import online_order
        online=online_order(o)
        methods=c.execute("SELECT p.method,sum(p.amount_halalas-coalesce((SELECT sum(r.amount_halalas) FROM refunds r WHERE r.collection_id=p.id),0))::bigint available_halalas FROM collections p WHERE p.order_id=%s GROUP BY p.method HAVING sum(p.amount_halalas-coalesce((SELECT sum(r.amount_halalas) FROM refunds r WHERE r.collection_id=p.id),0))>0 ORDER BY p.method",(oid,)).fetchall()
        return {'order':{'id':oid,'total_halalas':o['total_halalas'],'payment_status':o['payment_status'],'payment_method':o['payment_method'],'snapshot':{'items':o['snapshot'].get('items',[])}},'shifts':shifts,'financial_case':pending,'methods':methods,'online_payment':online,'manual_refund_allowed':not online,'refund_route':'provider' if online else 'manual'}
