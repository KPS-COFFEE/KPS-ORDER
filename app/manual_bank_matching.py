"""Manual POS batch/bank evidence linked to canonical collections and refunds.

Bank deposits are never confused with gross POS collections. Fees, refunds and
unreceived timing amounts remain separate. No route posts money or rewrites a
closure, and no source operation or bank amount can be counted twice.
"""
import os
from datetime import date,datetime
from zoneinfo import ZoneInfo
from typing import Literal
from fastapi import APIRouter,Depends,Request
from pydantic import BaseModel,ConfigDict,Field,StrictInt,StrictBool,model_validator,field_validator
from . import auth,db
from .operations import safe

router=APIRouter(prefix='/api/accounting/bank-reconciliation',tags=['manual-bank-matching'])
LIMIT=300

def enabled():
    return (os.getenv('KPS_MANUAL_BANK_MATCHING_ENABLED','').strip().lower() in {'1','true','yes'}
        and os.getenv('APP_ENV','production') in {'testing','development'})


class Command(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    evidence_reference:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')
    note:str=Field(min_length=5,max_length=2000,pattern=r'^[^\x00]+$')

class Batch(Command):
    batch_reference:str=Field(min_length=3,max_length=200,pattern=r'^[^\x00]+$')
    collection_ids:list[str]=Field(default_factory=list,max_length=300)
    refund_ids:list[str]=Field(default_factory=list,max_length=300)
    fees_halalas:StrictInt=Field(ge=0,le=100000000000)
    @model_validator(mode='after')
    def sources_required(self):
        if (not self.collection_ids and not self.refund_ids or
                any(not x or len(x)>150 for x in self.collection_ids+self.refund_ids) or
                len(set(self.collection_ids))!=len(self.collection_ids) or len(set(self.refund_ids))!=len(self.refund_ids)):
            raise ValueError('Select distinct actual collection/refund sources.')
        return self

class Receipt(Command):
    bank_account_reference:str=Field(min_length=3,max_length=200,pattern=r'^[^\x00]+$')
    statement_reference:str=Field(min_length=5,max_length=200,pattern=r'^[^\x00]+$')
    line_reference:str=Field(min_length=1,max_length=200,pattern=r'^[^\x00]+$')
    amount_halalas:StrictInt=Field(ge=-100000000000,le=100000000000)
    statement_amount_halalas:StrictInt=Field(ge=-100000000000,le=100000000000)
    received_on:date
    @field_validator('bank_account_reference','statement_reference','line_reference')
    @classmethod
    def normalized_reference(cls,value):
        return ' '.join(value.split()).casefold()
    @model_validator(mode='after')
    def nonzero(self):
        if self.amount_halalas==0 or self.statement_amount_halalas==0:raise ValueError('A zero is not an actual bank movement.')
        if self.amount_halalas*self.statement_amount_halalas<=0 or abs(self.amount_halalas)>abs(self.statement_amount_halalas):
            raise ValueError('The explicit allocation must fit the actual statement movement.')
        return self

class StatementLine(Receipt):
    @model_validator(mode='after')
    def original_only(self):
        if self.amount_halalas!=self.statement_amount_halalas:
            raise ValueError('Register the full actual statement line without allocation.')
        return self


class Decision(Command):
    decision:Literal['approved','rejected']
    confirmed:StrictBool


def access(user=Depends(auth.current_user)):
    if not enabled():raise db.error(404,'MANUAL_BANK_MATCHING_DISABLED','مطابقة دفعات البنك تجهيز غير مفعل تشغيليًا.')
    if (not user.get('active',True) or user['role'] not in {'owner','admin'} or
            not auth.has_permission(user,'accounting_reports') or not auth.has_permission(user,'accounting_review')):
        raise db.error(403,'BANK_MATCHING_FORBIDDEN','مطابقة الشبكة والبنك للمحاسبة المخولة فقط.')
    return user


def unrestricted(user):
    return user['role']=='owner' and not user.get('home_brand_id')


def source_rows(c,user,kind,ids):
    if not ids:return []
    table='effective_collections' if kind=='collection' else 'refunds'
    rows=c.execute(f'''SELECT x.id,x.order_id,x.shift_id,x.amount_halalas,x.method,x.created_at,
        o.brand_id,o.branch_id FROM {table} x JOIN orders o ON o.id=x.order_id
        WHERE x.id=ANY(%s) ORDER BY x.id''',(ids,)).fetchall()
    if len(rows)!=len(ids) or any(r['brand_id']!=user['brand_id'] or
        not unrestricted(user) and r['branch_id'] not in user.get('branch_ids',[]) for r in rows):
        raise db.error(404,'BANK_SOURCE_SCOPE','إحدى العمليات غير متاحة ضمن صلاحياتك.')
    if any(r['method']!='card' or r['amount_halalas']<=0 for r in rows):
        raise db.error(422,'BANK_SOURCE_METHOD','اختر عمليات شبكة فعلية موجبة فقط.')
    return rows


def view(c,user,bid,lock=False):
    row=c.execute('SELECT b.*,u.display_name proposer_name FROM bank_reconciliation_batches b JOIN users u ON u.id=b.proposed_by WHERE b.id=%s'+(' FOR UPDATE OF b' if lock else ''),(bid,)).fetchone()
    if not row or row['brand_id']!=user['brand_id'] or not unrestricted(user) and not set(row['branch_ids'])<=set(user.get('branch_ids',[])):
        raise db.error(404,'BANK_BATCH_SCOPE','دفعة الشبكة غير متاحة ضمن صلاحياتك.')
    return row


def project(c,row):
    decisions=c.execute('SELECT d.*,u.display_name reviewer_name FROM bank_reconciliation_decisions d JOIN users u ON u.id=d.decided_by WHERE batch_id=%s ORDER BY d.created_at,d.id',(row['id'],)).fetchall()
    approval=next((d for d in decisions if d['receipt_id'] is None),None)
    receipt_decisions={d['receipt_id']:d for d in decisions if d['receipt_id'] is not None}
    receipts=c.execute('SELECT r.*,u.display_name proposer_name FROM bank_reconciliation_receipts r JOIN users u ON u.id=r.proposed_by WHERE batch_id=%s ORDER BY r.created_at,r.id',(row['id'],)).fetchall()
    present=[]
    for r in receipts:
        allocated=c.execute('''SELECT coalesce(sum(amount_halalas),0) amount FROM bank_reconciliation_bank_claims
            WHERE bank_account_reference=%s AND statement_reference=%s AND line_reference=%s''',
            (r['bank_account_reference'],r['statement_reference'],r['line_reference'])).fetchone()['amount']
        present.append(dict(r,decision=receipt_decisions.get(r['id']),bank_line_allocated_halalas=allocated,
            bank_line_unallocated_halalas=r['statement_amount_halalas']-allocated))
    receipts=present
    received=sum(r['amount_halalas'] for r in receipts if r['decision'] and r['decision']['decision']=='approved')
    remaining=row['expected_net_halalas']-received
    state='rejected' if approval and approval['decision']=='rejected' else 'awaiting_batch_approval' if not approval else 'matched' if remaining==0 else 'partially_received' if received else 'awaiting_bank'
    return dict(row,approval=approval,receipts=receipts,bank_received_halalas=received,
        timing_pending_halalas=remaining,state=state,financially_matched=state=='matched',
        operational_work_may_continue=True)


def check_remaining(expected,received,amount):
    remaining=expected-received
    if amount==0 or remaining==0 or (remaining>0 and not 0<amount<=remaining) or (remaining<0 and not remaining<=amount<0):
        raise db.error(409,'BANK_AMOUNT_EXCEEDS_PENDING','مبلغ البنك لا يطابق اتجاه أو حد المبلغ المتبقي؛ راجع المستند دون تغيير الأصل.')


def assert_independent(c,user,record):
    if record['proposed_by']==user['id']:
        raise db.error(403,'BANK_SELF_APPROVAL','يلزم اعتماد محاسب مستقل عن معد المعاملة.')
    # No one approves the settlement of card cash-custody they collected.
    collector_ids=c.execute('SELECT DISTINCT collector_id FROM effective_collections WHERE id=ANY(%s)',
        ([r['id'] for r in record.get('source_snapshot',{}).get('collections',[])],)).fetchall()
    sources=record.get('source_snapshot',{})
    shift_ids=[r['shift_id'] for rows in (sources.get('collections',[]),sources.get('refunds',[])) for r in rows if r.get('shift_id')]
    shift_owners=c.execute('SELECT user_id FROM shifts WHERE id=ANY(%s)',(shift_ids,)).fetchall()
    refund_actors=c.execute('SELECT authorized_by FROM refunds WHERE id=ANY(%s)',
        ([r['id'] for r in sources.get('refunds',[])],)).fetchall()
    if (any(r['collector_id']==user['id'] for r in collector_ids) or any(r['user_id']==user['id'] for r in shift_owners)
            or any(r['authorized_by']==user['id'] for r in refund_actors)):
        raise db.error(403,'BANK_OWN_CUSTODY_APPROVAL','لا يعتمد منفذ التحصيل أو المرتجع أو صاحب عهدة الشفت مطابقته بنفسه.')


@router.get('/sources')
def available_sources(start:str='',end:str='',user=Depends(access)):
    from .accounting import period,branches
    with db.tx() as c:
        _,_,lo,hi=period(start,end);bids=[b['id'] for b in branches(c,user) if unrestricted(user) or b['id'] in user.get('branch_ids',[])]
        result={}
        for kind,table in (('collection','effective_collections'),('refund','refunds')):
            found=c.execute(f'''SELECT x.id,x.order_id,x.shift_id,x.amount_halalas,x.created_at,o.order_no,o.branch_id
                FROM {table} x JOIN orders o ON o.id=x.order_id
                WHERE o.brand_id=%s AND o.branch_id=ANY(%s) AND x.method='card' AND x.amount_halalas>0
                AND x.created_at>=%s AND x.created_at<%s AND NOT EXISTS(
                  SELECT 1 FROM bank_reconciliation_source_claims b WHERE b.source_kind=%s AND b.source_id=x.id)
                ORDER BY x.created_at,x.id LIMIT %s''',(user['brand_id'],bids,lo,hi,kind,LIMIT+1)).fetchall()
            if len(found)>LIMIT:raise db.error(413,'BANK_SOURCE_PERIOD_TOO_LARGE','اختر فترة أقصر لعرض جميع عمليات الشبكة دون إخفاء أي عملية.')
            result['collections' if kind=='collection' else 'refunds']=found
        return safe(result)


@router.get('')
def listing(user=Depends(access)):
    with db.tx() as c:
        rows=c.execute('SELECT * FROM bank_reconciliation_batches WHERE brand_id=%s AND (%s OR branch_ids <@ %s::jsonb) ORDER BY created_at DESC,id LIMIT 301',(user['brand_id'],unrestricted(user),db.json(user.get('branch_ids',[])))).fetchall()
        if len(rows)>LIMIT:raise db.error(413,'BANK_BATCH_LIST_TOO_LARGE','عدد دفعات المطابقة كبير؛ يلزم تضييق نطاق التقرير.')
        return safe({'batches':[project(c,r) for r in rows]})


@router.post('')
def propose_batch(body:Batch,request:Request,user=Depends(access)):
    with db.tx() as c:
        def perform():
            cols=source_rows(c,user,'collection',body.collection_ids);refs=source_rows(c,user,'refund',body.refund_ids)
            gross=sum(r['amount_halalas'] for r in cols);refund=sum(r['amount_halalas'] for r in refs)
            bid=db.new_id('bankbatch_');snapshot=safe({'collections':cols,'refunds':refs})
            row=c.execute('''INSERT INTO bank_reconciliation_batches
                (id,brand_id,branch_ids,batch_reference,source_snapshot,gross_halalas,refund_halalas,fees_halalas,expected_net_halalas,evidence_reference,note,proposed_by)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (bid,user['brand_id'],db.json(sorted({r['branch_id'] for r in cols+refs})),body.batch_reference,db.json(snapshot),gross,refund,body.fees_halalas,gross-refund-body.fees_halalas,body.evidence_reference,body.note,user['id'])).fetchone()
            db.audit(c,user['id'],'accounting.bank_batch_proposed','bank_batch',bid,{'collection_ids':body.collection_ids,'refund_ids':body.refund_ids,'fees_halalas':body.fees_halalas,'expected_net_halalas':row['expected_net_halalas'],'evidence_reference':body.evidence_reference})
            return safe(project(c,row))
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.bank_batch.propose',body.model_dump(mode='json'),perform)


@router.post('/{bid}/decision')
def decide_batch(bid:str,body:Decision,request:Request,user=Depends(access)):
    with db.tx() as c:
        def perform():
            c.execute("SELECT pg_advisory_xact_lock(hashtext('bank-reconciliation-sources'))")
            row=view(c,user,bid,True);assert_independent(c,user,row)
            if not body.confirmed:raise db.error(422,'BANK_CONFIRMATION_REQUIRED','أكد مراجعة مستندات العمليات والرسوم.')
            if c.execute('SELECT 1 FROM bank_reconciliation_decisions WHERE batch_id=%s AND receipt_id IS NULL',(bid,)).fetchone():
                raise db.error(409,'BANK_BATCH_ALREADY_REVIEWED','سبق اعتماد أو إرجاع هذه الدفعة.')
            if body.decision=='approved':
                for kind,key in (('collection','collections'),('refund','refunds')):
                    expected=row['source_snapshot'][key];actual=safe(source_rows(c,user,kind,[r['id'] for r in expected]))
                    if actual!=expected:raise db.error(409,'BANK_SOURCES_CHANGED','تغير نطاق أو مصدر العملية؛ أعد المقترح دون تعديل الأصل.')
                    for item in expected:
                        if c.execute('SELECT 1 FROM bank_reconciliation_source_claims WHERE source_kind=%s AND source_id=%s',(kind,item['id'])).fetchone():
                            raise db.error(409,'BANK_SOURCE_ALREADY_MATCHED','إحدى عمليات الشبكة مرتبطة بدفعة معتمدة أخرى.')
                        c.execute('INSERT INTO bank_reconciliation_source_claims(source_kind,source_id,batch_id,amount_halalas) VALUES(%s,%s,%s,%s)',(kind,item['id'],bid,item['amount_halalas']))
            c.execute('''INSERT INTO bank_reconciliation_decisions(id,batch_id,decision,evidence_reference,note,decided_by)
                VALUES(%s,%s,%s,%s,%s,%s)''',(db.new_id('bankdecision_'),bid,body.decision,body.evidence_reference,body.note,user['id']))
            db.audit(c,user['id'],'accounting.bank_batch_'+body.decision,'bank_batch',bid,body.model_dump(mode='json'))
            return safe(project(c,row))
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.bank_batch.decision:'+bid,body.model_dump(mode='json'),perform)


@router.post('/{bid}/receipts')
def propose_receipt(bid:str,body:Receipt,request:Request,user=Depends(access)):
    with db.tx() as c:
        def perform():
            row=view(c,user,bid,True);data=project(c,row)
            if not data['approval'] or data['approval']['decision']!='approved':
                raise db.error(409,'BANK_BATCH_APPROVAL_REQUIRED','اعتمد دفعة الشبكة مستقلًا قبل إرفاق وصول البنك.')
            if body.received_on>datetime.now(ZoneInfo('Asia/Riyadh')).date():raise db.error(422,'BANK_RECEIPT_FUTURE','تاريخ وصول البنك لا يكون في المستقبل.')
            check_remaining(row['expected_net_halalas'],data['bank_received_halalas'],body.amount_halalas)
            rid=db.new_id('bankreceipt_')
            c.execute('''INSERT INTO bank_reconciliation_receipts(id,batch_id,bank_account_reference,statement_reference,line_reference,amount_halalas,statement_amount_halalas,received_on,evidence_reference,note,proposed_by)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(rid,bid,body.bank_account_reference,body.statement_reference,body.line_reference,body.amount_halalas,body.statement_amount_halalas,body.received_on,body.evidence_reference,body.note,user['id']))
            db.audit(c,user['id'],'accounting.bank_receipt_proposed','bank_batch',bid,{'receipt_id':rid,**body.model_dump(mode='json')})
            return safe(project(c,row))
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.bank_receipt.propose:'+bid,body.model_dump(mode='json'),perform)


@router.post('/{bid}/receipts/{rid}/decision')
def decide_receipt(bid:str,rid:str,body:Decision,request:Request,user=Depends(access)):
    with db.tx() as c:
        def perform():
            c.execute("SELECT pg_advisory_xact_lock(hashtext('bank-reconciliation-statements'))")
            row=view(c,user,bid,True);data=project(c,row)
            receipt=c.execute('SELECT * FROM bank_reconciliation_receipts WHERE id=%s AND batch_id=%s',(rid,bid)).fetchone()
            if not receipt:raise db.error(404,'BANK_RECEIPT_SCOPE','حركة البنك غير متاحة.')
            assert_independent(c,user,dict(row,proposed_by=receipt['proposed_by']))
            if not body.confirmed:raise db.error(422,'BANK_CONFIRMATION_REQUIRED','أكد مراجعة سطر كشف البنك والمبلغ الفعلي.')
            if c.execute('SELECT 1 FROM bank_reconciliation_decisions WHERE receipt_id=%s',(rid,)).fetchone():
                raise db.error(409,'BANK_RECEIPT_ALREADY_REVIEWED','سبق اعتماد أو إرجاع حركة البنك.')
            if body.decision=='approved':
                check_remaining(row['expected_net_halalas'],data['bank_received_halalas'],receipt['amount_halalas'])
                refs=(receipt['bank_account_reference'],receipt['statement_reference'],receipt['line_reference'])
                line=c.execute('SELECT * FROM bank_reconciliation_bank_lines WHERE bank_account_reference=%s AND statement_reference=%s AND line_reference=%s FOR UPDATE',refs).fetchone()
                if line and (line['amount_halalas']!=receipt['statement_amount_halalas'] or line['received_on']!=receipt['received_on']):
                    raise db.error(409,'BANK_STATEMENT_AMOUNT_CHANGED','أصل حركة البنك مسجل بمبلغ مختلف؛ لا تعدّل الأصل لتغطية الفرق.')
                if not line:
                    c.execute('''INSERT INTO bank_reconciliation_bank_lines(id,received_on,bank_account_reference,statement_reference,line_reference,amount_halalas,evidence_reference,recorded_by)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',(db.new_id('bankline_'),receipt['received_on'],*refs,receipt['statement_amount_halalas'],receipt['evidence_reference'],user['id']))
                line=c.execute('SELECT * FROM bank_reconciliation_bank_lines WHERE bank_account_reference=%s AND statement_reference=%s AND line_reference=%s',refs).fetchone()
                claim_bank_line(c,user,line['id'],receipt['amount_halalas'],'pos_batch',rid,receipt_id=rid)
            c.execute('''INSERT INTO bank_reconciliation_decisions(id,batch_id,receipt_id,decision,evidence_reference,note,decided_by)
                VALUES(%s,%s,%s,%s,%s,%s,%s)''',(db.new_id('bankdecision_'),bid,rid,body.decision,body.evidence_reference,body.note,user['id']))
            db.audit(c,user['id'],'accounting.bank_receipt_'+body.decision,'bank_batch',bid,{'receipt_id':rid,**body.model_dump(mode='json')})
            return safe(project(c,row))
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.bank_receipt.decision:'+rid,body.model_dump(mode='json'),perform)


def closure_bank_status(c,user,report):
    """Evidence state only; never prorate a partial bank batch over its orders.

    A batch may contain multiple closures/days. Until its full documented net
    arrives, each source stays pending without inventing a per-driver amount.
    """
    snap=report.get('snapshot')
    undocumented={'state':'undocumented','batch_ids':[],'pending_source_count':None}
    if not isinstance(snap,dict) or any(not isinstance(snap.get(k),list) for k in ('collections','refunds')):
        return undocumented
    required=[]
    for kind,key in (('collection','collections'),('refund','refunds')):
        for row in snap[key]:
            if not isinstance(row,dict) or row.get('method') not in ('cash','card') or type(row.get('amount_halalas')) is not int:
                return undocumented
            if row['method']=='card' and row['amount_halalas']>0:
                if not isinstance(row.get('id'),str):return undocumented
                required.append((kind,row['id']))
    summary=snap.get('summary',{})
    if not isinstance(summary,dict):return undocumented
    for source,amount_key in (('collections','card_collected_halalas'),('refunds','card_refunded_halalas')):
        if (type(summary.get(amount_key)) is not int or summary[amount_key]!=sum(
                r['amount_halalas'] for r in snap[source] if r['method']=='card')):
            return undocumented
    external=snap.get('external_enbishan',[])
    if not isinstance(external,list) or any(not isinstance(r,dict) or r.get('card_halalas') for r in external):
        return undocumented
    # Tender corrections change the current canonical channel while the
    # original immutable snapshot remains evidence of what was first recorded.
    ids=[r.get('id') for r in snap['collections']]
    if any(not isinstance(identifier,str) for identifier in ids) or len(set(ids))!=len(ids):return undocumented
    current=c.execute('SELECT id,method,amount_halalas FROM effective_collections WHERE id=ANY(%s)',(ids,)).fetchall() if ids else []
    if len(current)!=len(ids) or any(r['method'] not in ('cash','card') or type(r['amount_halalas']) is not int for r in current):
        return undocumented
    required=[('collection',r['id']) for r in current if r['method']=='card' and r['amount_halalas']>0]+[
        ('refund',r['id']) for r in snap['refunds'] if r['method']=='card' and r['amount_halalas']>0]
    if not required:return {'state':'no_card_operations','batch_ids':[],'pending_source_count':0}
    if not enabled():return {'state':'unconfigured','batch_ids':[],'pending_source_count':None}
    required=set(required);batch_ids=set();pending=0;cache={}
    from fastapi import HTTPException
    for kind,sid in required:
        claim=c.execute('SELECT batch_id FROM bank_reconciliation_source_claims WHERE source_kind=%s AND source_id=%s',(kind,sid)).fetchone()
        if not claim:pending+=1;continue
        bid=claim['batch_id']
        if bid not in cache:
            try:cache[bid]=project(c,view(c,user,bid))
            except HTTPException as exc:
                if exc.status_code==404:return undocumented
                raise
        batch_ids.add(bid)
        if not cache[bid]['financially_matched']:pending+=1
    return {'state':'pending' if pending else 'matched','batch_ids':sorted(batch_ids),'pending_source_count':pending}


def bank_line_scope(c,user,line):
    # Only one's own approved batch/registration exposes this physical movement.
    # Other brands' allocations never reveal their customers or batch details.
    if not line:raise db.error(404,'BANK_LINE_SCOPE','حركة البنك المسجلة غير متاحة ضمن صلاحياتك.')
    refs=(line['bank_account_reference'],line['statement_reference'],line['line_reference'])
    permitted=c.execute("""SELECT 1 FROM bank_reconciliation_receipts r JOIN bank_reconciliation_batches b ON b.id=r.batch_id
      JOIN bank_reconciliation_decisions d ON d.receipt_id=r.id AND d.decision='approved'
      WHERE r.bank_account_reference=%s AND r.statement_reference=%s AND r.line_reference=%s
      AND b.brand_id=%s AND (%s OR b.branch_ids <@ %s::jsonb) LIMIT 1""",(*refs,user['brand_id'],unrestricted(user),db.json(user.get('branch_ids',[])))).fetchone()
    if not permitted:
        permitted=c.execute("""SELECT 1 FROM bank_reconciliation_line_proposals p JOIN bank_reconciliation_line_decisions d ON d.proposal_id=p.id
          WHERE d.bank_line_id=%s AND d.decision='approved' AND p.brand_id=%s AND (%s OR p.branch_ids <@ %s::jsonb) LIMIT 1""",
          (line['id'],user['brand_id'],unrestricted(user),db.json(user.get('branch_ids',[])))).fetchone()
    if not permitted:raise db.error(404,'BANK_LINE_SCOPE','حركة البنك المسجلة غير متاحة ضمن صلاحياتك.')
    return line


def bank_line(c,user,bank_line_id):
    if not enabled():raise db.error(404,'MANUAL_BANK_MATCHING_DISABLED','مصادر مطابقة البنك غير مفعلة تشغيليًا.')
    line=bank_line_scope(c,user,c.execute('SELECT * FROM bank_reconciliation_bank_lines WHERE id=%s',(bank_line_id,)).fetchone())
    allocated=c.execute('SELECT coalesce(sum(amount_halalas),0) amount FROM bank_reconciliation_bank_claims WHERE bank_account_reference=%s AND statement_reference=%s AND line_reference=%s',
        (line['bank_account_reference'],line['statement_reference'],line['line_reference'])).fetchone()['amount']
    return dict(line,unallocated_halalas=line['amount_halalas']-allocated)


def claim_bank_line(c,user,bank_line_id,amount_halalas,consumer_kind,consumer_id,receipt_id=None):
    """Consume one actual source inside its independent approval transaction.

    The claim is evidence allocation only. It never posts customer money, changes
    an invoice, or generates a cash-custody event.
    """
    if not enabled():raise db.error(404,'MANUAL_BANK_MATCHING_DISABLED','مصادر مطابقة البنك غير مفعلة تشغيليًا.')
    if consumer_kind not in ('pos_batch','financial_recovery') or type(amount_halalas) is not int:
        raise db.error(422,'BANK_CLAIM_INVALID','نوع أو مبلغ الإسناد غير صالح.')
    c.execute("SELECT pg_advisory_xact_lock(hashtext('bank-reconciliation-statements'))")
    line=c.execute('SELECT * FROM bank_reconciliation_bank_lines WHERE id=%s FOR UPDATE',(bank_line_id,)).fetchone()
    if consumer_kind=='financial_recovery':bank_line_scope(c,user,line)
    elif not line:raise db.error(404,'BANK_LINE_SCOPE','حركة البنك غير موجودة.')
    if c.execute('SELECT 1 FROM bank_reconciliation_bank_claims WHERE consumer_kind=%s AND consumer_id=%s',(consumer_kind,consumer_id)).fetchone():
        raise db.error(409,'BANK_CLAIM_ALREADY_USED','سبق إسناد حركة البنك لهذه المعاملة.')
    allocated=c.execute('SELECT coalesce(sum(amount_halalas),0) amount FROM bank_reconciliation_bank_claims WHERE bank_account_reference=%s AND statement_reference=%s AND line_reference=%s',
        (line['bank_account_reference'],line['statement_reference'],line['line_reference'])).fetchone()['amount']
    check_remaining(line['amount_halalas'],allocated,amount_halalas)
    claim_id=db.new_id('bankclaim_')
    c.execute("""INSERT INTO bank_reconciliation_bank_claims(id,consumer_kind,consumer_id,receipt_id,bank_account_reference,statement_reference,line_reference,amount_halalas)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",(claim_id,consumer_kind,consumer_id,receipt_id,line['bank_account_reference'],line['statement_reference'],line['line_reference'],amount_halalas))
    return dict(claim_id=claim_id,bank_line_id=bank_line_id,amount_halalas=amount_halalas,remaining_halalas=line['amount_halalas']-allocated-amount_halalas)


@router.get('/bank-lines')
def bank_line_listing(user=Depends(access)):
    with db.tx() as c:
        proposals=c.execute("""SELECT p.*,u.display_name proposer_name,d.decision,d.bank_line_id,d.note decision_note,
          d.decided_by,d.evidence_reference decision_evidence FROM bank_reconciliation_line_proposals p
          JOIN users u ON u.id=p.proposed_by LEFT JOIN bank_reconciliation_line_decisions d ON d.proposal_id=p.id
          WHERE p.brand_id=%s AND (%s OR p.branch_ids <@ %s::jsonb) ORDER BY p.created_at DESC,p.id LIMIT 301""",
          (user['brand_id'],unrestricted(user),db.json(user.get('branch_ids',[])))).fetchall()
        if len(proposals)>LIMIT:raise db.error(413,'BANK_LINE_LIST_TOO_LARGE','عدد مصادر البنك كبير؛ يلزم تضييق نطاق التقرير.')
        permitted=[]
        # Select only lines exposed by a currently authorized approved record;
        # scope applies before LIMIT so unrelated brands never hide older sources.
        lines=c.execute("""SELECT l.* FROM bank_reconciliation_bank_lines l WHERE EXISTS(
            SELECT 1 FROM bank_reconciliation_line_proposals p JOIN bank_reconciliation_line_decisions d ON d.proposal_id=p.id
            WHERE d.bank_line_id=l.id AND d.decision='approved' AND p.brand_id=%s AND (%s OR p.branch_ids <@ %s::jsonb))
          OR EXISTS(SELECT 1 FROM bank_reconciliation_receipts r JOIN bank_reconciliation_batches b ON b.id=r.batch_id
            JOIN bank_reconciliation_decisions d ON d.receipt_id=r.id AND d.decision='approved'
            WHERE r.bank_account_reference=l.bank_account_reference AND r.statement_reference=l.statement_reference AND r.line_reference=l.line_reference
            AND b.brand_id=%s AND (%s OR b.branch_ids <@ %s::jsonb))
          ORDER BY l.created_at DESC,l.id LIMIT 301""",(user['brand_id'],unrestricted(user),db.json(user.get('branch_ids',[])),user['brand_id'],unrestricted(user),db.json(user.get('branch_ids',[])))).fetchall()
        if len(lines)>LIMIT:raise db.error(413,'BANK_LINE_LIST_TOO_LARGE','اختر نطاقًا أصغر لمصادر البنك.')
        for line in lines:permitted.append(bank_line(c,user,line['id']))
        return safe({'proposals':proposals,'bank_lines':permitted})


@router.post('/bank-lines')
def propose_bank_line(body:StatementLine,request:Request,user=Depends(access)):
    from .accounting import branches
    with db.tx() as c:
        def perform():
            if body.received_on>datetime.now(ZoneInfo('Asia/Riyadh')).date():raise db.error(422,'BANK_RECEIPT_FUTURE','تاريخ الحركة الفعلية لا يكون مستقبلًا.')
            bids=[b['id'] for b in branches(c,user) if unrestricted(user) or b['id'] in user.get('branch_ids',[])]
            if not bids:raise db.error(403,'BANK_LINE_SCOPE','لا توجد فروع مخولة لهذا التسجيل.')
            identifier=db.new_id('banklineproposal_')
            c.execute("""INSERT INTO bank_reconciliation_line_proposals(id,brand_id,branch_ids,bank_account_reference,statement_reference,line_reference,amount_halalas,received_on,evidence_reference,note,proposed_by)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",(identifier,user['brand_id'],db.json(bids),body.bank_account_reference,body.statement_reference,body.line_reference,body.amount_halalas,body.received_on,body.evidence_reference,body.note,user['id']))
            db.audit(c,user['id'],'accounting.bank_line_proposed','bank_line_proposal',identifier,body.model_dump(mode='json'))
            return {'id':identifier,'decision':None,'bank_line_id':None}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.bank_line.propose',body.model_dump(mode='json'),perform)


@router.post('/bank-lines/{pid}/decision')
def decide_bank_line(pid:str,body:Decision,request:Request,user=Depends(access)):
    with db.tx() as c:
        def perform():
            c.execute("SELECT pg_advisory_xact_lock(hashtext('bank-reconciliation-statements'))")
            row=c.execute('SELECT * FROM bank_reconciliation_line_proposals WHERE id=%s FOR UPDATE',(pid,)).fetchone()
            if not row or row['brand_id']!=user['brand_id'] or not unrestricted(user) and not set(row['branch_ids'])<=set(user.get('branch_ids',[])):
                raise db.error(404,'BANK_LINE_SCOPE','اقتراح حركة البنك غير متاح.')
            if row['proposed_by']==user['id']:raise db.error(403,'BANK_SELF_APPROVAL','راجع الحركة من حساب محاسب مستقل.')
            if not body.confirmed:raise db.error(422,'BANK_CONFIRMATION_REQUIRED','أكد مراجعة أصل كشف البنك والمبلغ الفعلي.')
            if c.execute('SELECT 1 FROM bank_reconciliation_line_decisions WHERE proposal_id=%s',(pid,)).fetchone():
                raise db.error(409,'BANK_LINE_ALREADY_REVIEWED','سبق اتخاذ قرار لهذه الحركة.')
            line_id=None
            if body.decision=='approved':
                refs=(row['bank_account_reference'],row['statement_reference'],row['line_reference'])
                line=c.execute('SELECT * FROM bank_reconciliation_bank_lines WHERE bank_account_reference=%s AND statement_reference=%s AND line_reference=%s FOR UPDATE',refs).fetchone()
                if line and (line['amount_halalas']!=row['amount_halalas'] or line['received_on']!=row['received_on']):
                    raise db.error(409,'BANK_STATEMENT_AMOUNT_CHANGED','المبلغ أو التاريخ يخالف أصل الحركة المسجلة؛ استكمل الإثبات دون تغييره.')
                line_id=line['id'] if line else db.new_id('bankline_')
                if not line:
                    c.execute("""INSERT INTO bank_reconciliation_bank_lines(id,received_on,bank_account_reference,statement_reference,line_reference,amount_halalas,evidence_reference,recorded_by)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",(line_id,row['received_on'],*refs,row['amount_halalas'],row['evidence_reference'],user['id']))
            c.execute("""INSERT INTO bank_reconciliation_line_decisions(id,proposal_id,bank_line_id,decision,evidence_reference,note,decided_by)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""",(db.new_id('banklinedecision_'),pid,line_id,body.decision,body.evidence_reference,body.note,user['id']))
            db.audit(c,user['id'],'accounting.bank_line_'+body.decision,'bank_line_proposal',pid,{'bank_line_id':line_id,**body.model_dump(mode='json')})
            return safe({'id':pid,'decision':body.decision,'bank_line_id':line_id})
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.bank_line.decision:'+pid,body.model_dump(mode='json'),perform)
