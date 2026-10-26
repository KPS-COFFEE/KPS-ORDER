"""Append-only cancellation cases; operational closure never asserts a refund."""
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictBool
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
            allowed=o['branch_id'] in user.get('branch_ids',[]) and bool(c.execute('SELECT 1 FROM effective_driver_tasks WHERE order_id=%s AND driver_id=%s',(oid,user['id'])).fetchone())
        else:
            allowed=auth.can_access_order(user,o,c)
    if not allowed:raise db.error(404,'ORDER_NOT_FOUND','الطلب غير موجود ضمن صلاحياتك.')
    return o


def case(c, oid):
    row=c.execute("SELECT id,actor_id,created_at,details FROM order_events WHERE order_id=%s AND event_type='financial_case.opened' ORDER BY created_at DESC,id DESC LIMIT 1",(oid,)).fetchone()
    if not row:return None
    resolved=c.execute("SELECT actor_id,created_at,details FROM order_events WHERE order_id=%s AND event_type='financial_case.resolved' AND details->>'case_id'=%s ORDER BY created_at,id LIMIT 1",(oid,row['id'])).fetchone()
    from .order_amendments import balance
    from .fiscal_issuance import original
    order=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
    invoice=original(c,oid)
    kind='cancellation' if order['preparation_status'] in ('cancelled','rejected') else row['details'].get('kind','cancellation')
    collected=balance(c,order)
    from .order_financial_treatments import state as source_state
    financial=source_state(c,order,row['id'],collected_halalas=collected,review_resolved=bool(resolved)) if kind=='collection_shortfall' else {}
    return {**financial,'kind':kind,'collected_halalas':collected,'pending_amount_halalas':financial.get('pending_amount_halalas',max(0,order['total_halalas']-collected)) if kind=='collection_shortfall' else collected,'original_amount_halalas':order['total_halalas'],'payment_status':order['payment_status'],'invoice_number':invoice['number'] if invoice else None,'id':row['id'],'opened_at':row['created_at'],'requester_id':row['actor_id'],
            'responsible_role':'accounting','status':'resolved' if resolved else 'pending',
            'reason':row['details']['reason'],'resolution':resolved,'operationally_blocking':False}



def actual_payment_method(c, order_id, fallback):
    rows=c.execute("""SELECT p.method FROM effective_collections p WHERE p.order_id=%s
        GROUP BY p.method HAVING sum(p.amount_halalas-coalesce((SELECT sum(r.amount_halalas)
          FROM refunds r WHERE r.collection_id=p.id),0))>0""",(order_id,)).fetchall()
    methods={row['method'] for row in rows}
    return 'split' if len(methods)>1 else next(iter(methods),fallback)

def open_collection_case(c, order, user, reason, actual, remaining, shift_id, *, nonreceipt_evidence=None):
    """The order is locked by collection; never rewrite an invoice or assign blame."""
    details={'kind':'collection_shortfall','reason':reason,'actual_collected_halalas':actual,
             'remaining_halalas':remaining,'original_amount_halalas':order['total_halalas'],
             'shift_id':shift_id,'requester_id':user['id'],'responsible_role':'accounting',
             'financial_status':'pending','operationally_blocking':False,'responsibility':'unreviewed'}
    if nonreceipt_evidence:
        details['nonreceipt_evidence']=nonreceipt_evidence
        details['verified_zero_collection']=True
    existing=case(c,order['id'])
    name='financial_case.collection_updated' if existing and existing['status']=='pending' else 'financial_case.opened'
    db.event(c,order['id'],user['id'],name,details)
    db.audit(c,user['id'],name,'order',order['id'],details)


def documented_shortfall(c, order):
    pending=case(c,order['id'])
    return bool(pending and pending['kind']=='collection_shortfall' and pending['status']=='pending'
                and (pending['collected_halalas']>0 or c.execute("SELECT 1 FROM order_events WHERE id=%s AND details->>'verified_zero_collection'='true'",(pending['id'],)).fetchone()))



class Nonreceipt(Resolution):
    version: StrictInt = Field(ge=1)
    evidence: str = Field(min_length=3,max_length=2000,pattern=r'^[^\x00]+$')
    confirmed_zero_received: StrictBool


@router.post('/orders/{oid}/nonreceipt')
def nonreceipt(oid:str,body:Nonreceipt,request:Request,user=Depends(auth.current_user)):
    from .operations import mutate,lock_shift,shift_access
    if user['role'] not in ('driver','owner','delivery_manager') or not auth.has_permission(user,'payments_collect'):
        raise db.error(403,'NONRECEIPT_FORBIDDEN','توثيق عدم التحصيل للسائق المكلف ومدير مناوبته فقط.')
    with db.tx() as c:
        scoped(c,oid,user)
        def perform():
            o=scoped(c,oid,user,True)
            if body.version!=o['version']:raise db.error(409,'ORDER_VERSION_CONFLICT','تغير الطلب؛ حدّث تفاصيله قبل الإجراء.')
            from .branch_payments import online_order
            from .complimentary import is_free
            from .order_amendments import balance,guard
            if (not body.confirmed_zero_received or o['service']!='delivery' or online_order(o) or is_free(o)
                    or o['preparation_status'] in ('cancelled','rejected') or o.get('completed_at') is not None
                    or o['delivery_status'] in ('delivered','cancelled') or o['payment_status']!='unpaid'
                    or o['total_halalas']<=0 or balance(c,o)!=0):
                raise db.error(409,'NONRECEIPT_STATE','وثّق عدم التحصيل فقط عندما لم تستلم أي مبلغ؛ المدفوع الإلكتروني والضيافة لهما مسارهما.')
            t=c.execute("SELECT * FROM driver_tasks WHERE order_id=%s AND status IN ('picked_up','problem') AND picked_up_at IS NOT NULL FOR UPDATE",(oid,)).fetchone()
            if not t or user['role']=='driver' and t['driver_id']!=user['id']:
                raise db.error(409,'NONRECEIPT_PICKUP_REQUIRED','يلزم استلام فعلي مسجل من الفرع ومهمة السائق المكلف.')
            shift=lock_shift(c,t['shift_id']);shift_access(c,user,shift)
            if shift['status']!='open':raise db.error(409,'NONRECEIPT_SHIFT_CLOSED','المناوبة ليست مفتوحة؛ احِل الواقعة للمحاسبة دون تعديل التقفيلة.')
            guard(c,o,driver_id=t['driver_id'],require_branch=True)
            pending=case(c,oid)
            if pending and pending['status']=='pending':
                raise db.error(409,'NONRECEIPT_ALREADY_RECORDED','يوجد ملف مالي معلق؛ أكمل المتابعة داخله.')
            open_collection_case(c,o,user,body.reason,0,o['total_halalas'],shift['id'],nonreceipt_evidence=body.evidence)
            c.execute('UPDATE orders SET version=version+1 WHERE id=%s',(oid,))
            return {'order_id':oid,'actual_collected_halalas':0,'financial_case':case(c,oid)}
        return mutate(c,user,request,'financial-case.nonreceipt:'+oid,body.model_dump(),perform)

def guard_financial_closure(c, *, shift_id=None, brand_id=None, branch_ids=None, before=None):
    """A recorded receipt/operational closure may proceed; financial approval may not."""
    from .aggregate_driver_closure import guard_financial_allocation
    guard_financial_allocation(c,shift_id=shift_id,brand_id=brand_id,branch_ids=branch_ids,before=before)
    pending_orders=c.execute("""SELECT o.id FROM orders o
        WHERE (%s::text IS NULL OR o.brand_id=%s)
          AND (%s::jsonb IS NULL OR %s::jsonb ? o.branch_id)
          AND (%s::timestamptz IS NULL OR o.created_at<%s)
          AND (%s::text IS NULL OR EXISTS(SELECT 1 FROM driver_tasks t JOIN shifts s ON s.id=t.shift_id
                WHERE t.order_id=o.id AND (s.id=%s OR s.manager_shift_id=%s)))
          AND EXISTS(SELECT 1 FROM order_events e WHERE e.order_id=o.id AND e.event_type='financial_case.opened'
                AND e.details->>'kind'='collection_shortfall'
                AND NOT EXISTS(SELECT 1 FROM order_events r WHERE r.order_id=o.id AND r.event_type='financial_case.resolved' AND r.details->>'case_id'=e.id))
        """,(brand_id,brand_id,db.json(branch_ids) if branch_ids is not None else None,
                     db.json(branch_ids) if branch_ids is not None else None,before,before,shift_id,shift_id,shift_id)).fetchall()
    for pending in pending_orders:
        order=c.execute('SELECT * FROM orders WHERE id=%s',(pending['id'],)).fetchone()
        from .order_financial_treatments import state as source_state,current_case
        if source_state(c,order,current_case(c,order)['id'])['period_approval_allowed']:continue
        raise db.error(409,'COLLECTION_CASE_PENDING','يوجد فرق تحصيل بانتظار المحاسبة؛ أكمل معالجة الطلب قبل الاعتماد المالي. استلام النقد والإقفال التشغيلي متاحان.')


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
            previous_case=case(c,oid)
            if required and (not previous_case or previous_case['status']=='resolved'):db.event(c,oid,user['id'],'financial_case.opened',details)
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
            from .financial_clarifications import guard_case_resolution
            guard_case_resolution(c,oid,pending['id'])
            from .order_amendments import balance
            if pending['kind']=='collection_shortfall':
                from .order_financial_treatments import effective_claim
                if balance(c,o)!=effective_claim(c,o) or (o['payment_status']!='paid' and effective_claim(c,o)==o['total_halalas']):
                    raise db.error(409,'ACTUAL_COLLECTION_REQUIRED','فرق التحصيل ما زال قائمًا؛ وثّق تحصيل المتبقي قبل الإقفال. الملاحظة لا تسوّي المبلغ.')
            elif balance(c,o)!=0 or o['payment_status'] not in ('unpaid','refunded'):
                raise db.error(409,'ACTUAL_REFUND_REQUIRED','أكمل الاسترداد الفعلي بالمسار المعتمد أولًا؛ الإلغاء ليس استردادًا.')
            if pending['kind']!='collection_shortfall':
                from .fiscal_issuance import credit_on_cancellation
                credit_on_cancellation(c,o,user,body.reason)
            details={'case_id':pending['id'],'reason':body.reason,'approver_id':user['id'],'executor_id':user['id']}
            if pending['kind']=='collection_shortfall':
                from .order_financial_treatments import state as source_state
                details['source_financial_state']=source_state(c,o,pending['id'])
            db.event(c,oid,user['id'],'financial_case.resolved',details)
            db.audit(c,user['id'],'financial_case.resolved','order',oid,details)
            return {'financial_case':case(c,oid)}
        return db.idempotent(c,user['id'],idempotency_key,'financial-case.resolve:'+oid,body.model_dump(),perform)


class Recovery(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    amount_halalas:StrictInt=Field(gt=0,le=100000000000)
    method:Literal['cash','card']
    shift_id:str|None=None
    reference:str=Field(min_length=3,max_length=200,pattern=r'^[^\x00]+$')
    reason:str=Field(min_length=3,max_length=1000,pattern=r'^[^\x00]+$')
    version:StrictInt=Field(ge=1)


class FreeItemReturn(Resolution):
    version:StrictInt=Field(ge=1)
    line_index:StrictInt=Field(ge=0,le=200)
    quantity:StrictInt=Field(ge=1,le=1000)
    received_by:Literal['driver','branch']


def free_items(c,o):
    events=c.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='order.free_item_returned'",(o['id'],)).fetchall()
    result=[]
    for index,item in enumerate(o['snapshot'].get('items',[])):
        if item.get('line_total_halalas')!=0:continue
        returned=sum(e['details']['quantity'] for e in events if e['details']['line_index']==index)
        result.append({'line_index':index,'name':item.get('name',''),'quantity':item['quantity'],'returned':returned,'remaining':item['quantity']-returned})
    return result


@router.post('/orders/{oid}/free-item-return')
def free_item_return(oid:str,body:FreeItemReturn,user=Depends(auth.require_roles('owner','delivery_manager')),idempotency_key:str|None=Header(None)):
    if not auth.has_permission(user,'support_manage'):raise db.error(403,'STAFF_PERMISSION_FORBIDDEN','صلاحية متابعة المشكلات غير متاحة.')
    with db.tx() as c:
        scoped(c,oid,user)
        def perform():
            o=scoped(c,oid,user,True)
            if o['version']!=body.version:raise db.error(409,'ORDER_VERSION_CONFLICT','تغير الطلب؛ حدّث الصفحة.')
            line=next((x for x in free_items(c,o) if x['line_index']==body.line_index),None)
            if not line or body.quantity>line['remaining']:raise db.error(422,'FREE_RETURN_QUANTITY','اختر صنفًا مجانيًا وكمية لم تُسجّل مرتجعة من قبل.')
            details={**body.model_dump(),'name':line['name'],'financial_effect_halalas':0,'invoice_total_halalas':o['total_halalas']}
            db.event(c,oid,user['id'],'order.free_item_returned',details)
            db.audit(c,user['id'],'order.free_item_returned','order',oid,details)
            c.execute('UPDATE orders SET version=version+1 WHERE id=%s',(oid,))
            return {'financial_effect_halalas':0,'invoice_total_halalas':o['total_halalas'],'message':'تم توثيق إرجاع الصنف المجاني؛ لا يتغير مبلغ الفاتورة أو التحصيل.'}
        return db.idempotent(c,user['id'],idempotency_key,'free-item-return:'+oid,body.model_dump(),perform)


@router.get('/orders/{oid}/recovery-context')
def recovery_context(oid:str,user=Depends(auth.current_user)):
    if not accounting(user):raise db.error(403,'ACCOUNTING_REVIEW_REQUIRED','المعالجة المالية للمحاسبة فقط.')
    with db.tx() as c:
        o=scoped(c,oid,user); pending=case(c,oid)
        if not pending or pending['status']!='pending' or pending['kind']!='collection_shortfall':
            raise db.error(409,'COLLECTION_CASE_REQUIRED','لا يوجد فرق تحصيل معلق.')
        from .operations import shift_access
        shifts=[]
        for s in c.execute("SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.brand_id=%s AND s.kind='manager' AND s.status IN ('open','closing') AND s.branch_ids ? %s ORDER BY s.opened_at",(o['brand_id'],o['branch_id'])).fetchall():
            try:shift_access(c,user,s,capability='accounting_review')
            except HTTPException as exc:
                if exc.status_code in (403,404):continue
                raise
            shifts.append({k:s[k] for k in ('id','display_name','shift_no')})
        return {'version':o['version'],'financial_case':pending,'shifts':shifts}


@router.post('/orders/{oid}/recover')
def recover(oid:str,body:Recovery,user=Depends(auth.current_user),idempotency_key:str|None=Header(None)):
    if not accounting(user):raise db.error(403,'ACCOUNTING_REVIEW_REQUIRED','توثيق معالجة فرق التحصيل للمحاسبة فقط.')
    with db.tx() as c:
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('operations:'+user['brand_id'],))
        scoped(c,oid,user)
        def perform():
            o=scoped(c,oid,user,True); pending=case(c,oid)
            if o['version']!=body.version:raise db.error(409,'ORDER_VERSION_CONFLICT','تغير الطلب؛ حدّث الصفحة.')
            if not pending or pending['status']!='pending' or pending['kind']!='collection_shortfall':
                raise db.error(409,'COLLECTION_CASE_REQUIRED','لا يوجد فرق تحصيل معلق.')
            if body.amount_halalas>pending['pending_amount_halalas']:
                raise db.error(422,'COLLECTION_RECOVERY_AMOUNT','المبلغ يتجاوز المتبقي للتحصيل.')
            from .operations import lock_shift,shift_access,balance,event,valid_cash_amount
            sid=body.shift_id
            if body.method=='cash':
                shift=lock_shift(c,sid) if sid else None
                if not shift or shift['status'] not in ('open','closing') or shift['kind']!='manager' or shift['brand_id']!=o['brand_id'] or o['branch_id'] not in shift['branch_ids']:
                    raise db.error(409,'RECOVERY_CASH_CUSTODY_REQUIRED','اختر عهدة مدير مفتوحة لاستلام النقد الفعلي؛ لا نعدّل تقفيلة السائق السابقة.')
                shift_access(c,user,shift,capability='accounting_review')
                valid_cash_amount(balance(c,shift)+body.amount_halalas)
            elif sid:
                raise db.error(422,'RECOVERY_CARD_CUSTODY','عملية الشبكة لا تُضاف لعهدة نقدية.')
            cid=db.new_id('recovery_')
            c.execute('INSERT INTO collections(id,order_id,shift_id,collector_id,method,amount_halalas,reference,amendment_revision,payment_piece) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (cid,oid,sid,user['id'],body.method,body.amount_halalas,body.reference,o.get('amendment_revision',0),cid))
            if sid:event(c,sid,body.amount_halalas,'collection',cid,user['id'],body.reason+' / '+body.reference)
            paid=body.amount_halalas==pending['pending_amount_halalas']
            method=actual_payment_method(c,oid,o['payment_method'])
            c.execute('UPDATE orders SET payment_status=%s,payment_method=%s,version=version+1 WHERE id=%s',('paid' if paid else 'unpaid',method,oid))
            details={**body.model_dump(),'collection_id':cid,'case_id':pending['id'],'responsibility':'unreviewed'}
            db.event(c,oid,user['id'],'financial_case.recovery_recorded',details)
            db.audit(c,user['id'],'financial_case.recovery_recorded','order',oid,details)
            return {'financial_case':case(c,oid)}
        return db.idempotent(c,user['id'],idempotency_key,'financial-case.recover:'+oid,body.model_dump(),perform)


@router.get('')
def inbox(user=Depends(auth.require_roles('owner','admin','delivery_manager','branch_manager'))):
    if user['role']=='admin' and not accounting(user):raise db.error(403,'ACCOUNTING_REVIEW_REQUIRED','صلاحية المحاسبة مطلوبة.')
    with db.tx() as c:
        candidates=c.execute("SELECT DISTINCT o.id,o.order_no,o.branch_id FROM orders o JOIN order_events e ON e.order_id=o.id WHERE o.brand_id=%s AND e.event_type='financial_case.opened' AND NOT EXISTS(SELECT 1 FROM order_events r WHERE r.order_id=o.id AND r.event_type='financial_case.resolved' AND r.details->>'case_id'=e.id) ORDER BY o.id",(user['brand_id'],)).fetchall()
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
        tasks=c.execute('SELECT t.id,t.driver_id,u.display_name driver_name,t.shift_id,t.status,t.reason,t.created_at,t.accepted_at,t.picked_up_at,t.delivered_at FROM effective_driver_tasks t LEFT JOIN users u ON u.id=t.driver_id WHERE t.order_id=%s'+(' AND t.driver_id=%s' if user['role']=='driver' else '')+' ORDER BY t.created_at,t.id',(oid,user['id']) if user['role']=='driver' else (oid,)).fetchall()
        events=c.execute('SELECT e.id,e.event_type,e.actor_id,u.display_name actor_name,e.created_at,e.details FROM order_events e LEFT JOIN users u ON u.id=e.actor_id WHERE e.order_id=%s ORDER BY e.created_at,e.id',(oid,)).fetchall()
        if user['role']!='driver':
            for event in events:
                details=dict(event['details'] or {})
                for field in ('requester','approver','executor','operational_approver','financial_approver'):
                    uid=details.get(field+'_id')
                    if uid:
                        person=c.execute('SELECT display_name FROM users WHERE id=%s',(uid,)).fetchone()
                        details[field+'_name']=person['display_name'] if person else None
                complimentary=details.get('complimentary')
                if isinstance(complimentary,dict) and complimentary.get('authorized_by'):
                    person=c.execute('SELECT display_name FROM users WHERE id=%s',(complimentary['authorized_by'],)).fetchone()
                    details['complimentary']={**complimentary,'authorized_by_name':person['display_name'] if person else None}
                event['details']=details
        # Drivers see their retained assignment, not administrative financial notes.
        if user['role']=='driver':events=[{k:v for k,v in e.items() if k!='details'} for e in events if e['event_type'].startswith(('task.','order.operationally_cancelled'))]
        collections=[] if user['role']=='driver' else c.execute('SELECT id,collector_id,method,amount_halalas,created_at FROM effective_collections WHERE order_id=%s ORDER BY created_at,id',(oid,)).fetchall()
        refunds=[] if user['role']=='driver' else c.execute('SELECT id,authorized_by,method,amount_halalas,reason,created_at FROM refunds WHERE order_id=%s ORDER BY created_at,id',(oid,)).fetchall()
        online_collections=[] if user['role']=='driver' else c.execute('SELECT id,attempt_id,provider,amount_halalas,disposition,created_at FROM branch_online_receipts WHERE order_id=%s ORDER BY created_at,id',(oid,)).fetchall()
        online_refunds=[] if user['role']=='driver' else c.execute('SELECT id,attempt_id,authorized_by,amount_halalas,reason,status,created_at,verified_at FROM branch_online_refunds WHERE order_id=%s ORDER BY created_at,id',(oid,)).fetchall()
        from .driver_incentives import source_rows,classify
        followups=[]
        if user['role']!='driver':
            for source in source_rows(c,user,order_id=oid):
                entry=classify(source)
                if entry['state']=='pending_review':
                    followups.append({'type':'driver_incentive_review','status':'review_required','responsible_role':'delivery_manager','shift_id':entry['shift_id'],'task_id':entry['task_id'],'order_id':oid,'review_url':'/api/driver-incentives/shifts/'+entry['shift_id']})
        from .administrative_guidance import order as order_guidance
        from .order_requests import rows as request_rows
        pending_case=case(c,oid) if user['role']!='driver' else None
        guidance=order_guidance(o,financial_case=pending_case,requests=request_rows(c,oid),amendments=c.execute('SELECT id,status,expires_at FROM order_amendments WHERE order_id=%s ORDER BY created_at DESC',(oid,)).fetchall(),viewer_role=user['role'],events=events) if user['role']!='driver' else None
        return {'guidance':guidance,'free_items':[] if user['role']=='driver' else free_items(c,o),'operational_followups':followups,'online_collections':online_collections,'online_refunds':online_refunds,'collections':collections,'refunds':refunds,'order_id':oid,'order_no':o['order_no'],'preparation_status':o['preparation_status'],'delivery_status':o['delivery_status'],
                'payment_status':o['payment_status'],'version':o['version'],'tasks':tasks,'timeline':events,
                'financial_case':pending_case}


@router.get('/orders/{oid}/refund-context')
def refund_context(oid:str,user=Depends(auth.current_user)):
    if not accounting(user):raise db.error(403,'ACCOUNTING_REVIEW_REQUIRED','معالجة الاسترداد للمحاسبة فقط.')
    with db.tx() as c:
        o=scoped(c,oid,user)
        pending=case(c,oid)
        if not pending or pending['status']!='pending':raise db.error(409,'FINANCIAL_CASE_NOT_PENDING','لا توجد معالجة مالية معلقة.')
        if pending['kind']=='collection_shortfall':raise db.error(409,'COLLECTION_NOT_REFUND','هذه معالجة نقص تحصيل، وليست استردادًا للعميل.')
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
