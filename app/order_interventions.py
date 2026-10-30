"""Staged, evidence-bound delivery decisions on the immutable order log.

A proposal/approval is not a refund. Canonical cancellation still owns financial,
physical-goods and shift guards. Names never grant cancellation authority.
"""
import os
from typing import Literal
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from . import auth, db

router=APIRouter(prefix='/api/order-interventions',tags=['order-interventions'])
REASONS={'customer_cancel':'طلب العميل الإلغاء','customer_change':'طلب العميل التعديل',
         'no_answer':'العميل لا يرد','late_complaint':'شكوى تأخير','wrong_address':'مشكلة في العنوان',
         'damaged':'مشكلة في الطلب','branch_unavailable':'تعذر تنفيذ الفرع','duplicate':'طلب مكرر'}
CONTACT={'customer_requested':'طلب العميل داخل الموقع','agreed':'تم التواصل ووافق',
         'declined':'تم التواصل ولم يوافق','no_answer':'لا يرد','not_contacted':'لم يتم التواصل'}
CUSTOMER_REASONS={'customer_cancel','customer_change','duplicate'}
PENDING_ROLES={'delivery_manager':'مدير التوصيل','owner_or_cancel_delegate':'المالك أو المفوض بالإلغاء','accounting':'المحاسب'}
STATUSES={'requested':'بانتظار المراجعة','approved':'معتمد','rejected':'مرفوض','resolved':'تمت المعالجة'}


def controlled(o):
    return (o.get('service')=='delivery'
            and ((o.get('snapshot') or {}).get('journey_controls_version')==1
                 or os.getenv('DELIVERY_INTERVENTIONS_ENFORCED','false').lower()=='true')
            and (o.get('snapshot') or {}).get('delivery_period')!='morning')


def high_authority(user):
    return user.get('role')=='owner' or (user.get('role') in ('admin','delivery_manager')
        and auth.has_permission(user,'orders_cancel_after_print'))


def scoped(c,oid,user,lock=False):
    o=c.execute('SELECT * FROM orders WHERE id=%s'+(' FOR UPDATE' if lock else ''),(oid,)).fetchone()
    allowed=bool(o and auth.can_access_order(user,o,c))
    # An explicit cancellation grant gives only scoped intervention access;
    # it does not make an admin a delivery manager or a financial approver.
    if o and user.get('role')=='admin' and high_authority(user):
        allowed=bool(user.get('active',True) and o['brand_id']==user['brand_id']
                     and o['branch_id'] in user.get('branch_ids',[]) and controlled(o))
    if not allowed:raise db.error(404,'ORDER_NOT_FOUND','الطلب غير متاح ضمن الصلاحيات.')
    return o


def printed(c,o):
    # ANY queued command counts, even uncertain/cancelled/reprinted jobs.
    return bool(c.execute('SELECT 1 FROM branch_print_jobs WHERE order_id=%s LIMIT 1',(o['id'],)).fetchone())


def effective_pending_role(details,has_print):
    return 'owner_or_cancel_delegate' if details.get('kind')=='cancel' and has_print else details['pending_role']


def records(c,oid):
    return c.execute("""SELECT e.id,e.actor_id,e.created_at,e.details,
       d.actor_id decided_by,d.created_at decided_at,d.details decision
       FROM order_events e LEFT JOIN LATERAL
       (SELECT actor_id,created_at,details FROM order_events d WHERE d.order_id=e.order_id
        AND d.event_type IN ('intervention.approved','intervention.rejected','intervention.resolved')
        AND d.details->>'request_id'=e.id ORDER BY d.created_at,d.id LIMIT 1) d ON true
       WHERE e.order_id=%s AND e.event_type='intervention.requested' ORDER BY e.created_at,e.id""",(oid,)).fetchall()


def guard_cancellation(c,o,user):
    if not controlled(o):return
    if printed(c,o) and not high_authority(user):
        raise db.error(403,'CANCEL_AFTER_PRINT_AUTHORITY','بعد إرسال الطباعة، الإلغاء من المالك أو المفوض بالإلغاء فقط.')
    approval=c.execute("""SELECT d.id,d.actor_id,d.details FROM order_events d
       JOIN order_events r ON r.id=d.details->>'request_id' AND r.order_id=d.order_id
       WHERE d.order_id=%s AND d.event_type='intervention.approved'
         AND r.event_type='intervention.requested' AND r.details->>'kind'='cancel'
         AND (r.details->>'revision')::int=%s AND d.actor_id=%s
         AND NOT EXISTS(SELECT 1 FROM order_events x WHERE x.order_id=d.order_id
           AND x.event_type='intervention.executed' AND x.details->>'approval_id'=d.id)
       ORDER BY d.created_at DESC,d.id DESC LIMIT 1""",(o['id'],o.get('amendment_revision',0),user['id'])).fetchone()
    if not approval:raise db.error(409,'CANCELLATION_REQUEST_REQUIRED','ارفع طلب إلغاء موثق ثم اعتمده من مسار المعالجة.')
    db.event(c,o['id'],user['id'],'intervention.executed',{'approval_id':approval['id'],
        'request_id':approval['details']['request_id'],'kind':'cancel','financial_approval':False})


class RequestBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['cancel','amend','problem']
    reason_code:Literal['customer_cancel','customer_change','no_answer','late_complaint','wrong_address','damaged','branch_unavailable','duplicate']
    contact_result:Literal['agreed','declined','no_answer','not_contacted']='not_contacted'
    customer_request_id:str|None=Field(None,max_length=100)
    evidence_attachment_id:str|None=Field(None,max_length=100)
    amendment_id:str|None=Field(None,max_length=100)
    version:StrictInt=Field(ge=1)


def evidence(c,o,user,body):
    source='delivery_center' if o['snapshot'].get('order_source')=='delivery_center' else 'website'
    result={'source':source}
    if body.reason_code not in CUSTOMER_REASONS:
        # Operational causes are explicit, never relabelled as customer consent.
        return result
    if source=='delivery_center':
        a=c.execute('''SELECT a.id FROM chat_attachments a JOIN conversations v ON v.id=a.conversation_id
            WHERE a.id=%s AND v.order_id=%s AND v.brand_id=%s AND a.data IS NOT NULL
            AND a.content_type IN ('image/jpeg','image/png','image/webp')''',
            (body.evidence_attachment_id,o['id'],o['brand_id'])).fetchone()
        if not a:raise db.error(422,'CONVERSATION_IMAGE_REQUIRED','أرفق صورة المحادثة من محادثة هذا الطلب لإثبات طلب العميل.')
        result['evidence_attachment_id']=a['id']
    else:
        r=c.execute("""SELECT e.id,e.actor_id,e.details FROM order_events e JOIN users u ON u.id=e.actor_id
            WHERE e.id=%s AND e.order_id=%s AND e.event_type IN ('customer_request.opened','intervention.customer_amend_requested')
            AND u.role='customer' AND u.brand_id=%s AND (e.actor_id=%s OR EXISTS(SELECT 1 FROM customer_identity_links l WHERE l.brand_id=%s AND l.source_id=%s AND l.customer_id=e.actor_id))""",(body.customer_request_id,o['id'],o['brand_id'],o['customer_id'],o['brand_id'],o['customer_id'])).fetchone()
        if not r or not r['details'].get('evidence_message_id') or (body.kind=='cancel' and r['details'].get('kind') not in ('cancel','duplicate')) or (body.kind=='amend' and r['details'].get('kind')!='amend'):
            raise db.error(422,'CUSTOMER_REQUEST_REQUIRED','يلزم طلب العميل الصريح المسجل داخل محادثة الموقع.')
        if r['details'].get('revision')!=o.get('amendment_revision',0):
            raise db.error(409,'CUSTOMER_REQUEST_REVISION','طلب العميل يخص نسخة سابقة؛ يلزم طلب جديد.')
        if c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='customer_request.decided' AND details->>'request_id'=%s",(o['id'],r['id'])).fetchone():
            raise db.error(409,'CUSTOMER_REQUEST_DECIDED','تمت معالجة طلب العميل سابقًا؛ يلزم طلب جديد.')
        m=c.execute('''SELECT m.id FROM chat_messages m JOIN conversations v ON v.id=m.conversation_id
            WHERE m.id=%s AND v.order_id=%s AND m.sender_id=%s''',
            (r['details']['evidence_message_id'],o['id'],r['actor_id'])).fetchone()
        if not m:raise db.error(422,'CUSTOMER_REQUEST_REQUIRED','محادثة طلب العميل غير متاحة.')
        result.update(customer_request_id=r['id'],evidence_message_id=m['id'])
    return result


@router.get('/pending-cancellations')
def pending_cancellations(user=Depends(auth.require_roles('owner','delivery_manager','admin'))):
    """A cancellation-only inbox needs no general orders or accounting grant."""
    if not user.get('active',True) or not high_authority(user):
        raise db.error(403,'CANCEL_AFTER_PRINT_AUTHORITY','قائمة الاعتماد للمالك أو المفوض بالإلغاء فقط.')
    with db.tx() as c:
        rows=c.execute("""SELECT o.*,e.id request_id,e.created_at requested_at,e.details request_details,
            b.name branch_name,EXISTS(SELECT 1 FROM branch_print_jobs p WHERE p.order_id=o.id) has_print FROM order_events e JOIN orders o ON o.id=e.order_id
            JOIN branches b ON b.id=o.branch_id WHERE o.brand_id=%s AND o.service='delivery'
            AND (o.snapshot->>'delivery_period' IS DISTINCT FROM 'morning')
            AND (o.snapshot->>'journey_controls_version'='1' OR %s)
            AND (%s OR o.branch_id=ANY(%s))
            AND e.event_type='intervention.requested' AND e.details->>'kind'='cancel'
            AND NOT EXISTS(SELECT 1 FROM order_events d WHERE d.order_id=e.order_id
                AND d.event_type IN ('intervention.approved','intervention.rejected','intervention.resolved')
                AND d.details->>'request_id'=e.id)
            ORDER BY e.created_at,e.id LIMIT 200""",(user['brand_id'],
            os.getenv('DELIVERY_INTERVENTIONS_ENFORCED','false').lower()=='true',
            user['role']=='owner' and not user.get('home_brand_id'),user.get('branch_ids',[]))).fetchall()
        visible=[o for o in rows if user['role']=='admin' or auth.can_access_order(user,o,c)]
        from .operations import safe
        return safe({'requests':[{'id':o['request_id'],'order_id':o['id'],'order_no':o['order_no'],
            'branch_name':o['branch_name'],'created_at':o['requested_at'],
            'reason_label':REASONS.get(o['request_details']['reason_code']),
            'pending_label':PENDING_ROLES.get(effective_pending_role(o['request_details'],o['has_print']))} for o in visible]})


@router.get('/orders/{oid}')
def read(oid:str,user=Depends(auth.require_roles('owner','delivery_manager','admin','customer','driver'))):
    require_access(user)
    with db.tx() as c:
        o=scoped(c,oid,user)
        rows=records(c,oid)
        has_print=printed(c,o)
        rows=[{**r,'details':{**r['details'],'pending_role':effective_pending_role(r['details'],has_print)}}
              if not r['decision'] else r for r in rows]
        if user['role']=='customer':
            rows=[{'id':r['id'],'created_at':r['created_at'],'kind':r['details']['kind'],
                   'status':(r['decision'] or {}).get('status','requested')} for r in rows]
        attachments=[];customer_requests=[]
        if user['role'] in ('owner','delivery_manager','admin'):
            attachments=c.execute('''SELECT a.id,a.created_at FROM chat_attachments a JOIN conversations v ON v.id=a.conversation_id
                WHERE v.order_id=%s AND a.data IS NOT NULL AND a.content_type IN ('image/jpeg','image/png','image/webp') ORDER BY a.created_at DESC LIMIT 50''',(oid,)).fetchall()
            customer_requests=c.execute("SELECT id,created_at,details FROM order_events WHERE order_id=%s AND event_type IN ('customer_request.opened','intervention.customer_amend_requested') AND details ? 'evidence_message_id' ORDER BY created_at DESC LIMIT 50",(oid,)).fetchall()
        elif user['role']=='driver':
            rows=[{'id':r['id'],'created_at':r['created_at'],'details':{'kind':r['details']['kind'],'pending_role':r['details']['pending_role'],'reason_code':r['details']['reason_code'],'contact_result':r['details'].get('contact_result')},'decision':{'status':(r['decision'] or {}).get('status')} if r['decision'] else None} for r in rows if r['actor_id']==user['id']]
        from .operations import safe
        return safe({'enabled':controlled(o),'printed':has_print,'version':o['version'],
            'high_authority':high_authority(user),'requests':rows,'reasons':REASONS,'contact_results':CONTACT,
            'pending_roles':PENDING_ROLES,'statuses':STATUSES,'attachments':attachments,'customer_requests':customer_requests})


@router.post('/orders/{oid}')
def create(oid:str,body:RequestBody,user=Depends(auth.require_roles('owner','delivery_manager','driver')),
           idempotency_key:str|None=Header(None)):
    require_access(user)
    if user['role']!='driver' and not auth.has_permission(user,'orders_cancel' if body.kind=='cancel' else 'orders_view'):
        raise db.error(403,'STAFF_PERMISSION_FORBIDDEN','صلاحية المعالجة غير متاحة.')
    with db.tx() as c:
        scoped(c,oid,user)
        def perform():
            o=scoped(c,oid,user,True)
            if not controlled(o):raise db.error(409,'JOURNEY_CONTROL_LEGACY','هذا الطلب يتبع مسار المعالجة السابق.')
            if o['version']!=body.version:raise db.error(409,'ORDER_VERSION_CONFLICT','تغير الطلب؛ حدّث التفاصيل.')
            if o.get('completed_at') or o['preparation_status'] in ('cancelled','rejected','completed'):
                raise db.error(409,'INTERVENTION_TERMINAL','الطلب منتهٍ؛ استخدم المعالجة المالية المعتمدة.')
            if any(not r['decision'] for r in records(c,oid)):
                raise db.error(409,'INTERVENTION_PENDING','يوجد إجراء مفتوح؛ أكمل متابعته أولًا.')
            if user['role']=='driver' and body.reason_code in CUSTOMER_REASONS:
                raise db.error(422,'DRIVER_OPERATIONAL_REASON_ONLY','اختر سبب المشكلة؛ طلب العميل بالإلغاء أو التعديل يوثقه مدير التوصيل.')
            if user['role']=='driver' and body.kind!='problem':
                raise db.error(403,'DRIVER_PROBLEM_ONLY','السائق يرفع بلاغًا؛ لا يلغي أو يعدل الطلب.')
            if user['role']!='driver' and not auth.has_permission(user,'orders_cancel' if body.kind=='cancel' else 'orders_view'):
                raise db.error(403,'STAFF_PERMISSION_FORBIDDEN','صلاحية المعالجة غير متاحة.')
            proof=evidence(c,o,user,body) if user['role']!='driver' else {'source':'driver_problem'}
            if body.kind=='amend':
                a=c.execute('SELECT id FROM order_amendments WHERE id=%s AND order_id=%s',(body.amendment_id,oid)).fetchone()
                if not a:raise db.error(422,'AMENDMENT_REQUIRED','اربط طلب التعديل بتعديل الأصناف أو العنوان المطلوب.')
            details={**body.model_dump(),**proof,'revision':o.get('amendment_revision',0),
                'pending_role':'owner_or_cancel_delegate' if body.kind=='cancel' and printed(c,o) else 'delivery_manager',
                'requester_id':user['id'],'financial_approval':False}
            rid=db.new_id('intervention_')
            c.execute("INSERT INTO order_events(id,order_id,actor_id,event_type,details) VALUES(%s,%s,%s,'intervention.requested',%s)",(rid,oid,user['id'],db.json(details)))
            db.audit(c,user['id'],'intervention.requested','order',oid,{'request_id':rid,**details})
            return {'id':rid,'status':'requested','pending_role':details['pending_role']}
        return db.idempotent(c,user['id'],idempotency_key,'intervention.request:'+oid,body.model_dump(),perform)


class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['approve','reject','resolve']
    reason_code:Literal['customer_cancel','customer_change','no_answer','late_complaint','wrong_address','damaged','branch_unavailable','duplicate']
    contact_result:Literal['agreed','declined','no_answer','not_contacted']
    version:StrictInt=Field(ge=1)
    physical_resolution:Literal['returned_to_branch','customer_kept']|None=None


@router.post('/orders/{oid}/{rid}/decision')
def decide(oid:str,rid:str,body:Decision,user=Depends(auth.require_roles('owner','delivery_manager','admin')),
           idempotency_key:str|None=Header(None)):
    if not high_authority(user) and (user['role']!='delivery_manager' or not auth.has_permission(user,'orders_cancel')):
        raise db.error(403,'INTERVENTION_APPROVER','صلاحية اعتماد المعالجة متوقفة.')
    with db.tx() as c:
        scoped(c,oid,user)
        def perform():
            o=scoped(c,oid,user,True)
            r=next((r for r in records(c,oid) if r['id']==rid),None)
            if not r:raise db.error(404,'INTERVENTION_NOT_FOUND','طلب المعالجة غير موجود.')
            if r['decision']:raise db.error(409,'INTERVENTION_DECIDED','الإجراء معالج سابقًا.')
            if o['version']!=body.version or (body.action!='reject' and r['details']['revision']!=o.get('amendment_revision',0)):
                raise db.error(409,'ORDER_VERSION_CONFLICT','تغير الطلب؛ راجع المعالجة قبل الاعتماد.')
            kind=r['details']['kind']
            if body.reason_code!=r['details']['reason_code']:
                raise db.error(422,'INTERVENTION_REASON_CHANGED','سبب القرار يجب أن يطابق الطلب الموثق؛ ارفضه وارفع طلبًا جديدًا عند تغير السبب.')
            if printed(c,o) and kind=='cancel' and not high_authority(user):
                raise db.error(403,'CANCEL_AFTER_PRINT_AUTHORITY','الإلغاء بعد إرسال الطباعة للمالك أو المفوض فقط.')
            if not high_authority(user) and (user['role']!='delivery_manager' or not auth.has_permission(user,'orders_cancel')):
                raise db.error(403,'INTERVENTION_APPROVER','لا تملك صلاحية اعتماد هذه المعالجة.')
            if body.action=='approve' and kind!='cancel':
                raise db.error(409,'INTERVENTION_USE_EXISTING_FLOW','اعتماد التعديل من مسار التعديل؛ البلاغ يحال أو يقفل بعد المعالجة.')
            if body.action=='resolve' and kind=='amend':
                amendment=c.execute('SELECT status FROM order_amendments WHERE id=%s AND order_id=%s',(r['details'].get('amendment_id'),oid)).fetchone()
                if not amendment or amendment['status'] not in ('applied','rejected','withdrawn'):
                    raise db.error(409,'AMENDMENT_NOT_FINISHED','أكمل مراجعة التعديل واعتماد نسخته أو رفضه من مسار التعديل أولًا.')
            status={'approve':'approved','reject':'rejected','resolve':'resolved'}[body.action]
            if body.action=='resolve' and kind=='cancel':
                raise db.error(409,'CANCELLATION_REQUIRED','طلب الإلغاء لا يقفل دون تنفيذ الإلغاء المعتمد.')
            details={'request_id':rid,'status':status,'reason_code':body.reason_code,
                'contact_result':body.contact_result,'actor_id':user['id'],'financial_approval':False}
            db.event(c,oid,user['id'],'intervention.'+status,details)
            if body.action=='approve':
                from .order_financial_cases import Cancellation,cancel_locked,cancellation_scope
                cancellation_scope(c,oid,user)
                result=cancel_locked(c,oid,Cancellation(version=o['version'],reason=REASONS[body.reason_code],physical_resolution=body.physical_resolution),user)
                details['financial_pending']=bool(result.get('financial_case'))
                if details['financial_pending']:
                    db.event(c,oid,user['id'],'intervention.accounting_pending',{'request_id':rid,
                        'case_id':result['financial_case'].get('id'),'pending_role':'accounting',
                        'status':'requested','reason_code':body.reason_code,'contact_result':body.contact_result,
                        'financial_approval':False})
            if body.action=='reject' and r['details'].get('customer_request_id'):
                db.event(c,oid,user['id'],'customer_request.decided',{'request_id':r['details']['customer_request_id'],'status':'rejected','note':REASONS[body.reason_code]})
            db.audit(c,user['id'],'intervention.'+status,'order',oid,details)
            return details
        return db.idempotent(c,user['id'],idempotency_key,'intervention.decision:'+rid,body.model_dump(),perform)


def customer_message(c,o,user,kind,note,*,request_id=None):
    """Record an explicit authenticated customer action in the order conversation."""
    v=c.execute("SELECT id FROM conversations WHERE order_id=%s AND category='help' FOR UPDATE",(o['id'],)).fetchone()
    if v:cid=v['id']
    else:
        cid=db.new_id('conv_')
        c.execute("INSERT INTO conversations(id,order_id,brand_id,branch_id,customer_id,route,context_service) VALUES(%s,%s,%s,%s,%s,'delivery',%s)",(cid,o['id'],o['brand_id'],o['branch_id'],o['customer_id'],o['service']))
    mid=db.new_id('message_')
    label={'cancel':'أطلب إلغاء طلبي','duplicate':'أبلغ عن طلب مكرر','amend':'أطلب تعديل طلبي'}[kind]
    c.execute('INSERT INTO chat_messages(id,conversation_id,sender_id,text) VALUES(%s,%s,%s,%s)',(mid,cid,user['id'],label+' — '+note))
    c.execute("UPDATE conversations SET status='open' WHERE id=%s",(cid,))
    return mid


def apply_pending_customer_amendment(c,o,user,aid,q):
    """Before acceptance only; no staff intervention, paper, money or tasks."""
    if not (controlled(o) and user['role']=='customer' and o['preparation_status']=='new'
            and o['delivery_status']=='pending' and not printed(c,o)):
        return False
    from .order_amendments import balance
    if balance(c,o)!=0 or o['payment_status']!='unpaid':return False
    if c.execute("SELECT 1 FROM driver_tasks WHERE order_id=%s AND status NOT IN ('cancelled','reassigned','transferred')",(o['id'],)).fetchone():return False
    rev=o.get('amendment_revision',0)+1
    c.execute("UPDATE order_amendments SET status='applied',reviewed_by=%s,consent_by=%s,consent_channel='customer',consent_note='Customer edited before manager acceptance' WHERE id=%s",(user['id'],user['id'],aid))
    c.execute("INSERT INTO order_revisions(order_id,revision,amendment_id,before_snapshot,before_customer,after_snapshot,after_customer,before_total,after_total,approved_by,consent_by,consent_channel) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'customer')",(o['id'],rev,aid,db.json(o['snapshot']),db.json(o['customer']),db.json(q['snapshot']),db.json(q['customer']),o['total_halalas'],q['snapshot']['total_halalas'],user['id'],user['id']))
    c.execute("UPDATE orders SET snapshot=%s,customer=%s,subtotal_halalas=%s,delivery_fee_halalas=%s,total_halalas=%s,amendment_revision=%s,version=version+1 WHERE id=%s",(db.json(q['snapshot']),db.json(q['customer']),q['snapshot']['subtotal_halalas'],q['snapshot']['delivery_fee_halalas'],q['snapshot']['total_halalas'],rev,o['id']))
    db.event(c,o['id'],user['id'],'amendment.applied',{'amendment_id':aid,'source':'customer_before_acceptance','revision':rev})
    return True


def require_access(user):
    if user['role'] in ('owner','customer') or high_authority(user):return
    permission='tasks_view' if user['role']=='driver' else 'orders_view'
    if not auth.has_permission(user,permission):
        raise db.error(403,'STAFF_PERMISSION_FORBIDDEN','صلاحية عرض المعالجة متوقفة.')


def enrich_orders(c,orders):
    """Staff-only batch caller: current responsibility without N+1 reads."""
    if not orders:return
    found=c.execute("""SELECT DISTINCT ON (e.order_id) e.order_id,e.id,e.created_at,e.details,
       EXISTS(SELECT 1 FROM branch_print_jobs p WHERE p.order_id=e.order_id) has_print
       FROM order_events e WHERE e.order_id=ANY(%s) AND e.event_type='intervention.requested'
       AND NOT EXISTS(SELECT 1 FROM order_events d WHERE d.order_id=e.order_id
           AND d.event_type IN ('intervention.approved','intervention.rejected','intervention.resolved')
           AND d.details->>'request_id'=e.id)
       ORDER BY e.order_id,e.created_at,e.id""",([o['id'] for o in orders],)).fetchall()
    pending={r['order_id']:{'id':r['id'],'since':r['created_at'],'kind':r['details']['kind'],
        'pending_role':effective_pending_role(r['details'],r['has_print']),
        'pending_label':PENDING_ROLES.get(effective_pending_role(r['details'],r['has_print'])),
        'reason_code':r['details']['reason_code'],'reason_label':REASONS.get(r['details']['reason_code'])} for r in found}
    financial=c.execute("""SELECT DISTINCT ON (e.order_id) e.order_id,e.id,e.created_at,e.details FROM order_events e
       WHERE e.order_id=ANY(%s) AND e.event_type='intervention.accounting_pending'
       AND NOT EXISTS(SELECT 1 FROM order_events r WHERE r.order_id=e.order_id
           AND r.event_type='financial_case.resolved' AND r.details->>'case_id'=e.details->>'case_id')
       ORDER BY e.order_id,e.created_at DESC,e.id DESC""",([o['id'] for o in orders],)).fetchall()
    for r in financial:
        if r['order_id'] not in pending:
            pending[r['order_id']]={'id':r['id'],'since':r['created_at'],'kind':'financial',
                'pending_role':'accounting','pending_label':PENDING_ROLES['accounting'],
                'reason_code':r['details'].get('reason_code'),'reason_label':REASONS.get(r['details'].get('reason_code'))}
    for o in orders:o['intervention_pending']=pending.get(o['id'])


def delegated_cancel_shift_access(user,o,shift,tasks):
    """True grants this order's task cancellation only, never shift/cash authority."""
    if user.get('role')!='admin' or not high_authority(user) or not controlled(o):return False
    if (not user.get('active',True) or user.get('brand_id')!=o['brand_id']
            or o['branch_id'] not in user.get('branch_ids',[]) or not shift
            or shift.get('brand_id')!=o['brand_id'] or shift.get('kind')!='driver'
            or o['branch_id'] not in (shift.get('branch_ids') or [])
            or any(t['order_id']!=o['id'] or t['shift_id']!=shift['id']
                   or t['driver_id']!=shift['user_id'] for t in tasks)):
        raise db.error(403,'DELEGATED_CANCELLATION_SCOPE','مهمة الإلغاء خارج نطاق التفويض.')
    return True
