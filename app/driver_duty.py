"""Per-identity duty duration; snapshots never rewrite completed shifts."""
from datetime import timedelta
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from . import auth, db, multibrand
from .operations import mutate, safe, same_scope, unrestricted_owner

router = APIRouter(prefix='/api/driver-duty', tags=['driver-duty'])

class Duration(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    minutes: StrictInt = Field(ge=30, le=1440)
    reason: str = Field(min_length=3, max_length=1000)
    @field_validator('reason')
    @classmethod
    def valid_reason(cls, value):
        if '\x00' in value: raise ValueError('Invalid text')
        return value

def authorize(user):
    if user['role'] != 'owner' and not auth.has_permission(user, 'driver_duty_manage'):
        raise db.error(403, 'DRIVER_DUTY_FORBIDDEN', 'تحديد ساعات الدوام للمالك والمفوض بهذه الصلاحية فقط.')

def driver(c, user, identifier):
    canonical = c.execute("SELECT * FROM users WHERE id=%s AND role='driver' AND active", (identifier,)).fetchone()
    row = multibrand.project(c, canonical, user['brand_id']) if multibrand.enabled() else canonical
    if not row or not same_scope(user, row) or (not unrestricted_owner(user) and not set(row['branch_ids']) <= set(user['branch_ids'])):
        raise db.error(404, 'DRIVER_DUTY_SCOPE', 'السائق خارج نطاق الصلاحية.')
    if multibrand.enabled() and not unrestricted_owner(user):
        # One identity has one duration across its physical workday. A scoped
        # delegate cannot change another brand's duty through a shared driver.
        for brand in multibrand.available(c, canonical):
            subject = multibrand.project(c, canonical, brand['id'])
            principal = multibrand.project(c, user, brand['id'])
            if (not principal or not subject
                    or not auth.has_permission(principal, 'driver_duty_manage')
                    or not set(subject['branch_ids']) <= set(principal.get('branch_ids') or [])):
                raise db.error(404, 'DRIVER_DUTY_SCOPE', 'السائق خارج نطاق الصلاحية.')
    return row

@router.get('')
def listing(user=Depends(auth.current_user)):
    authorize(user)
    with db.tx() as c:
        candidates = c.execute("SELECT id FROM users WHERE role='driver' AND active ORDER BY display_name,id").fetchall()
        output=[]
        from fastapi import HTTPException
        for candidate in candidates:
            try: row=driver(c,user,candidate['id'])
            except HTTPException as exc:
                if exc.status_code==404: continue
                raise
            setting=c.execute('SELECT minutes,updated_at FROM driver_duty_profiles WHERE driver_id=%s',(row['id'],)).fetchone()
            output.append({'id':row['id'],'name':row['display_name'],'minutes':setting['minutes'] if setting else None,'updated_at':setting['updated_at'] if setting else None})
        return safe({'drivers':output,'applies_to':'next_shift'})

@router.put('/{identifier}')
def configure(identifier:str, body:Duration, request:Request, user=Depends(auth.current_user)):
    authorize(user)
    with db.tx() as c:
        driver(c,user,identifier)
        def perform():
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('shift:'+identifier,))
            before=c.execute('SELECT minutes FROM driver_duty_profiles WHERE driver_id=%s FOR UPDATE',(identifier,)).fetchone()
            c.execute('INSERT INTO driver_duty_profiles(driver_id,minutes,updated_by) VALUES(%s,%s,%s) ON CONFLICT(driver_id) DO UPDATE SET minutes=excluded.minutes,updated_by=excluded.updated_by,updated_at=now()', (identifier,body.minutes,user['id']))
            db.audit(c,user['id'],'driver_duty.configured','user',identifier,{'before':before['minutes'] if before else None,'minutes':body.minutes,'reason':body.reason,'applies_to':'next_shift'})
            return {'driver_id':identifier,'minutes':body.minutes,'applies_to':'next_shift'}
        return mutate(c,user,request,'driver_duty.configure:'+identifier,body.model_dump(),perform)

def snapshot(c, driver_id, shift_id, anchor=None):
    if anchor:
        c.execute('UPDATE shifts SET scheduled_end_at=(SELECT scheduled_end_at FROM shifts WHERE id=%s) WHERE id=%s',(anchor,shift_id))
        return
    profile=c.execute('SELECT minutes FROM driver_duty_profiles WHERE driver_id=%s',(driver_id,)).fetchone()
    if profile:
        c.execute('UPDATE shifts SET scheduled_end_at=opened_at + %s WHERE id=%s',(timedelta(minutes=profile['minutes']),shift_id))
