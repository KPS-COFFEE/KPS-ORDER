"""Accepted evidence requests attached to immutable financial source records.

Employee tickets remain owner-reviewed requests. These accounting clarifications
are a separate, explicit command with no cash, invoice, receipt or period writes.
"""
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from . import auth, db, multibrand
from .operations import safe
from .order_financial_cases import scoped, accounting, case

router=APIRouter(prefix='/api/financial-clarifications',tags=['financial-clarifications'])

class Question(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    version:StrictInt=Field(ge=1)
    assignee_id:str=Field(min_length=1,max_length=150)
    question:str=Field(min_length=3,max_length=2000)
    @field_validator('question','assignee_id')
    @classmethod
    def valid_text(cls,value):
        if '\x00' in value:raise ValueError('Invalid text')
        return value

class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    expected_version:StrictInt=Field(ge=1)
    action:Literal['accept','decline','respond','return','resolve','reassign']
    note:str=Field(min_length=3,max_length=2000)
    evidence:list[str]=Field(default_factory=list,max_length=20)
    assignee_id:str|None=Field(None,min_length=1,max_length=150)
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


def require_accounting(user):
    if not user.get('active',True) or not accounting(user):
        raise db.error(403,'ACCOUNTING_REVIEW_REQUIRED','طلب الاستكمال ومراجعة الرد للمحاسبة فقط.')


def eligible_manager(c,user,order,identifier):
    target=c.execute('SELECT * FROM users WHERE id=%s',(identifier,)).fetchone()
    if multibrand.enabled():target=multibrand.project(c,target,order['brand_id'])
    if (not target or not target.get('active') or target['role']!='delivery_manager'
            or target['brand_id']!=order['brand_id'] or order['branch_id'] not in target.get('branch_ids',[])
            or not auth.has_permission(target,'support_manage')):
        raise db.error(422,'CLARIFICATION_ASSIGNEE_SCOPE','اختر مدير توصيل مفعلًا ومخولًا لمتابعة الطلب ضمن فرعه.')
    # The source's actual service/period restrictions still apply to acceptance.
    if not auth.can_access_order(target,order,c):
        raise db.error(422,'CLARIFICATION_ASSIGNEE_SCOPE','المدير غير مخول بمتابعة هذا الطلب.')
    return target


def reviewer(user,row):
    require_accounting(user)
    if user['id']!=row['requested_by'] and user['role']!='owner':
        raise db.error(403,'CLARIFICATION_REVIEWER','الرد يعود للمحاسب طالب الاستكمال أو المالك المخول.')


def record(c,row,user,action,note,previous=None,evidence=()):
    identifier=db.new_id('fce_')
    c.execute('INSERT INTO financial_case_clarification_events(id,clarification_id,version,action,actor_id,assignee_id,from_status,to_status,note,evidence) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
        (identifier,row['id'],row['version'],action,user['id'],row['assignee_id'],previous,row['status'],note,db.json(list(evidence))))
    detail={'clarification_id':row['id'],'case_id':row['case_event_id'],'version':row['version'],'action':action,
        'requester_id':row['requested_by'],'assignee_id':row['assignee_id'],'from_status':previous,'to_status':row['status'],'note':note,'evidence':list(evidence),'source_order_version':row['source_order_version']}
    db.event(c,row['order_id'],user['id'],'financial_clarification.'+action,detail)
    db.audit(c,user['id'],'financial_clarification.'+action,'order',row['order_id'],detail)


def present(c,row,user):
    # No anonymous department or fabricated named handoff: identities are persisted.
    result=dict(row)
    names={r['id']:r['display_name'] for r in c.execute('SELECT id,display_name FROM users WHERE id=ANY(%s)',([row['requested_by'],row['assignee_id']],)).fetchall()}
    result['requester_name']=names.get(row['requested_by'])
    result['assignee_name']=names.get(row['assignee_id'])
    result['responsible_role']='delivery_manager' if row['status']=='accepted' else 'accounting'
    result['responsible_user_id']=row['assignee_id'] if row['status']=='accepted' else row['requested_by']
    result['awaiting_acceptance']=row['status']=='requested'
    result['response_destination_id']=row['requested_by']
    result['source_records_unchanged']=True
    result['actions']=[]
    if user['id']==row['assignee_id']:
        if row['status']=='requested':result['actions']=['accept','decline']
        elif row['status']=='accepted':result['actions']=['respond']
    if accounting(user) and (user['id']==row['requested_by'] or user['role']=='owner'):
        if row['status']=='responded':result['actions']=['resolve','return','reassign']
        elif row['status']!='resolved':result['actions']=['reassign']
    result['history']=c.execute('SELECT e.*,u.display_name actor_name FROM financial_case_clarification_events e JOIN users u ON u.id=e.actor_id WHERE e.clarification_id=%s ORDER BY e.version',(row['id'],)).fetchall()
    return safe(result)


def read(c,user,identifier,lock=False):
    row=c.execute('SELECT * FROM financial_case_clarifications WHERE id=%s'+(' FOR UPDATE' if lock else ''),(identifier,)).fetchone()
    if not row:raise db.error(404,'CLARIFICATION_NOT_FOUND','طلب الاستكمال غير متاح.')
    o=scoped(c,row['order_id'],user)
    if not accounting(user) and user['id']!=row['assignee_id']:
        raise db.error(404,'CLARIFICATION_NOT_FOUND','طلب الاستكمال غير متاح.')
    if user['id']==row['assignee_id'] and not accounting(user):eligible_manager(c,user,o,user['id'])
    return row,o


@router.get('/orders/{oid}')
def listing(oid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        o=scoped(c,oid,user)
        rows=c.execute('SELECT * FROM financial_case_clarifications WHERE order_id=%s ORDER BY created_at,id',(oid,)).fetchall()
        if not accounting(user):
            if user['role']!='delivery_manager':raise db.error(403,'CLARIFICATION_FORBIDDEN','متابعة الاستكمال للمحاسبة والمدير المسند فقط.')
            eligible_manager(c,user,o,user['id'])
            rows=[r for r in rows if r['assignee_id']==user['id']]
        source_case=case(c,oid) if accounting(user) else None
        assignees=[]
        if accounting(user) and source_case:
            from fastapi import HTTPException
            for candidate in c.execute("SELECT id FROM users WHERE active AND role='delivery_manager' ORDER BY display_name,id").fetchall():
                try:person=eligible_manager(c,user,o,candidate['id'])
                except HTTPException as exc:
                    if exc.status_code==422:continue
                    raise
                assignees.append({'id':person['id'],'name':person['display_name']})
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
            eligible_manager(c,user,o,body.assignee_id)
            row=c.execute("INSERT INTO financial_case_clarifications(id,order_id,case_event_id,requested_by,assignee_id,question,source_order_version,status) VALUES(%s,%s,%s,%s,%s,%s,%s,'requested') RETURNING *",
                (db.new_id('fcq_'),oid,pending['id'],user['id'],body.assignee_id,body.question,o['version'])).fetchone()
            record(c,row,user,'requested',body.question)
            return {'clarification':present(c,row,user)}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'financial_clarification.request:'+oid,body.model_dump(),perform)


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
            if action in ('accept','decline','respond'):
                if user['id']!=row['assignee_id']:raise db.error(403,'CLARIFICATION_ASSIGNEE','الإجراء للمدير المسند من حسابه فقط.')
                eligible_manager(c,user,o,user['id'])
                expected='requested' if action in ('accept','decline') else 'accepted'
                if before!=expected:raise db.error(409,'CLARIFICATION_STATE','الإجراء لا يطابق حالة الاستكمال الحالية.')
                status='accepted' if action=='accept' else 'declined' if action=='decline' else 'responded'
            else:
                reviewer(user,row)
                if action=='reassign':
                    if before=='resolved' or not body.assignee_id:raise db.error(409,'CLARIFICATION_STATE','حدد مديرًا بديلًا لطلب غير مقفل.')
                    eligible_manager(c,user,o,body.assignee_id)
                    if body.assignee_id==row['assignee_id']:raise db.error(422,'CLARIFICATION_SAME_ASSIGNEE','اختر مديرًا بديلًا مختلفًا.')
                    row['assignee_id']=body.assignee_id;status='requested'
                else:
                    if before!='responded':raise db.error(409,'CLARIFICATION_STATE','يلزم رد المدير قبل المراجعة أو الإرجاع.')
                    # A return stays with accounting until the named manager
                    # accepts the new requested correction. Never fake receipt.
                    status='requested' if action=='return' else 'resolved'
            if body.assignee_id and action!='reassign':raise db.error(422,'CLARIFICATION_TARGET','تغيير المستلم متاح بإجراء إعادة الإسناد فقط.')
            if body.evidence and action!='respond':raise db.error(422,'CLARIFICATION_EVIDENCE','المراجع ترفق مع الرد فقط.')
            row=c.execute('UPDATE financial_case_clarifications SET status=%s,assignee_id=%s,version=version+1,updated_at=now() WHERE id=%s RETURNING *',(status,row['assignee_id'],identifier)).fetchone()
            recorded={'accept':'accepted','decline':'declined','respond':'responded','return':'returned','resolve':'resolved','reassign':'reassigned'}[action]
            record(c,row,user,recorded,body.note,before,body.evidence)
            return {'clarification':present(c,row,user)}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'financial_clarification.decision:'+identifier,body.model_dump(),perform)


def guard_case_resolution(c,oid,case_id):
    if c.execute("SELECT 1 FROM financial_case_clarifications WHERE order_id=%s AND case_event_id=%s AND status<>'resolved' LIMIT 1",(oid,case_id)).fetchone():
        raise db.error(409,'FINANCIAL_CLARIFICATION_PENDING','طلب الاستكمال المالي لم يقبل المحاسب رده بعد؛ أكمل الاستكمال قبل إقفال المعالجة.')
