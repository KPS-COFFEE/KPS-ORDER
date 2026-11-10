"""Independent review of manager-to-accounting bag differences.

The driver's original receipt stays immutable. This adapter reuses the existing
financial treatment contract, records no money and never assigns driver debt.
Only an independent owner may approve a period with this amount outstanding.
"""
from datetime import datetime
from fastapi import APIRouter, Depends, Request, HTTPException
from . import auth, db, financial_treatments as ft, operations as ops

router=APIRouter()

class Proposal(ft.Proposal):
    expected_count_id:str
    expected_receipt_id:str

class Approval(ft.Approval):
    expected_count_id:str
    expected_receipt_id:str

class Rejection(ft.Rejection):
    expected_count_id:str
    expected_receipt_id:str


def source(c,user,bid):
    ft.require_accountant(user)
    from .aggregate_driver_closure import authorize, latest
    bag=c.execute('SELECT b.*,d.anchor_shift_id FROM driver_cash_bags b JOIN central_workdays d ON d.id=b.workday_id WHERE b.id=%s',(bid,)).fetchone()
    if not bag:raise db.error(404,'CASH_BAG_NOT_FOUND','الكيس غير موجود.')
    day,legs=authorize(c,user,bag['anchor_shift_id'])
    declaration=latest(c,day['id'])
    if not declaration or declaration['id']!=bag['declaration_id']:raise db.error(409,'CASH_BAG_SOURCE_CHANGED','تغير أصل تقفيلة الكيس؛ حدث الشاشة.')
    count=c.execute('SELECT n.*,b.declared_card_halalas FROM driver_cash_bag_counts n JOIN driver_cash_bags b ON b.id=n.bag_id WHERE n.bag_id=%s',(bid,)).fetchone()
    receipt=c.execute('SELECT * FROM shared_driver_closure_receipts WHERE declaration_id=%s',(bag['declaration_id'],)).fetchone()
    if not count or not receipt:raise db.error(409,'CASH_BAG_ACCOUNTING_RECEIPT_REQUIRED','سجل الاستلام الفعلي وجرد الكيس أولًا.')
    return bag,day,legs,count,receipt


def history(c,bid):
    events=c.execute("SELECT a.*,u.display_name actor_name FROM audit_events a JOIN users u ON u.id=a.actor_id WHERE a.entity_type='cash_bag' AND a.entity_id=%s AND a.action IN (%s,%s,%s) ORDER BY a.created_at,a.id",(bid,ft.PROPOSED,ft.APPROVED,ft.REJECTED)).fetchall()
    decisions={e['details']['treatment_id']:e for e in events if e['action'] in {ft.APPROVED,ft.REJECTED}}
    rows=[]
    for e in events:
        if e['action']!=ft.PROPOSED:continue
        payload=e['details'];decision=decisions.get(payload['treatment_id'])
        rows.append({'id':payload['treatment_id'],'payload':payload,'requested_by':e['actor_id'],'requester_name':e['actor_name'],'created_at':e['created_at'],
            'status':'approved' if decision and decision['action']==ft.APPROVED else 'rejected' if decision else 'pending',
            'approval':{**decision['details'],'approved_by':decision['actor_id'],'approver_name':decision['actor_name']} if decision else None})
    for row in rows:
        row['recoveries']=ft.recovery_history(c,bid,row['id'],entity_type='cash_bag')
        row['remaining_by_direction']=ft.remaining_by_direction(row)
        row['remaining']=ft.remaining(row)
    return rows


def values(count,receipt):
    difference=count['received_cash_halalas']-receipt['received_halalas']
    return {'revision':1,'cash_variance_halalas':difference,'bank_difference_halalas':count.get('card_halalas',0)-count.get('declared_card_halalas',0),'has_channel_variance':bool(difference or count.get('card_halalas',0)-count.get('declared_card_halalas',0)),'reconciliation_complete':True,'financial_approval_allowed':True}


def matches(payload,count,receipt):
    return (payload.get('expected_count_id')==count['id'] and payload.get('expected_receipt_id')==receipt['id']
        and payload.get('cash_variance_halalas')==count['received_cash_halalas']-receipt['received_halalas'] and payload.get('card_variance_halalas')==count.get('card_halalas',0)-count.get('declared_card_halalas',0))


def verify(body,count,receipt):
    if body.expected_count_id!=count['id'] or body.expected_receipt_id!=receipt['id'] or body.expected_revision!=1:
        raise db.error(409,'CASH_BAG_CUSTODY_STALE','تغير أصل الاستلام أو الجرد؛ أعد مراجعة الفرق الحالي.')


def projection(c,bid,count,receipt):
    rows=history(c,bid)
    approved=next((r for r in rows if r['status']=='approved' and matches(r['payload'],count,receipt)),None)
    return dict(ft.apply(values(count,receipt),approved),treatments=rows,scope='manager_to_accounting_custody',
        bag_id=bid,expected_count_id=count['id'],expected_receipt_id=receipt['id'],expected_revision=1,
        original_received_halalas=receipt['received_halalas'],count_received_halalas=count['received_cash_halalas'],count_card_halalas=count.get('card_halalas'),declared_card_halalas=count.get('declared_card_halalas'),bank_settlement_included=False,
        no_money_posted=True,no_payroll_deduction=True)


def followup(c,user,legs,identifier):
    person=None
    for _,shift in legs:person=ft.eligible_followup(c,user,{'brand_id':shift['brand_id'],'branch_ids':shift['branch_ids']},identifier)
    return person


@router.get('/{bid}/custody-review')
def listing(bid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        bag,day,legs,count,receipt=source(c,user,bid)
        result=projection(c,bid,count,receipt);users=[]
        for row in c.execute("SELECT id FROM users WHERE active AND role IN ('owner','admin') ORDER BY display_name,id").fetchall():
            try:person=followup(c,user,legs,row['id'])
            except HTTPException as error:
                if error.status_code==422:continue
                raise
            users.append({'id':person['id'],'name':person['display_name']})
        pending=any(r['status'] in {'pending','approved'} and matches(r['payload'],count,receipt) for r in result['treatments'])
        result.update(followup_users=users,can_propose=bool(result['has_channel_variance'] and not pending),
            can_approve=user['id'] not in {day['user_id'],receipt['received_by'],count['counted_by']},
            source={'cash_variance_halalas':result['cash_variance_halalas'],'card_variance_halalas':result['bank_difference_halalas']})
        return ops.safe(result)


@router.post('/{bid}/custody-review')
def propose(bid:str,body:Proposal,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        from .central_workdays import lock_lanes
        lock_lanes(c);bag,day,legs,count,receipt=source(c,user,bid)
        def perform():
            verify(body,count,receipt);ft.validate_proposal(body,values(count,receipt))
            if body.kind!='outstanding' or body.supersedes_treatment_id or body.source_correction_ids:
                raise db.error(422,'CASH_BAG_CUSTODY_KIND','فرق العهدة يبقى مبلغًا مستحقًا بمتابعة مستقلة؛ لا يغير أصل التحصيل.')
            if any(r['status'] in {'pending','approved'} and matches(r['payload'],count,receipt) for r in history(c,bid)):
                raise db.error(409,'FINANCIAL_TREATMENT_EXISTS','توجد معالجة لهذا الجرد والاستلام.')
            from .accounting import TZ
            if body.due_on<datetime.now(TZ).date():raise db.error(422,'FINANCIAL_TREATMENT_DUE','اختر مهلة اليوم أو لاحقًا.')
            followup(c,user,legs,body.followup_user_id)
            identifier=db.new_id('bct_')
            payload={**body.model_dump(mode='json'),'treatment_id':identifier,'bag_id':bid,'scope':'manager_to_accounting_custody',
                'original_received_halalas':receipt['received_halalas'],'count_received_halalas':count['received_cash_halalas'],
                'manager_receiver_id':receipt['received_by'],'accounting_receiver_id':count['counted_by'],
                'no_money_posted':True,'no_payroll_deduction':True}
            db.audit(c,user['id'],ft.PROPOSED,'cash_bag',bid,payload)
            return {'id':identifier,'status':'pending','payload':payload}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'cash_bag.custody.propose:'+bid,body.model_dump(mode='json'),perform)


@router.post('/{bid}/custody-review/{tid}/approve')
def approve(bid:str,tid:str,body:Approval,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        from .central_workdays import lock_lanes
        lock_lanes(c);bag,day,legs,count,receipt=source(c,user,bid)
        def perform():
            verify(body,count,receipt)
            if not body.confirmed:raise db.error(422,'FINANCIAL_TREATMENT_CONFIRMATION','أكد مراجعة أصل استلام المدير وجرد المحاسبة.')
            row=next((r for r in history(c,bid) if r['id']==tid),None)
            if not row or row['status']!='pending' or not matches(row['payload'],count,receipt):raise db.error(409,'CASH_BAG_CUSTODY_STALE','المعالجة لا تطابق الجرد الحالي أو سبق اتخاذ قرارها.')
            if user['id'] in {row['requested_by'],day['user_id'],receipt['received_by'],count['counted_by']}:
                raise db.error(403,'FINANCIAL_TREATMENT_SELF_APPROVAL','يلزم معتمد مستقل عن مقدم المعالجة والسائق ومستلمي الكيس.')
            proposal=Proposal(**{k:v for k,v in row['payload'].items() if k in Proposal.model_fields})
            verify(proposal,count,receipt);ft.validate_proposal(proposal,values(count,receipt))
            if (proposal.liable_party_type=='employee' or body.allow_period_approval_with_outstanding) and user['role']!='owner':
                raise db.error(403,'FINANCIAL_TREATMENT_OWNER_REQUIRED','إثبات ذمة موظف أو اعتماد الفترة بمبلغ متبق للمالك فقط.')
            followup(c,user,legs,proposal.followup_user_id)
            detail={**body.model_dump(mode='json'),'treatment_id':tid,'requested_by':row['requested_by'],'no_money_posted':True,'no_payroll_deduction':True}
            db.audit(c,user['id'],ft.APPROVED,'cash_bag',bid,detail)
            return {'id':tid,'status':'approved','financial_state':'review_approved_outstanding'}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'cash_bag.custody.approve:'+bid+':'+tid,body.model_dump(mode='json'),perform)


@router.post('/{bid}/custody-review/{tid}/reject')
def reject(bid:str,tid:str,body:Rejection,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        from .central_workdays import lock_lanes
        lock_lanes(c);bag,day,legs,count,receipt=source(c,user,bid)
        def perform():
            verify(body,count,receipt)
            row=next((r for r in history(c,bid) if r['id']==tid),None)
            if not row or row['status']!='pending' or not matches(row['payload'],count,receipt):raise db.error(409,'CASH_BAG_CUSTODY_STALE','المعالجة ليست معلقة على الأصل الحالي.')
            db.audit(c,user['id'],ft.REJECTED,'cash_bag',bid,{'treatment_id':tid,**body.model_dump(),'requested_by':row['requested_by'],'no_money_posted':True})
            return {'id':tid,'status':'rejected'}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'cash_bag.custody.reject:'+bid+':'+tid,body.model_dump(),perform)


def report_reviews(c,report,user=None):
    sub=((report.get('snapshot') or {}).get('shift') or {}).get('submission') or {}
    if not sub.get('shared_declaration_id'):return []
    bags=c.execute("SELECT DISTINCT b.id,b.declaration_id,d.anchor_shift_id FROM driver_cash_bags b JOIN central_workdays d ON d.id=b.workday_id JOIN central_workday_legs l ON l.workday_id=b.workday_id JOIN shifts s ON s.id=l.shift_id WHERE ((s.id=%s AND b.declaration_id=%s) OR s.manager_shift_id=%s) AND NOT EXISTS (SELECT 1 FROM shared_driver_closure_returns returned WHERE returned.declaration_id=b.declaration_id)",(report['shift_id'],sub['shared_declaration_id'],report['shift_id'])).fetchall()
    result=[]
    for bag in bags:
        count=c.execute('SELECT n.*,b.declared_card_halalas FROM driver_cash_bag_counts n JOIN driver_cash_bags b ON b.id=n.bag_id WHERE n.bag_id=%s',(bag['id'],)).fetchone()
        receipt=c.execute('SELECT * FROM shared_driver_closure_receipts WHERE declaration_id=%s',(bag['declaration_id'],)).fetchone()
        if not count or not receipt:
            result.append({'bag_id':bag['id'],'financial_state':'awaiting_accounting_receipt','period_approval_allowed':False,'financially_complete':False})
        else:
            review=projection(c,bag['id'],count,receipt)
            # A brand-scoped report cannot expose the other brand's share of a
            # physical bag. Only the fully authorized bag inbox shows totals.
            full_scope=False
            if user and user.get('role') in {'owner','admin'}:
                from .central_workdays import authorized
                try:authorized(c,user,bag['anchor_shift_id'],capability='accounting_review',lock=False);full_scope=True
                except HTTPException as error:
                    if error.status_code not in (403,404):raise
            if not full_scope:
                review={k:review[k] for k in ('bag_id','financial_state','financially_complete','period_approval_allowed')}
                review['custody_amounts_withheld']=True
            result.append(review)
    return result


def apply_report(c,report,result,user=None):
    reviews=report_reviews(c,report,user=user)
    if not reviews:return result
    result['cash_bag_custody_reviews']=reviews
    if any(not r['financially_complete'] for r in reviews):
        result['financially_complete']=False;result['overall_financially_complete']=False
        result['period_approval_allowed']=result['period_approval_allowed'] and all(r['period_approval_allowed'] for r in reviews)
        result['accounting_review_complete']=result['period_approval_allowed']
        result['financial_state']='review_approved_outstanding' if result['period_approval_allowed'] else 'cash_bag_custody_review_pending'
    return result


def require_report_receipt(c,report):
    if any(r['financial_state']=='awaiting_accounting_receipt' for r in report_reviews(c,report)):
        raise db.error(409,'CASH_BAG_ACCOUNTING_RECEIPT_REQUIRED','اسحب الكيس واستلمه وعده فعليًا قبل اعتماد الموازنة أو تسجيل التسوية؛ فتح التقفيلة لا يسجل استلامًا.')


def summarize(reviews):
    """One physical bag can back several brand reports; never multiply its debt."""
    unique={r['bag_id']:r for r in reviews}
    rows=list(unique.values())
    return {'cash_bag_custody_reviews':rows,
        'custody_outstanding_receivable_halalas':None if any(r.get('custody_amounts_withheld') for r in rows) else sum(r.get('outstanding_receivable_halalas',0) for r in rows),
        'custody_outstanding_payable_halalas':None if any(r.get('custody_amounts_withheld') for r in rows) else sum(r.get('outstanding_payable_halalas',0) for r in rows),
        'custody_review_pending':any(not r['period_approval_allowed'] for r in rows),
        'custody_financially_complete':all(r['financially_complete'] for r in rows)}


class Recovery(ft.Recovery):
    expected_count_id:str
    expected_receipt_id:str

class RecoveryApproval(ft.RecoveryApproval):
    expected_count_id:str
    expected_receipt_id:str

class RecoveryRejection(ft.RecoveryRejection):
    expected_count_id:str
    expected_receipt_id:str


def recovery_source(c,user,bid,tid):
    bag,day,legs,count,receipt=source(c,user,bid)
    treatment=next((r for r in history(c,bid) if r['id']==tid),None)
    if not treatment or treatment['status']!='approved' or not matches(treatment['payload'],count,receipt):
        raise db.error(409,'CASH_BAG_CUSTODY_STALE','اختر فرق عهدة معتمدًا مطابقًا لأصل الجرد والاستلام.')
    branch=c.execute('SELECT brand_id FROM branches WHERE id=%s',(bag['branch_id'],)).fetchone()
    # The place holding the bag determines the custody evidence scope; this is
    # not an allocation of a mixed bag's variance to a brand's sales or driver.
    branches=sorted({b for _,shift in legs if shift['brand_id']==branch['brand_id'] for b in shift['branch_ids']})
    principal=next(principal for principal,shift in legs if shift['brand_id']==branch['brand_id'])
    report={'id':bid,'user_id':day['user_id'],'shift_id':day['anchor_shift_id'],'brand_id':branch['brand_id'],
        'branch_ids':branches,'created_at':count['created_at'],'_recovery_principal':principal}
    return bag,day,legs,count,receipt,treatment,report


def verify_recovery(body,count,receipt):
    if body.expected_count_id!=count['id'] or body.expected_receipt_id!=receipt['id']:
        raise db.error(409,'CASH_BAG_CUSTODY_STALE','حركة التسوية لا تخص جرد الكيس واستلامه الحاليين.')


@router.post('/{bid}/custody-review/{tid}/recoveries')
def propose_recovery(bid:str,tid:str,body:Recovery,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        from .central_workdays import lock_lanes
        lock_lanes(c);bag,day,legs,count,receipt,treatment,report=recovery_source(c,user,bid,tid)
        def perform():
            verify_recovery(body,count,receipt)
            actors=ft.validate_recovery(c,report['_recovery_principal'],report,treatment,body)
            identifier=db.new_id('bcr_')
            detail={**body.model_dump(mode='json'),'recovery_id':identifier,'treatment_id':tid,'bag_id':bid,
                'brand_id':report['brand_id'],'cash_source_actor_ids':actors,'no_money_posted':True,'no_payroll_deduction':True}
            db.audit(c,user['id'],ft.RECOVERY_PROPOSED,'cash_bag',bid,detail)
            return {'id':identifier,'status':'pending','payload':detail}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'cash_bag.recovery:'+bid+':'+tid,body.model_dump(mode='json'),perform)


@router.post('/{bid}/custody-review/{tid}/recoveries/{recovery_id}/approve')
def approve_recovery(bid:str,tid:str,recovery_id:str,body:RecoveryApproval,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        from .central_workdays import lock_lanes
        lock_lanes(c);bag,day,legs,count,receipt,treatment,report=recovery_source(c,user,bid,tid)
        def perform():
            verify_recovery(body,count,receipt)
            if not body.confirmed:raise db.error(422,'RECOVERY_CONFIRMATION_REQUIRED','أكد مراجعة حركة العهدة الفعلية ودليلها.')
            row=next((r for r in treatment['recoveries'] if r['id']==recovery_id),None)
            if not row or row['status']!='pending':raise db.error(409,'RECOVERY_NOT_PENDING','طلب التحصيل أو الرد ليس معلقًا.')
            recovery=Recovery(**{k:v for k,v in row['payload'].items() if k in Recovery.model_fields})
            verify_recovery(recovery,count,receipt)
            actors=ft.validate_recovery(c,report['_recovery_principal'],report,treatment,recovery)
            if user['id'] in {row['requested_by'],day['user_id'],receipt['received_by'],count['counted_by'],*actors}:
                raise db.error(403,'RECOVERY_SELF_APPROVAL','الاعتماد من موظف مستقل عن المسجل ومستلمي الكيس وحركة النقد.')
            if recovery.cash_custody_event_id:
                c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('financial-recovery-cash:'+recovery.cash_custody_event_id,))
                used=c.execute("SELECT 1 FROM audit_events WHERE action=%s AND details->>'cash_custody_event_id'=%s LIMIT 1",(ft.RECOVERY_APPROVED,recovery.cash_custody_event_id)).fetchone()
                if used:raise db.error(409,'RECOVERY_SOURCE_ALREADY_USED','حركة النقد مستخدمة في تسوية سابقة؛ يمنع احتسابها مرتين.')
            if recovery.card_amount_halalas:
                from .manual_bank_matching import claim_bank_line
                claim_bank_line(c,report['_recovery_principal'],recovery.bank_reference,recovery.card_amount_halalas*(1 if recovery.direction=='receivable_recovery' else -1),'financial_recovery',recovery_id)
            db.audit(c,user['id'],ft.RECOVERY_APPROVED,'cash_bag',bid,{**row['payload'],**body.model_dump(),'requested_by':row['requested_by'],'approved_by':user['id']})
            return {'id':recovery_id,'status':'approved','treatment_id':tid,'no_money_posted':True}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'cash_bag.recovery_approve:'+bid+':'+tid+':'+recovery_id,body.model_dump(),perform)


@router.post('/{bid}/custody-review/{tid}/recoveries/{recovery_id}/reject')
def reject_recovery(bid:str,tid:str,recovery_id:str,body:RecoveryRejection,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        from .central_workdays import lock_lanes
        lock_lanes(c);bag,day,legs,count,receipt,treatment,report=recovery_source(c,user,bid,tid)
        def perform():
            verify_recovery(body,count,receipt)
            row=next((r for r in treatment['recoveries'] if r['id']==recovery_id),None)
            if not row or row['status']!='pending':raise db.error(409,'RECOVERY_NOT_PENDING','طلب التحصيل أو الرد ليس معلقًا.')
            db.audit(c,user['id'],ft.RECOVERY_REJECTED,'cash_bag',bid,{'recovery_id':recovery_id,'treatment_id':tid,'note':body.note,'requested_by':row['requested_by'],'no_money_posted':True})
            return {'id':recovery_id,'status':'rejected','treatment_id':tid}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'cash_bag.recovery_reject:'+bid+':'+tid+':'+recovery_id,body.model_dump(),perform)


@router.get('/{bid}/custody-review/{tid}/recovery-sources')
def recovery_sources(bid:str,tid:str,direction:str,user=Depends(auth.current_user)):
    if direction not in {'receivable_recovery','payable_settlement'}:raise db.error(422,'RECOVERY_DIRECTION','حدد اتجاه التسوية.')
    with db.tx() as c:
        bag,day,legs,count,receipt,treatment,report=recovery_source(c,user,bid,tid)
        left=ft.remaining_by_direction(treatment)[direction];sign=1 if direction=='receivable_recovery' else -1
        rows=c.execute("""SELECT e.*,u.display_name actor_name,p.display_name custodian_name,s.branch_ids
          FROM custody_events e JOIN shifts s ON s.id=e.shift_id JOIN users u ON u.id=e.actor_id JOIN users p ON p.id=s.user_id
          WHERE s.brand_id=%s AND e.created_at>=%s AND e.amount_halalas*%s>0 AND e.amount_halalas*%s<=%s
          AND e.kind=ANY(%s) AND NOT EXISTS(SELECT 1 FROM audit_events a WHERE a.action=%s AND a.details->>'cash_custody_event_id'=e.id)
          ORDER BY e.created_at DESC,e.id DESC LIMIT 500""",(report['brand_id'],report['created_at'],sign,sign,left['cash'],
            ['collection','direct_receipt'] if sign==1 else ['refund','expense'],ft.RECOVERY_APPROVED)).fetchall()
        return ops.safe({'bank_matching_enabled':False,'sources':[{k:r[k] for k in ('id','amount_halalas','created_at','actor_name','custodian_name','reference_id','kind')}
            for r in rows if set(r['branch_ids'])<=set(report['branch_ids'])]})
