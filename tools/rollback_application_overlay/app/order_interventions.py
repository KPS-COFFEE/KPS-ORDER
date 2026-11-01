"""Staged, evidence-bound delivery decisions on the immutable order log.

A proposal/approval is not a refund. Canonical cancellation still owns financial,
physical-goods and shift guards. Names never grant cancellation authority.
"""
import os
from typing import Literal
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from . import auth, db, collected_order_authority

router=APIRouter(prefix='/api/order-interventions',tags=['order-interventions'])
REASONS={'customer_cancel':'طلب العميل الإلغاء','customer_change':'طلب العميل التعديل',
         'no_answer':'العميل لا يرد','late_complaint':'شكوى تأخير','wrong_address':'مشكلة في العنوان',
         'damaged':'مشكلة في الطلب','branch_unavailable':'تعذر تنفيذ الفرع','duplicate':'طلب مكرر'}
CONTACT={'customer_requested':'طلب العميل داخل الموقع','agreed':'تم التواصل ووافق',
         'declined':'تم التواصل ولم يوافق','no_answer':'لا يرد','not_contacted':'لم يتم التواصل'}
CUSTOMER_REASONS={'customer_cancel','customer_change','duplicate'}
PENDING_ROLES={'delivery_manager':'مدير التوصيل','owner_or_cancel_delegate':'المالك أو المفوض بالإلغاء',
               'owner_or_collected_cancel_delegate':'المالك أو المفوض بإلغاء الطلبات المحصلة','accounting':'المحاسب',
               'owner':'المالك','admin':'المراجع المفوض','reviewer':'المراجع الذي أعاد المعاملة'}
STATUSES={'requested':'بانتظار المراجعة','returned':'معاد للاستكمال','resubmitted':'أعيد رفعه للاعتماد',
          'approved':'معتمد','rejected':'مرفوض','resolved':'تمت المعالجة'}
Reason=Literal['customer_cancel','customer_change','no_answer','late_complaint','wrong_address','damaged','branch_unavailable','duplicate']
ContactResult=Literal['agreed','declined','no_answer','not_contacted']


def controlled(o):
    return (o.get('service')=='delivery'
            and ((o.get('snapshot') or {}).get('journey_controls_version')==1
                 or os.getenv('DELIVERY_INTERVENTIONS_ENFORCED','false').lower()=='true')
            and (o.get('snapshot') or {}).get('delivery_period')!='morning')


def terminal(o):
    return bool(o.get('completed_at') or o['preparation_status'] in ('cancelled','rejected','completed')
                or o.get('delivery_status') in ('delivered','cancelled'))


def high_authority(user):
    return user.get('role')=='owner' or (user.get('role') in ('admin','delivery_manager')
        and auth.has_permission(user,'orders_cancel_after_print'))


def qualified_cancel_delegate(user):
    """Explicit operational authority across service types, never accounting."""
    return bool(user.get('active',True) and user.get('role')=='admin'
        and auth.has_admin_read_grant(user,'orders_view')
        and auth.has_permission(user,'orders_cancel_after_print')
        and collected_order_authority.authorized(user))


def _require_scope(c,o,user):
    allowed=bool(o and auth.can_access_order(user,o,c))
    # An explicit cancellation grant gives only scoped intervention access;
    # it does not make an admin a delivery manager or a financial approver.
    if o and user.get('role')=='admin' and high_authority(user):
        from .morning_manager_context import order_allowed
        allowed=allowed or bool(user.get('active',True) and o['brand_id']==user['brand_id']
                     and o['branch_id'] in user.get('branch_ids',[]) and order_allowed(user,o,c)
                     and (controlled(o) or qualified_cancel_delegate(user)))
    if not allowed:raise db.error(404,'ORDER_NOT_FOUND','الطلب غير متاح ضمن الصلاحيات.')
    return o


def scoped(c,oid,user,lock=False):
    o=c.execute('SELECT * FROM orders WHERE id=%s'+(' FOR UPDATE' if lock else ''),(oid,)).fetchone()
    return _require_scope(c,o,user)


def live_scope(c,oid,user,*,lock=False):
    """Order first, then current actor grants; no session can retain a revoked grant."""
    o=scoped(c,oid,user,lock)
    actor=collected_order_authority.current_actor(c,user,o['brand_id'],lock=lock)
    _require_scope(c,o,actor)
    return o,actor


def printed(c,o):
    # ANY queued command counts, even uncertain/cancelled/reprinted jobs.
    return bool(c.execute('SELECT 1 FROM branch_print_jobs WHERE order_id=%s LIMIT 1',(o['id'],)).fetchone())


def effective_pending_role(details,has_print,has_collection=False):
    if details.get('kind')=='cancel':
        if has_collection:return 'owner_or_collected_cancel_delegate'
        if has_print:return 'owner_or_cancel_delegate'
    if details.get('escalate'):return 'owner_or_cancel_delegate'
    return details['pending_role']


def workflow_events(c,request_ids,order_ids):
    """Batch related immutable events, shared by the detail and staff inbox views."""
    found={rid:[] for rid in request_ids}
    if not found:return found
    rows=c.execute("""SELECT e.id,e.event_type,e.actor_id,e.created_at,e.details,u.display_name actor_name,
        COALESCE(e.details->>'request_id',a.details->>'request_id') workflow_request_id
        FROM order_events e LEFT JOIN users u ON u.id=e.actor_id
        LEFT JOIN order_events a ON e.event_type='financial_case.resolved' AND a.order_id=e.order_id
            AND a.event_type='intervention.accounting_pending' AND a.details->>'case_id'=e.details->>'case_id'
        WHERE e.order_id=ANY(%s) AND COALESCE(e.details->>'request_id',a.details->>'request_id')=ANY(%s)
        AND e.event_type IN ('intervention.returned','intervention.resubmitted','intervention.approved',
            'intervention.rejected','intervention.resolved','intervention.accounting_pending','financial_case.resolved')
        ORDER BY CASE e.event_type WHEN 'financial_case.resolved' THEN 2 WHEN 'intervention.accounting_pending' THEN 1 ELSE 0 END,
            COALESCE((e.details->>'workflow_revision')::int,0),e.created_at,e.id""",(list(set(order_ids)),list(found))).fetchall()
    for row in rows:found[row['workflow_request_id']].append(row)
    return found


def records(c,oid):
    rows=c.execute("""SELECT e.id,e.actor_id,e.created_at,e.details,u.display_name requested_by_name,
       d.actor_id decided_by,d.created_at decided_at,d.details decision,
       EXISTS(SELECT 1 FROM order_events a WHERE a.order_id=e.order_id
          AND a.event_type='intervention.accounting_pending' AND a.details->>'request_id'=e.id
          AND NOT EXISTS(SELECT 1 FROM order_events f WHERE f.order_id=e.order_id
              AND f.event_type='financial_case.resolved' AND f.details->>'case_id'=a.details->>'case_id')) financial_pending
       FROM order_events e LEFT JOIN users u ON u.id=e.actor_id LEFT JOIN LATERAL
       (SELECT actor_id,created_at,details FROM order_events d WHERE d.order_id=e.order_id
        AND d.event_type IN ('intervention.approved','intervention.rejected','intervention.resolved')
        AND d.details->>'request_id'=e.id ORDER BY d.created_at,d.id LIMIT 1) d ON true
       WHERE e.order_id=%s AND e.event_type='intervention.requested' ORDER BY e.created_at,e.id""",(oid,)).fetchall()
    related=workflow_events(c,[r['id'] for r in rows],[oid])
    return [{**r,'_workflow_events':related[r['id']]} for r in rows]


def workflow_state(r):
    """Project corrections without changing the original request or its proof."""
    details=dict(r['details']);revision=0;status='requested';last_return=None
    for event in r.get('_workflow_events',[]):
        d=event['details']
        revision=max(revision,d.get('workflow_revision',0))
        if event['event_type']=='intervention.returned':
            status='returned';last_return={**d,'reviewer_id':event['actor_id'],'reviewer_name':event.get('actor_name')}
            details['reason_code']=d['reason_code']
        elif event['event_type']=='intervention.resubmitted':
            status='resubmitted'
            # Resubmission validates fresh evidence against the current order
            # revision. Do not carry evidence from a superseded explanation.
            for key in ('customer_request_id','evidence_attachment_id','evidence_message_id','customer_message_confirmed'):
                details.pop(key,None)
            details.update(d.get('proof') or {})
            details.update(reason_code=d['reason_code'],contact_result=d['contact_result'],
                           revision=d['order_amendment_revision'])
    if r.get('decision'):status=r['decision']['status']
    return {'revision':revision,'status':status,'details':details,'last_return':last_return}


def can_review(user):
    return bool(user.get('active',True) and (high_authority(user) or
        user.get('role')=='delivery_manager' and auth.has_permission(user,'orders_cancel')))


def require_reviewer(user):
    if not can_review(user):
        raise db.error(403,'INTERVENTION_APPROVER','صلاحية اعتماد المعالجة متوقفة.')


def can_complete(user,kind):
    if user.get('role')=='owner':return True
    if user.get('role')!='delivery_manager':return False
    permission='orders_cancel' if kind=='cancel' else 'orders_view'
    return bool(auth.has_permission(user,permission) and (high_authority(user) or auth.has_permission(user,'orders_view')))


def workflow_view(r,user,has_print,has_collection=False):
    state=workflow_state(r);d=state['details'];returned=state['last_return'] or {}
    terminal=bool(r.get('decision'));is_returned=state['status']=='returned'
    financial_pending=bool(r.get('financial_pending'))
    responsible=('accounting' if financial_pending else None) if terminal else ('delivery_manager' if is_returned else
        effective_pending_role(d,has_print,has_collection))
    responsible_id=returned.get('target_manager_id') if is_returned else None
    responsible_name=returned.get('target_manager_name') if is_returned else None
    if state['status']=='resubmitted':
        # This is the recorded handoff, never a permission grant. If printing or
        # collection requires a higher reviewer, keep that explicit escalation.
        same_reviewer=(d['kind']!='cancel' or
            (not has_print or returned.get('reviewer_after_print')) and
            (not has_collection or returned.get('reviewer_collected')))
        if same_reviewer:
            responsible_id=returned.get('reviewer_id');responsible_name=returned.get('reviewer_name')
            if responsible=='delivery_manager':responsible=returned.get('reviewer_role') or 'reviewer'
    reviewer=can_review(user) and (not d.get('escalate') or high_authority(user)) and not (d['kind']=='cancel' and has_print and not high_authority(user))
    actions=[]
    if not terminal:
        if is_returned:
            if can_complete(user,d['kind']) and (user.get('role')=='owner' or user['id']==returned.get('target_manager_id')):
                actions.append('resubmit')
            if reviewer:actions.append('return')
        elif reviewer:
            if d['kind']=='cancel':
                if not has_collection or collected_order_authority.authorized(user):actions.append('approve')
            else:actions.append('resolve')
            actions.extend(['reject','return'])
    next_action=('استكمال الملاحظة وإعادة رفع المعاملة للاعتماد' if is_returned else
                 'مراجعة سبب الإلغاء والأدلة واتخاذ قرار الاعتماد' if d['kind']=='cancel' else
                 'استكمال معالجة التعديل ثم إقفال المتابعة' if d['kind']=='amend' else
                 'معالجة البلاغ وتوثيق الإجراء المتخذ') if not terminal else (
                 'المحاسبة: مطابقة التحصيل والاسترداد الفعلي وإقفال الملف المالي' if financial_pending else
                 'اكتملت المعالجة التشغيلية')
    history=[{'id':r['id'],'event_type':'intervention.requested','created_at':r['created_at'],
              'actor_id':r['actor_id'],'actor_name':r.get('requested_by_name'),
              'reason_code':r['details']['reason_code'],'note':r['details'].get('note'),'revision':0}]
    for event in r.get('_workflow_events',[]):
        e=event['details']
        history.append({'id':event['id'],'event_type':event['event_type'],'created_at':event['created_at'],
            'actor_id':event['actor_id'],'actor_name':event.get('actor_name'),'reason_code':e.get('reason_code'),
            'note':e.get('note') or e.get('reason'),'target_manager_id':e.get('target_manager_id'),
            'target_manager_name':e.get('target_manager_name'),'revision':e.get('workflow_revision',0)})
    return {'revision':state['revision'],'status':state['status'],
        'original_reason_code':r['details']['reason_code'],'reason_code':d['reason_code'],
        'current_responsible_role':responsible,
        'current_responsible_user_id':responsible_id,
        'current_responsible_name':responsible_name,
        'next_action_label':next_action,'return_note':returned.get('note'),
        'financial_pending':financial_pending,
        'preferred_return_target_id':returned.get('target_manager_id') or r['actor_id'],
        'available_actions':actions,'history':history}


def return_targets(c,o,kind=None):
    """Only real active managers within the original order's current brand scope."""
    from . import multibrand
    targets=[]
    membership=" OR EXISTS(SELECT 1 FROM staff_brand_memberships m WHERE m.user_id=u.id AND m.brand_id=%s AND m.active)" if multibrand.enabled() else ''
    params=[o['brand_id']]+([o['brand_id']] if membership else [])
    users=c.execute("SELECT u.* FROM users u WHERE u.active AND u.role='delivery_manager' AND (u.brand_id=%s"+
        membership+") ORDER BY u.id",params).fetchall()
    for u in users:
        actor=multibrand.project(c,u,o['brand_id'])
        if (actor and auth.can_access_order(actor,o,c)
                and can_complete(actor,kind or 'cancel')):
            targets.append({'id':actor['id'],'display_name':actor['display_name']})
    return targets


def require_workflow_revision(body,state):
    if body.workflow_revision!=state['revision'] and not (body.workflow_revision is None and state['revision']==0):
        raise db.error(409,'INTERVENTION_WORKFLOW_CONFLICT','تغيرت متابعة المعاملة؛ حدّث التفاصيل قبل الإجراء.')


def guard_cancellation(c,o,user):
    # All canonical employee cancellation paths call this under the order lock,
    # including legacy/morning/pickup paths outside staged interventions.
    actor=collected_order_authority.current_actor(c,user,o['brand_id'],lock=True)
    _require_scope(c,o,actor)
    if actor['role']=='admin' and not controlled(o) and not qualified_cancel_delegate(actor):
        raise db.error(403,'CANCELLATION_DELEGATE_REQUIRED','يلزم التفويض الصريح الكامل لإلغاء هذا الطلب.')
    if not auth.has_permission(actor,'orders_cancel') and not high_authority(actor):
        raise db.error(403,'STAFF_PERMISSION_FORBIDDEN','صلاحية إلغاء الطلبات متوقفة.')
    collected_authority=collected_order_authority.require(c,o,actor,lock=True)
    if collected_authority:
        db.event(c,o['id'],actor['id'],'order.collected_cancellation_authorized',collected_authority)
    if not controlled(o):return
    if printed(c,o) and not high_authority(actor):
        raise db.error(403,'CANCEL_AFTER_PRINT_AUTHORITY','بعد إرسال الطباعة، الإلغاء من المالك أو المفوض بالإلغاء فقط.')
    approval=c.execute("""SELECT d.id,d.actor_id,d.details FROM order_events d
       JOIN order_events r ON r.id=d.details->>'request_id' AND r.order_id=d.order_id
       WHERE d.order_id=%s AND d.event_type='intervention.approved'
         AND r.event_type='intervention.requested' AND r.details->>'kind'='cancel'
         AND COALESCE((d.details->>'order_amendment_revision')::int,(r.details->>'revision')::int)=%s AND d.actor_id=%s
         AND NOT EXISTS(SELECT 1 FROM order_events x WHERE x.order_id=d.order_id
           AND x.event_type='intervention.executed' AND x.details->>'approval_id'=d.id)
       ORDER BY d.created_at DESC,d.id DESC LIMIT 1""",(o['id'],o.get('amendment_revision',0),user['id'])).fetchone()
    if not approval:raise db.error(409,'CANCELLATION_REQUEST_REQUIRED','ارفع طلب إلغاء موثق ثم اعتمده من مسار المعالجة.')
    db.event(c,o['id'],user['id'],'intervention.executed',{'approval_id':approval['id'],
        'request_id':approval['details']['request_id'],'kind':'cancel','financial_approval':False,
        'collected_authority':collected_authority})


class RequestBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind:Literal['cancel','amend','problem']
    reason_code:Reason
    contact_result:ContactResult='not_contacted'
    customer_request_id:str|None=Field(None,max_length=100)
    evidence_attachment_id:str|None=Field(None,max_length=100)
    evidence_message_id:str|None=Field(None,max_length=100)
    customer_message_confirmed:bool=False
    escalate:bool=False
    amendment_id:str|None=Field(None,max_length=100)
    version:StrictInt=Field(ge=1)


def customer_message_access(c,user,message):
    # Scoped order access is checked by the caller. Keep the same explicit
    # supervisory read grant in both current and prior chat implementations.
    from .chat import allowed
    if (auth.has_admin_read_grant(user,'chat_view') and user['brand_id']==message['brand_id']
            and message['branch_id'] in user.get('branch_ids',[])):
        return True
    return allowed(user,message,c)


def evidence(c,o,user,body):
    source='delivery_center' if o['snapshot'].get('order_source')=='delivery_center' else 'website'
    result={'source':source}
    if body.reason_code not in CUSTOMER_REASONS:
        # Operational causes are explicit, never relabelled as customer consent.
        return result
    if body.evidence_message_id:
        # Staff explicitly selects the customer's own message and attests that
        # its request matches this action. No synthetic customer consent/event.
        if not body.customer_message_confirmed:
            raise db.error(422,'CUSTOMER_MESSAGE_CONFIRMATION_REQUIRED','أكد أن رسالة العميل تطلب الإجراء المختار صراحة.')
        m=c.execute('''SELECT v.*,m.id evidence_id,m.text FROM chat_messages m JOIN conversations v ON v.id=m.conversation_id
            JOIN users u ON u.id=m.sender_id
            WHERE m.id=%s AND v.order_id=%s AND v.brand_id=%s AND u.role='customer'
            AND (m.sender_id=%s OR EXISTS(SELECT 1 FROM customer_identity_links l
                WHERE l.brand_id=%s AND l.source_id=%s AND l.customer_id=m.sender_id))''',
            (body.evidence_message_id,o['id'],o['brand_id'],o['customer_id'],o['brand_id'],o['customer_id'])).fetchone()
        if not m or not m['text'].strip() or not customer_message_access(c,user,m):
            raise db.error(422,'CUSTOMER_REQUEST_REQUIRED','اختر رسالة العميل من محادثة هذا الطلب المتاحة ضمن صلاحياتك.')
        result.update(evidence_message_id=m['evidence_id'],customer_message_confirmed=True)
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
    """Reviewers see approvals; managers see only work explicitly returned to them."""
    reviewer=high_authority(user)
    assignee=user['role']=='delivery_manager' and auth.has_permission(user,'orders_view')
    if not user.get('active',True) or not (reviewer or assignee):
        raise db.error(403,'INTERVENTION_INBOX_AUTHORITY','قائمة المعالجة للمراجع المخول أو مدير التوصيل المكلف بالاستكمال.')
    with db.tx() as c:
        rows=c.execute("""SELECT o.*,e.id request_id,e.actor_id requested_by,e.created_at requested_at,e.details request_details,
            u.display_name requested_by_name,b.name branch_name,
            EXISTS(SELECT 1 FROM branch_print_jobs p WHERE p.order_id=o.id) has_print,
            (EXISTS(SELECT 1 FROM collections p WHERE p.order_id=o.id AND p.amount_halalas>0)
             OR EXISTS(SELECT 1 FROM branch_online_receipts p WHERE p.order_id=o.id AND p.amount_halalas>0)) has_receipt
            FROM order_events e JOIN orders o ON o.id=e.order_id
            LEFT JOIN users u ON u.id=e.actor_id JOIN branches b ON b.id=o.branch_id
            LEFT JOIN LATERAL (SELECT w.event_type,w.details FROM order_events w WHERE w.order_id=e.order_id
                AND w.details->>'request_id'=e.id AND w.event_type IN ('intervention.returned','intervention.resubmitted')
                ORDER BY (w.details->>'workflow_revision')::int DESC,w.created_at DESC,w.id DESC LIMIT 1) w ON true
            WHERE o.brand_id=%s AND o.service='delivery'
            AND (o.snapshot->>'delivery_period' IS DISTINCT FROM 'morning')
            AND (o.snapshot->>'journey_controls_version'='1' OR %s)
            AND (%s OR o.branch_id=ANY(%s))
            AND e.event_type='intervention.requested'
            AND ((%s AND (e.details->>'kind'='cancel' OR e.details->>'escalate'='true' OR w.event_type IN ('intervention.returned','intervention.resubmitted')))
                OR (w.event_type='intervention.returned' AND w.details->>'target_manager_id'=%s
                    AND (e.details->>'kind'<>'cancel' OR %s)))
            AND NOT EXISTS(SELECT 1 FROM order_events d WHERE d.order_id=e.order_id
                AND d.event_type IN ('intervention.approved','intervention.rejected','intervention.resolved')
                AND d.details->>'request_id'=e.id)
            ORDER BY e.created_at,e.id LIMIT 200""",(user['brand_id'],
            os.getenv('DELIVERY_INTERVENTIONS_ENFORCED','false').lower()=='true',
            user['role']=='owner' and not user.get('home_brand_id'),user.get('branch_ids',[]),
            reviewer,user['id'],auth.has_permission(user,'orders_cancel'))).fetchall()
        visible=[o for o in rows if user['role']=='admin' or auth.can_access_order(user,o,c)]
        related=workflow_events(c,[o['request_id'] for o in visible],[o['id'] for o in visible])
        requests=[]
        for o in visible:
            r={'id':o['request_id'],'actor_id':o['requested_by'],'created_at':o['requested_at'],
               'details':o['request_details'],'requested_by_name':o['requested_by_name'],
               'decision':None,'_workflow_events':related[o['request_id']]}
            flow=workflow_view(r,user,o['has_print'],o['has_receipt'] or o['payment_status'] in collected_order_authority.PAID_STATES)
            requests.append({'id':r['id'],'order_id':o['id'],'order_no':o['order_no'],
                'brand_id':o['brand_id'],'branch_id':o['branch_id'],'branch_name':o['branch_name'],
                'created_at':r['created_at'],'requested_by_name':r['requested_by_name'],
                'kind':r['details']['kind'],'kind_label':{'cancel':'طلب إلغاء','amend':'طلب تعديل','problem':'بلاغ ومتابعة'}[r['details']['kind']],
                'reason_label':REASONS.get(flow['reason_code']),
                'pending_label':flow['current_responsible_name'] or PENDING_ROLES.get(flow['current_responsible_role']),
                'workflow':flow})
        from .operations import safe
        return safe({'requests':requests})


@router.get('/orders/{oid}')
def read(oid:str,user=Depends(auth.require_roles('owner','delivery_manager','admin','customer','driver'))):
    require_access(user)
    with db.tx() as c:
        o=scoped(c,oid,user)
        rows=records(c,oid)
        has_print=printed(c,o)
        has_collection=collected_order_authority.has_recorded_collection(c,o)
        create_kinds=[]
        if controlled(o) and not terminal(o) and not any(not r['decision'] for r in rows):
            if user['role']=='owner':create_kinds=['cancel','problem']
            elif user['role']=='delivery_manager' and auth.has_permission(user,'orders_view'):
                create_kinds=(['cancel'] if auth.has_permission(user,'orders_cancel') else [])+['problem']
            elif user['role']=='driver' and auth.has_permission(user,'tasks_view'):create_kinds=['problem']
        if user['role']=='customer':
            rows=[{'id':r['id'],'created_at':r['created_at'],'kind':r['details']['kind'],
                   'status':(r['decision'] or {}).get('status','requested')} for r in rows]
        attachments=[];customer_requests=[];customer_messages=[];targets=[]
        if user['role'] in ('owner','delivery_manager','admin'):
            active_kind=next((r['details']['kind'] for r in rows if not r['decision']),None)
            targets=return_targets(c,o,active_kind)
            projected=[]
            for r in rows:
                flow=workflow_view(r,user,has_print,has_collection)
                if flow['preferred_return_target_id'] not in {t['id'] for t in targets}:
                    flow['preferred_return_target_id']=None
                item={k:v for k,v in r.items() if k!='_workflow_events'}
                if not r['decision']:
                    item['details']={**r['details'],'pending_role':flow['current_responsible_role']}
                projected.append({**item,'workflow':flow})
            rows=projected
            attachments=c.execute('''SELECT a.id,a.created_at FROM chat_attachments a JOIN conversations v ON v.id=a.conversation_id
                WHERE v.order_id=%s AND a.data IS NOT NULL AND a.content_type IN ('image/jpeg','image/png','image/webp') ORDER BY a.created_at DESC LIMIT 50''',(oid,)).fetchall()
            customer_requests=c.execute("SELECT id,created_at,details FROM order_events WHERE order_id=%s AND event_type IN ('customer_request.opened','intervention.customer_amend_requested') AND details ? 'evidence_message_id' ORDER BY created_at DESC LIMIT 50",(oid,)).fetchall()
            message_rows=c.execute('''SELECT v.*,m.id evidence_id,m.text,m.created_at message_created_at FROM chat_messages m
                JOIN conversations v ON v.id=m.conversation_id JOIN users u ON u.id=m.sender_id
                WHERE v.order_id=%s AND v.brand_id=%s AND u.role='customer'
                AND (m.sender_id=%s OR EXISTS(SELECT 1 FROM customer_identity_links l
                    WHERE l.brand_id=%s AND l.source_id=%s AND l.customer_id=m.sender_id))
                ORDER BY m.created_at DESC,m.id DESC LIMIT 100''',
                (oid,o['brand_id'],o['customer_id'],o['brand_id'],o['customer_id'])).fetchall()
            conversation_access={}
            for m in message_rows:
                if not m['text'].strip():continue
                if m['id'] not in conversation_access:
                    conversation_access[m['id']]=customer_message_access(c,user,m)
                if conversation_access[m['id']]:
                    customer_messages.append({'id':m['evidence_id'],'text':m['text'],'created_at':m['message_created_at']})
        elif user['role']=='driver':
            rows=[{'id':r['id'],'created_at':r['created_at'],'details':{'kind':r['details']['kind'],'pending_role':effective_pending_role(r['details'],has_print,has_collection),'reason_code':r['details']['reason_code'],'contact_result':r['details'].get('contact_result')},'decision':{'status':(r['decision'] or {}).get('status')} if r['decision'] else None} for r in rows if r['actor_id']==user['id']]
        from .operations import safe
        return safe({'enabled':controlled(o),'printed':has_print,'version':o['version'],
            'high_authority':high_authority(user),'requests':rows,'reasons':REASONS,'contact_results':CONTACT,
            'pending_roles':PENDING_ROLES,'statuses':STATUSES,'attachments':attachments,'customer_requests':customer_requests,'customer_messages':customer_messages,
            'return_targets':targets,'has_recorded_collection':has_collection,
            'available_create_kinds':create_kinds,
            'collected_cancellation_authority':collected_order_authority.authorized(user),
            'legacy_cancel_allowed':not controlled(o) and (user['role']=='owner' or qualified_cancel_delegate(user))
                and not terminal(o)})


@router.post('/orders/{oid}')
def create(oid:str,body:RequestBody,user=Depends(auth.require_roles('owner','delivery_manager','driver')),
           idempotency_key:str|None=Header(None)):
    require_access(user)
    if user['role']!='driver' and not auth.has_permission(user,'orders_cancel' if body.kind=='cancel' else 'orders_view'):
        raise db.error(403,'STAFF_PERMISSION_FORBIDDEN','صلاحية المعالجة غير متاحة.')
    with db.tx() as c:
        _,current=live_scope(c,oid,user)
        if current['role'] not in ('owner','delivery_manager','driver'):
            raise db.error(403,'ROLE_FORBIDDEN','رفع المعالجة من مسؤول الخدمة أو السائق المكلف فقط.')
        require_access(current)
        if current['role']!='driver' and not auth.has_permission(current,'orders_cancel' if body.kind=='cancel' else 'orders_view'):
            raise db.error(403,'STAFF_PERMISSION_FORBIDDEN','صلاحية المعالجة غير متاحة.')
        def perform():
            o,actor=live_scope(c,oid,user,lock=True)
            if actor['role'] not in ('owner','delivery_manager','driver'):
                raise db.error(403,'ROLE_FORBIDDEN','رفع المعالجة من مسؤول الخدمة أو السائق المكلف فقط.')
            require_access(actor)
            if not controlled(o):raise db.error(409,'JOURNEY_CONTROL_LEGACY','هذا الطلب يتبع مسار المعالجة السابق.')
            if o['version']!=body.version:raise db.error(409,'ORDER_VERSION_CONFLICT','تغير الطلب؛ حدّث التفاصيل.')
            if terminal(o):
                raise db.error(409,'INTERVENTION_TERMINAL','الطلب منتهٍ؛ استخدم المعالجة المالية المعتمدة.')
            if any(not r['decision'] for r in records(c,oid)):
                raise db.error(409,'INTERVENTION_PENDING','يوجد إجراء مفتوح؛ أكمل متابعته أولًا.')
            if actor['role']=='driver' and body.reason_code in CUSTOMER_REASONS:
                raise db.error(422,'DRIVER_OPERATIONAL_REASON_ONLY','اختر سبب المشكلة؛ طلب العميل بالإلغاء أو التعديل يوثقه مدير التوصيل.')
            if actor['role']=='driver' and body.kind!='problem':
                raise db.error(403,'DRIVER_PROBLEM_ONLY','السائق يرفع بلاغًا؛ لا يلغي أو يعدل الطلب.')
            if actor['role']!='driver' and not auth.has_permission(actor,'orders_cancel' if body.kind=='cancel' else 'orders_view'):
                raise db.error(403,'STAFF_PERMISSION_FORBIDDEN','صلاحية المعالجة غير متاحة.')
            proof=evidence(c,o,actor,body) if actor['role']!='driver' else {'source':'driver_problem'}
            if body.kind=='amend':
                a=c.execute('SELECT id FROM order_amendments WHERE id=%s AND order_id=%s',(body.amendment_id,oid)).fetchone()
                if not a:raise db.error(422,'AMENDMENT_REQUIRED','اربط طلب التعديل بتعديل الأصناف أو العنوان المطلوب.')
            pending=effective_pending_role({'kind':body.kind,'escalate':body.escalate,'pending_role':'delivery_manager'},
                printed(c,o),collected_order_authority.has_recorded_collection(c,o))
            details={**body.model_dump(),**proof,'revision':o.get('amendment_revision',0),
                'pending_role':pending,'requester_id':actor['id'],'financial_approval':False}
            rid=db.new_id('intervention_')
            c.execute("INSERT INTO order_events(id,order_id,actor_id,event_type,details) VALUES(%s,%s,%s,'intervention.requested',%s)",(rid,oid,user['id'],db.json(details)))
            db.audit(c,user['id'],'intervention.requested','order',oid,{'request_id':rid,**details})
            return {'id':rid,'status':'requested','pending_role':details['pending_role']}
        return db.idempotent(c,user['id'],idempotency_key,'intervention.request:'+oid,body.model_dump(),perform)


class WorkflowAction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    version:StrictInt=Field(ge=1)
    workflow_revision:StrictInt=Field(ge=0)
    note:str=Field(min_length=3,max_length=1500)

    @field_validator('note')
    @classmethod
    def explanatory_note(cls,value):
        value=value.strip()
        if len(value)<3 or '\x00' in value:raise ValueError('سجل توضيحًا صحيحًا للإجراء.')
        return value


class ReturnBody(WorkflowAction):
    corrected_reason_code:Reason
    target_manager_id:str=Field(min_length=1,max_length=100)


class ResubmitBody(WorkflowAction):
    evidence_message_id:str|None=Field(None,max_length=100)
    customer_message_confirmed:bool=False
    contact_result:ContactResult='not_contacted'
    customer_request_id:str|None=Field(None,max_length=100)
    evidence_attachment_id:str|None=Field(None,max_length=100)


def request_record(c,oid,rid):
    r=next((row for row in records(c,oid) if row['id']==rid),None)
    if not r:raise db.error(404,'INTERVENTION_NOT_FOUND','طلب المعالجة غير موجود.')
    return r


def require_print_reviewer(c,o,r,actor):
    require_reviewer(actor)
    if r['details'].get('escalate') and not high_authority(actor):
        raise db.error(403,'INTERVENTION_ESCALATED','المعاملة مصعدة للمالك أو المفوض؛ يلزم اعتماده.')
    if r['details']['kind']=='cancel' and printed(c,o) and not high_authority(actor):
        raise db.error(403,'CANCEL_AFTER_PRINT_AUTHORITY','الإلغاء بعد إرسال الطباعة للمالك أو المفوض فقط.')


def require_open_workflow(o,r,body):
    if r['decision']:raise db.error(409,'INTERVENTION_DECIDED','الإجراء معالج سابقًا.')
    if o['version']!=body.version:raise db.error(409,'ORDER_VERSION_CONFLICT','تغير الطلب؛ حدّث التفاصيل.')
    if terminal(o):
        raise db.error(409,'INTERVENTION_TERMINAL','الطلب منتهٍ؛ استخدم المعالجة المالية المعتمدة.')
    state=workflow_state(r)
    require_workflow_revision(body,state)
    return state


@router.post('/orders/{oid}/{rid}/return')
def return_request(oid:str,rid:str,body:ReturnBody,user=Depends(auth.require_roles('owner','delivery_manager','admin')),
                   idempotency_key:str|None=Header(None)):
    with db.tx() as c:
        visible,current=live_scope(c,oid,user)
        require_print_reviewer(c,visible,request_record(c,oid,rid),current)
        def perform():
            o,actor=live_scope(c,oid,user,lock=True)
            r=request_record(c,oid,rid)
            require_print_reviewer(c,o,r,actor)
            state=require_open_workflow(o,r,body)
            target=next((t for t in return_targets(c,o,r['details']['kind']) if t['id']==body.target_manager_id),None)
            if not target:raise db.error(422,'INTERVENTION_RETURN_TARGET','اختر مدير توصيل فعالًا ومخولًا ضمن فرع الطلب.')
            details={'request_id':rid,'workflow_revision':state['revision']+1,'status':'returned',
                'original_reason_code':r['details']['reason_code'],'previous_reason_code':state['details']['reason_code'],
                'reason_code':body.corrected_reason_code,'note':body.note,'target_manager_id':target['id'],
                'target_manager_name':target['display_name'],'current_responsible_role':'delivery_manager',
                'reviewer_role':actor['role'],'reviewer_after_print':high_authority(actor),
                'reviewer_collected':collected_order_authority.authorized(actor),
                'order_version':o['version'],'order_amendment_revision':o.get('amendment_revision',0),
                'financial_approval':False}
            db.event(c,oid,actor['id'],'intervention.returned',details)
            db.audit(c,actor['id'],'intervention.returned','order',oid,details)
            return {'id':rid,'status':'returned','workflow_revision':details['workflow_revision']}
        return db.idempotent(c,user['id'],idempotency_key,'intervention.return:'+rid,body.model_dump(),perform)


def require_resubmitter(user,r):
    state=workflow_state(r)
    target=(state['last_return'] or {}).get('target_manager_id')
    if (not can_complete(user,r['details']['kind']) or
            user.get('role')!='owner' and target!=user['id']):
        raise db.error(403,'INTERVENTION_RESUBMITTER','استكمال المعاملة لمدير التوصيل المكلف بها أو المالك فقط.')


@router.post('/orders/{oid}/{rid}/resubmit')
def resubmit_request(oid:str,rid:str,body:ResubmitBody,user=Depends(auth.require_roles('owner','delivery_manager')),
                     idempotency_key:str|None=Header(None)):
    with db.tx() as c:
        _,current=live_scope(c,oid,user)
        require_resubmitter(current,request_record(c,oid,rid))
        def perform():
            o,actor=live_scope(c,oid,user,lock=True)
            r=request_record(c,oid,rid)
            require_resubmitter(actor,r)
            state=require_open_workflow(o,r,body)
            if state['status']!='returned':
                raise db.error(409,'INTERVENTION_NOT_RETURNED','المعاملة ليست معادة للاستكمال.')
            reason=state['details']['reason_code']
            proof=evidence(c,o,actor,RequestBody(kind=r['details']['kind'],reason_code=reason,
                contact_result=body.contact_result,version=o['version'],customer_request_id=body.customer_request_id,
                evidence_attachment_id=body.evidence_attachment_id,evidence_message_id=body.evidence_message_id,
                customer_message_confirmed=body.customer_message_confirmed))
            details={'request_id':rid,'workflow_revision':state['revision']+1,'status':'resubmitted',
                'original_reason_code':r['details']['reason_code'],'reason_code':reason,'note':body.note,
                'contact_result':body.contact_result,'proof':proof,
                'target_manager_id':(state['last_return'] or {})['target_manager_id'],
                'target_manager_name':(state['last_return'] or {})['target_manager_name'],
                'order_version':o['version'],'order_amendment_revision':o.get('amendment_revision',0),
                'financial_approval':False}
            db.event(c,oid,actor['id'],'intervention.resubmitted',details)
            db.audit(c,actor['id'],'intervention.resubmitted','order',oid,details)
            return {'id':rid,'status':'resubmitted','workflow_revision':details['workflow_revision']}
        return db.idempotent(c,user['id'],idempotency_key,'intervention.resubmit:'+rid,body.model_dump(),perform)


class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['approve','reject','resolve']
    reason_code:Reason
    contact_result:ContactResult
    version:StrictInt=Field(ge=1)
    workflow_revision:StrictInt|None=Field(None,ge=0)
    physical_resolution:Literal['returned_to_branch','customer_kept']|None=None


@router.post('/orders/{oid}/{rid}/decision')
def decide(oid:str,rid:str,body:Decision,user=Depends(auth.require_roles('owner','delivery_manager','admin')),
           idempotency_key:str|None=Header(None)):
    require_reviewer(user)
    with db.tx() as c:
        visible,current=live_scope(c,oid,user)
        require_print_reviewer(c,visible,request_record(c,oid,rid),current)
        if body.action=='approve':collected_order_authority.require(c,visible,current)
        def perform():
            o,actor=live_scope(c,oid,user,lock=True)
            r=request_record(c,oid,rid)
            require_print_reviewer(c,o,r,actor)
            if body.action=='approve':collected_order_authority.require(c,o,actor,lock=True)
            if r['decision']:raise db.error(409,'INTERVENTION_DECIDED','الإجراء معالج سابقًا.')
            state=workflow_state(r);effective=state['details']
            require_workflow_revision(body,state)
            if state['status']=='returned':
                raise db.error(409,'INTERVENTION_RETURNED','المعاملة لدى مدير التوصيل للاستكمال؛ يجب إعادة رفعها قبل القرار.')
            if o['version']!=body.version or (body.action!='reject' and effective['revision']!=o.get('amendment_revision',0)):
                raise db.error(409,'ORDER_VERSION_CONFLICT','تغير الطلب؛ راجع المعالجة قبل الاعتماد.')
            kind=r['details']['kind']
            if body.reason_code!=effective['reason_code']:
                raise db.error(422,'INTERVENTION_REASON_CHANGED','سبب القرار يجب أن يطابق السبب الحالي الموثق؛ أعد المعاملة للاستكمال عند تغير السبب.')
            if body.action=='approve' and kind!='cancel':
                raise db.error(409,'INTERVENTION_USE_EXISTING_FLOW','اعتماد التعديل من مسار التعديل؛ البلاغ يحال أو يقفل بعد المعالجة.')
            if body.action=='resolve' and kind=='amend':
                amendment=c.execute('SELECT status FROM order_amendments WHERE id=%s AND order_id=%s',(r['details'].get('amendment_id'),oid)).fetchone()
                if not amendment or amendment['status'] not in ('applied','rejected','withdrawn'):
                    raise db.error(409,'AMENDMENT_NOT_FINISHED','أكمل مراجعة التعديل واعتماد نسخته أو رفضه من مسار التعديل أولًا.')
            status={'approve':'approved','reject':'rejected','resolve':'resolved'}[body.action]
            if body.action=='resolve' and kind=='cancel':
                raise db.error(409,'CANCELLATION_REQUIRED','طلب الإلغاء لا يقفل دون تنفيذ الإلغاء المعتمد.')
            proof=None
            if body.action=='approve' and body.reason_code in CUSTOMER_REASONS:
                proof=evidence(c,o,actor,RequestBody(kind=kind,reason_code=body.reason_code,
                    contact_result=body.contact_result,version=o['version'],
                    customer_request_id=effective.get('customer_request_id'),
                    evidence_attachment_id=effective.get('evidence_attachment_id'),evidence_message_id=effective.get('evidence_message_id') if effective.get('customer_message_confirmed') else None,
                    customer_message_confirmed=effective.get('customer_message_confirmed',False)))
            details={'request_id':rid,'status':status,'reason_code':body.reason_code,
                'original_reason_code':r['details']['reason_code'],'workflow_revision':state['revision']+1,
                'order_amendment_revision':effective['revision'],
                'contact_result':body.contact_result,'actor_id':actor['id'],'financial_approval':False}
            if proof:details['proof']=proof
            db.event(c,oid,actor['id'],'intervention.'+status,details)
            if body.action=='approve':
                from .order_financial_cases import Cancellation,cancel_locked,cancellation_scope
                cancellation_scope(c,oid,actor)
                result=cancel_locked(c,oid,Cancellation(version=o['version'],reason=REASONS[body.reason_code],physical_resolution=body.physical_resolution),actor)
                details['financial_pending']=bool(result.get('financial_case'))
                if details['financial_pending']:
                    db.event(c,oid,user['id'],'intervention.accounting_pending',{'request_id':rid,
                        'case_id':result['financial_case'].get('id'),'pending_role':'accounting',
                        'status':'requested','reason_code':body.reason_code,'contact_result':body.contact_result,
                        'financial_approval':False})
            if body.action=='reject' and effective.get('customer_request_id'):
                db.event(c,oid,actor['id'],'customer_request.decided',{'request_id':effective['customer_request_id'],'status':'rejected','note':REASONS[body.reason_code]})
            db.audit(c,user['id'],'intervention.'+status,'order',oid,details)
            return details
        payload=body.model_dump()
        # Preserve the original digest of a legacy decision retried after deploy.
        if body.workflow_revision is None:payload.pop('workflow_revision')
        return db.idempotent(c,user['id'],idempotency_key,'intervention.decision:'+rid,payload,perform)


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
    found=c.execute("""SELECT DISTINCT ON (e.order_id) e.order_id,e.id,e.actor_id,e.created_at,e.details,
       u.display_name requested_by_name,o.payment_status,
       EXISTS(SELECT 1 FROM branch_print_jobs p WHERE p.order_id=e.order_id) has_print,
       (EXISTS(SELECT 1 FROM collections p WHERE p.order_id=e.order_id AND p.amount_halalas>0)
        OR EXISTS(SELECT 1 FROM branch_online_receipts p WHERE p.order_id=e.order_id AND p.amount_halalas>0)) has_receipt
       FROM order_events e JOIN orders o ON o.id=e.order_id LEFT JOIN users u ON u.id=e.actor_id
       WHERE e.order_id=ANY(%s) AND e.event_type='intervention.requested'
       AND NOT EXISTS(SELECT 1 FROM order_events d WHERE d.order_id=e.order_id
           AND d.event_type IN ('intervention.approved','intervention.rejected','intervention.resolved')
           AND d.details->>'request_id'=e.id)
       ORDER BY e.order_id,e.created_at,e.id""",([o['id'] for o in orders],)).fetchall()
    related=workflow_events(c,[r['id'] for r in found],[r['order_id'] for r in found])
    pending={}
    for r in found:
        flow=workflow_view({**r,'decision':None,'_workflow_events':related[r['id']]},{'role':None,'id':None},
            r['has_print'],r['has_receipt'] or r['payment_status'] in collected_order_authority.PAID_STATES)
        pending[r['order_id']]={'id':r['id'],'since':r['created_at'],'kind':r['details']['kind'],
            'pending_role':flow['current_responsible_role'],
            'pending_label':flow['current_responsible_name'] or PENDING_ROLES.get(flow['current_responsible_role']),
            'reason_code':flow['reason_code'],'reason_label':REASONS.get(flow['reason_code']),
            'workflow':flow}
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
    if user.get('role')!='admin' or not high_authority(user) or not (controlled(o) or qualified_cancel_delegate(user)):return False
    if (not user.get('active',True) or user.get('brand_id')!=o['brand_id']
            or o['branch_id'] not in user.get('branch_ids',[]) or not shift
            or shift.get('brand_id')!=o['brand_id'] or shift.get('kind')!='driver'
            or o['branch_id'] not in (shift.get('branch_ids') or [])
            or any(t['order_id']!=o['id'] or t['shift_id']!=shift['id']
                   or t['driver_id']!=shift['user_id'] for t in tasks)):
        raise db.error(403,'DELEGATED_CANCELLATION_SCOPE','مهمة الإلغاء خارج نطاق التفويض.')
    return True
