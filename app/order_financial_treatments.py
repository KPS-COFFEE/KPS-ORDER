"""Independently approved source price credits and documented order receivables.

The order, invoice, collections, refunds and saved closure totals are never
rewritten. A price credit changes only the linked effective claim on the order.
An acknowledged receivable keeps its actual amount and owner visible.
"""
from datetime import date,datetime
import os
from typing import Literal
from fastapi import APIRouter,Depends,Request
from pydantic import BaseModel,ConfigDict,Field,StrictBool,StrictInt
from . import auth,db,multibrand

router=APIRouter(prefix='/api/accounting/order-financial-treatments/orders',tags=['order-financial-treatments'])
PROPOSED='accounting.order_financial_treatment.proposed'
APPROVED='accounting.order_financial_treatment.approved'
REJECTED='accounting.order_financial_treatment.rejected'

class Proposal(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    expected_version:StrictInt=Field(ge=1)
    case_id:str=Field(min_length=1,max_length=150)
    kind:Literal['price_correction','outstanding']
    expected_collected_halalas:StrictInt=Field(ge=0,le=100000000000)
    corrected_total_halalas:StrictInt|None=Field(None,ge=0,le=100000000000)
    evidence_reference:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    external_credit_reference:str|None=Field(None,min_length=5,max_length=200,pattern=r'^[^\x00]+$')
    liable_party_type:Literal['customer','bank','provider','merchant','employee','other']|None=None
    liable_party_name:str|None=Field(None,min_length=2,max_length=200,pattern=r'^[^\x00]+$')
    followup_user_id:str|None=Field(None,min_length=1,max_length=150)
    due_on:date|None=None

class Approval(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    confirmed:StrictBool
    evidence_reference:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    allow_period_approval_with_outstanding:StrictBool=False

class Rejection(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')


def enabled():
    return (os.getenv('KPS_ORDER_SOURCE_TREATMENTS_ENABLED','false').strip().lower()=='true'
        and os.getenv('APP_ENV','production') in {'testing','development'})


def scoped(c,user,oid,lock=False):
    if not enabled():raise db.error(404,'ORDER_SOURCE_TREATMENTS_DISABLED','مسار معالجة أصل السعر قيد التطوير وغير متاح للتشغيل الحالي.')
    from .order_financial_cases import accounting,scoped as scope
    if not accounting(user):raise db.error(403,'ORDER_TREATMENT_FORBIDDEN','معالجة أصل المستحق للمحاسبة المخولة فقط.')
    return scope(c,oid,user,lock)


def history(c,oid):
    rows=c.execute("SELECT a.*,u.display_name actor_name FROM audit_events a JOIN users u ON u.id=a.actor_id WHERE a.entity_type='order' AND a.entity_id=%s AND a.action IN (%s,%s,%s) ORDER BY a.created_at,a.id",(oid,PROPOSED,APPROVED,REJECTED)).fetchall()
    decisions={r['details']['treatment_id']:r for r in rows if r['action'] in (APPROVED,REJECTED)}
    return [dict(id=r['details']['treatment_id'],payload=r['details'],requested_by=r['actor_id'],requester_name=r['actor_name'],
        created_at=r['created_at'],status=('approved' if decisions[r['details']['treatment_id']]['action']==APPROVED else 'rejected') if r['details']['treatment_id'] in decisions else 'pending',
        approval=decisions.get(r['details']['treatment_id'])) for r in rows if r['action']==PROPOSED]


def price_credit_rows(c,order_ids):
    if not order_ids:return []
    return c.execute("""SELECT a.id,a.entity_id order_id,a.created_at,a.actor_id,u.display_name actor_name,a.details,
        (a.details->>'credit_halalas')::bigint credit_halalas,a.details->>'note' reason FROM audit_events a LEFT JOIN users u ON u.id=a.actor_id WHERE a.entity_type='order' AND a.entity_id=ANY(%s) AND a.action=%s AND a.details->>'kind'='price_correction' ORDER BY a.created_at,a.id""",(list(order_ids),APPROVED)).fetchall()


def credited_total(order,records):
    original=order.get('total_halalas')
    if type(original) is not int or original<0:raise db.error(409,'ORDER_TREATMENT_SOURCE_MISSING','قيمة أصل الطلب غير موثقة.')
    credits=0
    for row in records:
        d=row.get('details',row)
        amount=d.get('credit_halalas')
        if type(amount) is not int or amount<=0 or d.get('original_total_halalas')!=original:
            raise db.error(409,'ORDER_TREATMENT_SOURCE_MISMATCH','مستند التصحيح لا يطابق أصل الطلب.')
        credits+=amount
    if credits>original:raise db.error(409,'ORDER_TREATMENT_CREDIT_LIMIT','الإشعارات المرتبطة تتجاوز أصل الطلب.')
    return original-credits


def claim_projections(c, orders):
    """Two bounded reads for task cards; no per-task financial query loop."""
    if not orders:return {}
    ids=[o['id'] for o in orders]
    rows=c.execute("SELECT entity_id order_id,details FROM audit_events WHERE entity_type='order' AND entity_id=ANY(%s) AND action=%s",(ids,APPROVED)).fetchall()
    approvals={};credits={}
    for row in rows:
        oid=row['order_id'];approvals[oid]=True
        if row['details'].get('kind')=='price_correction':credits.setdefault(oid,[]).append(row)
    refunds={}
    if approvals:
        refunds={r['order_id']:int(r['amount_halalas']) for r in c.execute("SELECT order_id,coalesce(sum(amount_halalas),0) amount_halalas FROM refunds WHERE order_id=ANY(%s) AND amendment_id IS NULL AND record_kind<>'test_void' GROUP BY order_id",(list(approvals),)).fetchall()}
    result={}
    for order in orders:
        effective=credited_total(order,credits.get(order['id'],[]))
        result[order['id']]=dict(original_total_halalas=order['total_halalas'],effective_total_halalas=effective,
            effective_claim_halalas=max(0,effective-refunds.get(order['id'],0)))
    return result


def effective_total(c,order):
    return credited_total(order,price_credit_rows(c,[order['id']]))


def returned_credit(c,order):
    return int(c.execute("SELECT coalesce(sum(amount_halalas),0) n FROM refunds WHERE order_id=%s AND amendment_id IS NULL AND record_kind<>'test_void'",(order['id'],)).fetchone()['n'])


def effective_claim(c,order):
    effective=effective_total(c,order)
    # The development feature must not change existing refund/debt policy on
    # orders without a source approval. Future approved records remain honored
    # even when the feature is subsequently disabled.
    approved=c.execute("SELECT 1 FROM audit_events WHERE entity_type='order' AND entity_id=%s AND action=%s LIMIT 1",(order['id'],APPROVED)).fetchone()
    return max(0,effective-returned_credit(c,order)) if approved else effective


def valuation_order(c,order):
    # A physical return consumes specific quantities, not the price of the
    # remaining items. prior_quantities excludes those already returned units.
    # Restore only their source credit while valuing future selected units;
    # other source price corrections still reduce the conservative ceiling.
    from .delivery_partial_returns import approved
    physical_credits=sum(r['credit_halalas'] for r in approved(c,order['id']))
    return {**order,'original_total_halalas':order['total_halalas'],
        'total_halalas':min(order['total_halalas'],effective_total(c,order)+physical_credits)}


def state(c,order,case_id=None,*,collected_halalas=None,review_resolved=False):
    from .order_amendments import balance
    effective=effective_total(c,order);collected=balance(c,order) if collected_halalas is None else collected_halalas
    treatments=history(c,order['id'])
    source_approved=effective<order['total_halalas'] or any(r['status']=='approved' for r in treatments)
    returned=returned_credit(c,order) if source_approved else 0
    claim=max(0,effective-returned);remaining=max(0,claim-collected)
    outstanding=next((r for r in reversed(treatments) if r['status']=='approved' and r['payload']['kind']=='outstanding'
        and (case_id is None or r['payload']['case_id']==case_id)
        and r['payload']['source_effective_total_halalas']==effective),None)
    amounts_matched=not remaining and collected==claim
    certified=source_approved or review_resolved
    allowed=bool((effective<order['total_halalas'] and not remaining and collected==claim) or
        outstanding and (not remaining or outstanding['approval']['details'].get('allow_period_approval_with_outstanding')))
    return dict(original_total_halalas=order['total_halalas'],effective_total_halalas=effective,effective_claim_halalas=claim,refund_credit_halalas=returned,
        price_credit_halalas=order['total_halalas']-effective,actual_collected_halalas=collected,
        effective_payment_status='paid' if collected==claim else 'partial' if collected>0 else 'unpaid',
        pending_amount_halalas=remaining,amounts_matched=amounts_matched,financially_complete=amounts_matched and certified,
        financial_state=('settled_by_source_price_credit' if effective<order['total_halalas'] else 'matched') if amounts_matched and certified else 'review_approved_outstanding' if outstanding else 'pending_review',
        period_approval_allowed=allowed,outstanding_treatment=outstanding,
        outstanding_receivable_halalas=remaining if outstanding else 0,
        followup_user_id=outstanding['payload']['followup_user_id'] if outstanding and remaining else None,
        due_on=outstanding['payload']['due_on'] if outstanding and remaining else None,
        liable_party_type=outstanding['payload']['liable_party_type'] if outstanding and remaining else None,
        liable_party_name=outstanding['payload']['liable_party_name'] if outstanding and remaining else None)


def validate(body,order,case_id,collected,effective,returned=0):
    if body.expected_version!=order['version'] or body.case_id!=case_id or body.expected_collected_halalas!=collected:
        raise db.error(409,'ORDER_TREATMENT_STALE','تغير الطلب أو التحصيل أو ملف الفرق؛ حدّث الأدلة.')
    if collected+returned>=effective:raise db.error(409,'ORDER_TREATMENT_NO_SHORTFALL','لا يوجد نقص تحصيل فعلي يحتاج هذا المسار.')
    if body.kind=='price_correction':
        if body.corrected_total_halalas is None or not collected+returned<=body.corrected_total_halalas<effective:
            raise db.error(422,'ORDER_TREATMENT_PRICE_LIMIT','السعر المصحح أقل من المستحق الحالي ولا يقل عن التحصيل الفعلي؛ الزيادة المسددة تحتاج مسار استرداد منفصل.')
        if any((body.liable_party_type,body.liable_party_name,body.followup_user_id,body.due_on)):
            raise db.error(422,'ORDER_TREATMENT_PRICE_SCOPE','تصحيح السعر لا ينشئ ذمة على شخص.')
    elif (body.corrected_total_halalas is not None or body.external_credit_reference or
          not all((body.liable_party_type,body.liable_party_name,body.followup_user_id,body.due_on))):
        raise db.error(422,'ORDER_TREATMENT_RECEIVABLE_DETAILS','حدد الجهة والمتابع والموعد؛ الذمة لا تغيّر السعر.')


def eligible_followup(c,user,order,identifier):
    target=c.execute('SELECT * FROM users WHERE id=%s',(identifier,)).fetchone()
    if multibrand.enabled():target=multibrand.project(c,target,order['brand_id'])
    from .order_financial_cases import accounting
    if (not target or not target.get('active') or target.get('brand_id')!=order['brand_id'] or not accounting(target)
            or not (target.get('role')=='owner' and not target.get('home_brand_id')
                    or order['branch_id'] in (target.get('branch_ids') or []))):
        raise db.error(422,'ORDER_TREATMENT_FOLLOWUP_SCOPE','اختر موظف محاسبة فعالًا ومخولًا بالفرع لمتابعة الذمة.')
    return target


def current_case(c,order,*,required=True):
    row=c.execute("SELECT id,details FROM order_events e WHERE order_id=%s AND event_type='financial_case.opened' AND details->>'kind'='collection_shortfall' AND NOT EXISTS(SELECT 1 FROM order_events r WHERE r.order_id=e.order_id AND r.event_type='financial_case.resolved' AND r.details->>'case_id'=e.id) ORDER BY created_at DESC,id DESC LIMIT 1",(order['id'],)).fetchone()
    if not row and required:raise db.error(409,'ORDER_TREATMENT_CASE_REQUIRED','اختر ملف فرق تحصيل معلقًا؛ لا يُختلق استحقاق جديد.')
    return row


def custody_actors(c,oid):
    actors={r['collector_id'] for r in c.execute('SELECT collector_id FROM collections WHERE order_id=%s',(oid,)).fetchall()}
    actors.update(r['user_id'] for r in c.execute('SELECT DISTINCT s.user_id FROM shifts s JOIN driver_tasks t ON t.shift_id=s.id WHERE t.order_id=%s',(oid,)).fetchall())
    actors.update(r['confirmed_by'] for r in c.execute("SELECT DISTINCT x.confirmed_by FROM custody_transfers x JOIN driver_tasks t ON t.shift_id=x.from_shift_id WHERE t.order_id=%s AND x.status='confirmed'",(oid,)).fetchall())
    return actors


@router.get('/{oid}')
def listing(oid:str,user=Depends(auth.current_user)):
    from .operations import safe
    with db.tx() as c:
        order=scoped(c,user,oid);case=current_case(c,order,required=False)
        followup=[]
        for row in c.execute("SELECT id FROM users WHERE active AND role IN ('owner','admin') ORDER BY display_name,id").fetchall():
            try:person=eligible_followup(c,user,order,row['id'])
            except Exception as exc:
                from fastapi import HTTPException
                if isinstance(exc,HTTPException) and exc.status_code==422:continue
                raise
            followup.append(dict(id=person['id'],name=person['display_name']))
        return safe(dict(order_id=oid,version=order['version'],case_id=case['id'] if case else None,treatments=history(c,oid),
            followup_users=followup,can_propose=bool(case),can_approve=user['id'] not in custody_actors(c,oid),**state(c,order,case['id'] if case else None)))


@router.post('/{oid}')
def propose(oid:str,body:Proposal,request:Request,user=Depends(auth.current_user)):
    from .order_amendments import balance,active
    from .operations import safe
    with db.tx() as c:
        scoped(c,user,oid)
        def perform():
            order=scoped(c,user,oid,True);case=current_case(c,order);effective=effective_total(c,order);collected=balance(c,order)
            from .branch_payments import online_order
            if online_order(order) or active(c,oid) or order.get('test_void') or order['preparation_status'] in ('cancelled','rejected'):
                raise db.error(409,'ORDER_TREATMENT_SOURCE_SCOPE','أكمل مسار المزود أو التعديل أو الإلغاء المختص؛ لا يستخدم تصحيح السعر لتجاوزه.')
            validate(body,order,case['id'],collected,effective,returned_credit(c,order))
            if any(r['status']=='approved' and r['payload']['kind']=='outstanding' and body.kind=='outstanding'
                   and r['payload']['case_id']==case['id'] and r['payload']['source_effective_total_halalas']==effective for r in history(c,oid)):
                raise db.error(409,'ORDER_TREATMENT_RECEIVABLE_EXISTS','الذمة موثقة سابقًا؛ تابع تحصيلها دون إنشاء استحقاق مكرر.')
            if any(r['status']=='pending' for r in history(c,oid)):
                raise db.error(409,'ORDER_TREATMENT_PENDING','يوجد طلب معالجة معلق؛ اقبله أو أعده بسبب محدد أولًا.')
            if body.kind=='outstanding':
                from .accounting import TZ
                if body.due_on<datetime.now(TZ).date():raise db.error(422,'ORDER_TREATMENT_DUE','حدد موعد متابعة اليوم أو لاحقًا.')
                eligible_followup(c,user,order,body.followup_user_id)
            if body.kind=='price_correction' and order['snapshot'].get('legacy_invoice') and not body.external_credit_reference:
                raise db.error(422,'ORDER_TREATMENT_EXTERNAL_CREDIT_REQUIRED','الفاتورة من مصدر خارجي؛ أرفق مرجع الإشعار المعتمد من المصدر.')
            identifier=db.new_id('oft_')
            payload={**body.model_dump(mode='json'),'treatment_id':identifier,'original_total_halalas':order['total_halalas'],
                'source_effective_total_halalas':effective,'credit_halalas':effective-body.corrected_total_halalas if body.kind=='price_correction' else 0,
                'outstanding_halalas':effective-collected,'brand_id':order['brand_id'],'branch_id':order['branch_id'],
                'no_money_posted':True,'no_payroll_deduction':True,'original_order_preserved':True}
            db.audit(c,user['id'],PROPOSED,'order',oid,payload)
            db.event(c,oid,user['id'],'financial_case.source_treatment_proposed',payload)
            return safe(dict(id=identifier,status='pending',payload=payload))
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'order_financial_treatment.propose:'+oid,body.model_dump(mode='json'),perform)


@router.post('/{oid}/{tid}/approve')
def approve(oid:str,tid:str,body:Approval,request:Request,user=Depends(auth.current_user)):
    from .order_amendments import balance
    from .operations import safe
    with db.tx() as c:
        scoped(c,user,oid)
        def perform():
            order=scoped(c,user,oid,True);case=current_case(c,order)
            row=next((r for r in history(c,oid) if r['id']==tid),None)
            if not row or row['status']!='pending':raise db.error(409,'ORDER_TREATMENT_NOT_PENDING','طلب المعالجة غير معلق.')
            proposal=Proposal(**{k:v for k,v in row['payload'].items() if k in Proposal.model_fields})
            from .order_amendments import active
            from .branch_payments import online_order
            if online_order(order) or active(c,oid) or order.get('test_void') or order['preparation_status'] in ('cancelled','rejected'):
                raise db.error(409,'ORDER_TREATMENT_SOURCE_SCOPE','تغير مسار الطلب؛ أكمل مسار المزود أو التعديل أو الإلغاء المختص.')
            validate(proposal,order,case['id'],balance(c,order),effective_total(c,order),returned_credit(c,order))
            if not body.confirmed:raise db.error(422,'ORDER_TREATMENT_CONFIRM','أكد مراجعة أصل السعر والتحصيل والمستند.')
            if user['id']==row['requested_by'] or user['id'] in custody_actors(c,oid):
                raise db.error(403,'ORDER_TREATMENT_SELF_APPROVAL','الاعتماد من موظف مستقل عن المقترح وصاحب العهدة ومستلمها.')
            if (body.allow_period_approval_with_outstanding or proposal.liable_party_type=='employee') and user['role']!='owner':
                raise db.error(403,'ORDER_TREATMENT_OWNER_REQUIRED','ذمة الموظف واعتماد الفترة بذمة متبقية للمالك فقط؛ لا ينفذ أي خصم آلي.')
            if proposal.kind=='price_correction' and body.allow_period_approval_with_outstanding:
                raise db.error(422,'ORDER_TREATMENT_ALLOWANCE_SCOPE','استثناء الفترة خاص بالذمة المثبتة فقط.')
            from .financial_clarifications import guard_case_resolution
            guard_case_resolution(c,oid,case['id'])
            document=None
            if proposal.kind=='price_correction':
                from .fiscal_issuance import credit_for_price_correction
                document=credit_for_price_correction(c,order,user,tid,row['payload']['credit_halalas'],body.note,
                    evidence_reference=body.evidence_reference,external_credit_reference=proposal.external_credit_reference)
            else:eligible_followup(c,user,order,proposal.followup_user_id)
            superseded=[r['id'] for r in history(c,oid) if r['status']=='approved' and r['payload']['kind']=='outstanding'
                and r['payload']['case_id']==case['id']] if proposal.kind=='price_correction' else []
            payload={**row['payload'],**body.model_dump(mode='json'),'supersedes_outstanding_ids':superseded,'requested_by':row['requested_by'],'approved_by':user['id'],
                'source_document':document,'no_money_posted':True,'original_order_preserved':True}
            db.audit(c,user['id'],APPROVED,'order',oid,payload)
            db.event(c,oid,user['id'],'financial_case.source_treatment_approved',payload)
            return safe(dict(id=tid,status='approved',source_document=document,**state(c,order,case['id'])))
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'order_financial_treatment.approve:'+oid+':'+tid,body.model_dump(),perform)


@router.post('/{oid}/{tid}/reject')
def reject(oid:str,tid:str,body:Rejection,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        scoped(c,user,oid)
        def perform():
            scoped(c,user,oid,True);row=next((r for r in history(c,oid) if r['id']==tid),None)
            if not row or row['status']!='pending':raise db.error(409,'ORDER_TREATMENT_NOT_PENDING','طلب المعالجة غير معلق.')
            detail=dict(treatment_id=tid,note=body.note,responsible_user_id=row['requested_by'],required_action='correct_price_or_receivable_evidence')
            db.audit(c,user['id'],REJECTED,'order',oid,detail);db.event(c,oid,user['id'],'financial_case.source_treatment_rejected',detail)
            return dict(id=tid,status='rejected',**detail)
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'order_financial_treatment.reject:'+oid+':'+tid,body.model_dump(),perform)
