"""Actual bags stay separate. Operational release never asserts accounting receipt."""
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import Field, StrictBool, StrictInt
from . import auth, db, operations as ops, multibrand, central_workdays as days
from .aggregate_driver_closure import accounting, authorize, latest, validate_operational, physical_finished
router=APIRouter(prefix='/api/cash-bags',tags=['cash-bags'])
LOCAL_CITIES=frozenset({'حفر الباطن'})


def branch_destination(branch):
    from .catalog import normalize_city
    return 'manager_vault' if normalize_city(branch['city']) in LOCAL_CITIES else 'branch'


def create(c,user,day,legs,declaration,branch_id,reference,delivered):
    if not branch_id:return None  # Older clients keep their existing closure path.
    allowed={b for _,s in legs for b in s['branch_ids']}
    branch=c.execute('SELECT * FROM branches WHERE id=%s',(branch_id,)).fetchone()
    if not branch or branch_id not in allowed:raise db.error(403,'CASH_BAG_BRANCH','اختر فرعًا ضمن نطاق شفت السائق.')
    if not reference.strip():raise db.error(422,'CASH_BAG_REFERENCE','سجل رقمًا أو وصفًا يميز كيس هذا السائق.')
    destination=branch_destination(branch)
    if destination=='branch' and not delivered:raise db.error(422,'CASH_BAG_DELIVERY','أكد تسليم الكيس فعليًا للفرع.')
    bag=c.execute('INSERT INTO driver_cash_bags(id,workday_id,declaration_id,driver_id,branch_id,destination,reference,declared_cash_halalas,declared_card_halalas,recorded_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(ops.uid(),day['id'],declaration['id'],day['user_id'],branch_id,destination,reference.strip(),declaration['cash_halalas']-declaration['retained_halalas'],declaration['card_halalas'],user['id'])).fetchone()
    if destination=='branch':
        # Preserve the driver's actual retained float pending manager approval;
        # a bag declaration is not an accounting receipt or a new float grant.
        sources=[r for r in declaration['expected_snapshot'] if r['permanent_float_halalas']]
        for row in sources:
            if len(sources)==1 and declaration['retained_halalas']:
                c.execute('INSERT INTO retained_carryovers(id,from_shift_id,user_id,amount_halalas,approved_by,reason) VALUES(%s,%s,%s,%s,%s,%s)',(ops.uid(),row['shift_id'],day['user_id'],declaration['retained_halalas'],user['id'],'Declared retained cash at branch bag deposit '+bag['id']))
        if len(sources)>1 or declaration['retained_halalas']<sum(r['permanent_float_halalas'] for r in sources):
            from datetime import timedelta
            from .aggregate_driver_closure import accounting_followup
            db.audit(c,user['id'],'cash_bag.float_followup','central_workday',day['id'],{'bag_id':bag['id'],'declaration_id':declaration['id'],'responsible_user_id':accounting_followup(c,legs,user['id']),'due_at':(bag['deposited_at']+timedelta(hours=24)).isoformat(),'declared_retained_halalas':declaration['retained_halalas'],'no_employee_liability_assumed':True,'no_money_posted':True})
        for _,s in legs:c.execute('UPDATE shifts SET submission=submission||%s::jsonb WHERE id=%s',(db.json({'cash_bag_id':bag['id'],'physical_state':'bag_at_branch','financial_state':'awaiting_accounting_receipt','manager_approval_required':True}),s['id']))
    db.audit(c,user['id'],'cash_bag.deposited' if destination=='branch' else 'cash_bag.declared','cash_bag',bag['id'],{'driver_id':day['user_id'],'branch_id':branch_id,'destination':destination,'declared_cash_halalas':bag['declared_cash_halalas'],'accounting_receipt_recorded':False})
    return bag


def allocation_count(c,declaration_id):
    """A manager's receipt never substitutes for accounting's actual bag count.

    Older declarations without bag metadata retain their existing receipt path.
    This check runs under the same workday lane lock as allocation/count writes.
    """
    bag=c.execute('SELECT b.id,b.declared_card_halalas,n.id count_id,n.received_cash_halalas,n.card_halalas FROM driver_cash_bags b LEFT JOIN driver_cash_bag_counts n ON n.bag_id=b.id WHERE b.declaration_id=%s',(declaration_id,)).fetchone()
    if bag and not bag['count_id']:
        raise db.error(409,'CASH_BAG_ACCOUNTING_RECEIPT_REQUIRED','اسحب كيس السائق واستلمه وعده فعليًا في جرد الأكياس قبل الإسناد والإقفال المالي؛ استلام مدير التوصيل وحده لا يكفي.')
    if bag:
        receipt=c.execute('SELECT * FROM shared_driver_closure_receipts WHERE declaration_id=%s',(declaration_id,)).fetchone()
        if receipt and (bag['received_cash_halalas']!=receipt['received_halalas'] or bag['card_halalas']!=bag['declared_card_halalas']):
            from .cash_bag_custody import projection
            review=projection(c,bag['id'],{'id':bag['count_id'],'received_cash_halalas':bag['received_cash_halalas'],'card_halalas':bag['card_halalas'],'declared_card_halalas':bag['declared_card_halalas']},receipt)
            if not review['financial_treatment']:
                raise db.error(409,'CASH_BAG_CUSTODY_REVIEW_REQUIRED','فرق بين استلام المدير وجرد المحاسبة؛ ارفع معالجة عهدة مستقلة واعتمدها قبل الإسناد دون تغيير الأصل أو تحميل السائق تلقائيًا.')
    return bag


def bag_view(c,day_id):
    row=c.execute('SELECT b.*,s.submission,s.staffing_ended_at,s.delivery_operational_date shift_date,u.display_name driver_name,br.name branch_name,n.id count_id,n.received_cash_halalas,n.card_halalas,n.created_at counted_at,n.reference count_reference,n.note count_note,a.display_name counted_by_name FROM driver_cash_bags b JOIN central_workdays d ON d.id=b.workday_id JOIN shifts s ON s.id=d.anchor_shift_id JOIN users u ON u.id=b.driver_id JOIN branches br ON br.id=b.branch_id LEFT JOIN driver_cash_bag_counts n ON n.bag_id=b.id LEFT JOIN users a ON a.id=n.counted_by WHERE b.workday_id=%s AND NOT EXISTS (SELECT 1 FROM shared_driver_closure_returns r WHERE r.declaration_id=b.declaration_id) ORDER BY b.deposited_at DESC LIMIT 1',(day_id,)).fetchone()
    if not row:return None
    row=dict(row)
    submission=row.pop('submission') or {}
    approval=submission.get('manager_approval')
    legacy_finished=bool(row.pop('staffing_ended_at') and 'manager_approval_required' not in submission)
    row['manager_approval_required']=not legacy_finished and (row['destination']=='branch' or submission.get('manager_approval_required',False))
    row['manager_approval']=approval if approval and approval.get('declaration_id')==row['declaration_id'] else None
    row['operational_state']='released' if legacy_finished else 'awaiting_manager_approval' if row['manager_approval_required'] and not row['manager_approval'] else 'approved' if row['manager_approval'] else 'awaiting_manager_receipt'
    return row


class Count(ops.OperationBody):
    received_cash_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    card_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    reference:str=Field(min_length=1,max_length=200)
    note:str=Field(default='',max_length=2000)
    confirmed:StrictBool


@router.get('')
def inbox(user=Depends(auth.current_user)):
    if not accounting(user):raise db.error(403,'ACCOUNTANT_REQUIRED','جرد الأكياس للمحاسبة المخولة.')
    with db.tx() as c:
        days.require_enabled(user,'accounting_review')
        result=[]
        for b in c.execute('SELECT b.*,d.anchor_shift_id,u.display_name driver_name,br.name branch_name FROM driver_cash_bags b JOIN central_workdays d ON d.id=b.workday_id JOIN users u ON u.id=b.driver_id JOIN branches br ON br.id=b.branch_id WHERE NOT EXISTS (SELECT 1 FROM shared_driver_closure_returns r WHERE r.declaration_id=b.declaration_id) ORDER BY b.deposited_at DESC LIMIT 500').fetchall():
            try:day,legs=authorize(c,user,b['anchor_shift_id'])
            except HTTPException as exc:
                if exc.status_code in (403,404):continue
                raise
            row=bag_view(c,day['id'])
            if row['id']!=b['id']:continue
            row=dict(row,anchor_shift_id=b['anchor_shift_id'])
            receipt=c.execute('SELECT id FROM shared_driver_closure_receipts WHERE declaration_id=%s',(b['declaration_id'],)).fetchone()
            row['ready_to_count']=b['destination']=='branch' or bool(receipt)
            row['state']='counted' if row['count_id'] else 'awaiting_accounting_receipt' if row['ready_to_count'] else 'awaiting_manager_receipt'
            row['cash_difference_halalas']=row['received_cash_halalas']-b['declared_cash_halalas'] if row['count_id'] else None
            full_receipt=c.execute('SELECT * FROM shared_driver_closure_receipts WHERE declaration_id=%s',(b['declaration_id'],)).fetchone()
            row['accounting_custody_difference_halalas']=row['received_cash_halalas']-full_receipt['received_halalas'] if row['count_id'] and full_receipt else None
            row['accounting_custody_card_difference_halalas']=row['card_halalas']-row['declared_card_halalas'] if row['count_id'] else None
            row['accounting_custody_review_required']=False
            if row['accounting_custody_difference_halalas'] or row['accounting_custody_card_difference_halalas']:
                from .cash_bag_custody import projection
                row['custody_review']=projection(c,b['id'],{'id':row['count_id'],'received_cash_halalas':row['received_cash_halalas'],'card_halalas':row['card_halalas'],'declared_card_halalas':row['declared_card_halalas']},full_receipt)
                row['accounting_custody_review_required']=not row['custody_review']['period_approval_allowed']
            result.append(row)
        return ops.safe({'bags':result})


@router.post('/{bid}/count')
def count(bid:str,body:Count,request:Request,user=Depends(auth.current_user)):
    if not accounting(user):raise db.error(403,'ACCOUNTANT_REQUIRED','جرد الأكياس للمحاسبة المخولة.')
    with db.tx() as c:
        days.lock_lanes(c)
        bag=c.execute('SELECT b.*,d.anchor_shift_id FROM driver_cash_bags b JOIN central_workdays d ON d.id=b.workday_id WHERE b.id=%s FOR UPDATE OF b',(bid,)).fetchone()
        if not bag:raise db.error(404,'CASH_BAG_NOT_FOUND','الكيس غير موجود.')
        day,legs=authorize(c,user,bag['anchor_shift_id'])
        def perform():
            if user['id']==bag['driver_id']:raise db.error(403,'CASH_BAG_SELF_RECEIPT','السائق لا يؤكد استلام كيسه كمحاسب.')
            if not body.confirmed:raise db.error(422,'CASH_BAG_COUNT_CONFIRMATION','أكد سحب الكيس واستلام النقد وعدّه فعليًا.')
            if c.execute('SELECT 1 FROM driver_cash_bag_counts WHERE bag_id=%s',(bid,)).fetchone():raise db.error(409,'CASH_BAG_ALREADY_COUNTED','الجرد محفوظ؛ لا تسجله مرة ثانية.')
            receipt=c.execute('SELECT * FROM shared_driver_closure_receipts WHERE declaration_id=%s',(bag['declaration_id'],)).fetchone()
            if bag['destination']=='manager_vault' and not receipt:raise db.error(409,'CASH_BAG_MANAGER_RECEIPT','الكيس ينتظر استلام مدير التوصيل أولًا.')
            identifier=ops.uid()
            c.execute('INSERT INTO driver_cash_bag_counts(id,bag_id,received_cash_halalas,card_halalas,counted_by,reference,note) VALUES(%s,%s,%s,%s,%s,%s,%s)',(identifier,bid,body.received_cash_halalas,body.card_halalas,user['id'],body.reference,body.note))
            if not receipt:
                c.execute('INSERT INTO shared_driver_closure_receipts(id,declaration_id,received_halalas,received_by,manager_shift_ids,reference) VALUES(%s,%s,%s,%s,%s,%s)',(ops.uid(),bag['declaration_id'],body.received_cash_halalas,user['id'],db.json([s['manager_shift_id'] for _,s in legs]),body.reference))
            db.audit(c,user['id'],'cash_bag.counted','cash_bag',bid,{'count_id':identifier,'received_cash_halalas':body.received_cash_halalas,'cash_difference_halalas':body.received_cash_halalas-bag['declared_cash_halalas'],'card_difference_halalas':body.card_halalas-bag['declared_card_halalas'],'no_employee_liability_assumed':True,'bank_matching_complete':False})
            return {'count_id':identifier}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),'cash_bag.count:'+bid,body.model_dump(),perform)


class VaultClose(ops.OperationBody):
    confirmed:StrictBool
    note:str=Field(default='',max_length=2000)


@router.post('/workdays/{sid}/vault-close')
def vault_close(sid:str,body:VaultClose,request:Request,user=Depends(auth.current_user)):
    ops.roles(user,'owner','delivery_manager')
    if not auth.has_permission(user,'shifts_close'):raise db.error(403,'VAULT_CLOSE_FORBIDDEN','غير مخول لإنهاء المناوبة.')
    with db.tx() as c:
        days.require_enabled(user,'shifts_close',existing_sid=sid)
        days.lock_lanes(c);day,legs=days.authorized(c,user,sid)
        if day['kind']!='manager' or day['user_id']!=user['id']:raise db.error(403,'VAULT_CLOSE_OWN_ONLY','المدير ينهي مناوبته من حسابه.')
        def perform():
            if not body.confirmed:raise db.error(422,'VAULT_CLOSE_CONFIRMATION','أكد حفظ أكياس السائقين منفصلة في الخزنة.')
            if c.execute('SELECT 1 FROM manager_vault_closures WHERE workday_id=%s',(day['id'],)).fetchone():raise db.error(409,'VAULT_ALREADY_CLOSED','انتهاء المناوبة محفوظ.')
            for _,s in legs:
                if s['status'] not in {'open','closing','submitted'} or s.get('staffing_ended_at'):raise db.error(409,'VAULT_SHIFT_STATE','المناوبة منتهية أو غير متاحة.')
                ops.guard_accepted_awaiting_dispatch(c,s)
                if ops.unresolved(c,s['id']):raise db.error(409,'OPEN_TASKS','أكمل تسليم مسؤولية الطلبات النشطة أولًا.')
                if c.execute("SELECT 1 FROM shifts WHERE manager_shift_id=%s AND status<>'closed' AND staffing_ended_at IS NULL",(s['id'],)).fetchone():raise db.error(409,'DRIVER_BAG_PENDING','أكمل استلام أكياس السائقين أو اعتماد تقفيلات الأكياس المسلمة للفرع أولًا.')
                if c.execute("SELECT 1 FROM manager_handovers WHERE (from_shift_id=%s OR to_shift_id=%s) AND status='pending'",(s['id'],s['id'])).fetchone():raise db.error(409,'HANDOVER_PENDING','أكمل تسليم مسؤولية المناوبة المعلق.')
                if c.execute("SELECT 1 FROM custody_transfers WHERE (from_shift_id=%s OR to_shift_id=%s) AND status='pending'",(s['id'],s['id'])).fetchone():raise db.error(409,'RECEIPT_PENDING','أكد أو أعد تسليم النقد المعلق أولًا.')
            shifts=[s['id'] for _,s in legs]
            bags=c.execute("SELECT DISTINCT b.id FROM driver_cash_bags b JOIN shared_driver_closure_receipts r ON r.declaration_id=b.declaration_id JOIN central_workday_legs l ON l.workday_id=b.workday_id JOIN shifts s ON s.id=l.shift_id WHERE s.manager_shift_id=ANY(%s) AND b.destination='manager_vault'",(shifts,)).fetchall()
            entry=c.execute('INSERT INTO manager_vault_closures(id,workday_id,manager_id,bag_ids,note) VALUES(%s,%s,%s,%s,%s) RETURNING *',(ops.uid(),day['id'],user['id'],db.json([b['id'] for b in bags]),body.note)).fetchone()
            for _,s in legs:c.execute("UPDATE shifts SET status='submitted',submitted_at=coalesce(submitted_at,now()),staffing_ended_at=%s,submission=coalesce(submission,'{}'::jsonb)||%s::jsonb WHERE id=%s",(entry['created_at'],db.json({'vault_closure_id':entry['id'],'physical_state':'bags_in_vault','financial_state':'awaiting_accounting_bag_counts'}),s['id']))
            db.audit(c,user['id'],'manager.vault_closed','central_workday',day['id'],{'vault_closure_id':entry['id'],'bag_ids':[b['id'] for b in bags],'owner_receipt_required':False,'cash_received_by_accounting':False})
            return {'vault_closure_id':entry['id']}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),'manager.vault_close:'+day['id'],body.model_dump(),perform)


from .cash_bag_custody import router as custody_router
router.include_router(custody_router)
