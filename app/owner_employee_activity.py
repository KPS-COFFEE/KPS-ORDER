"""Owner-only recorded account events and scoped work; never attendance inference."""
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo
import hmac
import psycopg
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from . import auth, db
from .operations import safe

router=APIRouter(prefix='/api/owner-employee-activity',tags=['owner-employee-activity'])
RIYADH=ZoneInfo('Asia/Riyadh')


def owner(c,request,expected,lock=False):
    if lock:
        c.execute('SELECT id FROM users WHERE id=%s FOR UPDATE',(expected['id'],)).fetchone()
        c.execute('SELECT id FROM sessions WHERE id=%s FOR UPDATE',(expected.get('session_id'),)).fetchone()
    current=auth._find_session(c,request)
    if not current or current['id']!=expected['id'] or current.get('session_id')!=expected.get('session_id') or current['role']!='owner' or not current['active']:
        raise db.error(403,'OWNER_ACTIVITY_REQUIRED','التقرير وإدارة الجلسات للمالك المخول فقط.')
    if lock:
        if not hmac.compare_digest(auth.token_hash(request.headers.get('x-csrf-token','')),current['csrf_hash']):
            raise db.error(403,'CSRF_REJECTED','أعد التحقق من جلسة المالك.')
        auth.require_owner_step_up(c,current)
    return current


def staff_scope(user,alias='u'):
    # Current account identity scope, not a claim about historical work grants.
    condition=f"{alias}.brand_id=%s AND {alias}.role<>'customer'"
    args=[user['brand_id']]
    if user.get('home_brand_id'):
        condition+=f" AND {alias}.branch_ids<>'[]'::jsonb AND {alias}.branch_ids <@ %s::jsonb"
        args.append(db.json(user.get('branch_ids',[])))
    return condition,args


@router.get('/options')
def options(request:Request,user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        user=owner(c,request,user)
        condition,args=staff_scope(user)
        people=c.execute('SELECT u.id,u.display_name,u.role,u.active FROM users u WHERE '+condition+' ORDER BY u.display_name,u.id LIMIT 501',args).fetchall()
    return safe({'employees':people[:500],'options_truncated':len(people)>500,'brand_id':user['brand_id'],
                 'today':datetime.now(RIYADH).date(),'can_end_sessions':not user.get('home_brand_id')})


@router.get('')
def listing(request:Request,kind:Literal['login','work']='login',start:date|None=None,end:date|None=None,
            employee_id:str=Query('',max_length=100),offset:int=Query(0,ge=0,le=10000),
            limit:int=Query(50,ge=1,le=100),user=Depends(auth.require_roles('owner'))):
    today=datetime.now(RIYADH).date();first=start or today-timedelta(days=6);last=end or today
    if last<first or (last-first).days>=93:
        raise db.error(422,'ACTIVITY_PERIOD_INVALID','اختر فترة مرتبة لا تتجاوز 93 يوما.')
    try:
        lower=datetime.combine(first,time.min,RIYADH);upper=datetime.combine(last+timedelta(days=1),time.min,RIYADH)
    except OverflowError:
        raise db.error(422,'ACTIVITY_PERIOD_INVALID','نهاية الفترة غير صالحة.')
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        user=owner(c,request,user)
        person,args=staff_scope(user)
        if employee_id:
            if not c.execute('SELECT 1 FROM users u WHERE u.id=%s AND '+person,[employee_id,*args]).fetchone():
                raise db.error(404,'ACTIVITY_EMPLOYEE_NOT_FOUND','الموظف غير متاح ضمن هذا البراند والنطاق.')
        filters=['a.created_at>=%s','a.created_at<%s',person];params=[lower,upper,*args]
        if employee_id:filters.append('u.id=%s');params.append(employee_id)
        joins=''
        if kind=='login':
            filters.extend(["a.action IN ('session.created','session.revoked','employee.sessions.ended')", "a.details->>'brand_id'=%s"])
            params.append(user['brand_id'])
            reference='a.entity_id'
        else:
            joins=" LEFT JOIN orders o ON a.entity_type='order' AND o.id=a.entity_id LEFT JOIN shifts s ON a.entity_type='shift' AND s.id=a.entity_id LEFT JOIN closure_reports r ON a.entity_type='report' AND r.id=a.entity_id LEFT JOIN driver_tasks t ON a.entity_type='task' AND t.id=a.entity_id LEFT JOIN orders task_order ON task_order.id=t.order_id"
            filters.append('COALESCE(o.brand_id,s.brand_id,r.brand_id,task_order.brand_id)=%s');params.append(user['brand_id'])
            if user.get('home_brand_id'):
                filters.append("(COALESCE(o.branch_id,task_order.branch_id)=ANY(%s) OR COALESCE(s.branch_ids,r.branch_ids,'[]'::jsonb)<>'[]'::jsonb AND COALESCE(s.branch_ids,r.branch_ids) <@ %s::jsonb)")
                params.extend([user.get('branch_ids',[]),db.json(user.get('branch_ids',[]))])
            reference="COALESCE(o.snapshot->>'order_reference',o.order_no::text,task_order.snapshot->>'order_reference',task_order.order_no::text,a.entity_id)"
        subject="CASE WHEN a.action='employee.sessions.ended' THEN a.entity_id ELSE a.actor_id END" if kind=='login' else 'a.actor_id'
        records=c.execute('SELECT a.id,a.created_at,a.action,a.entity_type,'+reference+' reference,u.id employee_id,u.display_name employee_name,u.role employee_role FROM audit_events a JOIN users u ON u.id='+subject+joins+' WHERE '+' AND '.join(filters)+' ORDER BY a.created_at DESC,a.id DESC LIMIT %s OFFSET %s',[*params,limit+1,offset]).fetchall()
    return safe({'rows':records[:limit],'next_offset':offset+limit if len(records)>limit and offset+limit<=10000 else None,'page_limit_reached':len(records)>limit and offset+limit>10000,'offset':offset,'limit':limit,
        'period':{'start':first,'end':last},'kind':kind,'brand_id':user['brand_id'],
        'coverage':'explicit_brand_authentication_only' if kind=='login' else 'known_order_shift_report_task_audit_only',
        'historical_login_coverage_complete':False,'attendance_report':False})


class EndSessions(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    reason:str=Field(min_length=3,max_length=500)


@router.post('/{identifier}/end-sessions')
def end_sessions(identifier:str,body:EndSessions,request:Request,user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        current=owner(c,request,user,lock=True)
        # Sessions belong to the whole identity, so a projected branch owner
        # cannot terminate a foreign account's sessions across other brands.
        if current.get('home_brand_id'):
            raise db.error(403,'OWNER_HOME_ACCOUNT_REQUIRED','إنهاء جلسات الحساب من مالك البراند الأصلي فقط.')
        preliminary=c.execute('SELECT role FROM users WHERE id=%s AND brand_id=%s',(identifier,current['brand_id'])).fetchone()
        if preliminary and preliminary['role'] in {'owner','customer'}:
            raise db.error(403,'EMPLOYEE_SESSION_TARGET_FORBIDDEN','اختر موظفا غير المالك والعميل.')
        target=auth._staff_record(c,identifier,current)
        if target['role'] in {'owner','customer'} or identifier==current['id']:
            raise db.error(403,'EMPLOYEE_SESSION_TARGET_FORBIDDEN','اختر موظفا غير المالك والحساب الحالي.')
        def perform():
            # Legacy email verification locks token then user. Refuse safely
            # rather than wait in the opposite order behind an in-flight proof.
            try:
                with c.transaction():
                    c.execute('SELECT id FROM identity_tokens WHERE user_id=%s AND consumed_at IS NULL AND revoked_at IS NULL FOR UPDATE NOWAIT',(identifier,)).fetchall()
            except psycopg.errors.LockNotAvailable:
                raise db.error(409,'EMPLOYEE_LOGIN_IN_PROGRESS','يوجد تحقق دخول جار؛ أعد المحاولة بعد انتهائه.')
            count=c.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL AND expires_at>now() RETURNING id',(identifier,)).fetchall()
            auth._invalidate_staff_tokens(c,identifier)
            c.execute('UPDATE otp_challenges SET expires_at=LEAST(expires_at,now()) WHERE source_user_id=%s AND verified_at IS NULL',(identifier,))
            db.audit(c,current['id'],'employee.sessions.ended','user',identifier,
                     {'brand_id':current['brand_id'],'employee_id':identifier,'reason':body.reason,'sessions_ended':len(count)})
            return {'ended':True,'employee_id':identifier,'sessions_ended':len(count),'account_active':target['active']}
        return db.idempotent(c,current['id'],request.headers.get('idempotency-key'),'owner.employee.sessions.end:'+identifier,body.model_dump(),perform)
