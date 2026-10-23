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
    confirm_full:bool=False
    amount_halalas:StrictInt|None=Field(None,ge=0)
    station:str=Field('',max_length=200)
    receipt_id:str|None=None
    fueled_at:str=Field('',max_length=50)
    liters:Decimal=Field(gt=0,le=500,max_digits=7,decimal_places=2)
    odometer_km:StrictInt=Field(ge=0,le=9999999)
    notes:str=Field(default='',max_length=500)

def stamp(c,user,value):
    return {**value.model_dump(mode='json'),'recorded_at':c.execute('SELECT now() n').fetchone()['n'].isoformat(),'recorded_by':user['id']}

def odometer_floor(log):
    checkpoint=log.get('odometer_checkpoint') or {}
    readings=[log.get('start') or {},log.get('fuel_readiness') or {},*(log.get('refuels') or [])]
    if checkpoint:
        readings=[r for r in readings if r.get('recorded_at','')>checkpoint['recorded_at']]
    return max([checkpoint.get('odometer_km',0),*[r.get('odometer_km',0) for r in readings]])

def start(c,user,target,inspection):
    if inspection is None:raise db.error(422,'VEHICLE_INSPECTION_REQUIRED','أكمل فحص المركبة وقراءة العداد والبنزين لفتح شفت السائق.')
    vehicle=dict(target.get('vehicle') or {})
    plate=re.sub(r'\s+', '', str(vehicle.get('plate','')).upper())
    if plate:
        from .central_operations import enabled as central_enabled
        if central_enabled():
            # Brand asset ledgers remain separate, but a plate is one physical car.
            # Serialize receipts across both ledgers before testing occupancy.
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('physical-vehicle:'+plate,))
            cards=c.execute('SELECT id,details FROM fleet_vehicles WHERE plate_key=%s ORDER BY id',(plate,)).fetchall()
            if any(card['details'].get('operating_status')=='paused' for card in cards):
                raise db.error(409,'VEHICLE_PAUSED','السيارة موقوفة عن التشغيل؛ راجع الإدارة.')
            if c.execute("SELECT 1 FROM fleet_operating_segments l JOIN fleet_vehicles v ON v.id=l.vehicle_id JOIN shifts s ON s.id=l.shift_id WHERE v.plate_key=%s AND s.status IN ('open','closing') AND l.returned_at IS NULL",(plate,)).fetchone():
                raise db.error(409,'VEHICLE_IN_USE','السيارة مستلمة في شفت آخر. سلّمها قبل استلامها من سائق آخر.')
            last=c.execute("SELECT max((coalesce(x.after_metrics,l.metrics)->>'end_odometer')::numeric) km FROM fleet_operating_segments l JOIN fleet_vehicles v ON v.id=l.vehicle_id LEFT JOIN LATERAL(SELECT after_metrics FROM vehicle_log_corrections WHERE shift_id=l.shift_id AND NOT l.multi_vehicle ORDER BY revision DESC LIMIT 1) x ON true WHERE v.plate_key=%s",(plate,)).fetchone()
            physical_floor=max([int(last['km'] or 0),*[int(card['details'].get('odometer_km') or 0) for card in cards]])
            if inspection.odometer_km<physical_floor:
                raise db.error(422,'ODOMETER_DECREASE','قراءة العداد أقل من آخر قراءة للسيارة عبر سجلات التشغيل.')
        vid='veh_'+hashlib.md5((target['brand_id']+':'+plate).encode()).hexdigest()
        c.execute('INSERT INTO fleet_vehicles(id,brand_id,plate_key,plate,details) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(vid,target['brand_id'],plate,vehicle['plate'],db.json(vehicle)))
        v=c.execute('SELECT id,details FROM fleet_vehicles WHERE brand_id=%s AND plate_key=%s FOR UPDATE',(target['brand_id'],plate)).fetchone()
        if v['details'].get('operating_status')=='paused':raise db.error(409,'VEHICLE_PAUSED','السيارة موقوفة عن التشغيل؛ اختر سيارة متاحة من الإدارة.')
        # The permanent car owns its specification/rate; freeze it for this shift.
        for key in ('type','model_year','color','engine_cc','transmission','baseline_kml','baseline_note'):
            if key in v['details']:vehicle[key]=v['details'][key]
        if c.execute("SELECT 1 FROM fleet_operating_segments l JOIN shifts s ON s.id=l.shift_id WHERE l.vehicle_id=%s AND s.status IN ('open','closing') AND l.returned_at IS NULL",(v['id'],)).fetchone():
            raise db.error(409,'VEHICLE_IN_USE','السيارة مستلمة في شفت آخر. سلّمها قبل استلامها من سائق آخر.')
        last=c.execute("SELECT max((coalesce(x.after_metrics,l.metrics)->>'end_odometer')::numeric) km FROM fleet_operating_segments l LEFT JOIN LATERAL(SELECT after_metrics FROM vehicle_log_corrections WHERE shift_id=l.shift_id AND NOT l.multi_vehicle ORDER BY revision DESC LIMIT 1) x ON true WHERE l.vehicle_id=%s",(v['id'],)).fetchone()
        checkpoint=v['details'].get('odometer_checkpoint')
        if checkpoint:
            last=c.execute("SELECT max((coalesce(x.after_metrics,l.metrics)->>'end_odometer')::numeric) km FROM fleet_operating_segments l LEFT JOIN LATERAL(SELECT after_metrics FROM vehicle_log_corrections WHERE shift_id=l.shift_id AND NOT l.multi_vehicle ORDER BY revision DESC LIMIT 1) x ON true WHERE l.vehicle_id=%s AND l.returned_at>%s::timestamptz",(v['id'],checkpoint['recorded_at'])).fetchone()
            vehicle['odometer_km']=v['details']['odometer_km']
        if last['km'] is not None and inspection.odometer_km<last['km']:
            raise db.error(422,'ODOMETER_DECREASE','قراءة العداد أقل من آخر قراءة للسيارة، حتى عند تغيير السائق.')
    previous=c.execute("SELECT vehicle_log FROM shifts WHERE user_id=%s AND kind='driver' AND vehicle_log->'end' IS NOT NULL ORDER BY opened_at DESC LIMIT 1",(target['id'],)).fetchone()
    floor=vehicle.get('odometer_km',0) or 0
    if previous and previous['vehicle_log'].get('vehicle',{}).get('plate')==vehicle.get('plate') and (not plate or not v['details'].get('odometer_checkpoint') or previous['vehicle_log']['end'].get('recorded_at','')>v['details']['odometer_checkpoint']['recorded_at']):
        floor=max(floor,previous['vehicle_log']['end']['odometer_km'])
    if inspection.odometer_km<floor:raise db.error(422,'ODOMETER_DECREASE','قراءة العداد أقل من آخر قراءة مسجلة للمركبة.')
    return {'version':1,'vehicle':vehicle,'start':stamp(c,user,inspection),'refuels':[]}

def finish(c,user,shift,inspection):
    anchor = (shift.get('vehicle_log') or {}).get('central_vehicle_shift_id')
    if anchor:
        from .central_workdays import association, require_action
        require_action()
        group, physical = association(c, shift['id']), association(c, anchor)
        if not group or not physical or group['id'] != physical['id'] or physical['anchor_shift_id'] != anchor:
            raise db.error(409, 'CENTRAL_VEHICLE_SCOPE', 'مرجع فحص المركبة غير مطابق للوردية.')
        log = c.execute('SELECT vehicle_log FROM shifts WHERE id=%s', (anchor,)).fetchone()['vehicle_log']
        if not log.get('end'):
            raise db.error(409, 'CENTRAL_VEHICLE_INSPECTION', 'أكمل فحص نهاية الوردية أولًا.')
        return shift['vehicle_log']
    if inspection is None:raise db.error(422,'VEHICLE_INSPECTION_REQUIRED','أكمل فحص نهاية الشفت وقراءة العداد والبنزين قبل إرسال التقفيلة.')
    from .fuel_exceptions import consume
    consume(c,user,shift,inspection,'end',(shift.get('vehicle_log') or {}).get('vehicle') or {})
    log=dict(shift.get('vehicle_log') or {})
    floor=odometer_floor(log)
    if inspection.odometer_km<floor:raise db.error(422,'ODOMETER_DECREASE','قراءة النهاية لا تقل عن البداية أو آخر تعبئة.')
    # Returned closures retain every earlier ending inspection in the audit trail.
    log['end']=stamp(c,user,inspection)
    c.execute('UPDATE shifts SET vehicle_log=%s WHERE id=%s',(db.json(log),shift['id']))
    db.audit(c,user['id'],'shift.vehicle.end','shift',shift['id'],log['end'])
    return log

def summary(log):
    log=log or {}
    if log.get('previous_segments'):
        parts=[summary(p) for p in log['previous_segments']]+[summary({k:v for k,v in log.items() if k!='previous_segments'})]
        return {'distance_km':sum(p['distance_km'] for p in parts) if all(p['distance_km'] is not None for p in parts) else None,'fuel_liters':str(sum(Decimal(p['fuel_liters']) for p in parts)),'refuel_count':sum(p['refuel_count'] for p in parts)}
    begin=log.get('start');end=log.get('end')
    return {'distance_km':end['odometer_km']-begin['odometer_km'] if begin and end and not log.get('odometer_adjusted') else None,
            'fuel_liters':str(sum((Decimal(str(x['liters'])) for x in log.get('refuels',[])),Decimal(0))),
            'refuel_count':len(log.get('refuels',[]))}
