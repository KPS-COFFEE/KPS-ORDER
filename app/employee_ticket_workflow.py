"""Accepted factual follow-ups in the existing employee-ticket audit ledger.

A task assignment confers no permission to execute its linked operational,
financial or account command. Those actions remain in their canonical controllers.
"""
import os
from datetime import datetime,timedelta,timezone
from typing import Literal
from fastapi import APIRouter,Depends,Request
from pydantic import BaseModel,ConfigDict,Field,StrictInt,field_validator
from . import auth,db,multibrand
from .operations import safe

router=APIRouter(prefix='/api/employee-tickets',tags=['employee-ticket-followup'])
EVENT='employee.ticket.followup'
DEPARTMENTS={'delivery','accounting','maintenance','printing','administration','driver','owner'}


def enabled():
    return (os.getenv('KPS_ADMIN_FACTUAL_WORKFLOW_ENABLED','false')=='true'
        and os.getenv('APP_ENV','production') in {'testing','development'})


def require_enabled():
    if not enabled():raise db.error(404,'ADMIN_FACTUAL_WORKFLOW_DISABLED','متابعة الإسناد الإداري قيد التطوير وغير مفعلة تشغيليًا.')


def history(c,identifier):
    return c.execute("SELECT a.*,u.display_name actor_name FROM audit_events a LEFT JOIN users u ON u.id=a.actor_id WHERE a.action=%s AND a.entity_type='employee_ticket' AND a.entity_id=%s ORDER BY (a.details->>'version')::int",(EVENT,identifier)).fetchall()


def state(events):
    return dict(events[-1]['details']) if events else None


def department(user):
    if user['role']=='owner':return 'owner'
    if user['role']=='driver':return 'driver'
    if user['role'] in {'delivery_manager','delivery_staff','branch_manager'}:return 'delivery'
    if auth.has_permission(user,'accounting_review'):return 'accounting'
    if auth.has_permission(user,'vehicle_maintenance_manage'):return 'maintenance'
    if auth.has_permission(user,'branch_printers_manage'):return 'printing'
    return 'administration'


def source(c,user,kind,identifier):
    if user['role']=='customer' or not user.get('active',True):raise db.error(403,'STAFF_REQUIRED','المتابعة الإدارية للموظفين المخولين فقط.')
    if not kind and not identifier:return None
    if not kind or not identifier:raise db.error(422,'TICKET_SOURCE_REQUIRED','حدد نوع المصدر ومرجعه معًا.')
    if kind=='order':
        from .order_financial_cases import scoped
        row=scoped(c,identifier,user)
    elif kind=='vehicle_report':
        from .maintenance import report
        row=report(c,identifier,user)
    elif kind=='print_job':
        from .branch_printing import scope
        row=c.execute('SELECT * FROM branch_print_jobs WHERE id=%s AND brand_id=%s',(identifier,user['brand_id'])).fetchone()
        if not row or not auth.has_permission(user,'orders_print'):raise db.error(404,'TICKET_SOURCE_NOT_FOUND','مصدر المتابعة غير متاح.')
        scope(c,user,row['branch_id'])
    else:raise db.error(422,'TICKET_SOURCE_TYPE','نوع المصدر غير مدعوم.')
    if user.get('home_brand_id') and row['branch_id'] not in (user.get('branch_ids') or []):
        raise db.error(404,'TICKET_SOURCE_NOT_FOUND','مصدر المتابعة غير متاح ضمن فروعك.')
    return dict(type=kind,id=identifier,brand_id=row['brand_id'],branch_ids=[row['branch_id']])


def branch_scope(c,created):
    if 'branch_ids' in created['details']:return created['details']['branch_ids']
    original=c.execute('SELECT branch_ids FROM users WHERE id=%s',(created['actor_id'],)).fetchone()
    return original['branch_ids'] if original else []


def target(c,user,created,identifier,requested_department=None):
    row=c.execute('SELECT * FROM users WHERE id=%s',(identifier,)).fetchone()
    row=multibrand.project(c,row,created['details']['brand_id']) if row else None
    branches=branch_scope(c,created)
    if (not row or not row.get('active') or row['role']=='customer' or
            (row['role']!='owner' or row.get('home_brand_id')) and not set(branches)<=set(row.get('branch_ids') or [])):
        raise db.error(422,'TICKET_ASSIGNEE_SCOPE','اختر موظفًا مفعلًا ومخولًا لنطاق التذكرة.')
    dept=requested_department or department(row)
    if dept not in DEPARTMENTS:raise db.error(422,'TICKET_DEPARTMENT','القسم غير صالح.')
    permitted={
        'owner':row['role']=='owner',
        'driver':row['role']=='driver' and row['id']==created['actor_id'],
        'delivery':row['role'] in {'delivery_manager','delivery_staff','branch_manager'} and auth.has_permission(row,'orders_view'),
        'accounting':row['role'] in {'owner','admin'} and auth.has_permission(row,'accounting_review'),
        'maintenance':auth.has_permission(row,'vehicle_maintenance_manage'),
        'printing':auth.has_permission(row,'branch_printers_manage'),
        'administration':row['role'] in {'owner','admin'},
    }
    if not permitted[dept]:raise db.error(422,'TICKET_ASSIGNEE_DEPARTMENT','الموظف غير مخول بالقسم المطلوب؛ الإسناد لا يمنحه صلاحيات جديدة.')
    return row,dept


def append(c,created,user,current,action,**changes):
    current=dict(current or {})
    previous={k:current.get(k) for k in ('status','assignee_id','responsible_user_id')}
    current.update(changes,brand_id=created['details']['brand_id'],version=current.get('version',0)+1,action=action)
    current.update(from_status=previous['status'],from_assignee_id=previous['assignee_id'],from_responsible_user_id=previous['responsible_user_id'])
    current['recorded_at']=datetime.now(timezone.utc).isoformat()
    db.audit(c,user['id'],EVENT,'employee_ticket',created['entity_id'],current)
    return current


def initial(c,created,user):
    # A named requester retains responsibility until an actual receiver accepts.
    receiver=user;receiver_department=department(user)
    if user['role']=='driver':
        manager=c.execute("SELECT u.* FROM shifts s JOIN shifts m ON m.id=s.manager_shift_id JOIN users u ON u.id=m.user_id WHERE s.user_id=%s AND s.brand_id=%s AND s.status<>'closed' AND m.status<>'closed' AND u.active ORDER BY s.opened_at DESC LIMIT 1",(user['id'],user['brand_id'])).fetchone()
        if manager:
            try:receiver,receiver_department=target(c,user,created,manager['id'],'delivery')
            except Exception as exc:
                from fastapi import HTTPException
                if not isinstance(exc,HTTPException):raise
    if receiver['id']==user['id'] and user['role'] in {'driver','delivery_staff','branch_manager'}:
        # Without a current manager, use a real active authorized fallback;
        # the reporter is never asked to invent a financial/permission decision.
        desired='delivery'
        linked=created['details'].get('source') or {}
        if linked.get('type')=='vehicle_report':
            native=c.execute('SELECT department FROM vehicle_reports WHERE id=%s',(linked['id'],)).fetchone()
            desired='maintenance' if native and native['department']=='maintenance' else 'delivery'
        elif linked.get('type')=='print_job':desired='printing'
        elif created['details'].get('type') in {'cash_correction'} or created['details'].get('issue')=='accounting_issue':desired='accounting'
        elif created['details'].get('type') in {'permission_change','permission_addition'}:desired='owner'
        candidates=c.execute("SELECT id,role FROM users WHERE active AND role IN ('admin','owner','delivery_manager') ORDER BY CASE role WHEN 'owner' THEN 1 ELSE 0 END,id").fetchall()
        for candidate in candidates:
            try:person,dept=target(c,user,created,candidate['id'],'owner' if candidate['role']=='owner' else desired)
            except Exception as exc:
                from fastapi import HTTPException
                if isinstance(exc,HTTPException) and exc.status_code==422:continue
                raise
            receiver=person;receiver_department=dept;break
    same=receiver['id']==user['id'];now=datetime.now(timezone.utc)
    return append(c,created,user,None,'created',status='accepted' if same else 'requested',
        dispatcher_id=user['id'],dispatcher_department=department(user),assignee_id=receiver['id'],department=receiver_department,
        responsible_user_id=user['id'],responsible_department=department(user),
        required_action='triage_request' if same else 'accept_and_triage_request',
        question=created['details'].get('description') or created['details'].get('issue_label',{}).get('ar') or 'Review recorded request',
        evidence_required='',due_at=(now+timedelta(hours=24)).isoformat(),return_reason=None,
        next_recipient_id=receiver['id'] if not same else user['id'],
        fallback_department='owner',no_source_mutation=True)


def visible(c,created,user):
    if user['role']=='customer' or not user.get('active',True):raise db.error(404,'TICKET_SCOPE','التذكرة غير متاحة.')
    events=history(c,created['entity_id']);current=state(events)
    if user.get('home_brand_id') and not set(branch_scope(c,created))<=set(user.get('branch_ids') or []):
        raise db.error(404,'TICKET_SCOPE','التذكرة غير متاحة.')
    if user['role']!='owner' and user['id']!=created['actor_id'] and (not current or user['id'] not in {current.get('assignee_id'),current.get('dispatcher_id')}):
        raise db.error(404,'TICKET_SCOPE','التذكرة غير متاحة.')
    # Current grants are checked for an assigned employee every time.
    if user['role']!='owner' and current and user['id']==current.get('assignee_id') and user['id']!=created['actor_id']:
        try:target(c,user,created,user['id'],current['department'])
        except Exception as exc:
            from fastapi import HTTPException
            if isinstance(exc,HTTPException) and exc.status_code==422:
                raise db.error(404,'TICKET_SCOPE','التذكرة غير متاحة بعد تغيير صلاحياتك.') from exc
            raise
    return events,current


def presentation(c,created,user,observer=False):
    if observer:
        events=history(c,created['entity_id']);current=state(events)
    else:events,current=visible(c,created,user)
    if not current:return dict(status='historical_unassigned',assignment_recorded=False,actions=['assign'] if user['role']=='owner' or user['id']==created['actor_id'] else [],history=[],no_source_mutation=True)
    participant=user['role']=='owner' or user['id']==created['actor_id'] or user['id'] in {current['assignee_id'],current['dispatcher_id']}
    result=dict(current,history=events if participant else [],assignment_recorded=True,actions=[],can_open=participant)
    result['overdue']=current['status']!='completed' and datetime.fromisoformat(current['due_at'])<datetime.now(timezone.utc)
    names={r['id']:r['display_name'] for r in c.execute('SELECT id,display_name FROM users WHERE id=ANY(%s)',([current['assignee_id'],current['dispatcher_id'],current['responsible_user_id']],)).fetchall()}
    result['names']=names
    result['fallback_users']=[]
    for owner in c.execute("SELECT id FROM users WHERE active AND role='owner' ORDER BY id").fetchall():
        try:person,_=target(c,user,created,owner['id'],'owner')
        except Exception as exc:
            from fastapi import HTTPException
            if isinstance(exc,HTTPException) and exc.status_code==422:continue
            raise
        result['fallback_users'].append(dict(id=person['id'],name=person['display_name']))
    if user['role']=='owner' or user['id']==current['dispatcher_id']:
        result['actions'].append('assign')
        if current['status']=='responded':result['actions']+=['return','complete']
    if user['id']==current['assignee_id']:
        if current['status']=='requested':result['actions']+=['accept','decline']
        elif current['status']=='accepted':result['actions']+=['respond','decline']
    return safe(result)


class Command(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    expected_version:StrictInt=Field(ge=0)
    action:Literal['assign','accept','decline','respond','return','complete']
    note:str=Field(min_length=3,max_length=2000)
    assignee_id:str|None=Field(default=None,min_length=1,max_length=150)
    department:str|None=None
    evidence_required:str=Field(default='',max_length=2000)
    evidence:list[str]=Field(default_factory=list,max_length=20)
    due_hours:StrictInt|None=Field(default=None,ge=1,le=720)
    @field_validator('note','evidence_required','department','assignee_id')
    @classmethod
    def text(cls,v):
        if v and '\x00' in v:raise ValueError('Invalid text')
        return v
    @field_validator('evidence')
    @classmethod
    def references(cls,v):
        if any(not x.strip() or len(x)>500 or '\x00' in x for x in v):raise ValueError('Invalid evidence')
        return [x.strip() for x in v]


@router.get('/{identifier}/followup')
def detail(identifier:str,user=Depends(auth.current_user)):
    require_enabled()
    from .employee_tickets import ticket_events
    with db.tx() as c:
        created,_=ticket_events(c,user,identifier)
        workflow=presentation(c,created,user)
        assignees=[]
        if 'assign' in workflow['actions']:
            for row in c.execute("SELECT id FROM users WHERE active AND role<>'customer' ORDER BY display_name,id").fetchall():
                try:person,dept=target(c,user,created,row['id'])
                except Exception as exc:
                    from fastapi import HTTPException
                    if isinstance(exc,HTTPException) and exc.status_code==422:continue
                    raise
                assignees.append(dict(id=person['id'],name=person['display_name'],department=dept))
        return dict(workflow=workflow,assignees=assignees,review_only=True,no_source_mutation=True)


@router.post('/{identifier}/followup')
def command(identifier:str,body:Command,request:Request,user=Depends(auth.current_user)):
    require_enabled()
    from .employee_tickets import ticket_events
    with db.tx() as c:
        ticket_events(c,user,identifier)
        def perform():
            created,_=ticket_events(c,user,identifier,True)
            events,current=visible(c,created,user);version=current['version'] if current else 0
            if body.expected_version!=version:raise db.error(409,'TICKET_FOLLOWUP_CHANGED','توجد متابعة أحدث؛ حدّث التذكرة.')
            allowed=presentation(c,created,user)['actions']
            if body.action not in allowed:raise db.error(403,'TICKET_FOLLOWUP_ACTION','الإجراء غير متاح للمسؤول الحالي أو لا يطابق الحالة.')
            now=datetime.now(timezone.utc);changes={'evidence':body.evidence}
            if body.action=='assign':
                if not body.assignee_id:raise db.error(422,'TICKET_ASSIGNEE_REQUIRED','حدد الموظف المسؤول.')
                person,dept=target(c,user,created,body.assignee_id,body.department)
                changes.update(dispatcher_id=user['id'],dispatcher_department=department(user),assignee_id=person['id'],department=dept,status='requested',
                    responsible_user_id=user['id'],responsible_department=department(user),next_recipient_id=person['id'],
                    question=body.note,evidence_required=body.evidence_required,required_action='accept_and_complete_request',
                    return_reason=None,due_at=(now+timedelta(hours=body.due_hours or 24)).isoformat(),fallback_department='owner',no_source_mutation=True)
            else:
                if body.assignee_id or body.department or body.due_hours is not None and body.action!='return':raise db.error(422,'TICKET_FOLLOWUP_TARGET','تغيير الموظف والقسم متاح بإعادة الإسناد فقط.')
                if body.action=='accept':
                    target(c,user,created,user['id'],current['department'])
                    changes.update(status='accepted',responsible_user_id=user['id'],responsible_department=current['department'],next_recipient_id=current['dispatcher_id'],required_action='complete_requested_facts')
                elif body.action in {'decline','respond'}:
                    if body.action=='respond' and current.get('evidence_required') and not body.evidence:raise db.error(422,'TICKET_EVIDENCE_REQUIRED','أرفق مرجع الإثبات المطلوب أو اطلب إعادة الإسناد عند التعذر.')
                    changes.update(status='declined' if body.action=='decline' else 'responded',responsible_user_id=current['dispatcher_id'],responsible_department=department(c.execute('SELECT * FROM users WHERE id=%s',(current['dispatcher_id'],)).fetchone()),next_recipient_id=current['dispatcher_id'],required_action='assign_authorized_replacement' if body.action=='decline' else 'review_response',due_at=(now+timedelta(hours=24)).isoformat())
                elif body.action=='return':
                    changes.update(status='requested',responsible_user_id=user['id'],responsible_department=department(user),question=body.note,return_reason=body.note,evidence_required=body.evidence_required or current.get('evidence_required',''),next_recipient_id=current['assignee_id'],required_action='accept_required_correction',due_at=(now+timedelta(hours=body.due_hours or 24)).isoformat())
                elif body.action=='complete':changes.update(status='completed',responsible_user_id=user['id'],next_recipient_id=None,required_action='factual_followup_complete_source_action_independent')
            append(c,created,user,current,body.action,note=body.note,**changes)
            return dict(workflow=presentation(c,created,user),no_source_mutation=True,review_only=True)
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'employee.ticket.followup:'+identifier,body.model_dump(),perform)


def source_followups(c,user,kind,identifier):
    require_enabled()
    from .employee_tickets import CREATED
    source(c,user,kind,identifier)
    records=c.execute("SELECT a.*,u.display_name actor_name FROM audit_events a JOIN users u ON u.id=a.actor_id WHERE a.action=%s AND a.entity_type='employee_ticket' AND a.details->>'brand_id'=%s AND a.details->'source'->>'type'=%s AND a.details->'source'->>'id'=%s ORDER BY a.created_at,a.id",(CREATED,user['brand_id'],kind,identifier)).fetchall()
    result=[]
    from fastapi import HTTPException
    for record in records:
        try:workflow=presentation(c,record,user,observer=True)
        except HTTPException as exc:
            if exc.status_code==404:continue
            raise
        result.append(dict(id=record['entity_id'],reference=record['details']['reference'],workflow=workflow))
    return dict(followups=result,no_source_mutation=True)


@router.get('/source/{kind}/{identifier}')
def linked_source(kind:Literal['order','vehicle_report','print_job'],identifier:str,user=Depends(auth.current_user)):
    require_enabled()
    with db.tx() as c:return source_followups(c,user,kind,identifier)
