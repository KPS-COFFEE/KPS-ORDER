"""Accepted evidence requests attached to immutable financial source records.

Employee tickets remain owner-reviewed requests. These accounting clarifications
are a separate, explicit command with no cash, invoice, receipt or period writes.
"""
from datetime import datetime, timedelta, timezone
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from . import auth, db, multibrand
from .operations import safe
from .order_financial_cases import scoped, accounting, case

router=APIRouter(prefix='/api/financial-clarifications',tags=['financial-clarifications'])

def assignments_enabled():
    from .employee_ticket_workflow import enabled
    return enabled()

class Question(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    version:StrictInt=Field(ge=1)
    assignee_id:str=Field(min_length=1,max_length=150)
    question:str=Field(min_length=3,max_length=2000)
    due_hours:StrictInt=Field(default=24,ge=1,le=720)
    @field_validator('question','assignee_id')
    @classmethod
    def valid_text(cls,value):
        if '\x00' in value:raise ValueError('Invalid text')
        return value

class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    expected_version:StrictInt=Field(ge=1)
    action:Literal['accept','decline','respond','return','resolve','reassign','takeover']
    note:str=Field(min_length=3,max_length=2000)
    evidence:list[str]=Field(default_factory=list,max_length=20)
    assignee_id:str|None=Field(None,min_length=1,max_length=150)
    due_hours:StrictInt|None=Field(default=None,ge=1,le=720)
    @field_validator('note','assignee_id')
    @classmethod
    def valid_text(cls,value):
        if value and '\x00' in value:raise ValueError('Invalid text')
        return value
    @field_validator('evidence')
    @classmethod
    def valid_evidence(cls,value):
        if any(not isinstance(x,str) or not x.strip() or len(x)>500 or '\x00' in x for x in value):raise ValueError('Invalid evidence reference')
        return [x.strip() for x in value]


def command_payload(body):
    # Preserve durable retries issued before deadline fields were introduced.
    return body.model_dump(exclude={'due_hours'} if 'due_hours' not in body.model_fields_set else set())


def require_accounting(user):
    if not user.get('active',True) or not accounting(user):
        raise db.error(403,'ACCOUNTING_REVIEW_REQUIRED','طلب الاستكمال ومراجعة الرد للمحاسبة فقط.')


def eligible_manager(c,user,order,identifier):
    target=c.execute('SELECT * FROM users WHERE id=%s',(identifier,)).fetchone()
    if multibrand.enabled():target=multibrand.project(c,target,order['brand_id'])
    if (not target or not target.get('active') or target['role']!='delivery_manager'
            or target['brand_id']!=order['brand_id'] or order['branch_id'] not in target.get('branch_ids',[])):
        raise db.error(422,'CLARIFICATION_ASSIGNEE_SCOPE','اختر مدير توصيل مفعلًا ومخولًا لمتابعة الطلب ضمن فرعه.')
    if (order['brand_id']=='kps' and order['service']=='delivery'
            and (order.get('snapshot') or {}).get('delivery_period')=='morning'):
        # An approved morning manager may lack standing evening capabilities.
        # Resolve the actual current duty afresh, including when accounting is
        # discovering an assignee. Never copy ephemeral rights into users.
        from .morning_manager_context import resolve
        from fastapi import HTTPException
        try:target=resolve(c,target)
        except HTTPException as exc:
            if exc.status_code==403:
                raise db.error(422,'CLARIFICATION_ASSIGNEE_SCOPE','يلزم مدير صباح معتمد ومناوبة فعلية تغطي هذا الطلب.') from exc
            raise
        # A clarification can concern a recorded completed/cancelled order, but
        # only with actual provenance to this current duty, as for journey reads.
        target['_morning_history_read']=True
    if not auth.has_permission(target,'support_manage') or not auth.can_access_order(target,order,c):
        raise db.error(422,'CLARIFICATION_ASSIGNEE_SCOPE','المدير غير مخول بمتابعة هذا الطلب.')
    return target


def eligible_assignee(c,user,order,identifier):
    """A driver may supply facts only about an order actually assigned to them."""
    target=c.execute('SELECT * FROM users WHERE id=%s',(identifier,)).fetchone()
    if target and target['role']=='driver':
        if multibrand.enabled():target=multibrand.project(c,target,order['brand_id'])
        if (not target or not target.get('active') or target['brand_id']!=order['brand_id']
                or order['branch_id'] not in target.get('branch_ids',[])
                or not c.execute('SELECT 1 FROM effective_driver_tasks WHERE order_id=%s AND driver_id=%s',
                    (order['id'],identifier)).fetchone()):
            raise db.error(422,'CLARIFICATION_ASSIGNEE_SCOPE','اختر السائق المكلف فعليًا بالطلب ضمن صلاحياته.')
        return target
    return eligible_manager(c,user,order,identifier)


def reviewer_identifier(row):
    return row.get('current_reviewer_id') or row['requested_by']


def eligible_reviewer(c,order,identifier):
    target=c.execute('SELECT * FROM users WHERE id=%s',(identifier,)).fetchone()
    if multibrand.enabled():target=multibrand.project(c,target,order['brand_id'])
    if (not target or not target.get('active') or not accounting(target)
            or target['brand_id']!=order['brand_id']
            or target['role']!='owner' and order['branch_id'] not in target.get('branch_ids',[])):
        return None
    return target


def fallback_reviewer(c,order,requester_id):
    # Routine work stays with another authorized accountant before escalation.
    candidates=c.execute("SELECT id FROM users WHERE active AND role IN ('admin','owner') ORDER BY CASE WHEN role='admin' THEN 0 ELSE 1 END,id").fetchall()
    return next((r['id'] for r in candidates if r['id']!=requester_id and eligible_reviewer(c,order,r['id'])),None)


def reviewer(user,row):
    require_accounting(user)
    if user['id']!=reviewer_identifier(row) and user['role']!='owner':
        raise db.error(403,'CLARIFICATION_REVIEWER','الرد يعود للمحاسب طالب الاستكمال أو المالك المخول.')


def record(c,row,user,action,note,previous=None,evidence=()):
    identifier=db.new_id('fce_')
    c.execute('INSERT INTO financial_case_clarification_events(id,clarification_id,version,action,actor_id,assignee_id,from_status,to_status,note,evidence,due_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (identifier,row['id'],row['version'],action,user['id'],row['assignee_id'],previous,row['status'],note,db.json(list(evidence)),row.get('due_at')))
    detail={'clarification_id':row['id'],'case_id':row['case_event_id'],'version':row['version'],'action':action,
        'requester_id':row['requested_by'],'reviewer_id':reviewer_identifier(row),'fallback_reviewer_id':row.get('fallback_reviewer_id'),'assignee_id':row['assignee_id'],'from_status':previous,'to_status':row['status'],'note':note,'evidence':list(evidence),'source_order_version':row['source_order_version'],'due_at':row.get('due_at').isoformat() if row.get('due_at') else None}
    db.event(c,row['order_id'],user['id'],'financial_clarification.'+action,detail)
    db.audit(c,user['id'],'financial_clarification.'+action,'order',row['order_id'],detail)


def present(c,row,user):
    # No anonymous department or fabricated named handoff: identities are persisted.
    result=dict(row)
    ids=[row['requested_by'],row['assignee_id'],reviewer_identifier(row),row.get('fallback_reviewer_id')]
    people=c.execute('SELECT id,display_name,role,active FROM users WHERE id=ANY(%s)',([x for x in ids if x],)).fetchall()
    names={r['id']:r['display_name'] for r in people}
    result['assignee_role']=next((r['role'] for r in people if r['id']==row['assignee_id']),'delivery_manager')
    result['requester_name']=names.get(row['requested_by'])
    result['assignee_name']=names.get(row['assignee_id'])
    result['responsible_role']=result['assignee_role'] if row['status']=='accepted' else 'accounting'
    active_reviewer=reviewer_identifier(row)
    reviewer_active=any(r['id']==active_reviewer and r['active'] for r in people)
    result['reviewer_id']=active_reviewer
    result['reviewer_name']=names.get(active_reviewer)
    result['fallback_reviewer_name']=names.get(row.get('fallback_reviewer_id'))
    result['reviewer_unavailable']=not reviewer_active
    result['responsible_user_id']=row['assignee_id'] if row['status']=='accepted' else row.get('fallback_reviewer_id') if not reviewer_active and row.get('fallback_reviewer_id') else active_reviewer
    result['awaiting_acceptance']=row['status']=='requested'
    result['response_destination_id']=row.get('fallback_reviewer_id') if not reviewer_active and row.get('fallback_reviewer_id') else active_reviewer
    result['source_records_unchanged']=True
    result['actions']=[]
    if user['id']==row['assignee_id']:
        if row['status']=='requested':result['actions']=['accept','decline']
        elif row['status']=='accepted':result['actions']=['respond']
    if accounting(user) and (user['id']==active_reviewer or user['role']=='owner'):
        if row['status']=='responded':result['actions']=['resolve','return','reassign']
        elif row['status']!='resolved':result['actions']=['reassign']
    if assignments_enabled() and row['status']!='resolved' and not reviewer_active and (user['id']==row.get('fallback_reviewer_id') or user['role']=='owner'):
        result['actions']=['takeover']
    result['history']=c.execute('SELECT e.*,u.display_name actor_name FROM financial_case_clarification_events e JOIN users u ON u.id=e.actor_id WHERE e.clarification_id=%s ORDER BY e.version',(row['id'],)).fetchall()
    result.update(followup({**row,'requested_by':result['response_destination_id']},result['history']))
    result['authorized_fallback_user_id']=row.get('fallback_reviewer_id')
    return safe(result)


def followup(row,history,now=None):
    """Describe only recorded responsibility; deadline never settles a source."""
    now=now or datetime.now(timezone.utc)
    resolved=row['status']=='resolved'
    latest_return=next((event for event in reversed(history) if event['action']=='returned'),None)
    due=row.get('due_at')
    return dict(due_at=due,deadline_documented=due is not None,
        overdue=bool(due and not resolved and due<now),
        required_correction=latest_return['note'] if latest_return and not resolved else None,
        escalation_role=None if resolved else 'accounting',
        escalation_user_id=None if resolved else row['requested_by'],
        authorized_fallback_role=None if resolved else 'owner',
        next_action=None if resolved else {'requested':'manager_acceptance','accepted':'manager_response',
            'declined':'accounting_reassignment','responded':'accounting_review'}[row['status']],
        next_recipient_id=None if resolved or row['status']=='declined' else row['assignee_id'] if row['status']=='requested' else row['requested_by'],
        financial_resolution_independent=True)


def read(c,user,identifier,lock=False):
    row=c.execute('SELECT * FROM financial_case_clarifications WHERE id=%s'+(' FOR UPDATE' if lock else ''),(identifier,)).fetchone()
    if not row:raise db.error(404,'CLARIFICATION_NOT_FOUND','طلب الاستكمال غير متاح.')
    o=scoped(c,row['order_id'],user)
    if not accounting(user) and user['id']!=row['assignee_id']:
        raise db.error(404,'CLARIFICATION_NOT_FOUND','طلب الاستكمال غير متاح.')
    if user['id']==row['assignee_id'] and not accounting(user):eligible_assignee(c,user,o,user['id'])
    return row,o


@router.get('/features')
def preparation_features(user=Depends(auth.current_user)):
    available=assignments_enabled()
    if user['role']=='driver' and not available:
        with db.tx() as c:
            available=bool(c.execute("SELECT 1 FROM financial_case_clarifications q JOIN orders o ON o.id=q.order_id WHERE q.assignee_id=%s AND o.brand_id=%s AND q.status IN ('requested','accepted') LIMIT 1",(user['id'],user['brand_id'])).fetchone())
    return {'driver_followup_available':available,'new_assignments_enabled':assignments_enabled()}


@router.get('/mine')
def assigned_to_me(user=Depends(auth.current_user)):
    if user['role'] not in ('driver','delivery_manager'):
        raise db.error(403,'CLARIFICATION_FORBIDDEN','صندوق الاستكمال للموظف المسند فقط.')
    with db.tx() as c:
        rows=c.execute("""SELECT q.*,o.order_no FROM financial_case_clarifications q JOIN orders o ON o.id=q.order_id
            WHERE q.assignee_id=%s AND o.brand_id=%s AND q.status IN ('requested','accepted')
            ORDER BY q.due_at NULLS LAST,q.created_at LIMIT 100""",(user['id'],user['brand_id'])).fetchall()
        result=[]
        from fastapi import HTTPException
        for row in rows:
            try:
                order=scoped(c,row['order_id'],user)
                eligible_assignee(c,user,order,user['id'])
            except HTTPException as exc:
                if exc.status_code in (403,404,422):continue
                raise
            # Facts-only inbox: no expected cash, variance or accounting source.
            result.append({k:safe(row[k]) for k in ('id','order_id','order_no','question','status','due_at','version')})
        return {'clarifications':result}


@router.get('/orders/{oid}')
def listing(oid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        o=scoped(c,oid,user)
        rows=c.execute('SELECT * FROM financial_case_clarifications WHERE order_id=%s ORDER BY created_at,id',(oid,)).fetchall()
        if not accounting(user):
            if user['role'] not in ('delivery_manager','driver'):raise db.error(403,'CLARIFICATION_FORBIDDEN','متابعة الاستكمال للمحاسبة والموظف المسند فقط.')
            eligible_assignee(c,user,o,user['id'])
            rows=[r for r in rows if r['assignee_id']==user['id']]
        source_case=case(c,oid) if accounting(user) else None
        assignees=[]
        if accounting(user) and source_case:
            from fastapi import HTTPException
            roles=['delivery_manager','driver'] if assignments_enabled() else ['delivery_manager']
            for candidate in c.execute("SELECT id FROM users WHERE active AND role=ANY(%s) ORDER BY display_name,id",(roles,)).fetchall():
                try:person=eligible_assignee(c,user,o,candidate['id'])
                except HTTPException as exc:
                    if exc.status_code==422:continue
                    raise
                assignees.append({'id':person['id'],'name':person['display_name'],'role':person['role']})
        return {'clarifications':[present(c,r,user) for r in rows],'assignees':assignees,'can_request':bool(accounting(user) and source_case),'order_version':o['version']}


@router.post('/orders/{oid}')
def request_clarification(oid:str,body:Question,request:Request,user=Depends(auth.current_user)):
    require_accounting(user)
    with db.tx() as c:
        scoped(c,oid,user)
        def perform():
            o=scoped(c,oid,user,True)
            if body.version!=o['version']:raise db.error(409,'ORDER_VERSION_CONFLICT','تغير الطلب؛ حدّث الصفحة قبل إرسال الاستكمال.')
            pending=case(c,oid)
            if not pending:raise db.error(409,'FINANCIAL_CASE_REQUIRED','طلب الاستكمال يجب أن يرتبط بمعالجة مالية مسجلة.')
            target=eligible_assignee(c,user,o,body.assignee_id)
            if target['role']=='driver' and not assignments_enabled():
                raise db.error(404,'ADMIN_WORKFLOW_NOT_ENABLED','مسار الإسناد الجديد مجهز تطويريًا وغير مفعل حاليًا.')
            row=c.execute("INSERT INTO financial_case_clarifications(id,order_id,case_event_id,requested_by,assignee_id,question,source_order_version,status,due_at,current_reviewer_id,fallback_reviewer_id) VALUES(%s,%s,%s,%s,%s,%s,%s,'requested',%s,%s,%s) RETURNING *",
                (db.new_id('fcq_'),oid,pending['id'],user['id'],body.assignee_id,body.question,o['version'],datetime.now(timezone.utc)+timedelta(hours=body.due_hours),user['id'],fallback_reviewer(c,o,user['id']) if assignments_enabled() else None)).fetchone()
            record(c,row,user,'requested',body.question)
            return {'clarification':present(c,row,user)}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'financial_clarification.request:'+oid,command_payload(body),perform)


@router.post('/{identifier}/decision')
def decide(identifier:str,body:Decision,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        read(c,user,identifier)
        def perform():
            # Order-first locking is shared with financial mutations/close guards.
            existing,_=read(c,user,identifier)
            o=scoped(c,existing['order_id'],user,True)
            row,_=read(c,user,identifier,True)
            if row['version']!=body.expected_version:raise db.error(409,'CLARIFICATION_VERSION_CONFLICT','تغير طلب الاستكمال؛ حدّث الصفحة.')
            before=row['status'];action=body.action
            if action=='takeover':
                if not assignments_enabled():raise db.error(404,'ADMIN_WORKFLOW_NOT_ENABLED','مسار الإسناد الجديد مجهز تطويريًا وغير مفعل حاليًا.')
                require_accounting(user)
                if before=='resolved' or eligible_reviewer(c,o,reviewer_identifier(row)):
                    raise db.error(409,'CLARIFICATION_REVIEWER_ACTIVE','المراجع الأصلي متاح؛ لا تُنقل المتابعة دون سبب وصلاحية.')
                if user['id']!=row.get('fallback_reviewer_id') and user['role']!='owner':
                    raise db.error(403,'CLARIFICATION_FALLBACK_SCOPE','استلام المتابعة للبديل المحدد أو المالك المخول فقط.')
                if not eligible_reviewer(c,o,user['id']):
                    raise db.error(403,'CLARIFICATION_FALLBACK_SCOPE','المراجع البديل خارج الصلاحية الحالية.')
                row['current_reviewer_id']=user['id'];status=before
            elif action in ('accept','decline','respond'):
                if user['id']!=row['assignee_id']:raise db.error(403,'CLARIFICATION_ASSIGNEE','الإجراء للمدير المسند من حسابه فقط.')
                eligible_assignee(c,user,o,user['id'])
                expected='requested' if action in ('accept','decline') else 'accepted'
                if before!=expected:raise db.error(409,'CLARIFICATION_STATE','الإجراء لا يطابق حالة الاستكمال الحالية.')
                status='accepted' if action=='accept' else 'declined' if action=='decline' else 'responded'
            else:
                reviewer(user,row)
                if action=='reassign':
                    if before=='resolved' or not body.assignee_id:raise db.error(409,'CLARIFICATION_STATE','حدد مديرًا بديلًا لطلب غير مقفل.')
                    target=eligible_assignee(c,user,o,body.assignee_id)
                    if target['role']=='driver' and not assignments_enabled():
                        raise db.error(404,'ADMIN_WORKFLOW_NOT_ENABLED','مسار الإسناد الجديد مجهز تطويريًا وغير مفعل حاليًا.')
                    if body.assignee_id==row['assignee_id']:raise db.error(422,'CLARIFICATION_SAME_ASSIGNEE','اختر مديرًا بديلًا مختلفًا.')
                    row['assignee_id']=body.assignee_id;status='requested'
                else:
                    if before!='responded':raise db.error(409,'CLARIFICATION_STATE','يلزم رد المدير قبل المراجعة أو الإرجاع.')
                    # A return stays with accounting until the named manager
                    # accepts the new requested correction. Never fake receipt.
                    status='requested' if action=='return' else 'resolved'
            if body.due_hours is not None and action not in ('return','reassign'):
                raise db.error(422,'CLARIFICATION_DEADLINE','تحديد مهلة جديدة متاح عند الإرجاع أو إعادة الإسناد فقط.')
            due_at=row.get('due_at')
            if action in ('return','reassign','respond','decline'):
                due_at=datetime.now(timezone.utc)+timedelta(hours=body.due_hours or 24)
            if body.assignee_id and action!='reassign':raise db.error(422,'CLARIFICATION_TARGET','تغيير المستلم متاح بإجراء إعادة الإسناد فقط.')
            if body.evidence and action!='respond':raise db.error(422,'CLARIFICATION_EVIDENCE','المراجع ترفق مع الرد فقط.')
            row=c.execute('UPDATE financial_case_clarifications SET status=%s,assignee_id=%s,due_at=%s,current_reviewer_id=%s,version=version+1,updated_at=now() WHERE id=%s RETURNING *',(status,row['assignee_id'],due_at,reviewer_identifier(row),identifier)).fetchone()
            recorded={'accept':'accepted','decline':'declined','respond':'responded','return':'returned','resolve':'resolved','reassign':'reassigned','takeover':'reviewer_taken_over'}[action]
            record(c,row,user,recorded,body.note,before,body.evidence)
            return {'clarification':present(c,row,user)}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'financial_clarification.decision:'+identifier,command_payload(body),perform)


def guard_case_resolution(c,oid,case_id):
    if c.execute("SELECT 1 FROM financial_case_clarifications WHERE order_id=%s AND case_event_id=%s AND status<>'resolved' LIMIT 1",(oid,case_id)).fetchone():
        raise db.error(409,'FINANCIAL_CLARIFICATION_PENDING','طلب الاستكمال المالي لم يقبل المحاسب رده بعد؛ أكمل الاستكمال قبل إقفال المعالجة.')
