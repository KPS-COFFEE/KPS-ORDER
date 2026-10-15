"""Explicitly ended breaks with manager-configured durations; no automatic resume."""
from datetime import timedelta
from typing import Literal
from fastapi import APIRouter,Depends,Request
from pydantic import BaseModel,ConfigDict,Field,StrictInt,StrictBool,model_validator
from . import auth,db
from .operations import lock_shift,shift_access,mutate,safe,uid,roles,unresolved
router=APIRouter(prefix='/api/driver-breaks',tags=['driver-breaks'])
REASONS=('prayer','short','restroom','other')

class Rule(BaseModel):
    model_config=ConfigDict(extra='forbid')
    default_minutes:StrictInt=Field(gt=0,le=1440)
    max_minutes:StrictInt=Field(gt=0,le=1440)
    approval_required:StrictBool
    @model_validator(mode='after')
    def valid(self):
        if self.default_minutes>self.max_minutes:raise ValueError('Default exceeds maximum')
        return self

class Policy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    reasons:dict[str,Rule]
    driver_max_minutes:dict[str,StrictInt]=Field(default_factory=dict)
    warning_minutes:StrictInt=Field(ge=0,le=1440)
    @model_validator(mode='after')
    def valid(self):
        if set(self.reasons)!=set(REASONS):raise ValueError('Configure every reason')
        if any(v<1 or v>1440 for v in self.driver_max_minutes.values()):raise ValueError('Invalid exception')
        return self

class BreakRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    shift_id:str
    reason:Literal['prayer','short','restroom','other']
    minutes:StrictInt=Field(gt=0,le=1440)
    notes:str=Field('',max_length=500)

class Action(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['approve','reject','extend','end']
    minutes:StrictInt|None=Field(None,gt=0,le=1440)
    note:str=Field('',max_length=500)


def manager(user):
    roles(user,'delivery_manager','owner')
    if not auth.has_permission(user,'drivers_availability'):raise db.error(403,'FORBIDDEN','إدارة الاستراحة غير مصرح بها.')


def active(c,sid):
    return c.execute("SELECT 1 FROM driver_breaks WHERE shift_id=%s AND status='active'",(sid,)).fetchone()


def guard(c,sid):
    if active(c,sid):raise db.error(409,'DRIVER_ON_BREAK','السائق في استراحة؛ يجب إنهاؤها صراحةً.')


def get_policy(c,s):
    parent=c.execute('SELECT user_id FROM shifts WHERE id=%s',(s['manager_shift_id'],)).fetchone()
    row=c.execute('SELECT policy FROM driver_break_policies WHERE manager_id=%s AND brand_id=%s',((parent or {}).get('user_id'),s['brand_id'])).fetchone()
    if not row:raise db.error(409,'BREAK_POLICY_REQUIRED','يحدد مدير التوصيل سياسة الاستراحة أولاً.')
    return Policy.model_validate(row['policy'])


def audit(c,b,user,action,details):
    c.execute('INSERT INTO driver_break_events(id,brand_id,break_id,actor_id,action,details) VALUES(%s,%s,%s,%s,%s,%s)',(uid(),b['brand_id'],b['id'],user['id'],action,db.json(details)))
    db.audit(c,user['id'],'driver_break.'+action,'driver_break',b['id'],details)


@router.put('/policy')
def configure(body:Policy,request:Request,user=Depends(auth.current_user)):
    manager(user)
    with db.tx() as c:
        def perform():
            for did in body.driver_max_minutes:
                d=c.execute("SELECT * FROM users WHERE id=%s AND brand_id=%s AND role='driver'",(did,user['brand_id'])).fetchone()
                if not d or (user['role']!='owner' and not set(d['branch_ids'])<=set(user.get('branch_ids',[]))):raise db.error(403,'BREAK_SCOPE','السائق خارج النطاق.')
            c.execute('INSERT INTO driver_break_policies(manager_id,brand_id,policy) VALUES(%s,%s,%s) ON CONFLICT(manager_id) DO UPDATE SET policy=EXCLUDED.policy,updated_at=now()',(user['id'],user['brand_id'],db.json(body.model_dump())))
            db.audit(c,user['id'],'driver_break.policy','user',user['id'],body.model_dump())
            return {'policy':body.model_dump()}
        return mutate(c,user,request,'break.policy',body.model_dump(),perform)


@router.get('')
def listing(user=Depends(auth.current_user)):
    roles(user,'driver','delivery_manager','owner')
    with db.tx() as c:
        query="SELECT b.*,s.manager_shift_id FROM driver_breaks b JOIN shifts s ON s.id=b.shift_id WHERE b.brand_id=%s AND b.status IN ('requested','active')"
        args=[user['brand_id']]
        if user['role']=='driver':query+=' AND b.driver_id=%s';args.append(user['id'])
        elif user['role']=='delivery_manager':query+=' AND s.manager_shift_id IN (SELECT id FROM shifts WHERE user_id=%s)';args.append(user['id'])
        rows=c.execute(query,args).fetchall();visible=[]
        for b in rows:
            s=c.execute('SELECT * FROM shifts WHERE id=%s',(b['shift_id'],)).fetchone()
            try:shift_access(c,user,s)
            except Exception as e:
                from fastapi import HTTPException
                if isinstance(e,HTTPException):continue
                raise
            b['overdue']=bool(b['status']=='active' and b['ends_at']<c.execute('SELECT now() n').fetchone()['n']);visible.append(b)
        p=None
        if user['role']=='driver':
            s=c.execute("SELECT * FROM shifts WHERE user_id=%s AND brand_id=%s AND status='open'",(user['id'],user['brand_id'])).fetchone()
            if s:
                try:p=get_policy(c,s).model_dump()
                except Exception as e:
                    from fastapi import HTTPException
                    if not isinstance(e,HTTPException):raise
        else:
            row=c.execute('SELECT policy FROM driver_break_policies WHERE manager_id=%s AND brand_id=%s',(user['id'],user['brand_id'])).fetchone();p=row['policy'] if row else None
        return safe({'breaks':visible,'policy':p})


@router.post('')
def request_break(body:BreakRequest,request:Request,user=Depends(auth.current_user)):
    roles(user,'driver')
    with db.tx() as c:
        shift_access(c,user,c.execute('SELECT * FROM shifts WHERE id=%s',(body.shift_id,)).fetchone())
        def perform():
            s=lock_shift(c,body.shift_id);shift_access(c,user,s)
            if s['user_id']!=user['id'] or s['kind']!='driver' or s['status']!='open':raise db.error(409,'BREAK_SHIFT','الشفت غير متاح.')
            if unresolved(c,s['id']):raise db.error(409,'BREAK_ACTIVE_TASKS','أكمل الطلبات أو سلّمها قبل الاستراحة.')
            if c.execute("SELECT 1 FROM driver_breaks WHERE shift_id=%s AND status IN ('requested','active')",(s['id'],)).fetchone():raise db.error(409,'BREAK_OVERLAP','توجد استراحة أو طلب استراحة قائم.')
            p=get_policy(c,s);rule=p.reasons[body.reason];maximum=p.driver_max_minutes.get(user['id'],rule.max_minutes)
            if body.minutes>maximum:raise db.error(422,'BREAK_LIMIT','تجاوز مدة الاستراحة المسموحة.')
            b=c.execute('INSERT INTO driver_breaks(id,brand_id,shift_id,driver_id,reason,notes,requested_minutes,status,previous_available) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(uid(),user['brand_id'],s['id'],user['id'],body.reason,body.notes,body.minutes,'requested' if rule.approval_required else 'active',s['available'])).fetchone()
            if not rule.approval_required:
                c.execute('UPDATE driver_breaks SET approved_minutes=%s,started_at=now(),ends_at=now()+%s WHERE id=%s',(body.minutes,timedelta(minutes=body.minutes),b['id']))
                c.execute('UPDATE shifts SET available=false WHERE id=%s',(s['id'],))
            audit(c,b,user,'requested',body.model_dump());return {'break_id':b['id'],'status':b['status']}
        return mutate(c,user,request,'break.request',body.model_dump(),perform)


@router.post('/{bid}')
def change(bid:str,body:Action,request:Request,user=Depends(auth.current_user)):
    roles(user,'driver','delivery_manager','owner')
    with db.tx() as c:
        # Recheck scope even on replay.
        b=c.execute('SELECT * FROM driver_breaks WHERE id=%s',(bid,)).fetchone()
        if not b or b['brand_id']!=user['brand_id']:raise db.error(404,'BREAK_NOT_FOUND','الاستراحة غير متاحة.')
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(b['shift_id'],)).fetchone();shift_access(c,user,s)
        if user['role']=='driver' and (b['driver_id']!=user['id'] or body.action!='end'):raise db.error(403,'FORBIDDEN','الإجراء لمدير التوصيل.')
        if user['role']!='driver':manager(user)
        def perform():
            s=lock_shift(c,b['shift_id']);shift_access(c,user,s)
            current=c.execute('SELECT * FROM driver_breaks WHERE id=%s FOR UPDATE',(bid,)).fetchone()
            if body.action=='end':
                if current['status'] not in ('active','requested'):raise db.error(409,'BREAK_ENDED','الاستراحة منتهية.')
                c.execute("UPDATE driver_breaks SET status='ended',ended_at=now() WHERE id=%s",(bid,))
                if current['status']=='active' and s['status']=='open':c.execute('UPDATE shifts SET available=%s WHERE id=%s',(current['previous_available'],s['id']))
            elif body.action=='reject':
                if current['status']!='requested':raise db.error(409,'BREAK_STATE','الطلب ليس بانتظار الموافقة.')
                c.execute("UPDATE driver_breaks SET status='rejected',ended_at=now() WHERE id=%s",(bid,))
            else:
                if s['status']!='open' or unresolved(c,s['id']):raise db.error(409,'BREAK_ACTIVE_TASKS','الشفت غير متاح أو توجد طلبات جارية.')
                p=get_policy(c,s);maximum=p.driver_max_minutes.get(b['driver_id'],p.reasons[b['reason']].max_minutes)
                minutes=body.minutes or current['requested_minutes']
                if minutes>maximum:raise db.error(422,'BREAK_LIMIT','تجاوز الحد المعتمد؛ عدّل السياسة أو الاستثناء أولاً.')
                expected='requested' if body.action=='approve' else 'active'
                if current['status']!=expected:raise db.error(409,'BREAK_STATE','حالة الاستراحة لا تسمح.')
                if body.action=='approve':c.execute("UPDATE driver_breaks SET status='active',approved_minutes=%s,started_at=now(),ends_at=now()+%s WHERE id=%s",(minutes,timedelta(minutes=minutes),bid))
                else:c.execute('UPDATE driver_breaks SET approved_minutes=%s,ends_at=started_at+%s WHERE id=%s',(minutes,timedelta(minutes=minutes),bid))
                c.execute('UPDATE shifts SET available=false WHERE id=%s',(s['id'],))
            audit(c,b,user,body.action,body.model_dump());return {'break_id':bid,'action':body.action}
        return mutate(c,user,request,'break.change:'+bid,body.model_dump(),perform)
