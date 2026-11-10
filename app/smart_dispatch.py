"""Dormant dispatch foundation. Both release flags default OFF; manual stays intact."""
import os
from datetime import timedelta
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from . import auth, db
from .operations import (Assignment, assign_in_transaction, eligible_driver_shift,
    lock_shift, mutate, order_access, roles, safe, uid)

router=APIRouter(prefix='/api/dispatch',tags=['dispatch'])

class DispatchPolicy(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    mode:Literal['MANUAL','OFFER_ACCEPT','AUTO_ASSIGN']='MANUAL'
    offer_timeout_seconds:int=Field(60,ge=15,le=600)
    max_active_tasks:int=Field(3,ge=1,le=20)
    fairness:Literal['least_active','oldest_assignment']='least_active'
    fallback:Literal['manager']='manager'
    # Optional configured zone restrictions narrow branch authorization; GPS is not required.
    zone_branches:dict[str,list[str]]=Field(default_factory=dict)

class OfferRequest(BaseModel):
    order_id:str=Field(min_length=1,max_length=200)
    driver_shift_id:str=Field(min_length=1,max_length=200)

class Decision(BaseModel):
    decision:Literal['accept','decline']


def flags():
    return {mode:os.getenv('DISPATCH_'+mode+'_ENABLED','false').lower()=='true'
            for mode in ('OFFER_ACCEPT','AUTO_ASSIGN')}


def policy(c,user):
    row=c.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(user['brand_id'],)).fetchone()
    return DispatchPolicy.model_validate((row or {}).get('settings',{}).get('dispatch',{}))


def enabled(p,mode):
    if p.mode!=mode or not flags()[mode]:
        raise db.error(409,'SMART_DISPATCH_DISABLED','الإسناد الذكي غير مفعل.')


def permitted(user,permission):
    if not auth.has_permission(user,permission):
        raise db.error(403,'STAFF_PERMISSION_FORBIDDEN','الإجراء غير مصرح.')


def manager_access(user):
    roles(user,'owner','delivery_manager','delivery_staff');permitted(user,'delivery_assign')


def candidate(c,user,o,s,p):
    manager=eligible_driver_shift(c,s,o)
    if user['role']=='delivery_manager' and manager['user_id']!=user['id']:
        raise db.error(403,'DISPATCH_SCOPE','الشفت خارج نطاق المدير.')
    zone=o.get('snapshot',{}).get('delivery_zone')
    if zone and p.zone_branches and o['branch_id'] not in p.zone_branches.get(zone,[]):
        raise db.error(409,'DISPATCH_ZONE','الفرع غير مؤهل للمنطقة.')
    count=c.execute("SELECT count(*) n FROM driver_tasks WHERE shift_id=%s AND status NOT IN ('delivered','cancelled','transferred','reassigned')",(s['id'],)).fetchone()['n']
    if count>=p.max_active_tasks:raise db.error(409,'DISPATCH_CAPACITY','السائق مشغول بالسعة المتاحة.')


def record(c,offer,user,action):
    c.execute('INSERT INTO dispatch_offer_events(id,brand_id,offer_id,actor_id,action) VALUES(%s,%s,%s,%s,%s)',(uid(),offer['brand_id'],offer['id'],user['id'],action))


@router.get('/policy')
def read_policy(user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager','delivery_staff','driver')
    with db.tx() as c:
        p=policy(c,user);f=flags()
        return {'mode':p.mode if p.mode=='MANUAL' or f.get(p.mode) else 'MANUAL','flags':f,
                'offer_timeout_seconds':p.offer_timeout_seconds}


@router.post('/offers')
def offer(body:OfferRequest,request:Request,user=Depends(auth.current_user)):
    manager_access(user)
    with db.tx() as c:
        p=policy(c,user);enabled(p,'OFFER_ACCEPT')
        if isinstance(body,OfferRequest):
            order_access(c,user,c.execute('SELECT * FROM orders WHERE id=%s',(body.order_id,)).fetchone())
        def perform():
            o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(body.order_id,)).fetchone();order_access(c,user,o)
            if o['service']!='delivery' or o.get('completed_at') or o['preparation_status'] in ('completed','cancelled','rejected'):
                raise db.error(409,'DISPATCH_ORDER_CLOSED','الطلب غير متاح للإسناد.')
            if c.execute("SELECT 1 FROM driver_tasks WHERE order_id=%s AND status NOT IN ('cancelled','transferred','reassigned')",(o['id'],)).fetchone():
                raise db.error(409,'DISPATCH_ALREADY_ASSIGNED','الطلب مسند بالفعل.')
            s=lock_shift(c,body.driver_shift_id);candidate(c,user,o,s,p)
            previous=c.execute("SELECT * FROM dispatch_offers WHERE order_id=%s AND driver_id=%s AND status='pending' AND expires_at>now()",(o['id'],s['user_id'])).fetchone()
            if previous:return {'offer_id':previous['id']}
            row=c.execute('INSERT INTO dispatch_offers(id,brand_id,order_id,driver_id,shift_id,offered_by,expires_at) VALUES(%s,%s,%s,%s,%s,%s,now()+%s) RETURNING *',(uid(),o['brand_id'],o['id'],s['user_id'],s['id'],user['id'],timedelta(seconds=p.offer_timeout_seconds))).fetchone()
            record(c,row,user,'offered');return {'offer_id':row['id']}
        return mutate(c,user,request,'dispatch.offer',body.model_dump(),perform)


@router.get('/offers')
def inbox(user=Depends(auth.current_user)):
    roles(user,'driver');permitted(user,'tasks_view')
    with db.tx() as c:
        p=policy(c,user)
        if p.mode!='OFFER_ACCEPT' or not flags()['OFFER_ACCEPT']:return {'offers':[]}
        # Explicit projection: no customer name, phone, coordinates, notes or financial values.
        rows=c.execute("""SELECT f.id,f.expires_at,o.order_no,b.name branch,
          o.snapshot->>'preparation_minutes' preparation_minutes,o.snapshot->>'delivery_zone' delivery_zone
          FROM dispatch_offers f JOIN orders o ON o.id=f.order_id JOIN branches b ON b.id=o.branch_id
          JOIN shifts s ON s.id=f.shift_id
          WHERE f.brand_id=%s AND f.driver_id=%s AND o.branch_id=ANY(%s)
          AND f.status='pending' AND f.expires_at>now() AND s.status='open' AND s.available
          AND o.completed_at IS NULL AND o.preparation_status NOT IN ('completed','cancelled','rejected')
          AND NOT EXISTS(SELECT 1 FROM driver_tasks t WHERE t.order_id=o.id AND t.status NOT IN ('cancelled','transferred','reassigned'))
          ORDER BY f.created_at,f.id""",(user['brand_id'],user['id'],user.get('branch_ids',[]))).fetchall()
        return safe({'offers':rows})


@router.post('/offers/{fid}/decision')
def decide(fid:str,body:Decision,request:Request,user=Depends(auth.current_user)):
    roles(user,'driver');permitted(user,'tasks_update')
    with db.tx() as c:
        p=policy(c,user);enabled(p,'OFFER_ACCEPT')
        # Serialize with all manual assignment, handover and shift mutations.
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('operations:'+user['brand_id'],))
        f=c.execute('SELECT f.*,o.branch_id FROM dispatch_offers f JOIN orders o ON o.id=f.order_id WHERE f.id=%s FOR UPDATE OF f',(fid,)).fetchone()
        if not f or f['brand_id']!=user['brand_id'] or f['driver_id']!=user['id'] or f['branch_id'] not in user.get('branch_ids',[]):
            raise db.error(404,'OFFER_NOT_FOUND','العرض غير متاح.')
        def perform():
            if f['status']!='pending':raise db.error(409,'OFFER_FINISHED','انتهى العرض.')
            if c.execute('SELECT %s<=now() expired',(f['expires_at'],)).fetchone()['expired']:
                c.execute("UPDATE dispatch_offers SET status='expired' WHERE id=%s",(fid,));record(c,f,user,'expired')
                return {'status':'expired','fallback':p.fallback}
            if body.decision=='decline':
                c.execute("UPDATE dispatch_offers SET status='declined' WHERE id=%s",(fid,));record(c,f,user,'declined')
                return {'status':'declined','fallback':p.fallback}
            manager=c.execute('SELECT * FROM users WHERE id=%s',(f['offered_by'],)).fetchone()
            if not manager or not manager['active']:raise db.error(403,'DISPATCH_SCOPE','صلاحية مقدم العرض غير سارية.')
            manager_access(manager)
            o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(f['order_id'],)).fetchone();order_access(c,manager,o)
            s=lock_shift(c,f['shift_id']);candidate(c,manager,o,s,p)
            result=assign_in_transaction(c,manager,o['id'],Assignment(driver_shift_id=s['id']))
            c.execute("UPDATE dispatch_offers SET status='accepted',task_id=%s WHERE id=%s",(result['task']['id'],fid));record(c,f,user,'accepted')
            for other in c.execute("UPDATE dispatch_offers SET status='superseded' WHERE order_id=%s AND id<>%s AND status='pending' RETURNING *",(o['id'],fid)).fetchall():record(c,other,user,'superseded')
            return {'status':'accepted','task_id':result['task']['id']}
        return mutate(c,user,request,'dispatch.decision:'+fid,body.model_dump(),perform)


@router.post('/orders/{oid}/auto-assign')
def auto_assign(oid:str,request:Request,user=Depends(auth.current_user)):
    manager_access(user)
    with db.tx() as c:
        p=policy(c,user);enabled(p,'AUTO_ASSIGN')
        def perform():
            o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone();order_access(c,user,o)
            # Stable least-load / oldest-assignment ordering; no GPS-only eligibility.
            rows=c.execute("""SELECT s.id,count(t.id) FILTER(WHERE t.status NOT IN ('delivered','cancelled','transferred','reassigned')) active,
              max(t.created_at) last_assignment FROM shifts s LEFT JOIN driver_tasks t ON t.shift_id=s.id
              WHERE s.brand_id=%s AND s.kind='driver' AND s.status='open' AND s.available
              GROUP BY s.id ORDER BY active,last_assignment NULLS FIRST,s.opened_at,s.id""",(user['brand_id'],)).fetchall()
            if p.fairness=='oldest_assignment':rows.sort(key=lambda r:(r['last_assignment'] is not None,str(r['last_assignment'] or ''),r['active'],r['id']))
            from fastapi import HTTPException
            for row in rows:
                s=lock_shift(c,row['id'])
                try:candidate(c,user,o,s,p)
                except HTTPException:continue
                result=assign_in_transaction(c,user,oid,Assignment(driver_shift_id=s['id']))
                db.audit(c,user['id'],'dispatch.auto_assigned','order',oid,{'task_id':result['task']['id'],'policy':p.fairness})
                return result
            return {'status':'manager_required'}
        return mutate(c,user,request,'dispatch.auto:'+oid,{},perform)


@router.post('/offers/expire')
def expire(request:Request,user=Depends(auth.current_user)):
    manager_access(user)
    with db.tx() as c:
        p=policy(c,user);enabled(p,'OFFER_ACCEPT')
        def perform():
            rows=c.execute("""SELECT f.*,o.branch_id FROM dispatch_offers f JOIN orders o ON o.id=f.order_id
              WHERE f.brand_id=%s AND f.status='pending' AND f.expires_at<=now() FOR UPDATE OF f""",(user['brand_id'],)).fetchall()
            ids=[]
            for f in rows:
                if user['role']!='owner' and f['branch_id'] not in user.get('branch_ids',[]):continue
                c.execute("UPDATE dispatch_offers SET status='expired' WHERE id=%s",(f['id'],));record(c,f,user,'expired');ids.append(f['id'])
            # Never cancel or mutate the customer order. Manager can reoffer explicitly.
            return {'expired':ids,'fallback':'manager'}
        return mutate(c,user,request,'dispatch.expire',{},perform)
