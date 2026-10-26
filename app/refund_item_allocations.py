"""Document prior monetary refunds by item, without another refund or rewrite."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from . import auth,db

router=APIRouter(prefix='/api/accounting/refund-item-allocations/orders',tags=['refund-item-evidence'])
PROPOSED='accounting.refund_item_allocation.proposed'
APPROVED='accounting.refund_item_allocation.approved'
REJECTED='accounting.refund_item_allocation.rejected'

class Proposal(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    refund_ids:list[str]=Field(min_length=1,max_length=100)
    line_quantities:dict[str,StrictInt]
    evidence_reference:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')

class Rejection(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')

class Approval(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    confirmed:StrictBool
    evidence_reference:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')


def scoped(c,user,oid,lock=False):
    from .order_financial_cases import accounting,scoped as order_scope
    if not accounting(user):raise db.error(403,'REFUND_ITEM_ALLOCATION_FORBIDDEN','ربط مستند الاسترداد بالأصناف للمحاسبة المخولة فقط.')
    return order_scope(c,oid,user,lock)


def history(c,oid):
    rows=c.execute("SELECT a.*,u.display_name actor_name FROM audit_events a JOIN users u ON u.id=a.actor_id WHERE a.entity_type='order' AND a.entity_id=%s AND a.action IN (%s,%s,%s) ORDER BY a.created_at,a.id",(oid,PROPOSED,APPROVED,REJECTED)).fetchall()
    approved={r['details']['allocation_id']:r for r in rows if r['action'] in (APPROVED,REJECTED)}
    return [dict(id=r['details']['allocation_id'],payload=r['details'],requested_by=r['actor_id'],requester_name=r['actor_name'],
        created_at=r['created_at'],status=('approved' if approved[r['details']['allocation_id']]['action']==APPROVED else 'rejected') if r['details']['allocation_id'] in approved else 'pending',
        approval=approved.get(r['details']['allocation_id'])) for r in rows if r['action']==PROPOSED]


def validate(c,order,body):
    from .refund_line_values import ceiling,prior_quantities,unallocated_prior_ids,approved_allocations
    rows=c.execute('SELECT id,amount_halalas,line_quantities,amendment_id,record_kind,shift_id FROM refunds WHERE order_id=%s ORDER BY created_at,id',(order['id'],)).fetchall()
    events=c.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='order.refund' ORDER BY created_at,id",(order['id'],)).fetchall()
    covered={rid for a in approved_allocations(c,order['id']) for rid in a['refund_ids']}
    available=set(unallocated_prior_ids(rows,events))-covered
    if len(body.refund_ids)!=len(set(body.refund_ids)) or not set(body.refund_ids)<=available:
        raise db.error(409,'REFUND_ITEM_ALLOCATION_SOURCE_CHANGED','اختر مستندات استرداد سابقة غير مربوطة؛ لا يعاد إسناد المبلغ مرتين.')
    if not body.line_quantities or any(not key or q<=0 for key,q in body.line_quantities.items()):
        raise db.error(422,'REFUND_ITEM_ALLOCATION_QUANTITIES','حدد كميات فعلية موجبة قابلة للإرجاع.')
    selected=[r for r in rows if r['id'] in body.refund_ids]
    amount=sum(r['amount_halalas'] for r in selected)
    from .order_financial_treatments import valuation_order
    maximum=ceiling(valuation_order(c,order),body.line_quantities,prior_quantities(c,order['id'],body.line_quantities))
    if amount>maximum:
        raise db.error(422,'REFUND_ITEM_ALLOCATION_VALUE','مبلغ الاسترداد السابق يتجاوز قيمة الأصناف المحددة بعد الخصم؛ المجاني قيمته صفر.')
    return selected,amount


@router.get('/{oid}')
def listing(oid:str,user=Depends(auth.current_user)):
    from .operations import safe
    from .refund_line_values import unallocated_prior_ids,approved_allocations,prior_quantities
    with db.tx() as c:
        order=scoped(c,user,oid)
        rows=c.execute('SELECT id,amount_halalas,line_quantities,amendment_id,record_kind,method,created_at,reason FROM refunds WHERE order_id=%s ORDER BY created_at,id',(oid,)).fetchall()
        events=c.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='order.refund' ORDER BY created_at,id",(oid,)).fetchall()
        covered={rid for a in approved_allocations(c,oid) for rid in a['refund_ids']}
        pending=set(unallocated_prior_ids(rows,events))-covered
        items=[{k:r.get(k) for k in ('product_id','name','quantity','line_total_halalas')} for r in order['snapshot'].get('items',[])]
        return safe(dict(order_id=oid,unallocated_refunds=[r for r in rows if r['id'] in pending],allocations=history(c,oid),
            items=items,previous_quantities=prior_quantities(c,oid,{r['product_id']:1 for r in items}),can_propose=bool(pending),can_approve=True,no_money_posted=True))


@router.post('/{oid}')
def propose(oid:str,body:Proposal,request:Request,user=Depends(auth.current_user)):
    from .operations import safe
    with db.tx() as c:
        scoped(c,user,oid)
        def perform():
            order=scoped(c,user,oid,True);rows,amount=validate(c,order,body)
            identifier=db.new_id('ria_')
            payload={**body.model_dump(),'allocation_id':identifier,'order_id':oid,'order_version':order['version'],
                'amount_halalas':amount,'brand_id':order['brand_id'],'branch_id':order['branch_id'],'no_money_posted':True}
            db.audit(c,user['id'],PROPOSED,'order',oid,payload)
            return safe(dict(id=identifier,status='pending',payload=payload))
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'refund_item_allocation.propose:'+oid,body.model_dump(),perform)


@router.post('/{oid}/{identifier}/approve')
def approve(oid:str,identifier:str,body:Approval,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        scoped(c,user,oid)
        def perform():
            order=scoped(c,user,oid,True)
            row=next((r for r in history(c,oid) if r['id']==identifier),None)
            if not row:raise db.error(404,'REFUND_ITEM_ALLOCATION_NOT_FOUND','طلب الربط غير متاح.')
            if row['status']!='pending' or row['payload']['order_version']!=order['version']:
                raise db.error(409,'REFUND_ITEM_ALLOCATION_STALE','تغير الطلب أو اعتمد الربط؛ حدّث المستندات ثم أعد التقديم.')
            if not body.confirmed:raise db.error(422,'REFUND_ITEM_ALLOCATION_CONFIRM','أكد مراجعة دليل الأصناف والمبلغ السابق.')
            if row['requested_by']==user['id']:
                raise db.error(403,'REFUND_ITEM_ALLOCATION_SELF_APPROVAL','اعتماد ربط الاسترداد من موظف مستقل عن مقدمه.')
            proposal=Proposal(**{k:v for k,v in row['payload'].items() if k in Proposal.model_fields})
            rows,amount=validate(c,order,proposal)
            shifts=[r['shift_id'] for r in rows if r.get('shift_id')]
            if shifts and c.execute('SELECT 1 FROM shifts WHERE id=ANY(%s) AND user_id=%s',(shifts,user['id'])).fetchone():
                raise db.error(403,'REFUND_ITEM_ALLOCATION_OWN_CUSTODY','لا تعتمد ربط استرداد يخص عهدتك.')
            db.audit(c,user['id'],APPROVED,'order',oid,{**row['payload'],**body.model_dump(),
                'requested_by':row['requested_by'],'approved_by':user['id'],'amount_halalas':amount,'no_money_posted':True})
            return dict(id=identifier,status='approved',no_money_posted=True)
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'refund_item_allocation.approve:'+oid+':'+identifier,body.model_dump(),perform)


@router.post('/{oid}/{identifier}/reject')
def reject(oid:str,identifier:str,body:Rejection,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        scoped(c,user,oid)
        def perform():
            scoped(c,user,oid,True)
            row=next((r for r in history(c,oid) if r['id']==identifier),None)
            if not row:raise db.error(404,'REFUND_ITEM_ALLOCATION_NOT_FOUND','طلب الربط غير متاح.')
            if row['status']!='pending':raise db.error(409,'REFUND_ITEM_ALLOCATION_STALE','قرار الربط مسجل سابقًا.')
            db.audit(c,user['id'],REJECTED,'order',oid,dict(allocation_id=identifier,note=body.note,
                requested_by=row['requested_by'],responsible_user_id=row['requested_by'],
                required_action='correct_refund_item_evidence',no_money_posted=True))
            return dict(id=identifier,status='rejected',responsible_user_id=row['requested_by'],required_correction=body.note,no_money_posted=True)
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'refund_item_allocation.reject:'+oid+':'+identifier,body.model_dump(),perform)
