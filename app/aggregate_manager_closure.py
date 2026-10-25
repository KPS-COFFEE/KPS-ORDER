"""One immutable manager cash bag; owner receipt ends physical staffing only.

Accounting attributes the witnessed bag after driver brand allocations. Financial
ledgers stay submitted and may receive only those delayed child attributions.
"""
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import Field, StrictBool, StrictInt
from . import auth, db, multibrand, operations as ops, central_workdays as days
from .aggregate_driver_closure import accounting, physical_finished
router=APIRouter(prefix='/api',tags=['shared-manager-closure'])
BASE='/central-operations/workdays/{sid}'

class Declaration(ops.OperationBody):
    cash_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    recipient_user_id:str
    reference:str=Field(min_length=1,max_length=200)
    confirmed:StrictBool

class Receipt(ops.OperationBody):
    declaration_id:str
    received_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    confirmed:StrictBool

class Leg(ops.OperationBody):
    shift_id:str
    cash_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    evidence:str=Field(min_length=3,max_length=1000)

class Allocation(ops.OperationBody):
    declaration_id:str
    receipt_id:str
    legs:list[Leg]=Field(min_length=1,max_length=2)
    reason:str=Field(min_length=5,max_length=2000)
    confirmed:StrictBool


def authorize(c,user,sid):
    days.require_enabled(user,'accounting_review' if accounting(user) else 'shifts_view',existing_sid=sid)
    day,legs=days.authorized(c,user,sid,capability='accounting_review' if accounting(user) else None)
    if day['kind']!='manager':raise db.error(409,'SHARED_MANAGER_REQUIRED','اختر وردية مدير توصيل.')
    return day,days.ordered(legs,day['anchor_shift_id'])


def latest(c,day_id):return c.execute('SELECT * FROM shared_manager_closure_declarations WHERE workday_id=%s ORDER BY revision DESC LIMIT 1',(day_id,)).fetchone()


def view(c,user,day,legs):
    dec=latest(c,day['id'])
    receipt=c.execute('SELECT * FROM shared_manager_closure_receipts WHERE declaration_id=%s',(dec['id'],)).fetchone() if dec else None
    alloc=c.execute('SELECT * FROM shared_manager_closure_allocations WHERE declaration_id=%s',(dec['id'],)).fetchone() if dec else None
    return ops.safe({'anchor_shift_id':day['anchor_shift_id'],'workday_id':day['id'],'manager_id':day['user_id'],'manager_name':c.execute('SELECT display_name FROM users WHERE id=%s',(day['user_id'],)).fetchone()['display_name'],
      'shift':{'id':day['anchor_shift_id'],'kind':'manager','user_id':day['user_id'],'status':legs[0][1]['status']},'declaration':dec,'receipt':receipt,'allocation':alloc,'can_allocate':accounting(user),
      'state':'allocated' if alloc else 'awaiting_accounting' if receipt else 'awaiting_owner_receipt' if dec else 'awaiting_manager',
      'legs':[{'shift_id':s['id'],'brand_id':s['brand_id'],'current_ledger_cash_halalas':ops.balance(c,s)} for _,s in legs]})


def recipient(c,user,identifier,legs):
    target=c.execute("SELECT * FROM users WHERE id=%s AND active AND role='owner'",(identifier,)).fetchone()
    if not target or target['id']==user['id']:raise db.error(422,'SHARED_MANAGER_RECIPIENT','اختر مالكًا مستقلًا يؤكد استلام النقد من حسابه.')
    for _,s in legs:
        principal=multibrand.project(c,target,s['brand_id'])
        if not principal or not ops.unrestricted_owner(principal) and not set(s['branch_ids'])<=set(principal.get('branch_ids',[])):raise db.error(403,'SHARED_MANAGER_RECIPIENT_SCOPE','المستلم غير مخول لجميع فروع الوردية.')
    return target


def operational_ready(c,legs):
    for _,s in legs:
        ops.guard_accepted_awaiting_dispatch(c,s)
        if ops.unresolved(c,s['id']):raise db.error(409,'OPEN_TASKS','أكمل معالجة مهام المناوبة أولًا.')
        if c.execute("SELECT 1 FROM manager_handovers WHERE (from_shift_id=%s OR to_shift_id=%s) AND status='pending'",(s['id'],s['id'])).fetchone():raise db.error(409,'HANDOVER_PENDING','أكمل تسليم مسؤولية المناوبة أولًا.')
        if c.execute("SELECT 1 FROM custody_transfers WHERE (from_shift_id=%s OR to_shift_id=%s) AND status='pending'",(s['id'],s['id'])).fetchone():raise db.error(409,'RECEIPT_PENDING','أكد أو أعد تسليم النقد المعلق قبل إرسال تقفيلة المدير.')
        children=c.execute("SELECT id FROM shifts WHERE manager_shift_id=%s AND status<>'closed'",(s['id'],)).fetchall()
        if any(not physical_finished(c,r['id']) for r in children):raise db.error(409,'DRIVER_PHYSICAL_RECEIPT_PENDING','استلم تقفيلات السائقين فعليًا أولًا؛ إسناد المحاسبة يمكن استكماله لاحقًا.')


@router.get(BASE+'/manager-physical-closure')
def detail(sid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        day,legs=authorize(c,user,sid)
        output=view(c,user,day,legs)
        if day['user_id']==user['id']:
            candidates=c.execute("SELECT * FROM users WHERE active AND role='owner' AND id<>%s ORDER BY display_name",(user['id'],)).fetchall()
            allowed=[]
            for target in candidates:
                try:recipient(c,user,target['id'],legs)
                except HTTPException:continue
                allowed.append({'id':target['id'],'name':target['display_name']})
            output['recipients']=allowed
        return output


@router.post(BASE+'/manager-physical-submit')
def submit(sid:str,body:Declaration,request:Request,user=Depends(auth.current_user)):
    ops.roles(user,'delivery_manager','owner')
    if not auth.has_permission(user,'shifts_close'):raise db.error(403,'SHARED_MANAGER_CLOSURE_FORBIDDEN','غير مخول لإرسال التقفيلة.')
    with db.tx() as c:
        days.lock_lanes(c);day,legs=authorize(c,user,sid)
        if day['user_id']!=user['id']:raise db.error(403,'SHARED_MANAGER_OWN_ONLY','مدير التوصيل يرسل تقفيلة ورديته من حسابه.')
        recipient(c,user,body.recipient_user_id,legs)
        def perform():
            if not body.confirmed or any(s['status'] not in {'open','closing'} for _,s in legs):raise db.error(409,'SHARED_MANAGER_CLOSURE_STATE','أكد المبلغ الفعلي واختر وردية غير مرسلة.')
            if latest(c,day['id']):raise db.error(409,'SHARED_MANAGER_DECLARATION_EXISTS','الأصل محفوظ؛ التصحيح اللاحق بمعالجة مرتبطة من المحاسبة.')
            operational_ready(c,legs)
            snapshot=[]
            for _,s in legs:
                pending=c.execute("SELECT DISTINCT d.id declaration_id FROM shared_driver_closure_declarations d JOIN shared_driver_closure_receipts r ON r.declaration_id=d.id JOIN central_workday_legs l ON l.workday_id=d.workday_id JOIN shifts child ON child.id=l.shift_id LEFT JOIN shared_driver_closure_allocations a ON a.declaration_id=d.id WHERE child.manager_shift_id=%s AND a.id IS NULL",(s['id'],)).fetchall()
                snapshot.append({'shift_id':s['id'],'brand_id':s['brand_id'],'ledger_cash_at_declaration':ops.balance(c,s),'pending_child_declaration_ids':[x['declaration_id'] for x in pending]})
            identifier=ops.uid()
            c.execute('INSERT INTO shared_manager_closure_declarations(id,workday_id,revision,cash_halalas,expected_snapshot,recorded_by,recipient_user_id,reference) VALUES(%s,%s,1,%s,%s,%s,%s,%s)',(identifier,day['id'],body.cash_halalas,db.json(snapshot),user['id'],body.recipient_user_id,body.reference))
            for _,s in legs:c.execute("UPDATE shifts SET status='submitted',submitted_at=now(),submission=%s WHERE id=%s",(db.json({'shared_manager_declaration_id':identifier,'physical_state':'awaiting_owner_receipt','financial_state':'awaiting_allocation'}),s['id']))
            db.audit(c,user['id'],'shared_manager.declared','central_workday',day['id'],{'declaration_id':identifier,**body.model_dump()})
            return {'declaration_id':identifier}
        value=db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),'shared_manager.declare:'+day['id'],body.model_dump(),perform)
        return {**value,**view(c,user,day,legs)}


@router.post(BASE+'/manager-physical-receipt')
def receive(sid:str,body:Receipt,request:Request,user=Depends(auth.current_user)):
    ops.roles(user,'owner')
    with db.tx() as c:
        days.lock_lanes(c);day,legs=authorize(c,user,sid)
        def perform():
            dec=latest(c,day['id'])
            if not dec or dec['id']!=body.declaration_id:raise db.error(409,'SHARED_MANAGER_DECLARATION_STALE','تغير أصل التقفيلة.')
            if dec['recipient_user_id']!=user['id']:raise db.error(403,'SHARED_MANAGER_ACTUAL_RECIPIENT','المستلم المسجل وحده يؤكد استلام النقد.')
            if c.execute('SELECT 1 FROM shared_manager_closure_receipts WHERE declaration_id=%s',(dec['id'],)).fetchone():raise db.error(409,'SHARED_MANAGER_RECEIPT_EXISTS','الاستلام محفوظ بالفعل.')
            if not body.confirmed or body.received_halalas!=dec['cash_halalas']:raise db.error(422,'SHARED_MANAGER_RECEIPT_MISMATCH','أكد استلام النقد الفعلي المطابق للتصريح؛ الاختلاف يحتاج معالجة مرتبطة قبل الاستلام.')
            if any(s['status']!='submitted' for _,s in legs):raise db.error(409,'SHARED_MANAGER_CLOSURE_STATE','الوردية غير مرسلة.')
            identifier=ops.uid()
            c.execute('INSERT INTO shared_manager_closure_receipts(id,declaration_id,received_halalas,received_by) VALUES(%s,%s,%s,%s)',(identifier,dec['id'],body.received_halalas,user['id']))
            at=c.execute('SELECT created_at FROM shared_manager_closure_receipts WHERE id=%s',(identifier,)).fetchone()['created_at']
            for _,s in legs:c.execute('UPDATE shifts SET staffing_ended_at=%s WHERE id=%s',(at,s['id']))
            db.audit(c,user['id'],'shared_manager.physical_received','central_workday',day['id'],{'receipt_id':identifier,'declaration_id':dec['id'],'received_halalas':body.received_halalas,'financial_state':'awaiting_accounting'})
            return {'receipt_id':identifier}
        value=db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),'shared_manager.receipt:'+day['id'],body.model_dump(),perform)
        return {**value,**view(c,user,day,legs)}


@router.post(BASE+'/manager-physical-allocation')
def allocate(sid:str,body:Allocation,request:Request,user=Depends(auth.current_user)):
    if not accounting(user):raise db.error(403,'SHARED_ACCOUNTANT_REQUIRED','الإسناد المالي للمحاسبة فقط.')
    from .shift_reconciliation import meaningful_reason
    if not meaningful_reason(body.reason) or any(not meaningful_reason(l.evidence) for l in body.legs):raise db.error(422,'SHARED_ALLOCATION_EVIDENCE','سجل أساس الإسناد ودليل كل براند بوضوح.')
    with db.tx() as c:
        days.lock_lanes(c);day,legs=authorize(c,user,sid)
        def perform():
            dec=latest(c,day['id']);receipt=c.execute('SELECT * FROM shared_manager_closure_receipts WHERE id=%s AND declaration_id=%s',(body.receipt_id,body.declaration_id)).fetchone()
            if not body.confirmed or not dec or dec['id']!=body.declaration_id or not receipt:raise db.error(409,'SHARED_ALLOCATION_STALE','راجع أصل التقفيلة والاستلام الفعلي.')
            if c.execute('SELECT 1 FROM shared_manager_closure_allocations WHERE declaration_id=%s',(dec['id'],)).fetchone():raise db.error(409,'SHARED_ALLOCATION_EXISTS','الإسناد محفوظ؛ التصحيح بقيد مرتبط.')
            supplied={r.shift_id:r for r in body.legs};expected={r['shift_id']:r for r in dec['expected_snapshot']}
            if len(supplied)!=len(body.legs) or set(supplied)!=set(expected) or sum(r.cash_halalas for r in body.legs)!=dec['cash_halalas']:raise db.error(422,'SHARED_ALLOCATION_TOTAL','أسند سجلات البراندات مرة واحدة بمجموع يطابق الكاش المستلم كاملًا.')
            identifier=ops.uid();reports=[]
            c.execute('INSERT INTO shared_manager_closure_allocations(id,declaration_id,receipt_id,legs,recorded_by,reason) VALUES(%s,%s,%s,%s,%s,%s)',(identifier,dec['id'],receipt['id'],db.json([r.model_dump() for r in body.legs]),user['id'],body.reason))
            for principal,s in legs:
                if s['status']!='submitted' or (s.get('submission') or {}).get('shared_manager_declaration_id')!=dec['id']:raise db.error(409,'SHARED_MANAGER_CLOSURE_STATE','سجل المدير لا يطابق أصل التقفيلة.')
                if c.execute("SELECT 1 FROM shifts WHERE manager_shift_id=%s AND status<>'closed'",(s['id'],)).fetchone():raise db.error(409,'SHARED_CHILD_ALLOCATION_PENDING','أسند تقفيلات السائقين أولًا ثم إسناد كيس المدير؛ الانتهاء التشغيلي محفوظ.')
                source=expected[s['id']];deferred=0
                for child_id in source['pending_child_declaration_ids']:
                    allocation=c.execute('SELECT legs FROM shared_driver_closure_allocations WHERE declaration_id=%s',(child_id,)).fetchone()
                    if not allocation:raise db.error(409,'SHARED_CHILD_ALLOCATION_PENDING','يوجد إسناد سائق غير مكتمل.')
                    for row in allocation['legs']:
                        child=c.execute('SELECT brand_id,manager_shift_id FROM shifts WHERE id=%s',(row['shift_id'],)).fetchone()
                        if child['manager_shift_id']==s['id']:deferred+=row['received_halalas']
                ledger=ops.balance(c,s)
                if ledger!=source['ledger_cash_at_declaration']+deferred:raise db.error(409,'SHARED_MANAGER_LEDGER_CHANGED','تغير دفتر المدير خارج إسناد السائقين المتوقع؛ يلزم تحقيق مرتبط.')
                amount=supplied[s['id']].cash_halalas
                child_card=c.execute("SELECT coalesce(sum((submission->>'terminal_total_halalas')::bigint),0) n FROM shifts WHERE manager_shift_id=%s AND kind='driver' AND status='closed'",(s['id'],)).fetchone()['n']
                own_card=ops.blind_closure.card_reconciliation(c,s['id'],0)['expected_card_halalas']
                values={'expected_halalas':ledger,'counted_halalas':amount,'handover_halalas':amount,'retained_halalas':0,'difference_halalas':amount-ledger,'terminal_total_halalas':int(child_card)+own_card,'provenance':'accounting_allocation','operational_approved':True,'shared_manager_declaration_id':dec['id'],'shared_manager_receipt_id':receipt['id'],'shared_manager_allocation_id':identifier,'allocation_evidence':supplied[s['id']].evidence,'allocated_by':user['id'],'financial_state':'allocated_pending_reconciliation'}
                transfer=ops.uid()
                c.execute("INSERT INTO custody_transfers(id,from_shift_id,recipient_user_id,amount_halalas,reference,closure,status,created_by,confirmed_by,confirmed_at) VALUES(%s,%s,%s,%s,%s,true,'confirmed',%s,%s,%s)",(transfer,s['id'],receipt['received_by'],amount,'Allocation of shared manager receipt '+receipt['id'],user['id'],receipt['received_by'],receipt['created_at']))
                ops.event(c,s['id'],-amount,'transfer_out',transfer,user['id'],'Accounting attribution of owner cash receipt '+receipt['id'])
                if amount!=ledger:ops.event(c,s['id'],amount-ledger,'adjustment','closure-difference:'+s['id'],user['id'],body.reason)
                c.execute("UPDATE shifts SET status='closed',closed_at=%s,approved_by=%s,exception_reason=%s,submission=%s WHERE id=%s",(receipt['created_at'],user['id'],body.reason,db.json(values),s['id']))
                from .reporting import create_snapshot
                reports.append(create_snapshot(c,s['id'],user['id']))
            db.audit(c,user['id'],'shared_manager.allocated','central_workday',day['id'],{'allocation_id':identifier,**body.model_dump(),'report_ids':reports})
            return {'allocation_id':identifier,'report_ids':reports}
        value=db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),'shared_manager.allocate:'+day['id'],body.model_dump(),perform)
        output={**value,**view(c,user,day,legs)}
    from .reporting import generate_files
    for report in value['report_ids']:generate_files(report)
    return output


@router.get('/shared-manager-closures')
def inbox(user=Depends(auth.current_user)):
    if user['role']!='owner' and not accounting(user):raise db.error(403,'SHARED_ACCOUNTANT_REQUIRED','متابعة إقفال المدير للمالك والمحاسبة.')
    with db.tx() as c:
        if not multibrand.enabled():return {'workdays':[]}
        candidates=c.execute('SELECT w.anchor_shift_id FROM central_workdays w JOIN shared_manager_closure_declarations d ON d.workday_id=w.id LEFT JOIN shared_manager_closure_allocations a ON a.declaration_id=d.id WHERE a.id IS NULL ORDER BY d.created_at,d.id').fetchall()
        rows=[]
        for row in candidates:
            try:day,legs=authorize(c,user,row['anchor_shift_id'])
            except HTTPException as error:
                if error.status_code in {403,404}:continue
                raise
            rows.append(view(c,user,day,legs))
        return {'workdays':rows}
