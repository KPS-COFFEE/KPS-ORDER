"""Immutable opening inspection and append-only refuelling within the shift lock."""
from decimal import Decimal
from typing import Literal
import re
import hashlib
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from . import db

CHECKS = {'tyres':'الإطارات','brakes':'الفرامل','lights':'الإنارة','oil':'الزيت والسوائل','damage':'هيكل المركبة','cleanliness':'النظافة'}
class Inspection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    odometer_km:StrictInt=Field(ge=0,le=9999999)
    fuel_percent:StrictInt=Field(ge=0,le=100)
    fuel_exception_id:str|None=None
    checks:dict[str,Literal['ok','issue']]
    notes:str=Field(default='',max_length=1500)
    @model_validator(mode='after')
    def complete(self):
        if set(self.checks)!=set(CHECKS):raise ValueError('Complete all inspection items / أكمل جميع بنود الفحص')
        if 'issue' in self.checks.values() and not self.notes.strip():raise ValueError('Describe inspection issues / وضح ملاحظات الفحص')
        if '\x00' in self.notes:raise ValueError('Invalid note')
        return self

class Refuel(BaseModel):
    model_config=ConfigDict(extra='forbid')
    amount_halalas:StrictInt|None=Field(None,ge=0)
    station:str=Field('',max_length=200)
    receipt_id:str|None=None
    fueled_at:str=Field('',max_length=50)
    liters:Decimal=Field(gt=0,le=500,max_digits=7,decimal_places=2)
    odometer_km:StrictInt=Field(ge=0,le=9999999)
    notes:str=Field(default='',max_length=500)

def stamp(c,user,value):
    return {**value.model_dump(mode='json'),'recorded_at':c.execute('SELECT now() n').fetchone()['n'].isoformat(),'recorded_by':user['id']}

def start(c,user,target,inspection):
    if inspection is None:raise db.error(422,'VEHICLE_INSPECTION_REQUIRED','أكمل فحص المركبة وقراءة العداد والبنزين لفتح شفت السائق.')
    vehicle=dict(target.get('vehicle') or {})
    plate=re.sub(r'\s+', '', str(vehicle.get('plate','')).upper())
    if plate:
        vid='veh_'+hashlib.md5((target['brand_id']+':'+plate).encode()).hexdigest()
        c.execute('INSERT INTO fleet_vehicles(id,brand_id,plate_key,plate,details) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(vid,target['brand_id'],plate,vehicle['plate'],db.json(vehicle)))
        v=c.execute('SELECT id,details FROM fleet_vehicles WHERE brand_id=%s AND plate_key=%s FOR UPDATE',(target['brand_id'],plate)).fetchone()
        if v['details'].get('operating_status')=='paused':raise db.error(409,'VEHICLE_PAUSED','السيارة موقوفة عن التشغيل؛ اختر سيارة متاحة من الإدارة.')
        # The permanent car owns its specification/rate; freeze it for this shift.
        for key in ('type','model_year','color','engine_cc','transmission','baseline_kml','baseline_note'):
            if key in v['details']:vehicle[key]=v['details'][key]
        if c.execute("SELECT 1 FROM vehicle_operating_logs l JOIN shifts s ON s.id=l.shift_id WHERE l.vehicle_id=%s AND s.status IN ('open','closing')",(v['id'],)).fetchone():
            raise db.error(409,'VEHICLE_IN_USE','السيارة مستلمة في شفت آخر. سلّمها قبل استلامها من سائق آخر.')
        last=c.execute("SELECT max((coalesce(x.after_metrics,l.metrics)->>'end_odometer')::numeric) km FROM vehicle_operating_logs l LEFT JOIN LATERAL(SELECT after_metrics FROM vehicle_log_corrections WHERE shift_id=l.shift_id ORDER BY revision DESC LIMIT 1) x ON true WHERE l.vehicle_id=%s",(v['id'],)).fetchone()
        if last['km'] is not None and inspection.odometer_km<last['km']:
            raise db.error(422,'ODOMETER_DECREASE','قراءة العداد أقل من آخر قراءة للسيارة، حتى عند تغيير السائق.')
    previous=c.execute("SELECT vehicle_log FROM shifts WHERE user_id=%s AND kind='driver' AND vehicle_log->'end' IS NOT NULL ORDER BY opened_at DESC LIMIT 1",(target['id'],)).fetchone()
    floor=vehicle.get('odometer_km',0) or 0
    if previous and previous['vehicle_log'].get('vehicle',{}).get('plate')==vehicle.get('plate'):
        floor=max(floor,previous['vehicle_log']['end']['odometer_km'])
    if inspection.odometer_km<floor:raise db.error(422,'ODOMETER_DECREASE','قراءة العداد أقل من آخر قراءة مسجلة للمركبة.')
    return {'version':1,'vehicle':vehicle,'start':stamp(c,user,inspection),'refuels':[]}

def finish(c,user,shift,inspection):
    if inspection is None:raise db.error(422,'VEHICLE_INSPECTION_REQUIRED','أكمل فحص نهاية الشفت وقراءة العداد والبنزين قبل إرسال التقفيلة.')
    from .fuel_exceptions import consume
    consume(c,user,shift,inspection,'end',(shift.get('vehicle_log') or {}).get('vehicle') or {})
    log=dict(shift.get('vehicle_log') or {})
    floor=max([log.get('start',{}).get('odometer_km',0)]+[x['odometer_km'] for x in log.get('refuels',[])])
    if inspection.odometer_km<floor:raise db.error(422,'ODOMETER_DECREASE','قراءة النهاية لا تقل عن البداية أو آخر تعبئة.')
    # Returned closures retain every earlier ending inspection in the audit trail.
    log['end']=stamp(c,user,inspection)
    c.execute('UPDATE shifts SET vehicle_log=%s WHERE id=%s',(db.json(log),shift['id']))
    db.audit(c,user['id'],'shift.vehicle.end','shift',shift['id'],log['end'])
    return log

def summary(log):
    log=log or {};begin=log.get('start');end=log.get('end')
    return {'distance_km':end['odometer_km']-begin['odometer_km'] if begin and end else None,
            'fuel_liters':str(sum((Decimal(str(x['liters'])) for x in log.get('refuels',[])),Decimal(0))),
            'refuel_count':len(log.get('refuels',[]))}
