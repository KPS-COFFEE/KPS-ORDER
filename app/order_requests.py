"""Customer cancellation/duplicate requests on the existing append-only order log.

Reading never acknowledges a request. A resolved cancellation requires the
existing cancellation workflow to have succeeded; no ledger or task bypass.
"""
from typing import Literal

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field

from . import auth, db
from .orders import scoped_order

router = APIRouter(prefix='/api/order-requests')
LABELS = {'cancel': 'طلب إلغاء', 'duplicate': 'طلب مكرر', 'amend':'طلب تعديل'}


class RequestBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['cancel', 'duplicate']
    note: str = Field(min_length=1, max_length=500)
    related_order_id: str | None = Field(default=None, max_length=100)


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['resolved', 'rejected', 'refer_financial']
    note: str = Field(min_length=1, max_length=500)


def rows(c, oid):
    found=c.execute("""SELECT e.id,e.created_at,e.details,u.display_name requester_name,
        d.details decision,d.created_at decided_at,d.decider_name
        FROM order_events e LEFT JOIN users u ON u.id=e.actor_id LEFT JOIN LATERAL (
          SELECT x.details,x.created_at,du.display_name decider_name FROM order_events x LEFT JOIN users du ON du.id=x.actor_id
          WHERE x.order_id=e.order_id AND x.event_type='customer_request.decided'
            AND x.details->>'request_id'=e.id ORDER BY x.created_at DESC,x.id DESC LIMIT 1
        ) d ON true WHERE e.order_id=%s AND e.event_type='customer_request.opened'
        ORDER BY e.created_at DESC LIMIT 100""", (oid,)).fetchall()
    if not found:return found
    referrals=c.execute("""SELECT e.details,e.created_at,EXISTS(SELECT 1 FROM order_events r
        WHERE r.order_id=e.order_id AND r.event_type='financial_case.resolved' AND r.details->>'case_id'=e.details->>'case_id') resolved
        FROM order_events e WHERE e.order_id=%s AND e.event_type='customer_request.referred' ORDER BY e.created_at DESC,e.id DESC""",(oid,)).fetchall()
    linked={}
    for referral in referrals:
        linked.setdefault(referral['details']['request_id'],{**referral['details'],'created_at':referral['created_at'],
            'status':'reviewed' if referral['resolved'] else 'pending'})
    for request in found:request['referral']=linked.get(request['id'])
    return found


def project_actions(c,o,user,requests):
    if not requests:
        return requests
    from .order_amendments import reviewer
    from .order_interventions import high_authority,terminal
    allowed=reviewer(o,user) and (auth.has_permission(user,'orders_cancel') or user.get('role')=='admin' and high_authority(user))
    cancelled=o['preparation_status']=='cancelled' or o['delivery_status']=='cancelled'
    for request in requests:
        actions=[]
        if allowed and not request['decision']:
            if not request.get('referral'):
                actions.append('rejected')
                if cancelled:actions.append('resolved')
                elif terminal(o):actions.append('refer_financial')
        request['available_actions']=actions
        request['pending_role']='accounting' if request.get('referral') and not request['decision'] else ('delivery_manager' if not request['decision'] else None)
        request['required_action']='مراجعة طلب العميل وتوثيق القرار المالي' if request.get('referral') and not request['decision'] else ('مراجعة طلب العميل واتخاذ الإجراء المناسب' if not request['decision'] else None)
    return requests


def pending_inbox(c, user):
    from .order_interventions import high_authority
    if not auth.has_permission(user, 'orders_view'):
        return []
    result = c.execute("""SELECT o.id,o.order_no,o.snapshot->>'order_reference' order_reference,o.service,e.id request_id,
        e.details->>'kind' request_kind,
        CASE WHEN EXISTS(SELECT 1 FROM order_events rf WHERE rf.order_id=o.id AND rf.event_type='customer_request.referred' AND rf.details->>'request_id'=e.id) THEN 'referred' ELSE 'requested' END status,
        CASE WHEN EXISTS(SELECT 1 FROM order_events rf WHERE rf.order_id=o.id AND rf.event_type='customer_request.referred' AND rf.details->>'request_id'=e.id) THEN 'accounting' ELSE 'delivery_manager' END current_responsible_role
        FROM orders o JOIN order_events e ON e.order_id=o.id
        WHERE o.brand_id=%s AND (%s OR o.branch_id=ANY(%s))
          AND (%s='owner' OR o.service=CASE WHEN %s='delivery_manager' THEN 'delivery' ELSE 'pickup' END
               OR (%s='branch_manager' AND o.service='car') OR (%s AND o.service='delivery'))
          AND e.event_type='customer_request.opened'
          AND NOT EXISTS(SELECT 1 FROM order_events d WHERE d.order_id=o.id
            AND d.event_type='customer_request.decided' AND d.details->>'request_id'=e.id)
        ORDER BY e.created_at LIMIT 100""",
        (user['brand_id'], user['role']=='owner' and not user.get('home_brand_id'), user.get('branch_ids', []),
         user['role'], user['role'], user['role'],user.get('role')=='admin' and high_authority(user))).fetchall()
    if user.get('_morning_manager_context'):
        from .morning_manager_context import order_allowed
        result=[r for r in result if order_allowed(user,c.execute('SELECT * FROM orders WHERE id=%s',(r['id'],)).fetchone(),c)]
    return result


@router.post('/orders/{oid}')
def create(oid: str, body: RequestBody, user=Depends(auth.require_roles('customer')),
           idempotency_key: str | None=Header(default=None)):
    with db.tx() as c:
        scoped_order(c, oid, user)
        def perform():
            o = scoped_order(c, oid, user, lock=True)
            if o['completed_at'] or o['preparation_status'] in ('completed','cancelled','rejected') or o['delivery_status'] in ('delivered','cancelled'):
                raise db.error(409, 'REQUEST_ORDER_CLOSED', 'الطلب منتهٍ؛ تواصل معنا من محادثة الطلب.')
            if not body.note.strip():
                raise db.error(422, 'REQUEST_NOTE', 'اكتب سبب الطلب.')
            if any(not r['decision'] for r in rows(c, oid)):
                raise db.error(409, 'REQUEST_PENDING', 'يوجد طلب بانتظار المعالجة لهذا الطلب.')
            if body.related_order_id:
                other = scoped_order(c, body.related_order_id, user)
                if body.kind != 'duplicate' or other['id'] == oid:
                    raise db.error(422, 'REQUEST_RELATED_ORDER', 'اختر الطلب الآخر المكرر.')
            if not db.rate_limit(c, 'order-request:'+user['id'], 10, 900):
                raise db.error(429, 'REQUEST_RATE', 'طلبات كثيرة؛ حاول لاحقًا.')
            rid = db.new_id('req_')
            details = {**body.model_dump(), 'note': body.note.strip()}
            from .order_interventions import controlled, customer_message, printed
            if controlled(o):
                details['evidence_message_id']=customer_message(c,o,user,body.kind,body.note.strip())
                details['pending_role']='delivery_manager'
                details['revision']=o.get('amendment_revision',0)

            c.execute("INSERT INTO order_events(id,order_id,actor_id,event_type,details) VALUES(%s,%s,%s,'customer_request.opened',%s)",
                      (rid, oid, user['id'], db.json(details)))
            db.audit(c, user['id'], 'customer_request.opened', 'order', oid, {'request_id':rid, **details})
            if controlled(o) and body.kind=='cancel' and o['preparation_status']=='new' and o['delivery_status']=='pending' and not printed(c,o):
                from .order_financial_cases import needs_accounting
                if not needs_accounting(c,o) and not c.execute("SELECT 1 FROM driver_tasks WHERE order_id=%s AND status NOT IN ('cancelled','reassigned','transferred')",(oid,)).fetchone():
                    c.execute("UPDATE orders SET preparation_status='cancelled',delivery_status='cancelled',version=version+1 WHERE id=%s",(oid,))
                    db.event(c,oid,user['id'],'customer_request.decided',{'request_id':rid,'status':'resolved','note':body.note.strip(),'source':'customer_before_acceptance','financial_status':'not_required'})
                    db.event(c,oid,user['id'],'order.operationally_cancelled',{'reason':body.note.strip(),'source':'customer_before_acceptance','customer_request_id':rid,'financial_status':'not_required'})
                    return {'id':rid,'status':'resolved','label':'تم الإلغاء قبل قبول المدير'}
            return {'id':rid, 'status':'requested', 'label':'بانتظار المعالجة'}
        return db.idempotent(c, user['id'], idempotency_key, 'order-request:'+oid, body.model_dump(), perform)


@router.post('/orders/{oid}/{rid}/decision')
def decide(oid: str, rid: str, body: Decision,
           user=Depends(auth.require_roles('owner','delivery_manager','branch_manager','admin')),
           idempotency_key: str | None=Header(default=None)):
    with db.tx() as c:
        o = scoped_order(c, oid, user)
        from .order_amendments import reviewer
        from .order_interventions import high_authority
        from .collected_order_authority import current_actor
        def require_reviewer(actor,order):
            if (not reviewer(order,actor) or not (auth.has_permission(actor,'orders_cancel')
                    or actor.get('role')=='admin' and high_authority(actor))):
                raise db.error(403, 'REQUEST_REVIEWER', 'المعالجة من المسؤول المختص المصرح له فقط.')
        current=current_actor(c,user,o['brand_id'])
        scoped_order(c,oid,current)
        require_reviewer(current,o)
        def perform():
            o = scoped_order(c, oid, user, lock=True)
            actor=current_actor(c,user,o['brand_id'],lock=True)
            scoped_order(c,oid,actor)
            require_reviewer(actor,o)
            request = next((r for r in rows(c, oid) if r['id']==rid), None)
            if not request:
                raise db.error(404, 'REQUEST_NOT_FOUND', 'طلب المعالجة غير موجود.')
            if request['decision']:
                raise db.error(409, 'REQUEST_DECIDED', 'تمت معالجة هذا الطلب مسبقًا.')
            if not body.note.strip():
                raise db.error(422, 'REQUEST_NOTE', 'اكتب نتيجة المعالجة.')
            if body.action=='refer_financial':
                from .order_interventions import terminal
                if not terminal(o):
                    raise db.error(409,'REQUEST_REFERRAL_TERMINAL_ONLY','الطلب النشط يعالج من مسار التعديل أو الإلغاء المعتاد.')
                if request.get('referral'):return {'request_id':rid,'status':'referred','referral':request['referral']}
                from .order_financial_cases import case
                pending=case(c,oid)
                if pending and pending['status']=='pending':case_id=pending['id']
                else:
                    case_id=db.new_id('financial_case_')
                    details={'kind':'customer_request_review','reason':body.note.strip(),'customer_request_id':rid,
                        'requester_id':actor['id'],'responsible_role':'accounting','financial_status':'pending',
                        'operationally_blocking':False,'review_only':True,'no_money_posted':True}
                    c.execute("INSERT INTO order_events(id,order_id,actor_id,event_type,details) VALUES(%s,%s,%s,'financial_case.opened',%s)",(case_id,oid,actor['id'],db.json(details)))
                    db.audit(c,actor['id'],'financial_case.opened','order',oid,{'case_id':case_id,**details})
                referral={'request_id':rid,'case_id':case_id,'responsible_role':'accounting',
                    'required_action':'مراجعة طلب العميل وتوثيق القرار المالي','note':body.note.strip(),'no_money_posted':True}
                db.event(c,oid,actor['id'],'customer_request.referred',referral)
                db.audit(c,actor['id'],'customer_request.referred','order',oid,referral)
                return {'request_id':rid,'status':'referred','referral':referral}
            if request.get('referral'):
                raise db.error(409,'REQUEST_ACCOUNTING_PENDING','طلب العميل محال للمحاسبة؛ يلزم استكمال القرار من المعالجة المالية.')
            if body.action == 'resolved' and o['preparation_status'] != 'cancelled' and o['delivery_status'] != 'cancelled':
                raise db.error(409, 'REQUEST_CANCELLATION_REQUIRED', 'أكمل إلغاء الطلب وتسوية التحصيل والمهمة بالمسار المعتمد أولًا.')
            details = {'request_id':rid, 'status':body.action, 'note':body.note.strip()}
            db.event(c, oid, user['id'], 'customer_request.decided', details)
            db.audit(c, user['id'], 'customer_request.decided', 'order', oid, details)
            return details
        return db.idempotent(c, user['id'], idempotency_key, 'order-request-decision:'+rid, body.model_dump(), perform)
