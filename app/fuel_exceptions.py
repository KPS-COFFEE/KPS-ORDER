"""Single-use, manager-approved FULL exceptions. No historical readings are rewritten."""
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from . import auth, db
from .operations import roles, mutate, safe, uid, lock_shift, shift_access
router=APIRouter(prefix='/api/fuel-exceptions',tags=['fuel-exceptions'])

class FuelRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    phase:Literal['start','end','activation']
    manager_shift_id:str
    shift_id:str|None=None
    reason:Literal['aldrees_unavailable','aldrees_balance_empty','aldrees_limit_reached']
    fuel_percent:StrictInt=Field(ge=0,lt=100)
    odometer_km:StrictInt=Field(ge=0,le=9999999)

class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    decision:Literal['approved','rejected']

def scope(c,user,driver_id,manager_id,shift_id=None):
    driver=c.execute("SELECT * FROM users WHERE id=%s AND role='driver' AND active",(driver_id,)).fetchone()
    from . import multibrand
    if multibrand.enabled():driver=multibrand.project(c,driver,user['brand_id'])
    elif driver and driver['brand_id']!=user['brand_id']:driver=None
    manager=c.execute('SELECT * FROM shifts WHERE id=%s',(manager_id,)).fetchone()
    if not driver or not manager or manager['brand_id']!=user['brand_id'] or manager['kind']!='manager':raise db.error(404,'FUEL_SCOPE','الطلب غير متاح / Request unavailable')
    if user['role']=='driver':
        if user['id']!=driver_id:raise db.error(403,'FUEL_SCOPE','غير مصرح / Forbidden')
    elif user['role']!='delivery_manager' or manager['user_id']!=user['id']:raise db.error(403,'FUEL_SCOPE','مدير التوصيل المسؤول فقط / Responsible delivery manager only')
    if not set(driver['branch_ids'])<=set(manager['branch_ids']) or not set(driver['branch_ids'])<=set(user['branch_ids']):raise db.error(403,'FUEL_SCOPE','الفروع خارج الصلاحية / Branch scope denied')
    if shift_id:
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(shift_id,)).fetchone();shift_access(c,user,s)
        if s['user_id']!=driver_id or s['manager_shift_id']!=manager_id:raise db.error(403,'FUEL_SCOPE','الشفت غير مطابق / Shift mismatch')
        vehicle=(s.get('vehicle_log') or {}).get('vehicle') or {}
    else:
        vehicle=driver.get('vehicle') or {}
    return driver,manager,vehicle

@router.post('')
def request_exception(body:FuelRequest,request:Request,user=Depends(auth.current_user)):
    roles(user,'driver')
    if (body.phase!='start') != bool(body.shift_id):raise db.error(422,'FUEL_PHASE','مرحلة غير صحيحة / Invalid phase')
    with db.tx() as c:
        driver,m,vehicle=scope(c,user,user['id'],body.manager_shift_id,body.shift_id)
        def perform():
            driver,m,vehicle=scope(c,user,user['id'],body.manager_shift_id,body.shift_id)
            if m['status'] not in (('open',) if body.phase=='start' else ('open','closing')):raise db.error(409,'FUEL_MANAGER_CLOSED','شفت المدير مغلق / Manager shift closed')
            if body.shift_id:
                if lock_shift(c,body.shift_id)['status'] not in ('open','closing'):raise db.error(409,'FUEL_SHIFT_CLOSED','الشفت منتهٍ / Shift ended')
                if body.phase=='activation' and not (lock_shift(c,body.shift_id).get('vehicle_log') or {}).get('awaiting_refuel'):raise db.error(409,'FUEL_ALREADY_READY','تم تأكيد الوقود / Fuel already confirmed')
            elif c.execute("SELECT 1 FROM shifts WHERE user_id=%s AND status<>'closed'",(user['id'],)).fetchone():raise db.error(409,'SHIFT_ALREADY_OPEN','يوجد شفت غير مقفل / Unclosed shift exists')
            c.execute("UPDATE fuel_exceptions SET status='superseded' WHERE driver_id=%s AND phase=%s AND status IN ('requested','approved')",(user['id'],body.phase))
            eid=uid()
            c.execute('INSERT INTO fuel_exceptions(id,brand_id,driver_id,manager_shift_id,shift_id,phase,reason,fuel_percent,odometer_km,vehicle) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(eid,user['brand_id'],user['id'],body.manager_shift_id,body.shift_id,body.phase,body.reason,body.fuel_percent,body.odometer_km,db.json(vehicle)))
            db.audit(c,user['id'],'fuel.request','fuel_exception',eid,body.model_dump())
            return {'id':eid,'status':'requested'}
        return mutate(c,user,request,'fuel.request',body.model_dump(),perform)

@router.get('')
def listing(user=Depends(auth.current_user)):
    roles(user,'driver','delivery_manager')
    with db.tx() as c:
        rows=c.execute("SELECT e.*,u.display_name FROM fuel_exceptions e JOIN users u ON u.id=e.driver_id JOIN shifts m ON m.id=e.manager_shift_id WHERE e.brand_id=%s AND e.status IN ('requested','approved','rejected') AND "+('e.driver_id=%s' if user['role']=='driver' else 'm.user_id=%s')+' ORDER BY e.created_at DESC LIMIT 100',(user['brand_id'],user['id'])).fetchall()
        visible=[]
        for r in rows:
            try:scope(c,user,r['driver_id'],r['manager_shift_id'],r['shift_id'])
            except Exception as exc:
                from fastapi import HTTPException
                if isinstance(exc,HTTPException):continue
                raise
            _,manager,vehicle=scope(c,user,r['driver_id'],r['manager_shift_id'],r['shift_id'])
            shift=c.execute('SELECT status FROM shifts WHERE id=%s',(r['shift_id'],)).fetchone() if r['shift_id'] else None
            r['state_reason']=('vehicle_changed' if vehicle!=r['vehicle'] else
                'manager_shift_ended' if manager['status'] not in (('open',) if r['phase']=='start' else ('open','closing')) else
                'driver_shift_ended' if shift and shift['status'] not in ('open','closing') else None)
            r['ui_status']='stale' if r['state_reason'] else r['status']
            r['requires_resubmission']=r['ui_status'] in ('stale','rejected')
            visible.append(r)
        return {'requests':safe(visible)}

@router.post('/{eid}')
def decide(eid:str,body:Decision,request:Request,user=Depends(auth.current_user)):
    roles(user,'delivery_manager')
    with db.tx() as c:
        row=c.execute('SELECT * FROM fuel_exceptions WHERE id=%s AND brand_id=%s',(eid,user['brand_id'])).fetchone()
        if not row:raise db.error(404,'FUEL_SCOPE','الطلب غير متاح / Request unavailable')
        scope(c,user,row['driver_id'],row['manager_shift_id'],row['shift_id'])
        def perform():
            r=c.execute('SELECT * FROM fuel_exceptions WHERE id=%s FOR UPDATE',(eid,)).fetchone()
            _,m,vehicle=scope(c,user,r['driver_id'],r['manager_shift_id'],r['shift_id'])
            if r['status']!='requested' or m['status'] not in (('open',) if r['phase']=='start' else ('open','closing')) or vehicle!=r['vehicle']:raise db.error(409,'FUEL_STALE','الطلب تغير؛ يلزم طلب جديد / Request changed; submit again')
            c.execute('UPDATE fuel_exceptions SET status=%s,decided_by=%s,decided_at=now() WHERE id=%s',(body.decision,user['id'],eid))
            db.audit(c,user['id'],'fuel.'+body.decision,'fuel_exception',eid,{'reason':r['reason'],'fuel_percent':r['fuel_percent']})
            return {'id':eid,'status':body.decision}
        return mutate(c,user,request,'fuel.decision:'+eid,body.model_dump(),perform)

def consume(c,user,shift,inspection,phase,vehicle):
    if inspection is None:raise db.error(422,'VEHICLE_INSPECTION_REQUIRED','أكمل فحص المركبة / Complete vehicle inspection')
    if inspection.fuel_percent==100:return
    r=c.execute('SELECT * FROM fuel_exceptions WHERE id=%s FOR UPDATE',(inspection.fuel_exception_id,)).fetchone()
    if not r or r['status']!='approved' or r['brand_id']!=shift['brand_id'] or r['driver_id']!=shift['user_id'] or r['manager_shift_id']!=shift['manager_shift_id'] or r['phase']!=phase or (phase!='start' and r['shift_id']!=shift['id']) or r['vehicle']!=vehicle or r['fuel_percent']!=inspection.fuel_percent or r['odometer_km']!=inspection.odometer_km:
        raise db.error(409,'FUEL_APPROVAL_REQUIRED','الخزان غير ممتلئ؛ اطلب موافقة مدير التوصيل / Tank not full; delivery manager approval required')
    approver=c.execute('SELECT * FROM users WHERE id=%s',(r['decided_by'],)).fetchone()
    from . import multibrand
    if multibrand.enabled():approver=multibrand.project(c,approver,shift['brand_id'])
    if not approver or not approver['active'] or approver['role']!='delivery_manager' or not auth.has_permission(approver,'shifts_approve'):raise db.error(403,'FUEL_APPROVER_REVOKED','صلاحية الاعتماد غير سارية / Approval no longer authorized')
    scope(c,approver,r['driver_id'],r['manager_shift_id'],r['shift_id'])
    c.execute("UPDATE fuel_exceptions SET status='consumed',consumed_shift_id=%s,consumed_at=now() WHERE id=%s",(shift['id'],r['id']))
    db.audit(c,user['id'],'fuel.consume','fuel_exception',r['id'],{'shift_id':shift['id'],'phase':phase})

def readiness_guard(shift):
    if shift and (shift.get('vehicle_log') or {}).get('awaiting_refuel'):
        raise db.error(409,'FUEL_NOT_READY','التوجه للتزود بالوقود قبل استقبال الطلبات / Refuel before receiving orders')

class ReadyFuel(BaseModel):
    model_config=ConfigDict(extra='forbid')
    odometer_km:StrictInt=Field(ge=0,le=9999999)
    fuel_percent:StrictInt=Field(ge=0,le=100)
    fuel_exception_id:str|None=None

@router.post('/shifts/{sid}/ready')
def confirm_ready(sid:str,body:ReadyFuel,request:Request,user=Depends(auth.current_user)):
    roles(user,'driver')
    with db.tx() as c:
        shift_access(c,user,c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone())
        def perform():
            s=lock_shift(c,sid);shift_access(c,user,s)
            log=dict(s.get('vehicle_log') or {})
            if s['user_id']!=user['id'] or s['status']!='open' or not log.get('awaiting_refuel'):raise db.error(409,'FUEL_ALREADY_READY','الشفت غير متاح للتأكيد / Shift not awaiting fuel')
            from .driver_breaks import guard
            guard(c,sid)
            floor=max([log.get('start',{}).get('odometer_km',0)]+[r['odometer_km'] for r in log.get('refuels',[])])
            if body.odometer_km<floor:raise db.error(422,'ODOMETER_DECREASE','قراءة العداد أقل من آخر قراءة / Odometer decreased')
            if body.fuel_percent==100 and not log.get('refuels'):raise db.error(409,'REFUEL_RECORD_REQUIRED','سجل تعبئة البنزين أولاً / Record refuelling first')
            consume(c,user,s,body,'activation',log.get('vehicle') or {})
            from .shift_vehicle import stamp
            log['fuel_readiness']=stamp(c,user,body);log['awaiting_refuel']=False
            c.execute('UPDATE shifts SET vehicle_log=%s WHERE id=%s',(db.json(log),sid))
            db.audit(c,user['id'],'fuel.ready','shift',sid,log['fuel_readiness'])
            return {'shift_id':sid,'available':s['available']}
        return mutate(c,user,request,'fuel.ready:'+sid,body.model_dump(),perform)
