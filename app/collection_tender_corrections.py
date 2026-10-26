"""Evidence-backed correction of an entire existing collection's tender.

Original receipts, invoices and closed snapshots stay immutable. Only an
independently approved source document changes the effective tender projection.
"""
from datetime import date, datetime
import os
from typing import Literal
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictBool
from . import auth, db, multibrand, operations as ops

router=APIRouter(prefix='/api/collections',tags=['collection tender corrections'])

class Proposal(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    expected_order_version:StrictInt=Field(ge=1)
    source_correction_id:str|None=None
    target_method:Literal['cash','card']
    target_tendered_halalas:StrictInt|None=Field(None,ge=0,le=ops.MAX_HALALAS)
    target_reference:str=Field('',max_length=200,pattern=r'^[^\x00]*$')
    evidence_reference:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    followup_user_id:str=Field(min_length=1,max_length=150)
    due_on:date

class Approval(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    expected_order_version:StrictInt=Field(ge=1)
    source_correction_id:str|None=None
    evidence_reference:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    confirmed:StrictBool

class Rejection(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')


def enabled():
    return os.getenv('KPS_ORDER_SOURCE_TREATMENTS_ENABLED','false').strip().lower()=='true'


@router.get('/tender-correction-capabilities')
def capabilities(user=Depends(auth.current_user)):
    return {'enabled':enabled() and user.get('active',True) and user['role'] in {'owner','admin'} and auth.has_permission(user,'accounting_review')}


def accountant(user):
    if not enabled():raise db.error(404,'ORDER_SOURCE_TREATMENTS_DISABLED','تصحيح مصدر وسيلة التحصيل قيد التطوير وغير متاح للتشغيل الحالي.')
    if (not user.get('active',True) or user['role'] not in {'owner','admin'} or not auth.has_permission(user,'accounting_review')):
        raise db.error(403,'TENDER_CORRECTION_FORBIDDEN','تصحيح وسيلة التحصيل للمحاسبة المخولة واعتماد مستقل فقط.')


def source(c,user,cid,lock=False):
    accountant(user)
    raw=c.execute('SELECT * FROM collections WHERE id=%s',(cid,)).fetchone()
    if not raw:raise db.error(404,'TENDER_COLLECTION_NOT_FOUND','التحصيل غير موجود ضمن نطاقك.')
    from .order_financial_cases import scoped
    order=scoped(c,raw['order_id'],user,lock)
    if lock:raw=c.execute('SELECT * FROM collections WHERE id=%s FOR UPDATE',(cid,)).fetchone()
    effective=c.execute('SELECT * FROM effective_collections WHERE id=%s',(cid,)).fetchone()
    shift=ops.lock_shift(c,effective['shift_id']) if lock and effective.get('shift_id') else c.execute('SELECT * FROM shifts WHERE id=%s',(effective.get('shift_id'),)).fetchone()
    if shift:ops.shift_access(c,user,shift,capability='accounting_review')
    current=c.execute('SELECT * FROM collection_tender_corrections WHERE collection_id=%s ORDER BY revision DESC LIMIT 1',(cid,)).fetchone()
    return raw,effective,order,shift,current


def blocked_reason(c,raw,effective,order,shift):
    from .branch_payments import online_order
    if online_order(order):return 'ONLINE_TENDER','الدفع الإلكتروني يتبع مسار بوابة الدفع؛ لا يعاد تصنيفه يدويًا.'
    if order.get('test_void') or order['snapshot'].get('complimentary'):return 'NONCOMMERCIAL_SOURCE','مصدر تجريبي أو مجاني لا يقبل تصحيح وسيلة تحصيل تجارية.'
    if c.execute('SELECT 1 FROM refunds WHERE order_id=%s',(order['id'],)).fetchone():return 'REFUNDED_SOURCE','يوجد مرتجع مرتبط؛ يلزم استكمال المحاسبة بمستند مترابط قبل تغيير الوسيلة.'
    if c.execute('SELECT 1 FROM channel_settlement_allocations WHERE order_id=%s',(order['id'],)).fetchone():return 'SETTLED_SOURCE','للطلب تسوية قناة محفوظة؛ يلزم مراجعة أثرها قبل تغيير الوسيلة.'
    if c.execute("SELECT 1 FROM bank_reconciliation_source_claims WHERE source_kind='collection' AND source_id=%s",(raw['id'],)).fetchone():return 'BANK_CLAIMED_SOURCE','التحصيل مرتبط بدفعة شبكة أو بنك؛ يلزم تصحيح المطابقة المرتبطة أولًا.'
    if not shift or shift['brand_id']!=order['brand_id'] or order['branch_id'] not in shift['branch_ids']:return 'UNDOCUMENTED_CUSTODY','عهدة التحصيل الأصلية غير موثقة بنطاق مطابق؛ لا تنشئ عهدة أو سندًا بديلًا.'
    if raw['method']=='cash' and not c.execute("SELECT 1 FROM custody_events WHERE shift_id=%s AND kind='collection' AND reference_id=%s AND amount_halalas=%s",(raw['shift_id'],raw['id'],raw['amount_halalas'])).fetchone():return 'MISSING_ORIGINAL_CASH_EVENT','دليل حركة العهدة النقدية الأصلية غير مكتمل؛ يلزم استكماله أولًا.'
    return None


def eligible_followup(c,user,order,identifier):
    target=c.execute('SELECT * FROM users WHERE id=%s',(identifier,)).fetchone()
    if multibrand.enabled():target=multibrand.project(c,target,order['brand_id'])
    if not target or not target.get('active') or target.get('brand_id')!=order['brand_id'] or target['role'] not in {'owner','admin'} or not auth.has_permission(target,'accounting_review') or ((target['role']!='owner' or target.get('home_brand_id')) and order['branch_id'] not in target.get('branch_ids',[])):
        raise db.error(422,'TENDER_FOLLOWUP_SCOPE','اختر محاسبًا مفعلًا ومخولًا بفرع المصدر لمتابعة المعالجة.')
    return target


def continuing_followup(c,user,order,requested_id):
    try:return eligible_followup(c,user,order,requested_id)
    except Exception as error:
        from fastapi import HTTPException
        if not isinstance(error,HTTPException) or error.status_code!=422:raise
        current=eligible_followup(c,user,order,user['id'])
        db.audit(c,user['id'],'collection_tender.followup_reassigned','order',order['id'],{'previous_responsible_user_id':requested_id,'responsible_user_id':current['id'],'required_action':'Continue evidence review after original reviewer absence or scope removal','no_money_posted':True})
        return current


def source_actors(c,raw,effective,shift):
    actors={raw['collector_id'],effective['collector_id']}
    if shift:
        actors.add(shift['user_id'])
        frontier={shift['id']};seen=set()
        while frontier:
            if len(seen)>500:raise db.error(409,'TENDER_CUSTODY_CHAIN_UNVERIFIED','سلسلة العهدة تحتاج مراجعة مستقلة قبل اعتماد التصحيح.')
            seen.update(frontier)
            transfers=c.execute("SELECT recipient_user_id,confirmed_by,to_shift_id FROM custody_transfers WHERE from_shift_id=ANY(%s) AND status='confirmed'",(sorted(frontier),)).fetchall()
            frontier=set()
            for row in transfers:
                actors.update([row['recipient_user_id'],row['confirmed_by']])
                if row['to_shift_id'] and row['to_shift_id'] not in seen:frontier.add(row['to_shift_id'])
    # Legacy/manual accounting receipts can precede a physical-transfer row.
    # Their recorded receiver must not approve reclassification of that same
    # custody source. Include all immutable receipt revisions, not just latest.
    for row in c.execute("SELECT DISTINCT d.actor_id FROM driver_accounting_settlements d JOIN closure_reports r ON r.id=d.report_id WHERE d.cash_received_halalas IS NOT NULL AND r.brand_id=%s AND EXISTS(SELECT 1 FROM jsonb_array_elements(coalesce(r.snapshot->'collections','[]'::jsonb)) p WHERE p->>'id'=%s)",(shift['brand_id'] if shift else None,raw['id'])).fetchall():
        actors.add(row['actor_id'])
    return actors-{None}


def require_version(body,order,current):
    if body.expected_order_version!=order['version'] or body.source_correction_id!=(current['id'] if current else None):
        raise db.error(409,'TENDER_CORRECTION_STALE','تغير الطلب أو مستند التصحيح الفعال؛ حدّث المصدر قبل الإجراء.')


def target_values(body,effective):
    if body.target_method==effective['method']:raise db.error(422,'TENDER_CORRECTION_NO_CHANGE','اختر الوسيلة الصحيحة المختلفة عن الوسيلة المسجلة؛ هذا المسار لا يغيّر مبلغ التحصيل.')
    if body.target_method=='cash':
        if body.target_tendered_halalas is None or body.target_tendered_halalas<effective['amount_halalas']:raise db.error(422,'TENDER_CASH_EVIDENCE','سجّل النقد الفعلي المسلم قبل الباقي بما يغطي المبلغ المحصل.')
        return body.target_tendered_halalas,body.target_tendered_halalas-effective['amount_halalas'],''
    if body.target_tendered_halalas is not None or not body.target_reference.strip():raise db.error(422,'TENDER_CARD_EVIDENCE','الشبكة تحتاج مرجع العملية الفعلية؛ لا تسجّل نقدًا مسلمًا أو باقيًا لها.')
    return None,0,body.target_reference


def followup(c,user,order,cid,reason,person,due_on,proposal_id=None):
    identifier=db.new_id('tcf_')
    detail={'followup_id':identifier,'collection_id':cid,'proposal_id':proposal_id,'responsible_user_id':person['id'],'responsible_role':'accounting',
        'required_action':reason[1],'blocked_code':reason[0],'due_on':due_on.isoformat(),'next_role':'independent_financial_reviewer','no_money_posted':True}
    db.audit(c,user['id'],'collection_tender.followup_required','order',order['id'],detail)
    db.event(c,order['id'],user['id'],'collection_tender.followup_required',detail)
    return {'status':'requires_followup','code':'TENDER_CORRECTION_REQUIRES_FOLLOWUP','message':reason[1],'followup_id':identifier,'responsible_user_id':person['id'],'due_on':due_on.isoformat()}


def result_response(result):
    if result.get('status')=='requires_followup':return JSONResponse(status_code=409,content={'detail':result})
    return ops.safe(result)


@router.get('/{cid}/tender-corrections')
def listing(cid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        raw,effective,order,shift,current=source(c,user,cid)
        reason=blocked_reason(c,raw,effective,order,shift)
        users=[]
        for row in c.execute("SELECT id FROM users WHERE active AND role IN ('owner','admin') ORDER BY display_name,id").fetchall():
            try:person=eligible_followup(c,user,order,row['id'])
            except Exception as error:
                from fastapi import HTTPException
                if isinstance(error,HTTPException) and error.status_code==422:continue
                raise
            users.append({'id':person['id'],'name':person['display_name']})
        proposals=c.execute('SELECT p.*,u.display_name requester_name,a.id approved_id,a.approved_by,a.created_at approved_at,r.rejected_by,r.note rejection_note FROM collection_tender_correction_proposals p JOIN users u ON u.id=p.requested_by LEFT JOIN collection_tender_corrections a ON a.proposal_id=p.id LEFT JOIN collection_tender_correction_rejections r ON r.proposal_id=p.id WHERE p.collection_id=%s ORDER BY p.created_at,p.id',(cid,)).fetchall()
        actors=source_actors(c,raw,effective,shift)
        return ops.safe({'collection':effective,'original_collection':raw,'order_id':order['id'],'expected_order_version':order['version'],'source_correction_id':current['id'] if current else None,
            'current_correction':current,'proposals':proposals,'followup_users':users,'can_approve':user['id'] not in actors,'blocked_reason':reason[1] if reason else None})


@router.post('/{cid}/tender-corrections')
def propose(cid:str,body:Proposal,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        from .central_workdays import lock_lanes
        lock_lanes(c)
        c.execute("SELECT pg_advisory_xact_lock(hashtext('bank-reconciliation-sources'))")
        raw,effective,order,shift,current=source(c,user,cid,True)
        def perform():
            require_version(body,order,current)
            person=eligible_followup(c,user,order,body.followup_user_id)
            from .accounting import TZ
            if body.due_on<datetime.now(TZ).date():raise db.error(422,'TENDER_FOLLOWUP_DUE','مهلة المتابعة اليوم أو لاحقًا.')
            reason=blocked_reason(c,raw,effective,order,shift)
            if reason:return followup(c,user,order,cid,reason,person,body.due_on)
            tendered,change,reference=target_values(body,effective)
            if c.execute('SELECT 1 FROM collection_tender_correction_proposals p LEFT JOIN collection_tender_corrections a ON a.proposal_id=p.id LEFT JOIN collection_tender_correction_rejections r ON r.proposal_id=p.id WHERE p.collection_id=%s AND a.id IS NULL AND r.proposal_id IS NULL',(cid,)).fetchone():raise db.error(409,'TENDER_PROPOSAL_PENDING','يوجد طلب تصحيح معلق لهذا التحصيل؛ اعتمده أو أعده لاستكمال الدليل.')
            identifier=db.new_id('tcp_')
            snapshot={'raw_collection':ops.safe(raw),'effective_collection':ops.safe(effective),'shift_id':shift['id'],'custodian_id':shift['user_id'],'source_actor_ids':sorted(source_actors(c,raw,effective,shift)),'invoice_unchanged':True,'amount_unchanged':True,'no_receipt_created':True}
            c.execute('INSERT INTO collection_tender_correction_proposals(id,collection_id,order_id,brand_id,branch_id,expected_order_version,source_correction_id,source_method,target_method,amount_halalas,target_tendered_halalas,target_change_halalas,target_reference,evidence_reference,note,source_snapshot,requested_by,followup_user_id,due_on) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(identifier,cid,order['id'],order['brand_id'],order['branch_id'],order['version'],body.source_correction_id,effective['method'],body.target_method,effective['amount_halalas'],tendered,change,reference,body.evidence_reference,body.note,db.json(snapshot),user['id'],person['id'],body.due_on))
            db.audit(c,user['id'],'collection_tender.proposed','order',order['id'],{'proposal_id':identifier,'collection_id':cid,**body.model_dump(mode='json'),'no_money_posted':True})
            return {'id':identifier,'status':'pending','collection_id':cid}
        result=db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'collection_tender.propose:'+cid,body.model_dump(mode='json'),perform)
    return result_response(result)


@router.post('/{cid}/tender-corrections/{pid}/approve')
def approve(cid:str,pid:str,body:Approval,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        from .central_workdays import lock_lanes
        lock_lanes(c)
        c.execute("SELECT pg_advisory_xact_lock(hashtext('bank-reconciliation-sources'))")
        raw,effective,order,shift,current=source(c,user,cid,True)
        def perform():
            require_version(body,order,current)
            proposal=c.execute('SELECT * FROM collection_tender_correction_proposals WHERE id=%s AND collection_id=%s FOR UPDATE',(pid,cid)).fetchone()
            if not proposal:raise db.error(404,'TENDER_PROPOSAL_NOT_FOUND','طلب التصحيح غير موجود ضمن المصدر.')
            if not body.confirmed:raise db.error(422,'TENDER_APPROVAL_CONFIRM','أكد مراجعة المصدر والدليل والوسيلة الصحيحة.')
            if proposal['requested_by']==user['id'] or user['id'] in source_actors(c,raw,effective,shift):raise db.error(403,'TENDER_CORRECTION_SELF_APPROVAL','يلزم معتمد مستقل عن مقدم الطلب والمحصّل وصاحب العهدة ومستلمها.')
            if proposal['expected_order_version']!=order['version'] or proposal['source_correction_id']!=(current['id'] if current else None):raise db.error(409,'TENDER_CORRECTION_STALE','مصدر الطلب تغير؛ أعد الطلب لاستكماله على النسخة الحالية.')
            if c.execute('SELECT 1 FROM collection_tender_correction_rejections WHERE proposal_id=%s',(pid,)).fetchone() or c.execute('SELECT 1 FROM collection_tender_corrections WHERE proposal_id=%s',(pid,)).fetchone():raise db.error(409,'TENDER_PROPOSAL_DECIDED','صدر قرار لهذا الطلب بالفعل.')
            person=continuing_followup(c,user,order,proposal['followup_user_id'])
            reason=blocked_reason(c,raw,effective,order,shift)
            if reason:return followup(c,user,order,cid,reason,person,proposal['due_on'],pid)
            original=proposal['source_snapshot']['effective_collection']
            if any(original.get(k)!=effective.get(k) for k in ('id','order_id','shift_id','collector_id','method','amount_halalas','tendered_halalas','change_halalas')):raise db.error(409,'TENDER_SOURCE_CHANGED','المصدر الفعال تغير؛ أعد الطلب للاستكمال.')
            identifier=db.new_id('tcc_');event_id=db.new_id('cash_')
            delta=effective['amount_halalas'] if proposal['target_method']=='cash' else -effective['amount_halalas']
            ops.valid_cash_amount(ops.balance(c,shift)+delta)
            # Append even after the day/month closed. The original closed row and
            # snapshot remain untouched; this is a linked reclassification only.
            c.execute("INSERT INTO custody_events(id,shift_id,amount_halalas,kind,reference_id,actor_id,reason) VALUES(%s,%s,%s,'adjustment',%s,%s,%s)",(event_id,shift['id'],delta,identifier,user['id'],'Tender reclassification '+identifier+' / '+body.note))
            c.execute('INSERT INTO collection_tender_corrections(id,proposal_id,collection_id,order_id,brand_id,branch_id,source_correction_id,revision,effective_shift_id,amount_halalas,source_method,target_method,target_tendered_halalas,target_change_halalas,target_reference,cash_delta_halalas,custody_event_id,evidence_reference,note,approved_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(identifier,pid,cid,order['id'],order['brand_id'],order['branch_id'],current['id'] if current else None,(current['revision']+1 if current else 1),shift['id'],effective['amount_halalas'],effective['method'],proposal['target_method'],proposal['target_tendered_halalas'],proposal['target_change_halalas'],proposal['target_reference'],delta,event_id,body.evidence_reference,body.note,user['id']))
            from .order_financial_cases import actual_payment_method
            method=actual_payment_method(c,order['id'],order['payment_method'])
            c.execute('UPDATE orders SET payment_method=%s,version=version+1 WHERE id=%s',(method,order['id']))
            reports=c.execute("SELECT id FROM closure_reports WHERE brand_id=%s AND EXISTS(SELECT 1 FROM jsonb_array_elements(coalesce(snapshot->'collections','[]'::jsonb)) p WHERE p->>'id'=%s)",(order['brand_id'],cid)).fetchall()
            for report in reports:
                version=c.execute('SELECT coalesce(max(correction_version),1)+1 n FROM report_corrections WHERE report_id=%s',(report['id'],)).fetchone()['n']
                c.execute('INSERT INTO report_corrections(id,report_id,correction_version,shift_id,event_reference_id,amount_halalas,reason,authorized_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(db.new_id('rc_'),report['id'],version,shift['id'],event_id,delta,'Tender correction '+identifier+' / '+body.note,user['id']))
            detail={'correction_id':identifier,'proposal_id':pid,'collection_id':cid,'source_correction_id':current['id'] if current else None,'source_method':effective['method'],'target_method':proposal['target_method'],'amount_halalas':effective['amount_halalas'],'cash_delta_halalas':delta,'card_delta_halalas':-delta,'custody_event_id':event_id,'evidence_reference':body.evidence_reference,'note':body.note,'approved_by':user['id'],'requested_by':proposal['requested_by'],'invoice_unchanged':True,'no_receipt_created':True,'no_bank_confirmation':True,'report_ids':[r['id'] for r in reports]}
            db.audit(c,user['id'],'collection_tender.approved','order',order['id'],detail);db.event(c,order['id'],user['id'],'collection_tender.approved',detail)
            return {'id':identifier,'status':'approved',**detail}
        result=db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'collection_tender.approve:'+cid+':'+pid,body.model_dump(mode='json'),perform)
    return result_response(result)


@router.post('/{cid}/tender-corrections/{pid}/reject')
def reject(cid:str,pid:str,body:Rejection,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        from .central_workdays import lock_lanes
        lock_lanes(c)
        c.execute("SELECT pg_advisory_xact_lock(hashtext('bank-reconciliation-sources'))")
        raw,effective,order,shift,current=source(c,user,cid,True)
        def perform():
            proposal=c.execute('SELECT * FROM collection_tender_correction_proposals WHERE id=%s AND collection_id=%s FOR UPDATE',(pid,cid)).fetchone()
            if not proposal:raise db.error(404,'TENDER_PROPOSAL_NOT_FOUND','طلب التصحيح غير موجود.')
            if c.execute('SELECT 1 FROM collection_tender_corrections WHERE proposal_id=%s',(pid,)).fetchone() or c.execute('SELECT 1 FROM collection_tender_correction_rejections WHERE proposal_id=%s',(pid,)).fetchone():raise db.error(409,'TENDER_PROPOSAL_DECIDED','صدر قرار لهذا الطلب.')
            responsible=continuing_followup(c,user,order,proposal['requested_by'])
            c.execute('INSERT INTO collection_tender_correction_rejections(proposal_id,rejected_by,note) VALUES(%s,%s,%s)',(pid,user['id'],body.note))
            db.audit(c,user['id'],'collection_tender.returned','order',order['id'],{'proposal_id':pid,'collection_id':cid,'responsible_user_id':responsible['id'],'due_on':proposal['due_on'].isoformat(),'required_action':body.note,'no_money_posted':True})
            return {'id':pid,'status':'returned','responsible_user_id':responsible['id']}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'collection_tender.reject:'+cid+':'+pid,body.model_dump(),perform)


def snapshot_evidence(c,collection_ids):
    if not collection_ids:return []
    rows=c.execute('SELECT t.*,p.method raw_method,p.shift_id original_shift_id,p.collector_id original_collector_id FROM collection_tender_corrections t JOIN collections p ON p.id=t.collection_id WHERE t.collection_id=ANY(%s) ORDER BY t.collection_id,t.revision',(list(collection_ids),)).fetchall()
    return ops.safe(rows)


def corrections_for_report(c,report_id):
    report=c.execute('SELECT * FROM closure_reports WHERE id=%s',(report_id,)).fetchone()
    if not report:return []
    originals={p['id']:p for p in report['snapshot'].get('collections',[]) if isinstance(p,dict) and p.get('id')}
    if not originals:return []
    rows=c.execute('SELECT DISTINCT ON (t.collection_id) t.* FROM collection_tender_corrections t WHERE t.collection_id=ANY(%s) AND t.brand_id=%s ORDER BY t.collection_id,t.revision DESC',(list(originals),report['brand_id'])).fetchall()
    output=[]
    for row in rows:
        original=originals[row['collection_id']]
        if original.get('amount_halalas')!=row['amount_halalas'] or original.get('method') not in {'cash','card'}:continue
        effective=c.execute('SELECT * FROM effective_collections WHERE id=%s',(row['collection_id'],)).fetchone()
        if not effective or effective['amount_halalas']!=original['amount_halalas'] or effective['method']!=row['target_method']:continue
        delta=(row['amount_halalas'] if effective['method']=='cash' else 0)-(row['amount_halalas'] if original['method']=='cash' else 0)
        if not delta:continue
        output.append({**dict(row),'source_method':original['method'],'cash_delta_halalas':delta,'card_delta_halalas':-delta})
    return ops.safe(output)


def matching_channel_correction(c,report,cash_variance,card_variance,correction_ids=None):
    rows=corrections_for_report(c,report['id'])
    if correction_ids is not None:
        if not correction_ids or len(set(correction_ids))!=len(correction_ids):return None
        by_id={r['id']:r for r in rows}
        if not set(correction_ids)<=set(by_id):return None
        rows=[by_id[identifier] for identifier in correction_ids]
    if not rows:return None
    cash=sum(r['cash_delta_halalas'] for r in rows);card=sum(r['card_delta_halalas'] for r in rows)
    if not cash or cash!=cash_variance or card!=card_variance or cash+card:return None
    return {'ids':[r['id'] for r in rows],'cash_delta_halalas':cash,'card_delta_halalas':card}
