"""Manager approved physical partial returns; never mutate the original sale or cash."""
from typing import Literal
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from . import auth, db

router = APIRouter(prefix='/api/delivery-partial-returns', tags=['delivery-partial-returns'])
REQUESTED = 'delivery.partial_return.requested'
DECIDED = 'delivery.partial_return.decided'
APPROVED = 'delivery.partial_return.approved'

class Line(BaseModel):
    model_config = ConfigDict(extra='forbid')
    line_index: StrictInt = Field(ge=0, le=200)
    quantity: StrictInt = Field(gt=0, le=1000)

class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    version: StrictInt = Field(ge=1)
    lines: list[Line] = Field(min_length=1, max_length=50)
    cause: Literal['customer_reduction', 'branch_error']
    reason: str = Field(min_length=3, max_length=1000, pattern=r'^[^\x00]+$')

class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    action: Literal['approve', 'reject']
    reason: str = Field(min_length=3, max_length=1000, pattern=r'^[^\x00]+$')
    approve_below_minimum: StrictBool = False
    physical_resolution: Literal['with_driver_return_to_branch', 'received_by_branch'] | None = None


def approved(c, oid):
    return [r['details'] for r in c.execute('SELECT details FROM order_events WHERE order_id=%s AND event_type=%s ORDER BY created_at,id', (oid, APPROVED)).fetchall()]


def value(order, lines, previous):
    """Allocate the immutable discounted item value; fees never become item credit."""
    items = order['snapshot']['items']
    gross = sum(i['line_total_halalas'] for i in items)
    net = min(gross, max(0, order['total_halalas'] - order['delivery_fee_halalas']))
    if not gross: raise db.error(422, 'PARTIAL_RETURN_NO_VALUE', 'المرتجع المجاني لا يخفض التحصيل؛ استخدم إرجاع الصنف المجاني.')
    seen = set(); credit = 0
    # A retained gift cannot disguise returning the whole purchased order.
    retained = sum(i['quantity'] - previous.get(index,0) for index,i in enumerate(items) if i['line_total_halalas']>0)
    for line in lines:
        if line.line_index in seen or line.line_index >= len(items):
            raise db.error(422, 'PARTIAL_RETURN_LINE', 'اختر كل سطر مرة واحدة من الفاتورة الأصلية.')
        seen.add(line.line_index)
        i = items[line.line_index]
        if i['line_total_halalas']>0:retained-=line.quantity
        prior = previous.get(line.line_index, 0); qty = i['quantity']
        if prior + line.quantity > qty:
            raise db.error(422, 'PARTIAL_RETURN_QUANTITY', 'الكمية تتجاوز المتبقي من الصنف.')
        units = i.get('units') or []
        prices = {u.get('price_halalas') for u in units}
        if len(prices) > 1 and prior + line.quantity < qty:
            raise db.error(409, 'PARTIAL_RETURN_UNIT_AMBIGUOUS', 'تختلف أسعار وحدات الصنف؛ أحل تحديد الوحدة للمحاسبة.')
        denominator = gross * qty
        credit += (i['line_total_halalas'] * net * (prior + line.quantity) // denominator) - (i['line_total_halalas'] * net * prior // denominator)
    if retained <= 0:
        raise db.error(422, 'PARTIAL_RETURN_FULL', 'إرجاع الطلب كاملًا يحتاج مسار الإلغاء والمرتجع الكامل.')
    if credit <= 0: raise db.error(422, 'PARTIAL_RETURN_NO_VALUE', 'الأصناف المجانية لا تخفض التحصيل.')
    return credit


def minimum_policy(c, order):
    frozen=order['snapshot'].get('minimum_order_halalas')
    if type(frozen) is int and frozen>=0:return frozen
    # Legacy orders use the exact catalog rule: morning uses selected only.
    selected=((order['snapshot'].get('internal_routing') or {}).get('selected_branch') or {}).get('branch_id') or order['branch_id']
    ids=[selected] if order['snapshot'].get('delivery_period')=='morning' else [selected,order['branch_id']]
    rows=c.execute('SELECT settings FROM branches WHERE brand_id=%s AND id=ANY(%s)',(order['brand_id'],ids)).fetchall()
    return max((r['settings']['minimum_order_halalas'] for r in rows if type(r.get('settings',{}).get('minimum_order_halalas')) is int),default=0)


def context(c, tid, user, lock=False):
    from .operations import lock_shift, shift_access
    from .order_financial_cases import scoped
    task = c.execute('SELECT * FROM driver_tasks WHERE id=%s', (tid,)).fetchone()
    if not task: raise db.error(404, 'TASK_NOT_FOUND', 'المهمة غير موجودة.')
    order = scoped(c, task['order_id'], user, lock)
    if lock: task = c.execute('SELECT * FROM driver_tasks WHERE id=%s FOR UPDATE', (tid,)).fetchone()
    shift = lock_shift(c, task['shift_id']) if lock else c.execute('SELECT * FROM shifts WHERE id=%s', (task['shift_id'],)).fetchone()
    shift_access(c, user, shift)
    if user['role'] == 'driver' and task['driver_id'] != user['id']:
        raise db.error(404, 'TASK_NOT_FOUND', 'المهمة غير موجودة.')
    return order, task, shift


def history(c, oid):
    rows = c.execute('SELECT id,actor_id,created_at,details FROM order_events WHERE order_id=%s AND event_type=%s ORDER BY created_at,id', (oid, REQUESTED)).fetchall()
    decisions = {r['details']['return_id']:r['details'] for r in c.execute('SELECT details FROM order_events WHERE order_id=%s AND event_type=%s', (oid, DECIDED)).fetchall()}
    return [dict(**r, status=decisions.get(r['id'], {}).get('action', 'pending'), decision=decisions.get(r['id'])) for r in rows]


def guard(c, order):
    if any(r['status']=='pending' for r in history(c,order['id'])):
        raise db.error(409,'PARTIAL_RETURN_PENDING','يوجد مرتجع جزئي بانتظار اعتماد المدير؛ احسمه قبل متابعة التحصيل أو التسليم.')


def remaining(c, oid):
    result = {}
    for record in approved(c, oid):
        for line in record['lines']:
            index = line['line_index']; result[index] = result.get(index, 0) + line['quantity']
    return result


def eligible(order, task, shift):
    if shift['status'] != 'open' or task['status'] not in ('picked_up', 'problem') or not task.get('picked_up_at') or order.get('completed_at') or order['preparation_status'] in ('cancelled', 'rejected'):
        raise db.error(409, 'PARTIAL_RETURN_STATE', 'المرتجع الجزئي من مهمة مستلمة في شفت مفتوح قبل إتمام التسليم.')
    from .branch_payments import online_order
    if online_order(order) or order['snapshot'].get('legacy_invoice') or order['snapshot'].get('complimentary'):
        raise db.error(409, 'PARTIAL_RETURN_SPECIAL_SOURCE', 'يلزم مسار المزود أو المصدر أو الضيافة المختص.')


@router.get('/tasks/{tid}')
def listing(tid: str, user=Depends(auth.require_roles('driver', 'owner', 'delivery_manager'))):
    from .operations import safe
    with db.tx() as c:
        o, t, _ = context(c, tid, user)
        from .order_amendments import balance
        from .order_financial_treatments import effective_claim
        claim=effective_claim(c,o);collected=balance(c,o)
        return safe(dict(order_id=o['id'],brand_id=o['brand_id'],task_id=tid,order_reference=o['snapshot'].get('order_reference') or o['order_no'],
            version=o['version'],items=o['snapshot']['items'],returns=history(c,o['id']),returned_quantities=remaining(c,o['id']),
            effective_claim_halalas=claim,actual_collected_halalas=collected,due_halalas=max(0,claim-collected),
            minimum_order_halalas=minimum_policy(c,o)))


@router.post('/tasks/{tid}')
def request_return(tid: str, body: Proposal, user=Depends(auth.require_roles('driver', 'owner', 'delivery_manager')), idempotency_key: str | None=Header(None)):
    from .operations import safe
    with db.tx() as c:
        context(c, tid, user)
        if user['role']!='driver' and not auth.has_permission(user,'support_manage'):
            raise db.error(403,'STAFF_PERMISSION_FORBIDDEN','صلاحية متابعة المشكلات غير متاحة.')
        def perform():
            o, t, s = context(c, tid, user, True); eligible(o, t, s)
            if o['version'] != body.version: raise db.error(409, 'ORDER_VERSION_CONFLICT', 'تغير الطلب؛ حدّث الصفحة.')
            if any(r['status'] == 'pending' for r in history(c, o['id'])):
                raise db.error(409, 'PARTIAL_RETURN_PENDING', 'يوجد طلب مرتجع جزئي بانتظار قرار المدير.')
            credit = value(o, body.lines, remaining(c, o['id']))
            identifier = db.new_id('partial_')
            details = dict(**body.model_dump(), task_id=tid, credit_halalas=credit, return_id=identifier,
                original_total_halalas=o['total_halalas'], source_version=o['version'])
            # Event ID is the durable request identity used in all decisions.
            c.execute('INSERT INTO order_events(id,order_id,actor_id,event_type,details) VALUES(%s,%s,%s,%s,%s)', (identifier,o['id'],user['id'],REQUESTED,db.json(details)))
            c.execute("UPDATE driver_tasks SET status='problem',reason=%s WHERE id=%s", (body.reason,tid))
            c.execute("UPDATE orders SET delivery_status='problem',version=version+1 WHERE id=%s", (o['id'],))
            db.event(c,o['id'],user['id'],'task.problem',dict(task_id=tid,previous=t['status'],status='problem',reason=body.reason,branch_id=o['branch_id'],actor_name=user.get('display_name',''),partial_return_id=identifier,manager_decision=False))
            db.audit(c,user['id'],REQUESTED,'order',o['id'],details)
            return safe(dict(id=identifier, status='pending', **details))
        return db.idempotent(c,user['id'],idempotency_key,'partial-return.request:'+tid,body.model_dump(),perform)


@router.post('/{rid}/decision')
def decide(rid: str, body: Decision, user=Depends(auth.require_roles('owner', 'delivery_manager')), idempotency_key: str | None=Header(None)):
    if not auth.has_permission(user, 'support_manage'):
        raise db.error(403, 'STAFF_PERMISSION_FORBIDDEN', 'صلاحية متابعة المشكلات غير متاحة.')
    from .operations import safe
    with db.tx() as c:
        request=c.execute('SELECT * FROM order_events WHERE id=%s AND event_type=%s', (rid,REQUESTED)).fetchone()
        if not request: raise db.error(404, 'PARTIAL_RETURN_NOT_FOUND', 'طلب المرتجع غير موجود.')
        tid=request['details']['task_id'];context(c,tid,user)
        def perform():
            o,t,s=context(c,tid,user,True);eligible(o,t,s)
            row=next((r for r in history(c,o['id']) if r['id']==rid),None)
            if not row or row['status']!='pending':raise db.error(409,'PARTIAL_RETURN_DECIDED','تم حسم المرتجع مسبقًا.')
            if t['status']!='problem':raise db.error(409,'PARTIAL_RETURN_TASK_CHANGED','تغيرت حالة البلاغ؛ حدّث المهمة.')
            details={**row['details'],**body.model_dump(),'requested_by':request['actor_id'],'approved_by':user['id'],'no_money_posted':True,'original_order_preserved':True,'responsibility':'branch_review' if row['details']['cause']=='branch_error' else 'customer_reduction','no_driver_shortfall':True}
            if body.action=='approve':
                if not body.physical_resolution:raise db.error(422,'PARTIAL_RETURN_CUSTODY','وثّق مكان الأصناف المرتجعة.')
                lines=[Line(**x) for x in row['details']['lines']];credit=value(o,lines,remaining(c,o['id']))
                from .order_amendments import balance
                from .order_financial_treatments import effective_total, price_credit_rows
                effective=effective_total(c,o)
                prior=approved(c,o['id'])
                if len(price_credit_rows(c,[o['id']]))!=len(prior) or c.execute('SELECT 1 FROM refunds WHERE order_id=%s LIMIT 1',(o['id'],)).fetchone():
                    raise db.error(409,'PARTIAL_RETURN_PRIOR_FINANCIAL_REVIEW','توجد تسوية أخرى؛ يلزم مراجعة المحاسبة منعًا لتكرار التخفيض.')
                target=effective-credit; collected=balance(c,o)
                if target-o['delivery_fee_halalas']<=0:
                    raise db.error(422,'PARTIAL_RETURN_FULL','لم يبق مبلغ أصناف مدفوعة؛ استخدم الإلغاء والمرتجع الكامل.')
                if target<collected:raise db.error(409,'PARTIAL_RETURN_REFUND_REQUIRED','سبق تحصيل مبلغ أكبر؛ استخدم المرتجع والاسترداد الفعلي دون تكرار رده.')
                minimum=minimum_policy(c,o)
                below=target-o['delivery_fee_halalas']<minimum
                if below and not body.approve_below_minimum:raise db.error(422,'PARTIAL_RETURN_MINIMUM_APPROVAL','التخفيض تحت الحد الأدنى يحتاج اعتماد المدير صراحة وسببًا.')
                from .fiscal_issuance import credit_for_price_correction
                document=credit_for_price_correction(c,o,user,rid,credit,body.reason,evidence_reference='Physical partial return '+rid)
                details.update(credit_halalas=credit,corrected_total_halalas=target,minimum_order_halalas=minimum,below_minimum=below,source_document=document,kind='price_correction',treatment_id=rid,note=body.reason,source='delivery_partial_return')
                # Reuse the established source-credit projection across collect, accounting and fiscal issuance.
                from .order_financial_treatments import APPROVED as SOURCE_APPROVED
                db.audit(c,user['id'],SOURCE_APPROVED,'order',o['id'],details)
                db.event(c,o['id'],user['id'],APPROVED,details)
                if row['details']['cause']=='branch_error':
                    db.event(c,o['id'],user['id'],'branch.error_review_required',dict(return_id=rid,reason=body.reason,lines=details['lines'],responsible_role='branch_manager',no_driver_shortfall=True))
                c.execute("UPDATE orders SET payment_status=%s WHERE id=%s",('paid' if collected==target else 'unpaid',o['id']))
            db.event(c,o['id'],user['id'],DECIDED,details)
            db.audit(c,user['id'],DECIDED,'order',o['id'],details)
            c.execute("UPDATE driver_tasks SET status='picked_up',reason=%s WHERE id=%s",(body.reason,tid))
            c.execute("UPDATE orders SET delivery_status='picked_up',version=version+1 WHERE id=%s",(o['id'],))
            return safe(dict(id=rid,status=body.action,**details))
        return db.idempotent(c,user['id'],idempotency_key,'partial-return.decide:'+rid,body.model_dump(),perform)
