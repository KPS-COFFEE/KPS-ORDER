"""Physical cash/card evidence once; accountant-only evidenced brand allocation.

No declaration or physical receipt invents a brand's actual cash. Canonical
financial snapshots are created only after an accountant attributes exact totals.
"""
from fastapi import APIRouter, Depends, Request
from pydantic import Field, StrictBool, StrictInt
from . import auth, db, multibrand
from . import operations as ops
from . import central_workdays as workdays

router=APIRouter(prefix='/api',tags=['shared-driver-closure'])
BASE='/central-operations/workdays/{sid}'


def staffing_pending_sql(alias='shifts'):
    if alias not in {'shifts','s','driver','child'}:raise ValueError('Unknown shift alias')
    return f"{alias}.staffing_ended_at IS NULL"


def physical_finished(c,sid):
    return bool(c.execute("SELECT 1 FROM shifts WHERE id=%s AND status IN ('submitted','closed') AND staffing_ended_at IS NOT NULL",(sid,)).fetchone())


def pending_pool(c,manager_sid):
    if not multibrand.enabled():return []
    return c.execute("SELECT DISTINCT d.id declaration_id,r.id receipt_id,r.received_halalas,r.received_by,d.workday_id FROM shared_driver_closure_receipts r JOIN shared_driver_closure_declarations d ON d.id=r.declaration_id JOIN central_workday_legs l ON l.workday_id=d.workday_id JOIN shifts s ON s.id=l.shift_id LEFT JOIN shared_driver_closure_allocations a ON a.declaration_id=d.id WHERE s.manager_shift_id=%s AND a.id IS NULL AND NOT EXISTS (SELECT 1 FROM driver_cash_bags b WHERE b.declaration_id=d.id AND b.destination='branch')",(manager_sid,)).fetchall()


def accounting(user):
    return user['role'] in {'owner','admin'} and auth.has_permission(user,'accounting_review')


def authorize(c,user,sid,*,manager=False):
    workdays.require_enabled(user,'accounting_review' if accounting(user) else 'shifts_view',existing_sid=sid)
    day,legs=workdays.authorized(c,user,sid,manager=manager,capability='accounting_review' if accounting(user) else None)
    if day['kind']!='driver':raise db.error(409,'SHARED_DRIVER_REQUIRED','اختر وردية سائق.')
    return day,workdays.ordered(legs,day['anchor_shift_id'])


def latest(c,day_id):
    return c.execute('SELECT * FROM shared_driver_closure_declarations WHERE workday_id=%s ORDER BY revision DESC LIMIT 1',(day_id,)).fetchone()


def manager_approval_required(c,legs):
    """A remote duty needs a final decision even for clients without bag metadata."""
    from .catalog import normalize_city
    branches={bid for _,shift in legs for bid in shift['branch_ids']}
    return any(normalize_city(row['city'])!='حفر الباطن' for row in
               c.execute('SELECT city FROM branches WHERE id=ANY(%s)',(list(branches),)).fetchall())


def legacy_released(legs):
    """Preserve completed duties recorded before the final-approval contract."""
    return bool(legs) and all(s.get('staffing_ended_at') and 'manager_approval_required' not in (s.get('submission') or {}) for _,s in legs)


def manager_approval(c,legs,declaration):
    if not declaration:return None
    approvals=[]
    for _,shift in legs:
        row=c.execute('SELECT submission FROM shifts WHERE id=%s',(shift['id'],)).fetchone()
        approval=(row['submission'] or {}).get('manager_approval') if row else None
        if not approval or approval.get('declaration_id')!=declaration['id']:return None
        approvals.append(approval)
    return approvals[0] if approvals and all(a==approvals[0] for a in approvals) else None


def accounting_followup(c,legs,fallback_user_id):
    """Choose a currently scoped accountant; receiver retains escalation if absent."""
    candidates=c.execute("SELECT * FROM users WHERE active AND role IN ('admin','owner') ORDER BY (role='owner'),display_name,id").fetchall()
    for target in candidates:
        allowed=True
        for _,shift in legs:
            principal=multibrand.project(c,target,shift['brand_id'])
            if not principal or not accounting(principal) or (principal['role']!='owner' and not set(shift['branch_ids'])<=set(principal.get('branch_ids',[]))):
                allowed=False;break
        if allowed:return target['id']
    return fallback_user_id


def receipt_difference(declaration,receipt):
    """Compare two independently witnessed facts; never rewrite either amount."""
    if not declaration or not receipt:return None
    return receipt['received_halalas']-(declaration['cash_halalas']-declaration['retained_halalas'])


def view(c,user,day,legs):
    declaration=latest(c,day['id'])
    receipt=c.execute('SELECT * FROM shared_driver_closure_receipts WHERE declaration_id=%s',(declaration['id'],)).fetchone() if declaration else None
    allocation=c.execute('SELECT * FROM shared_driver_closure_allocations WHERE declaration_id=%s',(declaration['id'],)).fetchone() if declaration else None
    returned=c.execute('SELECT * FROM shared_driver_closure_returns WHERE declaration_id=%s',(declaration['id'],)).fetchone() if declaration else None
    handover_difference=receipt_difference(declaration,receipt)
    from .shared_financial_treatments import shared_approved,allocated_review
    review=allocated_review(c,day['id']) if allocation else None
    treatment=shared_approved(c,day['id'],declaration['id'],receipt['id']) if declaration and receipt else None
    expected=sum(row['expected_halalas'] for row in declaration['expected_snapshot']) if declaration else sum(ops.balance(c,s) for _,s in legs)
    cards=sum(ops.blind_closure.card_reconciliation(c,s['id'],0,(s.get('external_enbishan') or {}).get('card_halalas',0))['expected_card_halalas'] for _,s in legs)
    if declaration:cards=sum(row['expected_card_halalas'] for row in declaration['expected_snapshot'])
    output={'driver_id':day['user_id'],'driver_name':c.execute('SELECT display_name FROM users WHERE id=%s',(day['user_id'],)).fetchone()['display_name'],'shift':{'id':day['anchor_shift_id'],'kind':'driver','user_id':day['user_id'],'status':legs[0][1]['status'],'delivery_operational_date':legs[0][1]['delivery_operational_date']},'anchor_shift_id':day['anchor_shift_id'],'workday_id':day['id'],'declaration':declaration,'receipt':receipt,'allocation':allocation,'returned':returned,
      'state':'allocated_amount_settled' if allocation and treatment and review and review['financially_complete'] else 'allocated_with_outstanding' if allocation and treatment else 'allocated' if allocation else 'review_approved_outstanding' if treatment else 'receipt_difference_review' if handover_difference else 'awaiting_accounting' if receipt else 'returned' if returned else 'awaiting_receipt' if declaration else 'awaiting_driver',
      'expected_cash_halalas':expected,'expected_card_halalas':cards,
      'receipt_difference_halalas':handover_difference,
      'receipt_review_required':bool(handover_difference and not treatment),'receipt_treatment':treatment,'receipt_accounting_review':review,
      'cash_difference_halalas':declaration['cash_halalas']-sum(x['expected_halalas'] for x in declaration['expected_snapshot']) if declaration else None,
      'card_difference_halalas':declaration['card_halalas']-sum(x['expected_card_halalas'] for x in declaration['expected_snapshot']) if declaration else None,
      'legs':[{'shift_id':s['id'],'brand_id':s['brand_id'],'expected_cash_halalas':ops.balance(c,s),'permanent_float_halalas':(s.get('permanent_float') or {}).get('amount',0)} for _,s in legs],
      'can_allocate':accounting(user) and user['id']!=day['user_id'] and (not receipt or user['id']!=receipt['received_by']),'staffing_finished':all(physical_finished(c,s['id']) for _,s in legs),'staffing_block_reason':'retained_float_attribution' if receipt and not all(physical_finished(c,s['id']) for _,s in legs) else None}
    from .cash_bags import bag_view
    output['cash_bag']=bag_view(c,day['id'])
    output['handover_branches']=c.execute('SELECT id,name,city FROM branches WHERE id=ANY(%s) ORDER BY branch_no', (list({bid for _,shift in legs for bid in shift['branch_ids']}),)).fetchall()
    if output['cash_bag'] and not receipt:
        output['state']='awaiting_accounting_receipt' if output['cash_bag']['destination']=='branch' else 'awaiting_manager_receipt'
    required=bool(declaration and not legacy_released(legs) and (manager_approval_required(c,legs) or (output['cash_bag'] and output['cash_bag']['destination']=='branch')))
    approval=manager_approval(c,legs,declaration)
    output.update(manager_approval_required=required,manager_approval=approval,
                  can_approve=bool(required and declaration and not returned and not approval and
                    (receipt or (output['cash_bag'] and output['cash_bag']['destination']=='branch')) and
                    user['role']=='delivery_manager' and auth.has_permission(user,'shifts_approve') and
                    user['id']!=day['user_id'] and all(row['manager_user_id']==user['id'] for row in declaration['expected_snapshot'])))
    if required and not approval and not returned:
        output.update(state='awaiting_manager_approval',staffing_finished=False,staffing_block_reason='awaiting_manager_approval')
    if user['role']=='driver':
        for key in ['expected_cash_halalas','expected_card_halalas','cash_difference_halalas','card_difference_halalas','receipt_difference_halalas','receipt_review_required','receipt_treatment','receipt_accounting_review','legs']:output.pop(key,None)
        if declaration:output['declaration']={k:v for k,v in declaration.items() if k not in {'expected_snapshot'}}
        output['allocation']=None
    return ops.safe(output)


class PhysicalSubmission(ops.OperationBody):
    cash_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    card_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    vehicle_inspection:ops.shift_vehicle.Inspection|None=None
    reason:str=Field('',max_length=2000)
    handover_branch_id:str|None=None
    bag_reference:str=Field(default='',max_length=200)
    bag_delivered:StrictBool=False


class PhysicalReceipt(ops.OperationBody):
    declaration_id:str
    received_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    confirmed:StrictBool
    reference:str=Field(min_length=1,max_length=200)


class ManagerApproval(ops.OperationBody):
    declaration_id:str
    confirmed:StrictBool
    reason:str=Field(default='',max_length=2000)


class AllocationLeg(ops.OperationBody):
    shift_id:str
    counted_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    received_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    retained_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    terminal_total_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    evidence:str=Field(min_length=3,max_length=1000)
    receipt_difference_halalas:StrictInt=Field(0,ge=-ops.MAX_HALALAS,le=ops.MAX_HALALAS)


class Allocation(ops.OperationBody):
    declaration_id:str
    receipt_id:str
    legs:list[AllocationLeg]=Field(min_length=1,max_length=2)
    reason:str=Field(min_length=5,max_length=2000)
    confirmed:StrictBool


class Return(ops.OperationBody):
    declaration_id:str
    reason:str=Field(min_length=3,max_length=1000)


@router.get(BASE+'/physical-closure')
def detail(sid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        day,legs=authorize(c,user,sid)
        return view(c,user,day,legs)


def validate_operational(c,user,legs):
    from .driver_work_hours import guard_shift_completion
    from .driver_breaks import guard as break_guard
    for principal,s in legs:
        ops.guard_accepted_awaiting_dispatch(c,s)
        guard_shift_completion(c,s);break_guard(c,s['id'])
        if ops.unresolved(c,s['id']):raise db.error(409,'OPEN_TASKS','أكمل معالجة المهام المعلقة قبل إرسال التقفيلة.')
        if c.execute("SELECT 1 FROM custody_transfers WHERE (from_shift_id=%s OR to_shift_id=%s) AND status='pending'",(s['id'],s['id'])).fetchone():raise db.error(409,'RECEIPT_PENDING','أكد أو أعد تسليم النقد المعلق أولًا.')
        if c.execute("SELECT 1 FROM manager_handovers WHERE (from_shift_id=ANY(%s) OR to_shift_id=ANY(%s)) AND status='pending'",([s['id'],s['manager_shift_id']],[s['id'],s['manager_shift_id']])).fetchone():raise db.error(409,'HANDOVER_PENDING','أكمل تسليم مسؤولية المناوبة أولًا.')


@router.post(BASE+'/physical-submit')
def submit(sid:str,body:PhysicalSubmission,request:Request,user=Depends(auth.current_user)):
    ops.roles(user,'driver','delivery_manager')
    with db.tx() as c:
        workdays.lock_lanes(c)
        day,legs=authorize(c,user,sid,manager=user['role']=='delivery_manager')
        if not auth.has_permission(user,'shifts_close' if user['role']=='driver' else 'shifts_delegate_close'):raise db.error(403,'SHARED_CLOSURE_FORBIDDEN','غير مخول لإرسال التقفيلة.')
        if user['role']=='driver' and day['user_id']!=user['id']:raise db.error(404,'SHARED_DRIVER_REQUIRED','الوردية غير متاحة.')
        def perform():
            if any(s['status'] not in ({'closing'} if user['role']=='driver' else {'open','closing'}) for _,s in legs):raise db.error(409,'SHARED_CLOSURE_STATE','ابدأ إنهاء الوردية أولًا أو راجع التقفيلة المرسلة.')
            if user['role']=='delivery_manager' and not body.reason.strip():raise db.error(422,'SHARED_DELEGATION_REASON','سجل سبب الإرسال نيابة عن السائق.')
            validate_operational(c,user,legs)
            previous=latest(c,day['id'])
            if previous and not c.execute('SELECT 1 FROM shared_driver_closure_returns WHERE declaration_id=%s',(previous['id'],)).fetchone():raise db.error(409,'SHARED_DECLARATION_EXISTS','التقفيلة مرسلة؛ أعدها للتصحيح قبل إدخال أرقام جديدة.')
            snapshot=[]
            for principal,s in legs:
                manager=ops.lock_shift(c,s['manager_shift_id'])
                if not manager or manager['kind']!='manager' or manager['status'] not in {'open','closing'}:raise db.error(409,'SHARED_MANAGER_CLOSED','شفت مدير الاستلام غير متاح.')
                with workdays.action():ops.shift_vehicle.finish(c,principal,s,body.vehicle_inspection)
                card=ops.blind_closure.card_reconciliation(c,s['id'],0,(s.get('external_enbishan') or {}).get('card_halalas',0))
                snapshot.append({'shift_id':s['id'],'brand_id':s['brand_id'],'manager_shift_id':manager['id'],'manager_user_id':manager['user_id'],'expected_halalas':ops.balance(c,s),'expected_card_halalas':card['expected_card_halalas'],'permanent_float_halalas':(s.get('permanent_float') or {}).get('amount',0)})
            if len({row['manager_user_id'] for row in snapshot})!=1:raise db.error(409,'SHARED_MANAGER_SCOPE','وردية السائق يجب أن تتبع مدير استلام واحدًا.')
            retained=min(body.cash_halalas,sum(row['permanent_float_halalas'] for row in snapshot))
            identifier=ops.uid();revision=(previous['revision']+1 if previous else 1)
            approval_required=manager_approval_required(c,legs)
            c.execute('INSERT INTO shared_driver_closure_declarations(id,workday_id,revision,cash_halalas,card_halalas,retained_halalas,expected_snapshot,recorded_by,reason) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',(identifier,day['id'],revision,body.cash_halalas,body.card_halalas,retained,db.json(snapshot),user['id'],body.reason))
            for _,s in legs:
                # No actual amount is attributed to either brand at submission.
                c.execute("UPDATE shifts SET status='submitted',submitted_at=now(),submission=%s WHERE id=%s",(db.json({'shared_declaration_id':identifier,'physical_state':'awaiting_receipt','financial_state':'awaiting_allocation','manager_approval_required':approval_required}),s['id']))
            if body.handover_branch_id:
                from .cash_bags import create
                declaration=c.execute('SELECT * FROM shared_driver_closure_declarations WHERE id=%s',(identifier,)).fetchone()
                create(c,user,day,legs,declaration,body.handover_branch_id,body.bag_reference,body.bag_delivered)
            db.audit(c,user['id'],'shared_driver.declared','central_workday',day['id'],{'declaration_id':identifier,'cash_halalas':body.cash_halalas,'card_halalas':body.card_halalas,'retained_halalas':retained,'reason':body.reason})
            return {'declaration_id':identifier}
        value=db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),'shared_driver.declare:'+day['id'],body.model_dump(mode='json'),perform)
        return {**value,**view(c,user,day,legs)}


@router.post(BASE+'/physical-receipt')
def receive(sid:str,body:PhysicalReceipt,request:Request,user=Depends(auth.current_user)):
    ops.roles(user,'delivery_manager')
    if not auth.has_permission(user,'shifts_delegate_close'):raise db.error(403,'SHARED_CLOSURE_FORBIDDEN','غير مخول للاستلام.')
    with db.tx() as c:
        workdays.lock_lanes(c);day,legs=authorize(c,user,sid,manager=True)
        def perform():
            declaration=latest(c,day['id'])
            if not declaration or declaration['id']!=body.declaration_id or c.execute('SELECT 1 FROM shared_driver_closure_returns WHERE declaration_id=%s',(body.declaration_id,)).fetchone():raise db.error(409,'SHARED_DECLARATION_STALE','تغيرت التقفيلة؛ حدّث الشاشة.')
            if c.execute('SELECT 1 FROM shared_driver_closure_receipts WHERE declaration_id=%s',(declaration['id'],)).fetchone():raise db.error(409,'SHARED_RECEIPT_EXISTS','الاستلام محفوظ؛ لا تسجله مرة ثانية.')
            remote_bag=c.execute("SELECT id FROM driver_cash_bags WHERE declaration_id=%s AND destination='branch'",(declaration['id'],)).fetchone()
            if remote_bag:raise db.error(409,'CASH_BAG_AWAITING_ACCOUNTANT','الكيس في الفرع وينتظر استلام المحاسبة؛ لا يسجل مدير التوصيل استلامًا لم يحدث.')
            if not body.confirmed:raise db.error(422,'SHARED_RECEIPT_CONFIRMATION','أكد فقط النقد الذي استلمته فعليًا؛ يبقى إقرار السائق محفوظًا مستقلاً.')
            if any(row['manager_user_id']!=user['id'] for row in declaration['expected_snapshot']):raise db.error(403,'SHARED_RECEIPT_RECIPIENT','مدير الوردية المسجل وحده يؤكد استلام النقد.')
            if any(s['status']!='submitted' for _,s in legs):raise db.error(409,'SHARED_CLOSURE_STATE','التقفيلة ليست مرسلة.')
            validate_operational(c,user,legs)
            # Witnessing physical cash is independent of entitlement decisions.
            # Those remain mandatory at accounting allocation below.
            identifier=ops.uid()
            c.execute('INSERT INTO shared_driver_closure_receipts(id,declaration_id,received_halalas,received_by,manager_shift_ids,reference) VALUES(%s,%s,%s,%s,%s,%s)',(identifier,declaration['id'],body.received_halalas,user['id'],db.json([s['manager_shift_id'] for _,s in legs]),body.reference))
            for _,s in legs:
                c.execute('UPDATE shifts SET submission=submission||%s::jsonb WHERE id=%s',(db.json({'physical_state':'received','physical_receipt_id':identifier}),s['id']))
            recorded=c.execute('SELECT created_at FROM shared_driver_closure_receipts WHERE id=%s',(identifier,)).fetchone()['created_at']
            approved_float=sum(row['permanent_float_halalas'] for row in declaration['expected_snapshot'])
            float_sources=[row for row in declaration['expected_snapshot'] if row['permanent_float_halalas']]
            shortfall=max(0,approved_float-declaration['retained_halalas'])
            if shortfall or len(float_sources)>1:
                from datetime import timedelta
                # A shortage is evidence for review, never an employee debt.
                responsible=accounting_followup(c,legs,user['id'])
                db.audit(c,user['id'],'shared_driver.float_followup','central_workday',day['id'],{
                    'declaration_id':declaration['id'],'receipt_id':identifier,'responsible_user_id':responsible,
                    'responsible_role':'accounting','due_at':(recorded+timedelta(hours=24)).isoformat(),
                    'approved_float_halalas':approved_float,'declared_retained_halalas':declaration['retained_halalas'],
                    'shortfall_halalas':shortfall,'duplicate_brand_float_review':len(float_sources)>1,
                    'required_action':'Review original float documents and actual retained cash; establish responsibility only from evidence',
                    'no_employee_liability_assumed':True,'no_money_posted':True})
            for row in declaration['expected_snapshot']:
                # A single approved float has an unambiguous declared retained
                # amount. Multiple brand approvals require accounting evidence;
                # never invent an allocation or reissue their combined amount.
                retained=declaration['retained_halalas'] if len(float_sources)==1 and row['permanent_float_halalas'] else 0
                if retained:
                    c.execute('INSERT INTO retained_carryovers(id,from_shift_id,user_id,amount_halalas,approved_by,reason) VALUES(%s,%s,%s,%s,%s,%s)',(ops.uid(),row['shift_id'],day['user_id'],retained,user['id'],'Actual retained cash declared once at shared receipt '+identifier))
                if not manager_approval_required(c,legs) or manager_approval(c,legs,declaration):
                    c.execute('UPDATE shifts SET staffing_ended_at=%s WHERE id=%s',(recorded,row['shift_id']))
            db.audit(c,user['id'],'shared_driver.physical_received','central_workday',day['id'],{'declaration_id':declaration['id'],'receipt_id':identifier,'received_halalas':body.received_halalas,'reference':body.reference,'receipt_difference_halalas':body.received_halalas-(declaration['cash_halalas']-declaration['retained_halalas']),'responsible_role':'accounting','financial_state':'receipt_difference_review' if body.received_halalas!=declaration['cash_halalas']-declaration['retained_halalas'] else 'awaiting_accounting_allocation'})
            return {'receipt_id':identifier}
        value=db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),'shared_driver.receipt:'+day['id'],body.model_dump(),perform)
        return {**value,**view(c,user,day,legs)}


@router.post(BASE+'/physical-approval')
def approve_physical(sid:str,body:ManagerApproval,request:Request,user=Depends(auth.current_user)):
    ops.roles(user,'delivery_manager')
    if not auth.has_permission(user,'shifts_approve'):raise db.error(403,'SHARED_CLOSURE_FORBIDDEN','غير مخول لاعتماد التقفيلة.')
    with db.tx() as c:
        workdays.lock_lanes(c);day,legs=authorize(c,user,sid,manager=True)
        def perform():
            declaration=latest(c,day['id'])
            if not declaration or declaration['id']!=body.declaration_id or c.execute('SELECT 1 FROM shared_driver_closure_returns WHERE declaration_id=%s',(body.declaration_id,)).fetchone():raise db.error(409,'SHARED_DECLARATION_STALE','تغيرت التقفيلة؛ حدّث الشاشة.')
            if user['id']==day['user_id']:raise db.error(403,'SHARED_APPROVAL_SELF','لا يعتمد السائق تقفيلة ورديته بنفسه.')
            if any(row['manager_user_id']!=user['id'] for row in declaration['expected_snapshot']):raise db.error(403,'SHARED_APPROVAL_MANAGER','مدير الوردية المسجل وحده يعتمد التقفيلة.')
            if any(not auth.has_permission(principal,'shifts_approve') for principal,_ in legs):raise db.error(403,'SHARED_CLOSURE_FORBIDDEN','غير مخول لاعتماد أحد سجلات البراندات.')
            if any(s['status']!='submitted' or (s.get('submission') or {}).get('shared_declaration_id')!=declaration['id'] for _,s in legs):raise db.error(409,'SHARED_CLOSURE_STATE','التقفيلة ليست مرسلة أو لا تطابق الأصل.')
            if manager_approval(c,legs,declaration):raise db.error(409,'SHARED_APPROVAL_EXISTS','اعتماد التقفيلة محفوظ.')
            bag=c.execute('SELECT * FROM driver_cash_bags WHERE declaration_id=%s',(declaration['id'],)).fetchone()
            if legacy_released(legs) or (not manager_approval_required(c,legs) and not (bag and bag['destination']=='branch')):raise db.error(409,'SHARED_APPROVAL_NOT_REQUIRED','هذه التقفيلة تتطلب الاستلام الفعلي من مدير التوصيل.')
            if not body.confirmed:raise db.error(422,'SHARED_APPROVAL_CONFIRMATION','أكد مراجعة واعتماد التقفيلة النهائية بعد إرسال السائق.')
            receipt=c.execute('SELECT id FROM shared_driver_closure_receipts WHERE declaration_id=%s',(declaration['id'],)).fetchone()
            if not (bag and bag['destination']=='branch') and not receipt:raise db.error(409,'SHARED_APPROVAL_HANDOVER_PENDING','سجل تسليم الكيس الفعلي أولًا.')
            validate_operational(c,user,legs)
            recorded=c.execute('SELECT now() approved_at').fetchone()['approved_at']
            approval={'id':ops.uid(),'declaration_id':declaration['id'],'approved_by':user['id'],'approved_at':recorded.isoformat(),'reason':body.reason,'delegated_submission':declaration['recorded_by']!=day['user_id'],'submitted_by':declaration['recorded_by'],'submission_reason':declaration.get('reason','')}
            db.audit(c,user['id'],'shared_driver.manager_approved','central_workday',day['id'],{**approval,'cash_receipt_recorded':False,'bag_id':bag['id'] if bag else None})
            for _,s in legs:
                c.execute('UPDATE shifts SET staffing_ended_at=%s,submission=submission||%s::jsonb WHERE id=%s',(recorded,db.json({'manager_approval':approval,'manager_approval_required':True}),s['id']))
            return {'approval_id':approval['id']}
        value=db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),'shared_driver.manager_approval:'+day['id'],body.model_dump(),perform)
        return {**value,**view(c,user,day,legs)}


@router.post(BASE+'/physical-return')
def return_declaration(sid:str,body:Return,request:Request,user=Depends(auth.current_user)):
    ops.roles(user,'delivery_manager')
    with db.tx() as c:
        workdays.lock_lanes(c);day,legs=authorize(c,user,sid,manager=True)
        if not auth.has_permission(user,'shifts_approve'):raise db.error(403,'SHARED_CLOSURE_FORBIDDEN','غير مخول لإرجاع التقفيلة.')
        def perform():
            declaration=latest(c,day['id'])
            if not declaration or declaration['id']!=body.declaration_id:raise db.error(409,'SHARED_DECLARATION_STALE','تغيرت التقفيلة.')
            if c.execute("SELECT 1 FROM driver_cash_bags WHERE declaration_id=%s AND destination='branch'",(declaration['id'],)).fetchone():raise db.error(409,'CASH_BAG_DECLARATION_IMMUTABLE','الكيس مسجل؛ صحح الفروقات بمعالجة محاسبية مرتبطة دون إعادة أصل التقفيلة.')
            if c.execute('SELECT 1 FROM shared_driver_closure_receipts WHERE declaration_id=%s',(declaration['id'],)).fetchone():raise db.error(409,'SHARED_RECEIPT_IMMUTABLE','تم الاستلام؛ المعالجة اللاحقة للمحاسبة مع الحفاظ على الأصل.')
            if c.execute('SELECT 1 FROM shared_driver_closure_returns WHERE declaration_id=%s',(declaration['id'],)).fetchone():raise db.error(409,'SHARED_RETURN_EXISTS','التقفيلة أُعيدت بالفعل.')
            c.execute('INSERT INTO shared_driver_closure_returns(declaration_id,recorded_by,reason) VALUES(%s,%s,%s)',(declaration['id'],user['id'],body.reason))
            for _,s in legs:c.execute("UPDATE shifts SET status='closing',submission=NULL,submitted_at=NULL WHERE id=%s",(s['id'],))
            db.audit(c,user['id'],'shared_driver.returned','central_workday',day['id'],body.model_dump())
            return {'status':'returned'}
        value=db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),'shared_driver.return:'+day['id'],body.model_dump(),perform)
        return {**value,**view(c,user,day,legs)}


def validate_allocation(declaration,receipt,rows,treatment=None):
    from .shift_reconciliation import meaningful_reason
    if any(not meaningful_reason(row.evidence) for row in rows):raise db.error(422,'SHARED_ALLOCATION_EVIDENCE','أدخل مرجع دليل واضح لكل براند؛ الأرقام وحدها لا تكفي.')
    discrepancy=receipt_difference(declaration,receipt)
    if discrepancy and (not treatment or treatment['status']!='approved' or treatment['payload']['cash_variance_halalas']!=discrepancy):raise db.error(409,'SHARED_RECEIPT_REVIEW_REQUIRED','إقرار السائق والاستلام الفعلي مختلفان؛ يلزم حسم محاسبي معتمد مرتبط بالأصل قبل الإسناد. لا تغيّر أيًا من المبلغين لإظهار التطابق.')
    supplied={row.shift_id:row for row in rows}
    expected={row['shift_id']:row for row in declaration['expected_snapshot']}
    if len(supplied)!=len(rows) or set(supplied)!=set(expected):raise db.error(422,'SHARED_ALLOCATION_LEGS','أدخل سجلات البراندات المعتمدة مرة واحدة.')
    if (sum(r.counted_halalas for r in rows)!=declaration['cash_halalas'] or sum(r.terminal_total_halalas for r in rows)!=declaration['card_halalas'] or sum(r.retained_halalas for r in rows)!=declaration['retained_halalas'] or sum(r.received_halalas for r in rows)!=receipt['received_halalas']):raise db.error(422,'SHARED_ALLOCATION_TOTAL','توزيع المحاسبة يجب أن يساوي الأصل النقدي والشبكة والاستلام الفعلي تمامًا.')
    if sum(r.receipt_difference_halalas for r in rows)!=-(discrepancy or 0):raise db.error(422,'SHARED_RECEIPT_ALLOCATION_DIFFERENCE','مجموع ذمة الاستلام المنسوبة بالأدلة يجب أن يطابق فرق الإقرار والاستلام المعتمد بالضبط.')
    for row in rows:
        if row.counted_halalas!=row.received_halalas+row.retained_halalas+row.receipt_difference_halalas:raise db.error(422,'SHARED_ALLOCATION_BALANCE','المعدود يساوي المستلم والمحتفظ به وفرق الاستلام الموثق لكل سجل.')
        if row.retained_halalas>expected[row.shift_id]['permanent_float_halalas']:raise db.error(422,'SHARED_ALLOCATION_FLOAT','لا تتجاوز العهدة الدائمة المسجلة لكل براند.')
    return supplied,expected


@router.post(BASE+'/physical-allocation')
def allocate(sid:str,body:Allocation,request:Request,user=Depends(auth.current_user)):
    if not accounting(user):raise db.error(403,'SHARED_ACCOUNTANT_REQUIRED','إسناد المبالغ للبراندات للمحاسبة فقط.')
    from .shift_reconciliation import meaningful_reason
    if not meaningful_reason(body.reason):raise db.error(422,'SHARED_ALLOCATION_REASON','اكتب أساس الإسناد المحاسبي بوضوح؛ الأرقام وحدها لا تكفي.')
    with db.tx() as c:
        workdays.lock_lanes(c);day,legs=authorize(c,user,sid)
        def perform():
            declaration=latest(c,day['id'])
            receipt=c.execute('SELECT * FROM shared_driver_closure_receipts WHERE id=%s AND declaration_id=%s',(body.receipt_id,body.declaration_id)).fetchone()
            if not body.confirmed or not declaration or declaration['id']!=body.declaration_id or not receipt:raise db.error(409,'SHARED_ALLOCATION_STALE','راجع أصل التقفيلة والاستلام الفعلي وحدّث الشاشة.')
            if user['id'] in {day['user_id'],receipt['received_by']}:raise db.error(403,'SHARED_CUSTODY_SELF_APPROVAL','لا يعتمد صاحب العهدة أو مستلمها إسنادها ومعالجتها المالية بنفسه؛ يلزم محاسب أو معتمد مستقل مخول.')
            if c.execute('SELECT 1 FROM shared_driver_closure_allocations WHERE declaration_id=%s',(declaration['id'],)).fetchone():raise db.error(409,'SHARED_ALLOCATION_EXISTS','الإسناد المحاسبي محفوظ؛ التصحيح اللاحق بقيد مرتبط.')
            from .shared_financial_treatments import shared_approved
            treatment=shared_approved(c,day['id'],declaration['id'],receipt['id'])
            bag=c.execute('SELECT destination FROM driver_cash_bags WHERE declaration_id=%s',(declaration['id'],)).fetchone()
            approval=manager_approval(c,legs,declaration)
            approval_required=manager_approval_required(c,legs) or bool(bag and bag['destination']=='branch')
            if approval_required and not approval and not legacy_released(legs):raise db.error(409,'SHARED_MANAGER_APPROVAL_PENDING','يلزم اعتماد مدير التوصيل للتقفيلة النهائية قبل الإقفال.')
            supplied,expected=validate_allocation(declaration,receipt,body.legs,treatment)
            validate_operational(c,user,legs)
            identifier=ops.uid();reports=[]
            c.execute('INSERT INTO shared_driver_closure_allocations(id,declaration_id,receipt_id,legs,recorded_by,reason) VALUES(%s,%s,%s,%s,%s,%s)',(identifier,declaration['id'],receipt['id'],db.json([r.model_dump() for r in body.legs]),user['id'],body.reason))
            for principal,s in legs:
                row=supplied[s['id']];source=expected[s['id']]
                if s['status']!='submitted' or (s.get('submission') or {}).get('shared_declaration_id')!=declaration['id']:raise db.error(409,'SHARED_ALLOCATION_STATE','سجل الوردية لا يطابق أصل التقفيلة.')
                bag=c.execute('SELECT destination FROM driver_cash_bags WHERE declaration_id=%s',(declaration['id'],)).fetchone()
                remote=bool(bag and bag['destination']=='branch')
                manager=ops.lock_shift(c,source['manager_shift_id'])
                if not remote and (not manager or manager['status']=='closed' or manager['user_id']!=receipt['received_by']):raise db.error(409,'SHARED_MANAGER_LEDGER_CLOSED','يلزم إسناد النقد قبل الإقفال المالي لسجل مدير الاستلام.')
                if ops.balance(c,s)!=source['expected_halalas']:raise db.error(409,'SHARED_EXPECTED_CHANGED','تغير دفتر النقد بعد التصريح؛ يلزم مراجعة مرتبطة من المحاسبة.')
                from .driver_incentives import require_decisions
                require_decisions(c,principal,s)
                values={'expected_halalas':source['expected_halalas'],'counted_halalas':row.counted_halalas,'handover_halalas':row.received_halalas,'retained_halalas':row.retained_halalas,'terminal_total_halalas':row.terminal_total_halalas,'expected_card_halalas':source['expected_card_halalas'],'difference_halalas':row.counted_halalas-source['expected_halalas'],'card_difference_halalas':row.terminal_total_halalas-source['expected_card_halalas'],'provenance':'accounting_allocation','shared_declaration_id':declaration['id'],'shared_receipt_id':receipt['id'],'shared_allocation_id':identifier,'allocation_evidence':row.evidence,'allocated_by':user['id'],'financial_state':'allocated_pending_reconciliation','receipt_difference_halalas':row.receipt_difference_halalas,'shared_treatment_id':treatment['id'] if treatment else None,'shared_workday_id':day['id'],'declared_handover_halalas':row.counted_halalas-row.retained_halalas,'financial_review_required':bool(row.counted_halalas-source['expected_halalas'] or row.terminal_total_halalas-source['expected_card_halalas'] or row.receipt_difference_halalas),'physical_received_by':receipt['received_by'],'physical_state':'received','physical_receipt_id':receipt['id']}
                if approval:
                    values.update(manager_approval=approval,manager_approval_required=True)
                # This is attribution of an already witnessed physical receipt,
                # recorded by the actual accountant, never another receipt.
                transfer=ops.uid()
                c.execute("INSERT INTO custody_transfers(id,from_shift_id,to_shift_id,recipient_user_id,amount_halalas,reference,closure,status,created_by,confirmed_by,confirmed_at) VALUES(%s,%s,%s,%s,%s,%s,true,'confirmed',%s,%s,%s)",(transfer,s['id'],None if remote else manager['id'],receipt['received_by'],row.received_halalas,'Allocation of shared receipt '+receipt['id'],user['id'],receipt['received_by'],receipt['created_at']))
                ops.event(c,s['id'],-row.received_halalas,'transfer_out',transfer,user['id'],'Accounting attribution of shared receipt '+receipt['id'])
                if not remote:ops.event(c,manager['id'],row.received_halalas,'transfer_in',transfer,user['id'],'Accounting attribution of shared receipt '+receipt['id'])
                # Attribution does not authorize a cash adjustment or erase debt.
                carry=c.execute('SELECT amount_halalas FROM retained_carryovers WHERE from_shift_id=%s',(s['id'],)).fetchone()
                if carry and carry['amount_halalas']!=row.retained_halalas:raise db.error(409,'SHARED_FLOAT_ALREADY_CARRIED','العهدة الدائمة نُقلت للشفت التالي؛ لا تغير أصلها.')
                if row.retained_halalas and not carry:
                    c.execute('INSERT INTO retained_carryovers(id,from_shift_id,user_id,amount_halalas,approved_by,reason) VALUES(%s,%s,%s,%s,%s,%s)',(ops.uid(),s['id'],s['user_id'],row.retained_halalas,user['id'],'Shared receipt allocation '+identifier))
                c.execute("UPDATE shifts SET status='closed',closed_at=%s,staffing_ended_at=coalesce(staffing_ended_at,%s),approved_by=%s,exception_reason=%s,submission=%s WHERE id=%s",(receipt['created_at'],receipt['created_at'],user['id'],body.reason,db.json(values),s['id']))
                from .reporting import create_snapshot
                reports.append(create_snapshot(c,s['id'],user['id']))
            db.audit(c,user['id'],'shared_driver.allocated','central_workday',day['id'],{'allocation_id':identifier,**body.model_dump(),'report_ids':reports})
            return {'allocation_id':identifier,'report_ids':reports}
        value=db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),'shared_driver.allocate:'+day['id'],body.model_dump(),perform)
        output={**value,**view(c,user,day,legs)}
    from .reporting import generate_files
    for report in value['report_ids']:generate_files(report)
    return output


@router.get('/shared-driver-closures')
def inbox(user=Depends(auth.current_user)):
    if not accounting(user):raise db.error(403,'SHARED_ACCOUNTANT_REQUIRED','المتابعة للمحاسبة فقط.')
    from fastapi import HTTPException
    with db.tx() as c:
        if not multibrand.enabled():return {'workdays':[]}
        rows=c.execute("SELECT DISTINCT w.anchor_shift_id FROM central_workdays w JOIN shared_driver_closure_declarations d ON d.workday_id=w.id JOIN shared_driver_closure_receipts r ON r.declaration_id=d.id LEFT JOIN shared_driver_closure_allocations a ON a.declaration_id=d.id WHERE a.id IS NULL OR EXISTS (SELECT 1 FROM audit_events t WHERE t.entity_type='central_workday' AND t.entity_id=w.id AND t.action='accounting.financial_treatment.approved' AND t.details->>'declaration_id'=d.id AND t.details->>'receipt_id'=r.id AND t.details->>'kind'='outstanding') ORDER BY w.anchor_shift_id").fetchall()
        output=[]
        for row in rows:
            try:day,legs=authorize(c,user,row['anchor_shift_id'])
            except HTTPException as error:
                if error.status_code in {403,404}:continue
                raise
            item=view(c,user,day,legs)
            if item.get('allocation') and (item.get('receipt_accounting_review') or {}).get('financially_complete'):continue
            output.append(item)
        return {'workdays':output}


def pending_financial_rows(c,*,shift_id=None,brand_id=None,branch_ids=None,before=None):
    if not multibrand.enabled():return []
    rows=[]
    for family in ('driver','manager'):
        clauses=["(a.id IS NULL OR EXISTS (SELECT 1 FROM audit_events treatment WHERE treatment.entity_type='central_workday' AND treatment.entity_id=w.id AND treatment.action='accounting.financial_treatment.approved' AND treatment.details->>'declaration_id'=d.id AND treatment.details->>'kind'='outstanding'))"];params=[]
        clauses.append('returned.declaration_id IS NULL')
        if shift_id:clauses.append('(s.id=%s OR s.manager_shift_id=%s)');params.extend([shift_id,shift_id])
        if brand_id:clauses.append('s.brand_id=%s');params.append(brand_id)
        if branch_ids is not None:clauses.append('s.branch_ids ?| %s');params.append(list(branch_ids))
        if before is not None:clauses.append('d.created_at<%s');params.append(before)
        returns=f' LEFT JOIN shared_{family}_closure_returns returned ON returned.declaration_id=d.id'
        query=f'SELECT DISTINCT d.id declaration_id,d.workday_id,w.anchor_shift_id,w.user_id,d.created_at FROM shared_{family}_closure_declarations d JOIN central_workdays w ON w.id=d.workday_id JOIN central_workday_legs l ON l.workday_id=w.id JOIN shifts s ON s.id=l.shift_id LEFT JOIN shared_{family}_closure_allocations a ON a.declaration_id=d.id'+returns+' WHERE '+' AND '.join(clauses)+' ORDER BY d.created_at,d.id'
        from .shared_financial_treatments import allocated_review
        for row in c.execute(query,params).fetchall():
            review=allocated_review(c,row['workday_id'])
            if review and review['period_approval_allowed']:continue
            rows.append(dict(row,kind=family))
    return rows


def guard_financial_allocation(c,*,shift_id=None,brand_id=None,branch_ids=None,before=None):
    if pending_financial_rows(c,shift_id=shift_id,brand_id=brand_id,branch_ids=branch_ids,before=before):
        raise db.error(409,'SHARED_DRIVER_ALLOCATION_PENDING','توجد تقفيلة موحدة بانتظار استلام أو إسناد محاسبي أو ذمة لم يسمح المالك باعتماد الفترة مع بقائها. استلام النقد والإقفال التشغيلي متاحان؛ أكمل المتابعة المحاسبية قبل الإقفال المالي.')
