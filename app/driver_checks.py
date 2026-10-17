"""Temporary operator-requested readings; no permanent fuel-policy exemption."""
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict, Field, StrictInt
from . import auth, db, fleet_management, shift_vehicle
from .operations import OperationBody, mutate, safe, uid

router=APIRouter(prefix='/api/driver-checks',tags=['driver-checks'])

class Body(OperationBody):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)

class Send(Body):
    driver_ids:list[str]=Field(min_length=1,max_length=100)
    kind:Literal['odometer','fuel']

class Reply(Body):
    value:StrictInt=Field(ge=0,le=9999999)

class Decision(Body):
    decision:Literal['approved','rejected','cancelled']
    value:StrictInt|None=Field(default=None,ge=0,le=9999999)
    reason:str=Field(default='',max_length=500)

class Correction(Body):
    driver_id:str
    value:StrictInt=Field(ge=0,le=9999999)
    expected_revision:StrictInt=Field(ge=0)
    reason:str=Field(min_length=3,max_length=500)

def manager(user):
    if user['role'] not in ('owner','admin') or not auth.has_permission(user,'vehicle_maintenance_manage'):
        raise db.error(403,'CHECK_PERMISSION','صلاحية إدارة السيارات مطلوبة / Vehicle management permission required')

def lock_fleet(c,user):
    c.execute("SELECT id FROM users WHERE brand_id=%s AND role='driver' ORDER BY id FOR UPDATE",(user['brand_id'],)).fetchall()
    c.execute("SELECT id FROM shifts WHERE brand_id=%s AND kind='driver' AND status IN ('open','closing') ORDER BY id FOR UPDATE",(user['brand_id'],)).fetchall()
    c.execute('SELECT id FROM fleet_vehicles WHERE brand_id=%s ORDER BY id FOR UPDATE',(user['brand_id'],)).fetchall()

def target(c,user,driver_id):
    d=c.execute("SELECT * FROM users WHERE id=%s AND brand_id=%s AND role='driver' AND active",(driver_id,user['brand_id'])).fetchone()
    if not d or not d['branch_ids'] or (user['role']=='driver' and d['id']!=user['id']) or (user['role']!='driver' and not fleet_management.driver_scope(user,d)):
        raise db.error(404,'CHECK_SCOPE','السائق خارج النطاق / Driver unavailable')
    s=c.execute("SELECT * FROM shifts WHERE user_id=%s AND brand_id=%s AND kind='driver' AND status='open'",(driver_id,user['brand_id'])).fetchone()
    if not s or not s['branch_ids'] or (user['role']!='owner' and not set(s['branch_ids'])<=set(user['branch_ids'])):
        raise db.error(409,'CHECK_SHIFT','يلزم شفت مفتوح ضمن نطاقك / An authorized open shift is required')
    log=s.get('vehicle_log') or {}
    car=c.execute('SELECT * FROM fleet_vehicles WHERE brand_id=%s AND plate_key=%s',(user['brand_id'],fleet_management.plate(log.get('vehicle')))).fetchone()
    if not car or fleet_management.plate(d['vehicle'])!=car['plate_key']:
        raise db.error(409,'CHECK_VEHICLE','راجع إسناد السيارة / Check vehicle assignment')
    if user['role']!='driver':
        drivers=c.execute("SELECT * FROM users WHERE brand_id=%s AND role='driver'",(user['brand_id'],)).fetchall()
        if not fleet_management.car_scope(c,user,car,drivers):raise db.error(404,'CHECK_SCOPE','السيارة خارج نطاقك / Vehicle outside scope')
    return d,s,car

def resolve(c,user,r):
    d,s,car=target(c,user,r['driver_id'])
    if r['shift_id']!=s['id'] or r['vehicle_id']!=car['id'] or r['segment_started_at']!=s['vehicle_log']['start']['recorded_at']:
        raise db.error(409,'CHECK_STALE','تغير الشفت أو السيارة؛ أرسل طلبًا جديدًا / Shift or vehicle changed; send a new request')
    return d,s,car

def row(c,user,rid):
    r=c.execute('SELECT * FROM driver_check_requests WHERE id=%s AND brand_id=%s',(rid,user['brand_id'])).fetchone()
    if not r:raise db.error(404,'CHECK_SCOPE','الطلب غير موجود / Request not found')
    # Revalidate current authorization even on idempotent replays.
    d=c.execute('SELECT * FROM users WHERE id=%s',(r['driver_id'],)).fetchone()
    if not d or (user['role']=='driver' and d['id']!=user['id']) or (user['role']!='driver' and not fleet_management.driver_scope(user,d)):
        raise db.error(404,'CHECK_SCOPE','الطلب خارج النطاق / Request outside scope')
    s=c.execute('SELECT branch_ids FROM shifts WHERE id=%s',(r['shift_id'],)).fetchone()
    if user['role']!='owner' and (not s or not s['branch_ids'] or not set(s['branch_ids'])<=set(user['branch_ids'])):
        raise db.error(404,'CHECK_SCOPE','فروع الطلب خارج النطاق / Request branches outside scope')
    return r

@router.get('')
def listing(user=Depends(auth.current_user)):
    if user['role']!='driver':manager(user)
    with db.tx() as c:
        now=c.execute('SELECT now() n').fetchone()['n'];drivers=[]
        if user['role']!='driver':
            for d in c.execute("SELECT id FROM users WHERE brand_id=%s AND role='driver' AND active ORDER BY display_name",(user['brand_id'],)).fetchall():
                try:
                    d,s,v=target(c,user,d['id'])
                except Exception as exc:
                    from fastapi import HTTPException
                    if isinstance(exc,HTTPException):continue
                    raise
                drivers.append({'id':d['id'],'name':d['display_name'],'plate':v['plate'],'odometer_km':v['details'].get('odometer_km'),
                    'revision':v['details'].get('management_revision',0),'checkpoint':v['details'].get('odometer_checkpoint'),'awaiting_refuel':bool(s['vehicle_log'].get('awaiting_refuel'))})
        rows=c.execute('SELECT r.*,u.display_name,v.plate FROM driver_check_requests r JOIN users u ON u.id=r.driver_id JOIN fleet_vehicles v ON v.id=r.vehicle_id WHERE r.brand_id=%s'+(" AND r.driver_id=%s AND r.status='pending' AND r.expires_at>now()" if user['role']=='driver' else '')+' ORDER BY r.created_at DESC LIMIT 200',([user['brand_id'],user['id']] if user['role']=='driver' else [user['brand_id']])).fetchall()
        visible=[]
        for r in rows:
            try:
                row(c,user,r['id'])
            except Exception as exc:
                from fastapi import HTTPException
                if isinstance(exc,HTTPException):continue
                raise
            r['ui_status']='expired' if r['status']=='pending' and r['expires_at']<=now else r['status']
            if r['status'] in ('pending','submitted'):
                try:resolve(c,user,r)
                except Exception as exc:
                    from fastapi import HTTPException
                    if isinstance(exc,HTTPException):r['ui_status']='stale'
                    else:raise
            if user['role']=='driver' and r['ui_status']!='pending':continue
            visible.append(r)
        return safe({'requests':visible,'drivers':drivers,'server_time':now})

@router.post('')
def send(body:Send,request:Request,user=Depends(auth.current_user)):
    manager(user)
    with db.tx() as c:
        for did in set(body.driver_ids):target(c,user,did)
        def perform():
            lock_fleet(c,user)
            ids=[]
            for did in sorted(set(body.driver_ids)):
                d,s,v=target(c,user,did)
                c.execute("UPDATE driver_check_requests SET status='cancelled',decided_by=%s,decided_at=now(),decision=%s WHERE driver_id=%s AND kind=%s AND status IN ('pending','submitted')",(user['id'],db.json({'reason':'replaced_by_new_request'}),did,body.kind))
                rid=uid();ids.append(rid)
                c.execute('INSERT INTO driver_check_requests(id,brand_id,driver_id,shift_id,vehicle_id,segment_started_at,kind,requested_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(rid,user['brand_id'],did,s['id'],v['id'],s['vehicle_log']['start']['recorded_at'],body.kind,user['id']))
                db.audit(c,user['id'],'driver_check.send','driver_check',rid,{'kind':body.kind,'driver_id':did,'shift_id':s['id'],'expires_in_minutes':20})
            return {'ids':ids}
        return mutate(c,user,request,'driver_check.send',body.model_dump(),perform)

@router.post('/{rid}/reply')
def reply(rid:str,body:Reply,request:Request,user=Depends(auth.current_user)):
    if user['role']!='driver':raise db.error(403,'CHECK_DRIVER','السائق فقط / Driver only')
    with db.tx() as c:
        row(c,user,rid)
        def perform():
            lock_fleet(c,user)
            r=row(c,user,rid);resolve(c,user,r)
            if r['status']!='pending' or r['expires_at']<=c.execute('SELECT now() n').fetchone()['n']:
                raise db.error(409,'CHECK_EXPIRED','انتهى الطلب أو تم الرد عليه / Request expired or already answered')
            if r['kind']=='fuel' and body.value not in (0,25,50,75,100):raise db.error(422,'CHECK_FUEL','اختر مستوى البنزين / Select fuel level')
            c.execute("UPDATE driver_check_requests SET status='submitted',response=%s,responded_at=now() WHERE id=%s",(db.json(body.model_dump()),rid))
            db.audit(c,user['id'],'driver_check.reply','driver_check',rid,body.model_dump())
            return {'id':rid,'status':'submitted'}
        return mutate(c,user,request,'driver_check.reply:'+rid,body.model_dump(),perform)

def save_odometer(c,user,d,s,v,value,reason,source,observed_at):
    log=dict(s['vehicle_log']);details=v['details'];old=details.get('odometer_km')
    last=details.get('odometer_checkpoint',{})
    readings=[log.get('start') or {},log.get('fuel_readiness') or {},*log.get('refuels',[])]
    if last.get('observed_at','')>observed_at or any(x.get('recorded_at','')>observed_at and 'odometer_km' in x for x in readings):raise db.error(409,'CHECK_STALE','توجد قراءة أحدث؛ أرسل طلبًا جديدًا / A newer reading exists')
    floor=shift_vehicle.odometer_floor(log)
    # A lower corrected reading establishes a new reference; original readings
    # remain intact and this interval is excluded from distance/efficiency totals.
    checkpoint={'odometer_km':value,'recorded_by':user['id'],'recorded_at':c.execute('SELECT now() n').fetchone()['n'].isoformat(),
                'observed_at':observed_at,'reason':reason,'source':source,'previous_odometer_km':old}
    if value<floor and not reason:raise db.error(422,'CHECK_REASON','اكتب سبب تصحيح القراءة الأقل / Explain the lower corrected reading')
    if value<floor:log['odometer_adjusted']=True
    log['odometer_checkpoint']=checkpoint
    c.execute('UPDATE shifts SET vehicle_log=%s WHERE id=%s',(db.json(log),s['id']))
    patch={'odometer_km':value,'odometer_checkpoint':checkpoint,'management_revision':int(details.get('management_revision',0))+1}
    c.execute('UPDATE fleet_vehicles SET details=details||%s WHERE id=%s',(db.json(patch),v['id']))
    # Only the currently assigned driver's current card is updated.
    c.execute('UPDATE users SET vehicle=vehicle||%s,updated_at=now() WHERE id=%s',(db.json({'odometer_km':value}),d['id']))
    db.audit(c,user['id'],'fleet.odometer','vehicle',v['id'],checkpoint)

@router.post('/{rid}/decision')
def decide(rid:str,body:Decision,request:Request,user=Depends(auth.current_user)):
    manager(user)
    with db.tx() as c:
        row(c,user,rid)
        def perform():
            lock_fleet(c,user)
            r=row(c,user,rid)
            if r['status'] not in ('pending','submitted'):raise db.error(409,'CHECK_DONE','تمت معالجة الطلب / Request already processed')
            d,s,v=resolve(c,user,r)
            if body.decision=='approved':
                if r['status']!='submitted':raise db.error(409,'CHECK_REPLY_REQUIRED','انتظر رد السائق / Wait for the driver response')
                if r['kind']=='odometer':
                    value=r['response']['value'] if body.value is None else body.value
                    if value!=r['response']['value'] and not body.reason:raise db.error(422,'CHECK_REASON','سبب التعديل مطلوب / Correction reason required')
                    save_odometer(c,user,d,s,v,value,body.reason,rid,r['responded_at'].isoformat())
                else:
                    if body.value is not None:raise db.error(422,'CHECK_FUEL','اعتمد مستوى السائق كما هو / Approve the reported fuel level')
                    log=dict(s['vehicle_log']);value=r['response']['value']
                    if (log.get('fuel_readiness') or {}).get('recorded_at','')>r['responded_at'].isoformat():raise db.error(409,'CHECK_STALE','تم تحديث الوقود بعد الرد؛ أرسل طلبًا جديدًا / Fuel was updated after this response')
                    log['fuel_readiness']={'fuel_percent':value if value else None,'fuel_level':'below_quarter' if value==0 else 'reported_percent','recorded_at':c.execute('SELECT now() n').fetchone()['n'].isoformat(),'recorded_by':user['id'],
                        'driver_check_id':rid,'temporary_exception':value<100,'reason':body.reason or 'temporary_owner_authorized_review','reported_at':r['responded_at'].isoformat()}
                    log['awaiting_refuel']=False
                    c.execute('UPDATE shifts SET vehicle_log=%s WHERE id=%s',(db.json(log),s['id']))
                    c.execute("UPDATE fuel_exceptions SET status='superseded' WHERE shift_id=%s AND phase='activation' AND status IN ('requested','approved')",(s['id'],))
                    db.audit(c,user['id'],'fuel.temporary_ready','shift',s['id'],log['fuel_readiness'])
            c.execute('UPDATE driver_check_requests SET status=%s,decided_by=%s,decided_at=now(),decision=%s WHERE id=%s',(body.decision,user['id'],db.json(body.model_dump()),rid))
            db.audit(c,user['id'],'driver_check.decision','driver_check',rid,body.model_dump())
            return {'id':rid,'status':body.decision}
        return mutate(c,user,request,'driver_check.decision:'+rid,body.model_dump(),perform)

@router.post('/odometer/correct')
def correct(body:Correction,request:Request,user=Depends(auth.current_user)):
    manager(user)
    with db.tx() as c:
        target(c,user,body.driver_id)
        def perform():
            lock_fleet(c,user)
            d,s,v=target(c,user,body.driver_id)
            if int(v['details'].get('management_revision',0))!=body.expected_revision:raise db.error(409,'CHECK_STALE','تغيرت القراءة؛ حدّث الصفحة / Reading changed; refresh')
            save_odometer(c,user,d,s,v,body.value,body.reason,'manual_correction',c.execute('SELECT now() n').fetchone()['n'].isoformat())
            return {'vehicle_id':v['id'],'odometer_km':body.value}
        return mutate(c,user,request,'driver_check.correct',body.model_dump(),perform)
