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
    return bool(c.execute("SELECT 1 FROM shifts WHERE id=%s AND kind='driver' AND status IN ('submitted','closed') AND staffing_ended_at IS NOT NULL",(sid,)).fetchone())


def pending_pool(c,manager_sid):
    if not multibrand.enabled():return []
    return c.execute("SELECT DISTINCT d.id declaration_id,r.id receipt_id,r.received_halalas,r.received_by,d.workday_id FROM shared_driver_closure_receipts r JOIN shared_driver_closure_declarations d ON d.id=r.declaration_id JOIN central_workday_legs l ON l.workday_id=d.workday_id JOIN shifts s ON s.id=l.shift_id LEFT JOIN shared_driver_closure_allocations a ON a.declaration_id=d.id WHERE s.manager_shift_id=%s AND a.id IS NULL",(manager_sid,)).fetchall()


def accounting(user):
    return user['role'] in {'owner','admin'} and auth.has_permission(user,'accounting_review')


def authorize(c,user,sid,*,manager=False):
    workdays.require_enabled(user,'accounting_review' if accounting(user) else 'shifts_view',existing_sid=sid)
    day,legs=workdays.authorized(c,user,sid,manager=manager,capability='accounting_review' if accounting(user) else None)
    if day['kind']!='driver':raise db.error(409,'SHARED_DRIVER_REQUIRED','اختر وردية سائق.')
    return day,workdays.ordered(legs,day['anchor_shift_id'])


def latest(c,day_id):
    return c.execute('SELECT * FROM shared_driver_closure_declarations WHERE workday_id=%s ORDER BY revision DESC LIMIT 1',(day_id,)).fetchone()


def view(c,user,day,legs):
    declaration=latest(c,day['id'])
    receipt=c.execute('SELECT * FROM shared_driver_closure_receipts WHERE declaration_id=%s',(declaration['id'],)).fetchone() if declaration else None
    allocation=c.execute('SELECT * FROM shared_driver_closure_allocations WHERE declaration_id=%s',(declaration['id'],)).fetchone() if declaration else None
    returned=c.execute('SELECT * FROM shared_driver_closure_returns WHERE declaration_id=%s',(declaration['id'],)).fetchone() if declaration else None
    expected=sum(ops.balance(c,s) for _,s in legs)
    cards=sum(ops.blind_closure.card_reconciliation(c,s['id'],0,(s.get('external_enbishan') or {}).get('card_halalas',0))['expected_card_halalas'] for _,s in legs)
    output={'driver_id':day['user_id'],'driver_name':c.execute('SELECT display_name FROM users WHERE id=%s',(day['user_id'],)).fetchone()['display_name'],'shift':{'id':day['anchor_shift_id'],'kind':'driver','user_id':day['user_id'],'status':legs[0][1]['status']},'anchor_shift_id':day['anchor_shift_id'],'workday_id':day['id'],'declaration':declaration,'receipt':receipt,'allocation':allocation,'returned':returned,
      'state':'allocated' if allocation else 'awaiting_accounting' if receipt else 'returned' if returned else 'awaiting_receipt' if declaration else 'awaiting_driver',
      'expected_cash_halalas':expected,'expected_card_halalas':cards,
      'cash_difference_halalas':declaration['cash_halalas']-sum(x['expected_halalas'] for x in declaration['expected_snapshot']) if declaration else None,
      'card_difference_halalas':declaration['card_halalas']-sum(x['expected_card_halalas'] for x in declaration['expected_snapshot']) if declaration else None,
      'legs':[{'shift_id':s['id'],'brand_id':s['brand_id'],'expected_cash_halalas':ops.balance(c,s),'permanent_float_halalas':(s.get('permanent_float') or {}).get('amount',0)} for _,s in legs],
      'can_allocate':accounting(user)}
    if user['role']=='driver':
        for key in ['expected_cash_halalas','expected_card_halalas','cash_difference_halalas','card_difference_halalas','legs']:output.pop(key,None)
        if declaration:output['declaration']={k:v for k,v in declaration.items() if k not in {'expected_snapshot'}}
        output['allocation']=None
    return ops.safe(output)


class PhysicalSubmission(ops.OperationBody):
    cash_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    card_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    vehicle_inspection:ops.shift_vehicle.Inspection|None=None
    reason:str=Field('',max_length=2000)


class PhysicalReceipt(ops.OperationBody):
    declaration_id:str
    received_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    confirmed:StrictBool
    reference:str=Field(min_length=1,max_length=200)


class AllocationLeg(ops.OperationBody):
    shift_id:str
    counted_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    received_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    retained_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    terminal_total_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    evidence:str=Field(min_length=3,max_length=1000)


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
        if c.execute("SELECT 1 FROM manager_handovers WHERE (from_shift_id=%s OR to_shift_id=%s) AND status='pending'",(s['id'],s['id'])).fetchone():raise db.error(409,'HANDOVER_PENDING','أكمل تسليم مسؤولية المناوبة أولًا.')


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
            c.execute('INSERT INTO shared_driver_closure_declarations(id,workday_id,revision,cash_halalas,card_halalas,retained_halalas,expected_snapshot,recorded_by,reason) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',(identifier,day['id'],revision,body.cash_halalas,body.card_halalas,retained,db.json(snapshot),user['id'],body.reason))
            for _,s in legs:
                # No actual amount is attributed to either brand at submission.
                c.execute("UPDATE shifts SET status='submitted',submitted_at=now(),submission=%s WHERE id=%s",(db.json({'shared_declaration_id':identifier,'physical_state':'awaiting_receipt','financial_state':'awaiting_allocation'}),s['id']))
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
            if not body.confirmed or body.received_halalas!=declaration['cash_halalas']-declaration['retained_halalas']:raise db.error(422,'SHARED_RECEIPT_MISMATCH','أكد استلام الكاش الفعلي المطابق لتسليم السائق؛ أعد التقفيلة عند اختلافه.')
            if any(row['manager_user_id']!=user['id'] for row in declaration['expected_snapshot']):raise db.error(403,'SHARED_RECEIPT_RECIPIENT','مدير الوردية المسجل وحده يؤكد استلام النقد.')
            if any(s['status']!='submitted' for _,s in legs):raise db.error(409,'SHARED_CLOSURE_STATE','التقفيلة ليست مرسلة.')
            validate_operational(c,user,legs)
            from .driver_incentives import require_decisions
            for principal,s in legs:require_decisions(c,principal,s)
            identifier=ops.uid()
            c.execute('INSERT INTO shared_driver_closure_receipts(id,declaration_id,received_halalas,received_by,manager_shift_ids,reference) VALUES(%s,%s,%s,%s,%s,%s)',(identifier,declaration['id'],body.received_halalas,user['id'],db.json([s['manager_shift_id'] for _,s in legs]),body.reference))
            recorded=c.execute('SELECT created_at FROM shared_driver_closure_receipts WHERE id=%s',(identifier,)).fetchone()['created_at']
            if declaration['retained_halalas']>=sum(row['permanent_float_halalas'] for row in declaration['expected_snapshot']):
                for row in declaration['expected_snapshot']:
                    if row['permanent_float_halalas']:
                        c.execute('INSERT INTO retained_carryovers(id,from_shift_id,user_id,amount_halalas,approved_by,reason) VALUES(%s,%s,%s,%s,%s,%s)',(ops.uid(),row['shift_id'],day['user_id'],row['permanent_float_halalas'],user['id'],'Standing float physically retained at shared receipt '+identifier))
                    c.execute('UPDATE shifts SET staffing_ended_at=%s WHERE id=%s',(recorded,row['shift_id']))
            db.audit(c,user['id'],'shared_driver.physical_received','central_workday',day['id'],{'declaration_id':declaration['id'],'receipt_id':identifier,'received_halalas':body.received_halalas,'reference':body.reference,'financial_state':'awaiting_accounting_allocation'})
            return {'receipt_id':identifier}
        value=db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),'shared_driver.receipt:'+day['id'],body.model_dump(),perform)
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
            if c.execute('SELECT 1 FROM shared_driver_closure_receipts WHERE declaration_id=%s',(declaration['id'],)).fetchone():raise db.error(409,'SHARED_RECEIPT_IMMUTABLE','تم الاستلام؛ المعالجة اللاحقة للمحاسبة مع الحفاظ على الأصل.')
            c.execute('INSERT INTO shared_driver_closure_returns(declaration_id,recorded_by,reason) VALUES(%s,%s,%s)',(declaration['id'],user['id'],body.reason))
            for _,s in legs:c.execute("UPDATE shifts SET status='closing',submission=NULL,submitted_at=NULL WHERE id=%s",(s['id'],))
            db.audit(c,user['id'],'shared_driver.returned','central_workday',day['id'],body.model_dump())
            return {'status':'returned'}
        value=db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),'shared_driver.return:'+day['id'],body.model_dump(),perform)
        return {**value,**view(c,user,day,legs)}


def validate_allocation(declaration,receipt,rows):
    supplied={row.shift_id:row for row in rows}
    expected={row['shift_id']:row for row in declaration['expected_snapshot']}
    if len(supplied)!=len(rows) or set(supplied)!=set(expected):raise db.error(422,'SHARED_ALLOCATION_LEGS','أدخل سجلات البراندات المعتمدة مرة واحدة.')
    if (sum(r.counted_halalas for r in rows)!=declaration['cash_halalas'] or sum(r.terminal_total_halalas for r in rows)!=declaration['card_halalas'] or sum(r.retained_halalas for r in rows)!=declaration['retained_halalas'] or sum(r.received_halalas for r in rows)!=receipt['received_halalas']):raise db.error(422,'SHARED_ALLOCATION_TOTAL','توزيع المحاسبة يجب أن يساوي الأصل النقدي والشبكة والاستلام الفعلي تمامًا.')
    for row in rows:
        if row.counted_halalas!=row.received_halalas+row.retained_halalas:raise db.error(422,'SHARED_ALLOCATION_BALANCE','المعدود يساوي المستلم والمحتفظ به لكل سجل.')
        if row.retained_halalas>expected[row.shift_id]['permanent_float_halalas']:raise db.error(422,'SHARED_ALLOCATION_FLOAT','لا تتجاوز العهدة الدائمة المسجلة لكل براند.')
    return supplied,expected


@router.post(BASE+'/physical-allocation')
def allocate(sid:str,body:Allocation,request:Request,user=Depends(auth.current_user)):
    if not accounting(user):raise db.error(403,'SHARED_ACCOUNTANT_REQUIRED','إسناد المبالغ للبراندات للمحاسبة فقط.')
    with db.tx() as c:
        workdays.lock_lanes(c);day,legs=authorize(c,user,sid)
        def perform():
            declaration=latest(c,day['id'])
            receipt=c.execute('SELECT * FROM shared_driver_closure_receipts WHERE id=%s AND declaration_id=%s',(body.receipt_id,body.declaration_id)).fetchone()
            if not body.confirmed or not declaration or declaration['id']!=body.declaration_id or not receipt:raise db.error(409,'SHARED_ALLOCATION_STALE','راجع أصل التقفيلة والاستلام الفعلي وحدّث الشاشة.')
            supplied,expected=validate_allocation(declaration,receipt,body.legs)
            validate_operational(c,user,legs)
            identifier=ops.uid();reports=[]
            c.execute('INSERT INTO shared_driver_closure_allocations(id,declaration_id,receipt_id,legs,recorded_by,reason) VALUES(%s,%s,%s,%s,%s,%s)',(identifier,declaration['id'],receipt['id'],db.json([r.model_dump() for r in body.legs]),user['id'],body.reason))
            for principal,s in legs:
                row=supplied[s['id']];source=expected[s['id']]
                if s['status']!='submitted' or (s.get('submission') or {}).get('shared_declaration_id')!=declaration['id']:raise db.error(409,'SHARED_ALLOCATION_STATE','سجل الوردية لا يطابق أصل التقفيلة.')
                manager=ops.lock_shift(c,source['manager_shift_id'])
                if not manager or manager['status']=='closed' or manager['user_id']!=receipt['received_by']:raise db.error(409,'SHARED_MANAGER_LEDGER_CLOSED','يلزم إسناد النقد قبل الإقفال المالي لسجل مدير الاستلام.')
                if ops.balance(c,s)!=source['expected_halalas']:raise db.error(409,'SHARED_EXPECTED_CHANGED','تغير دفتر النقد بعد التصريح؛ يلزم مراجعة مرتبطة من المحاسبة.')
                from .driver_incentives import require_decisions
                require_decisions(c,principal,s)
                values={'expected_halalas':source['expected_halalas'],'counted_halalas':row.counted_halalas,'handover_halalas':row.received_halalas,'retained_halalas':row.retained_halalas,'terminal_total_halalas':row.terminal_total_halalas,'expected_card_halalas':source['expected_card_halalas'],'difference_halalas':row.counted_halalas-source['expected_halalas'],'card_difference_halalas':row.terminal_total_halalas-source['expected_card_halalas'],'provenance':'accounting_allocation','shared_declaration_id':declaration['id'],'shared_receipt_id':receipt['id'],'shared_allocation_id':identifier,'allocation_evidence':row.evidence,'allocated_by':user['id'],'financial_state':'allocated_pending_reconciliation','physical_received_by':receipt['received_by']}
                # This is attribution of an already witnessed physical receipt,
                # recorded by the actual accountant, never another receipt.
                transfer=ops.uid()
                c.execute("INSERT INTO custody_transfers(id,from_shift_id,to_shift_id,recipient_user_id,amount_halalas,reference,closure,status,created_by,confirmed_by,confirmed_at) VALUES(%s,%s,%s,%s,%s,%s,true,'confirmed',%s,%s,%s)",(transfer,s['id'],manager['id'],receipt['received_by'],row.received_halalas,'Allocation of shared receipt '+receipt['id'],user['id'],receipt['received_by'],receipt['created_at']))
                ops.event(c,s['id'],-row.received_halalas,'transfer_out',transfer,user['id'],'Accounting attribution of shared receipt '+receipt['id'])
                ops.event(c,manager['id'],row.received_halalas,'transfer_in',transfer,user['id'],'Accounting attribution of shared receipt '+receipt['id'])
                if values['difference_halalas']:ops.event(c,s['id'],values['difference_halalas'],'adjustment','closure-difference:'+s['id'],user['id'],body.reason)
                carry=c.execute('SELECT amount_halalas FROM retained_carryovers WHERE from_shift_id=%s',(s['id'],)).fetchone()
                if carry and carry['amount_halalas']!=row.retained_halalas:raise db.error(409,'SHARED_FLOAT_ALREADY_CARRIED','العهدة الدائمة نُقلت للشفت التالي؛ لا تغير أصلها.')
                if row.retained_halalas and not carry:
                    c.execute('INSERT INTO retained_carryovers(id,from_shift_id,user_id,amount_halalas,approved_by,reason) VALUES(%s,%s,%s,%s,%s,%s)',(ops.uid(),s['id'],s['user_id'],row.retained_halalas,user['id'],'Shared receipt allocation '+identifier))
                c.execute("UPDATE shifts SET status='closed',closed_at=%s,staffing_ended_at=%s,approved_by=%s,exception_reason=%s,submission=%s WHERE id=%s",(receipt['created_at'],receipt['created_at'],user['id'],body.reason,db.json(values),s['id']))
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
        rows=c.execute('SELECT DISTINCT w.anchor_shift_id FROM central_workdays w JOIN shared_driver_closure_declarations d ON d.workday_id=w.id JOIN shared_driver_closure_receipts r ON r.declaration_id=d.id LEFT JOIN shared_driver_closure_allocations a ON a.declaration_id=d.id WHERE a.id IS NULL ORDER BY w.anchor_shift_id').fetchall()
        output=[]
        for row in rows:
            try:day,legs=authorize(c,user,row['anchor_shift_id'])
            except HTTPException as error:
                if error.status_code in {403,404}:continue
                raise
            output.append(view(c,user,day,legs))
        return {'workdays':output}


def pending_financial_rows(c,*,shift_id=None,brand_id=None,branch_ids=None,before=None):
    if not multibrand.enabled():return []
    clauses=['a.id IS NULL','returned.declaration_id IS NULL'];params=[]
    if shift_id:clauses.append('(s.id=%s OR s.manager_shift_id=%s)');params.extend([shift_id,shift_id])
    if brand_id:clauses.append('s.brand_id=%s');params.append(brand_id)
    if branch_ids is not None:clauses.append('s.branch_ids ?| %s');params.append(list(branch_ids))
    if before is not None:clauses.append('d.created_at<%s');params.append(before)
    return c.execute('SELECT DISTINCT d.id declaration_id,w.anchor_shift_id,w.user_id,d.created_at FROM shared_driver_closure_declarations d JOIN central_workdays w ON w.id=d.workday_id JOIN central_workday_legs l ON l.workday_id=w.id JOIN shifts s ON s.id=l.shift_id LEFT JOIN shared_driver_closure_allocations a ON a.declaration_id=d.id LEFT JOIN shared_driver_closure_returns returned ON returned.declaration_id=d.id WHERE '+' AND '.join(clauses)+' ORDER BY d.created_at,d.id',params).fetchall()


def guard_financial_allocation(c,*,shift_id=None,brand_id=None,branch_ids=None,before=None):
    if pending_financial_rows(c,shift_id=shift_id,brand_id=brand_id,branch_ids=branch_ids,before=before):
        raise db.error(409,'SHARED_DRIVER_ALLOCATION_PENDING','توجد تقفيلة سائق موحدة بانتظار استلام أو إسناد محاسبي موثق. افتح تقفيلات السائقين الموحدة لدى المحاسبة قبل الإقفال المالي.')
