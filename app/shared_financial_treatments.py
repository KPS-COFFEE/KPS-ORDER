"""Shared receipt differences use the canonical append-only treatment ledger.

No declaration, receipt, custody allocation or employee balance is rewritten.
"""
from datetime import datetime
from fastapi import APIRouter, Depends, Request, HTTPException
from . import auth, db, multibrand
from . import financial_treatments as ft
from . import operations as ops

router=APIRouter(prefix='/api/central-operations/workdays',tags=['shared-financial-treatments'])

class Proposal(ft.Proposal):
    declaration_id:str
    receipt_id:str

class Approval(ft.Approval):
    declaration_id:str
    receipt_id:str


def source(c,user,sid):
    ft.require_accountant(user)
    from . import central_workdays as days, aggregate_driver_closure as driver, aggregate_manager_closure as manager
    days.require_enabled(user,'accounting_review',existing_sid=sid)
    day,legs=days.authorized(c,user,sid,capability='accounting_review')
    module=driver if day['kind']=='driver' else manager
    declaration=module.latest(c,day['id'])
    family=day['kind']
    receipt=c.execute(f'SELECT * FROM shared_{family}_closure_receipts WHERE declaration_id=%s',(declaration['id'],)).fetchone() if declaration else None
    if not declaration or not receipt:raise db.error(409,'SHARED_TREATMENT_RECEIPT_REQUIRED','سجّل الاستلام الفعلي أولًا؛ المعالجة لا تنشئ استلامًا.')
    if c.execute(f'SELECT 1 FROM shared_{family}_closure_returns WHERE declaration_id=%s',(declaration['id'],)).fetchone():raise db.error(409,'SHARED_TREATMENT_RETURNED','الأصل أعيد للتصحيح.')
    retained=declaration.get('retained_halalas',0)
    difference=receipt['received_halalas']-(declaration['cash_halalas']-retained)
    values={'revision':declaration['revision'],'cash_variance_halalas':difference,'bank_difference_halalas':0,
        'has_channel_variance':bool(difference),'reconciliation_complete':True,'financial_approval_allowed':True}
    return day,legs,declaration,receipt,values


def history(c,workday_id):
    events=c.execute("SELECT a.*,u.display_name actor_name FROM audit_events a JOIN users u ON u.id=a.actor_id WHERE a.entity_type='central_workday' AND a.entity_id=%s AND a.action IN (%s,%s,%s) ORDER BY a.created_at,a.id",(workday_id,ft.PROPOSED,ft.APPROVED,ft.REJECTED)).fetchall()
    decisions={r['details']['treatment_id']:r for r in events if r['action'] in {ft.APPROVED,ft.REJECTED}}
    output=[]
    for event in events:
        if event['action']!=ft.PROPOSED:continue
        payload=event['details'];decision=decisions.get(payload['treatment_id'])
        output.append({'id':payload['treatment_id'],'payload':payload,'requested_by':event['actor_id'],'requester_name':event['actor_name'],'created_at':event['created_at'],
            'status':'approved' if decision and decision['action']==ft.APPROVED else 'rejected' if decision else 'pending',
            'approval':{**decision['details'],'approved_by':decision['actor_id'],'approver_name':decision['actor_name'],'created_at':decision['created_at']} if decision else None})
    return output


def shared_approved(c,workday_id,declaration_id,receipt_id):
    return next((r for r in history(c,workday_id) if r['status']=='approved' and r['payload'].get('declaration_id')==declaration_id and r['payload'].get('receipt_id')==receipt_id),None)


def followup(c,user,legs,identifier):
    person=None
    for _,shift in legs:
        person=ft.eligible_followup(c,user,{'brand_id':shift['brand_id'],'branch_ids':shift['branch_ids']},identifier)
    return person


def verify_ids(body,declaration,receipt):
    if (body.declaration_id!=declaration['id'] or body.receipt_id!=receipt['id'] or body.expected_revision!=declaration['revision']):raise db.error(409,'SHARED_TREATMENT_STALE','تغير أصل التقفيلة أو الاستلام؛ حدّث الشاشة.')


@router.get('/{sid}/financial-treatments')
def listing(sid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        day,legs,dec,receipt,values=source(c,user,sid)
        users=[]
        for row in c.execute("SELECT id FROM users WHERE active AND role IN ('owner','admin') ORDER BY display_name,id").fetchall():
            try:person=followup(c,user,legs,row['id'])
            except HTTPException as error:
                if error.status_code==422:continue
                raise
            users.append({'id':person['id'],'name':person['display_name']})
        approved=shared_approved(c,day['id'],dec['id'],receipt['id'])
        review=ft.apply(values,approved)
        # This adapter compares custody handover only, never terminal/bank or
        # invoice completeness. A matched bag cannot certify the entire closure.
        review['financially_complete']=False
        if not values['has_channel_variance']:
            review.update(financial_state='receipt_matched_pending_accounting',period_approval_allowed=False)
        return ops.safe({'treatments':history(c,day['id']),'followup_users':users,'can_propose':bool(values['has_channel_variance'] and not approved),'can_approve':user['id'] not in {day['user_id'],receipt['received_by']},
            'expected_revision':dec['revision'],'declaration_id':dec['id'],'receipt_id':receipt['id'],'scope':'cash_handover_only','source':{'cash_variance_halalas':values['cash_variance_halalas'],'card_variance_halalas':0},**review})


@router.post('/{sid}/financial-treatments')
def propose(sid:str,body:Proposal,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        from .central_workdays import lock_lanes
        lock_lanes(c);day,legs,dec,receipt,values=source(c,user,sid)
        def perform():
            verify_ids(body,dec,receipt);ft.validate_proposal(body,values)
            # A receipt difference is a separate outstanding amount. It is not a
            # cash/card reclassification, an invented receipt or an invoice edit.
            if body.kind!='outstanding':raise db.error(422,'SHARED_TREATMENT_KIND','فرق الاستلام يحتاج ذمة موثقة ومتابعة، وليس تصحيح وسيلة دفع.')
            if any(r['status'] in {'pending','approved'} and r['payload'].get('declaration_id')==dec['id'] and r['payload'].get('receipt_id')==receipt['id'] for r in history(c,day['id'])):raise db.error(409,'FINANCIAL_TREATMENT_EXISTS','توجد معالجة للأصل والاستلام الحاليين.')
            from .accounting import TZ
            if body.due_on<datetime.now(TZ).date():raise db.error(422,'FINANCIAL_TREATMENT_DUE','اختر مهلة اليوم أو لاحقًا.')
            followup(c,user,legs,body.followup_user_id)
            identifier=db.new_id('ft_')
            payload={**body.model_dump(mode='json'),'treatment_id':identifier,'workday_id':day['id'],'custodian_id':day['user_id'],'receiver_id':receipt['received_by'],
                'original_declared_cash_halalas':dec['cash_halalas'],'original_retained_halalas':dec.get('retained_halalas',0),'actual_received_halalas':receipt['received_halalas'],
                'no_payroll_deduction':True,'no_money_posted':True}
            db.audit(c,user['id'],ft.PROPOSED,'central_workday',day['id'],payload)
            return {'id':identifier,'status':'pending','payload':payload}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'shared_treatment.propose:'+day['id'],body.model_dump(mode='json'),perform)


@router.post('/{sid}/financial-treatments/{tid}/approve')
def approve(sid:str,tid:str,body:Approval,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        from .central_workdays import lock_lanes
        lock_lanes(c);day,legs,dec,receipt,values=source(c,user,sid)
        def perform():
            verify_ids(body,dec,receipt)
            if not body.confirmed:raise db.error(422,'FINANCIAL_TREATMENT_CONFIRMATION','أكد مراجعة الدليل والأصلين.')
            row=next((r for r in history(c,day['id']) if r['id']==tid),None)
            if not row or row['status']!='pending':raise db.error(409,'FINANCIAL_TREATMENT_STALE','المعالجة ليست معلقة.')
            proposal=Proposal(**{k:v for k,v in row['payload'].items() if k in Proposal.model_fields})
            verify_ids(proposal,dec,receipt);ft.validate_proposal(proposal,values)
            if user['id'] in {row['requested_by'],day['user_id'],receipt['received_by']}:raise db.error(403,'FINANCIAL_TREATMENT_SELF_APPROVAL','يلزم معتمد مستقل عن مقدم الطلب وصاحب العهدة ومستلمها.')
            if (proposal.liable_party_type=='employee' or body.allow_period_approval_with_outstanding) and user['role']!='owner':raise db.error(403,'FINANCIAL_TREATMENT_OWNER_REQUIRED','إثبات ذمة موظف أو اعتماد فترة بذمة متبقية للمالك فقط.')
            followup(c,user,legs,proposal.followup_user_id)
            detail={**body.model_dump(mode='json'),'treatment_id':tid,'requested_by':row['requested_by'],'approved_by':user['id'],'kind':proposal.kind,
                'cash_variance_halalas':proposal.cash_variance_halalas,'card_variance_halalas':0,'no_payroll_deduction':True,'no_money_posted':True}
            db.audit(c,user['id'],ft.APPROVED,'central_workday',day['id'],detail)
            return {'id':tid,'status':'approved','financial_state':'review_approved_outstanding'}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'shared_treatment.approve:'+day['id']+':'+tid,body.model_dump(mode='json'),perform)


class Rejection(ft.Rejection):
    declaration_id:str
    receipt_id:str


@router.post('/{sid}/financial-treatments/{tid}/reject')
def reject(sid:str,tid:str,body:Rejection,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        from .central_workdays import lock_lanes
        lock_lanes(c);day,legs,dec,receipt,values=source(c,user,sid)
        def perform():
            verify_ids(body,dec,receipt)
            row=next((r for r in history(c,day['id']) if r['id']==tid),None)
            if not row or row['status']!='pending':raise db.error(409,'FINANCIAL_TREATMENT_STALE','المعالجة ليست معلقة.')
            db.audit(c,user['id'],ft.REJECTED,'central_workday',day['id'],{'treatment_id':tid,**body.model_dump(),'requested_by':row['requested_by']})
            return {'id':tid,'status':'rejected'}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'shared_treatment.reject:'+day['id']+':'+tid,body.model_dump(),perform)
