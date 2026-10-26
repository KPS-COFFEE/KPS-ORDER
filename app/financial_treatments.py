"""Append-only, independently approved treatments of exact closure differences.

A review note cannot settle money. Corrections reclassify exact channels in a
linked review projection; receivables/payables remain outstanding and never turn
into an invented receipt, invoice rewrite or employee payroll deduction.
"""
from datetime import date, datetime
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from . import auth, db, multibrand

router=APIRouter(prefix='/api/accounting/driver-settlements',tags=['financial-treatments'])
PROPOSED='accounting.financial_treatment.proposed'
APPROVED='accounting.financial_treatment.approved'
REJECTED='accounting.financial_treatment.rejected'

class Proposal(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    expected_revision:StrictInt=Field(ge=1)
    kind:Literal['documented_channel_correction','outstanding']
    cash_variance_halalas:StrictInt=Field(ge=-100000000000,le=100000000000)
    card_variance_halalas:StrictInt=Field(ge=-100000000000,le=100000000000)
    evidence_reference:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    liable_party_type:Literal['customer','bank','provider','merchant','employee','other']|None=None
    liable_party_name:str|None=Field(None,min_length=2,max_length=200,pattern=r'^[^\x00]+$')
    followup_user_id:str|None=Field(None,min_length=1,max_length=150)
    due_on:date|None=None

class Approval(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    expected_revision:StrictInt=Field(ge=1)
    evidence_reference:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    allow_period_approval_with_outstanding:StrictBool=False
    confirmed:StrictBool

class Rejection(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    expected_revision:StrictInt=Field(ge=1)
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')


def require_accountant(user):
    if (not user.get('active',True) or user.get('role') not in {'owner','admin'}
            or not auth.has_permission(user,'accounting_review')):
        raise db.error(403,'FINANCIAL_TREATMENT_FORBIDDEN','المعالجة المالية للمحاسبة المخولة فقط.')


def source(c,user,rid):
    from .accounting import driver_settlement_scope,driver_settlement_values
    require_accountant(user)
    report=driver_settlement_scope(c,user,rid)
    latest=c.execute('SELECT * FROM driver_accounting_settlements WHERE report_id=%s ORDER BY revision DESC LIMIT 1',(rid,)).fetchone()
    values=driver_settlement_values(report['snapshot'],latest)
    return report,latest,values


def custody_actors(c,report):
    actors={report['user_id']}
    snapshot=report.get('snapshot') or {}
    sub=(snapshot.get('shift') or {}).get('submission') or {}
    if sub.get('physical_received_by'):actors.add(sub['physical_received_by'])
    for transfer in snapshot.get('transfers',[]):
        if transfer.get('status')=='confirmed' and transfer.get('closure') and transfer.get('from_shift_id')==report['shift_id']:
            actors.add(transfer.get('confirmed_by'))
    previous=None
    from .accounting import settlement_action
    for event in c.execute('SELECT * FROM driver_accounting_settlements WHERE report_id=%s ORDER BY revision',(report['id'],)).fetchall():
        if settlement_action(snapshot,previous,event) in {'cash_receipt','cash_receipt_correction'}:
            actors.add(event['actor_id'])
        previous=event
    return actors


def history(c,rid):
    events=c.execute("SELECT a.*,u.display_name actor_name FROM audit_events a JOIN users u ON u.id=a.actor_id WHERE a.entity_type='report' AND a.entity_id=%s AND a.action IN (%s,%s,%s) ORDER BY a.created_at,a.id",(rid,PROPOSED,APPROVED,REJECTED)).fetchall()
    decisions={e['details']['treatment_id']:e for e in events if e.get('action') in {APPROVED,REJECTED}}
    rows=[]
    for event in events:
        if event.get('action')!=PROPOSED:continue
        d=event['details'];decision=decisions.get(d['treatment_id'])
        rows.append({'id':d['treatment_id'],'payload':d,'requested_by':event['actor_id'],
            'requester_name':event['actor_name'],'created_at':event['created_at'],
            'status':'approved' if decision and decision['action']==APPROVED else 'rejected' if decision else 'pending',
            'approval':{'approved_by':decision['actor_id'],'approver_name':decision['actor_name'],
                        'created_at':decision['created_at'],**decision['details']} if decision else None})
    return rows


def approved(c,rid,revision):
    rows=history(c,rid)
    row=next((r for r in rows if r['status']=='approved' and r['payload']['expected_revision']==revision),None)
    if row and row['payload']['kind']=='outstanding':row['recoveries']=recovery_history(c,rid,row['id'])
    return row


def apply(values,treatment):
    result=dict(values,financial_state='pending_review',financially_complete=False,
        period_approval_allowed=False,outstanding_receivable_halalas=0,outstanding_payable_halalas=0,
        financial_treatment=treatment)
    if not values['reconciliation_complete']:return result
    if not values['has_channel_variance']:
        result.update(financial_state='matched' if values.get('financial_approval_allowed',True) else 'undocumented',
            financially_complete=values.get('financial_approval_allowed',True),period_approval_allowed=values.get('financial_approval_allowed',True))
        return result
    if not treatment or treatment['status']!='approved':return result
    d=treatment['payload']
    if (d.get('cash_variance_halalas')!=values['cash_variance_halalas']
            or d.get('card_variance_halalas')!=values['bank_difference_halalas']):return result
    if not values.get('financial_approval_allowed',True):return result
    if d['kind']=='documented_channel_correction':
        # The immutable adjustment records precisely which expected channel was
        # wrong. Original source amounts and actual receipts remain separately visible.
        result.update(financial_state='corrected_by_approved_document',financially_complete=True,
            period_approval_allowed=True,reviewed_cash_variance_halalas=0,reviewed_card_variance_halalas=0,
            channel_reclassification={'cash_halalas':d['cash_variance_halalas'],
                                      'card_halalas':d['card_variance_halalas'],
                                      'treatment_id':treatment['id']})
    else:
        left=remaining(treatment)
        cash=d['cash_variance_halalas'];card=d['card_variance_halalas']
        resolved=not any(left.values())
        result.update(financial_state='settled_by_actual_recovery' if resolved else 'review_approved_outstanding',
            financially_complete=resolved,remaining_cash_halalas=left['cash'],remaining_card_halalas=left['card'],
            outstanding_receivable_halalas=sum(left[channel] for channel,v in [('cash',cash),('card',card)] if v<0),
            outstanding_payable_halalas=sum(left[channel] for channel,v in [('cash',cash),('card',card)] if v>0),
            liable_party_type=d['liable_party_type'],liable_party_name=d['liable_party_name'],
            followup_user_id=d['followup_user_id'],due_on=d['due_on'],
            period_approval_allowed=resolved or bool(treatment['approval'].get('allow_period_approval_with_outstanding')))
    return result


def shared_for_report(c,report,values):
    sub=((report.get('snapshot') or {}).get('shift') or {}).get('submission') or {}
    required=('shared_workday_id','shared_treatment_id','shared_declaration_id','shared_receipt_id','shared_allocation_id')
    if not all(sub.get(k) for k in required):return None
    difference=sub.get('receipt_difference_halalas')
    if type(difference) is not int or not difference or values['cash_variance_halalas']!=-difference or values['bank_difference_halalas']!=0:
        return None
    from .shared_financial_treatments import shared_approved
    treatment=shared_approved(c,sub['shared_workday_id'],sub['shared_declaration_id'],sub['shared_receipt_id'])
    if not treatment or treatment['id']!=sub['shared_treatment_id']:return None
    allocation=c.execute('SELECT * FROM shared_driver_closure_allocations WHERE id=%s AND declaration_id=%s AND receipt_id=%s',
        (sub['shared_allocation_id'],sub['shared_declaration_id'],sub['shared_receipt_id'])).fetchone()
    if not allocation:return None
    leg=next((r for r in allocation['legs'] if r['shift_id']==report['shift_id']),None)
    if not leg or leg.get('receipt_difference_halalas')!=difference:return None
    if sum(r.get('receipt_difference_halalas',0) for r in allocation['legs'])!=-treatment['payload']['cash_variance_halalas']:return None
    payload={**treatment['payload'],'source_shared_cash_variance_halalas':treatment['payload']['cash_variance_halalas'],
        'cash_variance_halalas':-difference,'card_variance_halalas':0,'expected_revision':values['revision']}
    result={**treatment,'origin':'shared_receipt','payload':payload}
    result['recoveries']=recovery_history(c,report['id'],result['id'])
    return result


def reviewed_values(c,report,latest=None):
    from .accounting import driver_settlement_values
    values=driver_settlement_values(report['snapshot'],latest)
    treatment=approved(c,report['id'],values['revision']) if values['has_channel_variance'] else None
    if not treatment and values['has_channel_variance']:treatment=shared_for_report(c,report,values)
    return apply(values,treatment)



def eligible_followup(c,user,report,identifier):
    target=c.execute('SELECT * FROM users WHERE id=%s',(identifier,)).fetchone()
    if multibrand.enabled():target=multibrand.project(c,target,report['brand_id'])
    if (not target or not target.get('active') or target.get('brand_id')!=report['brand_id']
            or target.get('role') not in {'owner','admin'}
            or not auth.has_permission(target,'accounting_review')
            or (target['role']!='owner' or target.get('home_brand_id')) and not set(report['branch_ids'])<=set(target.get('branch_ids',[]))):
        raise db.error(422,'FINANCIAL_FOLLOWUP_SCOPE','اختر موظف محاسبة مفعلًا ومخولًا بفروع المعالجة.')
    return target


def validate_proposal(body,values):
    if (body.expected_revision!=values['revision'] or not values['reconciliation_complete']
            or not values['has_channel_variance']):
        raise db.error(409,'FINANCIAL_TREATMENT_SOURCE_CHANGED','حدّث التسوية؛ يجب مراجعة فرق فعلي على النسخة الحالية.')
    if (body.cash_variance_halalas!=values['cash_variance_halalas']
            or body.card_variance_halalas!=values['bank_difference_halalas']):
        raise db.error(409,'FINANCIAL_TREATMENT_CHANNEL_CHANGED','مبالغ المعالجة يجب أن تطابق فرق الكاش والشبكة كلًا على حدة.')
    if body.kind=='documented_channel_correction':
        if not body.cash_variance_halalas or body.cash_variance_halalas+body.card_variance_halalas!=0:
            raise db.error(422,'FINANCIAL_CORRECTION_UNBALANCED','تصحيح وسيلة الدفع يتطلب قيمتين متعاكستين متساويتين ودليلًا محددًا.')
        if any((body.liable_party_type,body.liable_party_name,body.followup_user_id,body.due_on)):
            raise db.error(422,'FINANCIAL_CORRECTION_SCOPE','تصحيح وسيلة الدفع لا ينشئ ذمة على شخص.')
    elif not all((body.liable_party_type,body.liable_party_name,body.followup_user_id,body.due_on)):
        raise db.error(422,'FINANCIAL_RECEIVABLE_DETAILS_REQUIRED','حدد الجهة المستحقة عليها أو لها، والمتابع والموعد والدليل.')


@router.get('/{rid}/treatments')
def listing(rid:str,user=Depends(auth.current_user)):
    from .operations import safe
    with db.tx() as c:
        report,latest,values=source(c,user,rid)
        users=[]
        for row in c.execute("SELECT id FROM users WHERE active AND role IN ('owner','admin') ORDER BY display_name,id").fetchall():
            try:person=eligible_followup(c,user,report,row['id'])
            except Exception as exc:
                from fastapi import HTTPException
                if isinstance(exc,HTTPException) and exc.status_code==422:continue
                raise
            users.append({'id':person['id'],'name':person['display_name']})
        treatments=history(c,rid)
        inherited=shared_for_report(c,report,values)
        if inherited and not any(r['id']==inherited['id'] for r in treatments):treatments.append(inherited)
        for row in treatments:
            if row['status']=='approved' and row['payload']['kind']=='outstanding':
                row['recoveries']=recovery_history(c,rid,row['id']);row['remaining']=remaining(row)
        return safe({'treatments':treatments,'followup_users':users,'can_propose':bool(values['has_channel_variance'] and not any(r['status'] in {'pending','approved'} and r['payload']['expected_revision']==values['revision'] for r in treatments)),
            'can_approve':user['id'] not in custody_actors(c,report),'expected_revision':values['revision'],'source':{'cash_variance_halalas':values['cash_variance_halalas'],
            'card_variance_halalas':values['bank_difference_halalas']},**apply(values,approved(c,rid,values['revision']) or inherited)})


@router.post('/{rid}/treatments')
def propose(rid:str,body:Proposal,request:Request,user=Depends(auth.current_user)):
    from .operations import safe
    with db.tx() as c:
        report,latest,values=source(c,user,rid)
        def perform():
            from .financial_integrity import require_verified
            require_verified(report['snapshot']);validate_proposal(body,values)
            if any(r['status'] in {'pending','approved'} and r['payload']['expected_revision']==values['revision'] for r in history(c,rid)):
                raise db.error(409,'FINANCIAL_TREATMENT_EXISTS','توجد معالجة معلقة أو معتمدة لهذه النسخة؛ راجعها أولًا.')
            if body.kind=='outstanding':
                from .accounting import TZ
                if body.due_on<datetime.now(TZ).date():raise db.error(422,'FINANCIAL_TREATMENT_DUE','اختر مهلة متابعة اليوم أو لاحقًا.')
                eligible_followup(c,user,report,body.followup_user_id)
            identifier=db.new_id('ft_')
            payload={**body.model_dump(mode='json'),'treatment_id':identifier,'report_id':rid,
                'shift_id':report['shift_id'],'brand_id':report['brand_id'],'branch_ids':report['branch_ids'],
                'custodian_id':report['user_id'],'source_settlement_id':latest['id'],
                'original_system_cash_halalas':values['system_cash_halalas'],'original_system_card_halalas':values['system_bank_halalas'],
                'actual_cash_halalas':values['cash_received_halalas'],'actual_card_halalas':values['bank_halalas'],
                'no_payroll_deduction':True,'no_money_posted':True}
            db.audit(c,user['id'],PROPOSED,'report',rid,payload)
            return safe({'id':identifier,'status':'pending','payload':payload})
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'financial_treatment.propose:'+rid,body.model_dump(mode='json'),perform)


def selected(c,rid,tid,revision):
    row=next((r for r in history(c,rid) if r['id']==tid),None)
    if not row:raise db.error(404,'FINANCIAL_TREATMENT_NOT_FOUND','المعالجة غير متاحة.')
    if row['status']!='pending' or row['payload']['expected_revision']!=revision:
        raise db.error(409,'FINANCIAL_TREATMENT_STALE','المعالجة ليست معلقة على نسخة التسوية الحالية.')
    return row


@router.post('/{rid}/treatments/{tid}/approve')
def approve(rid:str,tid:str,body:Approval,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        report,latest,values=source(c,user,rid)
        def perform():
            from .financial_integrity import require_verified
            require_verified(report['snapshot'])
            if not body.confirmed or body.expected_revision!=values['revision']:
                raise db.error(409,'FINANCIAL_TREATMENT_STALE','أكد مراجعة الدليل وحدّث نسخة التسوية.')
            row=selected(c,rid,tid,values['revision']);proposal=Proposal(**{k:v for k,v in row['payload'].items() if k in Proposal.model_fields})
            validate_proposal(proposal,values)
            if row['requested_by']==user['id'] or user['id'] in custody_actors(c,report):
                raise db.error(403,'FINANCIAL_TREATMENT_SELF_APPROVAL','اعتماد المعالجة من موظف مستقل عن مقدمها وصاحب العهدة.')
            if ((proposal.liable_party_type=='employee' or body.allow_period_approval_with_outstanding)
                    and user['role']!='owner'):
                raise db.error(403,'FINANCIAL_TREATMENT_OWNER_REQUIRED','إثبات ذمة موظف أو اعتماد فترة بذمة متبقية قرار محفوظ للمالك.')
            if proposal.kind=='outstanding':eligible_followup(c,user,report,proposal.followup_user_id)
            elif body.allow_period_approval_with_outstanding:
                raise db.error(422,'FINANCIAL_TREATMENT_ALLOWANCE_SCOPE','استثناء اعتماد الفترة خاص بالذمة المثبتة فقط.')
            detail={'treatment_id':tid,'expected_revision':values['revision'],**body.model_dump(mode='json'),
                'requested_by':row['requested_by'],'approved_by':user['id'],'kind':proposal.kind,
                'cash_variance_halalas':proposal.cash_variance_halalas,'card_variance_halalas':proposal.card_variance_halalas,
                'no_payroll_deduction':True,'no_money_posted':True}
            db.audit(c,user['id'],APPROVED,'report',rid,detail)
            from .operations import safe
            return safe({'id':tid,'status':'approved','financial_state':'corrected_by_approved_document' if proposal.kind=='documented_channel_correction' else 'review_approved_outstanding',
                'outstanding_receivable_halalas':sum(max(0,-v) for v in (proposal.cash_variance_halalas,proposal.card_variance_halalas)) if proposal.kind=='outstanding' else 0,
                'outstanding_payable_halalas':sum(max(0,v) for v in (proposal.cash_variance_halalas,proposal.card_variance_halalas)) if proposal.kind=='outstanding' else 0})
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'financial_treatment.approve:'+rid+':'+tid,body.model_dump(mode='json'),perform)


@router.post('/{rid}/treatments/{tid}/reject')
def reject(rid:str,tid:str,body:Rejection,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        report,latest,values=source(c,user,rid)
        def perform():
            if body.expected_revision!=values['revision']:raise db.error(409,'FINANCIAL_TREATMENT_STALE','حدّث نسخة التسوية.')
            row=selected(c,rid,tid,values['revision'])
            db.audit(c,user['id'],REJECTED,'report',rid,{'treatment_id':tid,**body.model_dump(),'requested_by':row['requested_by']})
            return {'id':tid,'status':'rejected'}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'financial_treatment.reject:'+rid+':'+tid,body.model_dump(),perform)

RECOVERY_PROPOSED='accounting.financial_treatment.recovery_proposed'
RECOVERY_APPROVED='accounting.financial_treatment.recovery_approved'
RECOVERY_REJECTED='accounting.financial_treatment.recovery_rejected'

class Recovery(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    direction:Literal['receivable_recovery','payable_settlement']
    cash_amount_halalas:StrictInt=Field(ge=0,le=100000000000)
    card_amount_halalas:StrictInt=Field(ge=0,le=100000000000)
    cash_custody_event_id:str|None=Field(None,min_length=1,max_length=150)
    bank_reference:str|None=Field(None,min_length=5,max_length=200,pattern=r'^[^\x00]+$')
    evidence_reference:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    occurred_on:date

class RecoveryApproval(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    confirmed:StrictBool
    evidence_reference:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')


def recovery_history(c,rid,tid):
    rows=c.execute("SELECT a.*,u.display_name actor_name FROM audit_events a JOIN users u ON u.id=a.actor_id WHERE a.entity_type='report' AND a.entity_id=%s AND a.action IN (%s,%s,%s) AND a.details->>'treatment_id'=%s ORDER BY a.created_at,a.id",(rid,RECOVERY_PROPOSED,RECOVERY_APPROVED,RECOVERY_REJECTED,tid)).fetchall()
    decisions={r['details']['recovery_id']:r for r in rows if r['action']!=RECOVERY_PROPOSED}
    result=[]
    for row in rows:
        if row['action']!=RECOVERY_PROPOSED:continue
        d=row['details'];decision=decisions.get(d['recovery_id'])
        result.append({'id':d['recovery_id'],'payload':d,'requested_by':row['actor_id'],
            'requester_name':row['actor_name'],'created_at':row['created_at'],
            'status':'approved' if decision and decision['action']==RECOVERY_APPROVED else 'rejected' if decision else 'pending',
            'approval':{'approved_by':decision['actor_id'],'approver_name':decision['actor_name'],
                'created_at':decision['created_at'],**decision['details']} if decision else None})
    return result


def remaining(treatment):
    d=treatment['payload'];amounts={}
    for channel,key in [('cash','cash_variance_halalas'),('card','card_variance_halalas')]:
        variance=d[key]
        received=sum(r['payload'][channel+'_amount_halalas'] for r in treatment.get('recoveries',[])
            if r['status']=='approved' and r['payload']['direction']==('receivable_recovery' if variance<0 else 'payable_settlement'))
        amounts[channel]=max(0,abs(variance)-received)
    return amounts


def outstanding_source(c,rid,tid,current_values=None):
    row=next((r for r in history(c,rid) if r['id']==tid),None)
    if not row and current_values is not None:
        report=c.execute('SELECT * FROM closure_reports WHERE id=%s',(rid,)).fetchone()
        if report:row=shared_for_report(c,report,current_values)
        if row and row['id']!=tid:row=None
    if not row or row['status']!='approved' or row['payload']['kind']!='outstanding':
        raise db.error(409,'FINANCIAL_OUTSTANDING_REQUIRED','اختر ذمة معتمدة لم تُسدد بالكامل بعد.')
    if current_values is not None and (row['payload']['expected_revision']!=current_values['revision']
            or row['payload']['cash_variance_halalas']!=current_values['cash_variance_halalas']
            or row['payload']['card_variance_halalas']!=current_values['bank_difference_halalas']):
        raise db.error(409,'FINANCIAL_TREATMENT_STALE','تغير أصل التسوية؛ يلزم معالجة مستقلة مرتبطة بالنسخة الحالية قبل تحصيل ذمة قديمة.')
    row['recoveries']=recovery_history(c,rid,tid)
    return row


def validate_recovery(c,user,report,treatment,body):
    from .accounting import TZ
    left=remaining(treatment);d=treatment['payload']
    if body.cash_amount_halalas+body.card_amount_halalas<=0:
        raise db.error(422,'RECOVERY_POSITIVE_AMOUNT','سجل مبلغًا فعليًا موجبًا.')
    for channel,key in [('cash','cash_variance_halalas'),('card','card_variance_halalas')]:
        amount=getattr(body,channel+'_amount_halalas');variance=d[key]
        if amount and (amount>left[channel] or (variance<0)!=(body.direction=='receivable_recovery') or not variance):
            raise db.error(422,'RECOVERY_OUTSTANDING_LIMIT','المبلغ ووسيلته واتجاهه يجب أن تطابق الذمة المتبقية دون مقاصة.')
    if body.occurred_on>datetime.now(TZ).date() or body.occurred_on<report['created_at'].astimezone(TZ).date():
        raise db.error(422,'RECOVERY_ACTUAL_DATE','تاريخ التحصيل أو الرد الفعلي بين أصل التقفيلة واليوم.')
    cash_actor=[]
    if body.cash_amount_halalas:
        if not body.cash_custody_event_id:raise db.error(422,'RECOVERY_CASH_SOURCE_REQUIRED','اربط حركة عهدة نقدية فعلية مسجلة؛ لا ينشئ هذا الإجراء سند قبض.')
        event=c.execute('SELECT e.*,s.brand_id,s.branch_ids,s.user_id custodian_id FROM custody_events e JOIN shifts s ON s.id=e.shift_id WHERE e.id=%s',(body.cash_custody_event_id,)).fetchone()
        sign=1 if body.direction=='receivable_recovery' else -1
        if (not event or event['brand_id']!=report['brand_id']
                or not set(event['branch_ids'])<=set(report['branch_ids'])
                or event['amount_halalas']!=sign*body.cash_amount_halalas
                or event['created_at']<report['created_at']
                or event['kind'] not in ({'collection','direct_receipt'} if sign==1 else {'refund','expense'})):
            raise db.error(422,'RECOVERY_CASH_SOURCE_MISMATCH','حركة العهدة لا تطابق المبلغ والاتجاه والفروع وتاريخ الذمة.')
        cash_actor=[x for x in {event['actor_id'],event.get('custodian_id')} if x]
    elif body.cash_custody_event_id:raise db.error(422,'RECOVERY_CASH_SOURCE_SCOPE','مرجع العهدة خاص بمبلغ الكاش فقط.')
    if body.card_amount_halalas and not body.bank_reference:
        raise db.error(422,'RECOVERY_BANK_REFERENCE_REQUIRED','أدخل مرجع عملية البنك الفعلية ودليلها للمراجعة المستقلة.')
    if not body.card_amount_halalas and body.bank_reference:
        raise db.error(422,'RECOVERY_BANK_REFERENCE_SCOPE','مرجع البنك خاص بمبلغ الشبكة فقط.')
    return cash_actor


@router.post('/{rid}/treatments/{tid}/recoveries')
def propose_recovery(rid:str,tid:str,body:Recovery,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        report,latest,values=source(c,user,rid)
        def perform():
            treatment=outstanding_source(c,rid,tid,values)
            cash_actor=validate_recovery(c,user,report,treatment,body)
            identifier=db.new_id('ftr_')
            detail={**body.model_dump(mode='json'),'recovery_id':identifier,'treatment_id':tid,
                'brand_id':report['brand_id'],'report_id':rid,'cash_source_actor_ids':cash_actor,
                'no_money_posted':True,'no_payroll_deduction':True}
            db.audit(c,user['id'],RECOVERY_PROPOSED,'report',rid,detail)
            return {'id':identifier,'status':'pending','payload':detail}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'financial_treatment.recovery:'+rid+':'+tid,body.model_dump(mode='json'),perform)


@router.post('/{rid}/treatments/{tid}/recoveries/{recovery_id}/approve')
def approve_recovery(rid:str,tid:str,recovery_id:str,body:RecoveryApproval,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        report,latest,values=source(c,user,rid)
        def perform():
            if not body.confirmed:raise db.error(422,'RECOVERY_CONFIRMATION_REQUIRED','أكد مراجعة التحصيل أو الرد الفعلي ودليله.')
            treatment=outstanding_source(c,rid,tid,values)
            row=next((r for r in treatment['recoveries'] if r['id']==recovery_id),None)
            if not row or row['status']!='pending':raise db.error(409,'RECOVERY_NOT_PENDING','طلب التحصيل أو الرد ليس معلقًا.')
            recovery=Recovery(**{k:v for k,v in row['payload'].items() if k in Recovery.model_fields})
            cash_actor=validate_recovery(c,user,report,treatment,recovery)
            if user['id']==row['requested_by'] or user['id'] in cash_actor or user['id'] in custody_actors(c,report):
                raise db.error(403,'RECOVERY_SELF_APPROVAL','الاعتماد من موظف مستقل عن المسجل وصاحب العهدة وحركة النقد.')
            if recovery.cash_custody_event_id:
                c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('financial-recovery-cash:'+recovery.cash_custody_event_id,))
            if recovery.bank_reference:
                c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('financial-recovery-bank:'+report['brand_id']+':'+recovery.bank_reference.strip().lower(),))
            used=c.execute("SELECT 1 FROM audit_events WHERE action=%s AND (details->>'cash_custody_event_id'=%s OR (details->>'brand_id'=%s AND lower(details->>'bank_reference')=%s)) LIMIT 1",(RECOVERY_APPROVED,recovery.cash_custody_event_id,report['brand_id'],recovery.bank_reference.strip().lower() if recovery.bank_reference else None)).fetchone()
            if used:raise db.error(409,'RECOVERY_SOURCE_ALREADY_USED','حركة النقد أو مرجع البنك مرتبطان بمعالجة محفوظة؛ يمنع احتسابهما مرتين.')
            detail={**row['payload'],**body.model_dump(),'requested_by':row['requested_by'],'approved_by':user['id']}
            db.audit(c,user['id'],RECOVERY_APPROVED,'report',rid,detail)
            return {'id':recovery_id,'status':'approved','treatment_id':tid,'no_money_posted':True}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'financial_treatment.recovery_approve:'+rid+':'+tid+':'+recovery_id,body.model_dump(),perform)

class RecoveryRejection(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')


@router.post('/{rid}/treatments/{tid}/recoveries/{recovery_id}/reject')
def reject_recovery(rid:str,tid:str,recovery_id:str,body:RecoveryRejection,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        report,latest,values=source(c,user,rid)
        def perform():
            treatment=outstanding_source(c,rid,tid,values)
            row=next((r for r in treatment['recoveries'] if r['id']==recovery_id),None)
            if not row or row['status']!='pending':raise db.error(409,'RECOVERY_NOT_PENDING','طلب التحصيل أو الرد ليس معلقًا.')
            db.audit(c,user['id'],RECOVERY_REJECTED,'report',rid,{'recovery_id':recovery_id,'treatment_id':tid,
                'note':body.note,'requested_by':row['requested_by'],'no_money_posted':True})
            return {'id':recovery_id,'status':'rejected','treatment_id':tid}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'financial_treatment.recovery_reject:'+rid+':'+tid+':'+recovery_id,body.model_dump(),perform)


@router.get('/{rid}/treatments/{tid}/recovery-sources')
def recovery_sources(rid:str,tid:str,direction:Literal['receivable_recovery','payable_settlement'],user=Depends(auth.current_user)):
    from .operations import safe
    with db.tx() as c:
        report,latest,values=source(c,user,rid)
        treatment=outstanding_source(c,rid,tid,values);left=remaining(treatment)
        sign=1 if direction=='receivable_recovery' else -1
        if (treatment['payload']['cash_variance_halalas']<0)!=(sign==1):return {'sources':[]}
        rows=c.execute("""SELECT e.*,u.display_name actor_name,p.display_name custodian_name,s.branch_ids,s.brand_id
            FROM custody_events e JOIN shifts s ON s.id=e.shift_id JOIN users u ON u.id=e.actor_id JOIN users p ON p.id=s.user_id
            WHERE s.brand_id=%s AND e.created_at>=%s AND e.amount_halalas*%s>0
            AND e.amount_halalas*%s<=%s AND e.kind=ANY(%s)
            AND NOT EXISTS(SELECT 1 FROM audit_events a WHERE a.action=%s AND a.details->>'cash_custody_event_id'=e.id)
            ORDER BY e.created_at DESC,e.id DESC LIMIT 500""",(report['brand_id'],report['created_at'],sign,sign,left['cash'],
            ['collection','direct_receipt'] if sign==1 else ['refund','expense'],RECOVERY_APPROVED)).fetchall()
        return safe({'sources':[{k:r[k] for k in ('id','amount_halalas','created_at','actor_name','custodian_name','reference_id','kind')}
            for r in rows if set(r['branch_ids'])<=set(report['branch_ids'])]})
