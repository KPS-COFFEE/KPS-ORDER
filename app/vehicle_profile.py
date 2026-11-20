"""Validated vehicle specifications and per-vehicle operating fuel baselines."""
import math
from decimal import Decimal
from . import db

TEXT_FIELDS={'type','plate','description','color','baseline_note'}
FIELDS=TEXT_FIELDS|{'owned_by_kps','odometer_km','model_year','engine_cc','transmission','baseline_kml'}

def validate(value):
    if not isinstance(value,dict) or set(value)-FIELDS:
        raise db.error(422,'VEHICLE_INVALID','بيانات المركبة غير صحيحة.')
    result=dict(value)
    for key in TEXT_FIELDS & result.keys():
        v=result[key]
        if not isinstance(v,str) or len(v)>(500 if key=='baseline_note' else 150) or '\x00' in v:
            raise db.error(422,'VEHICLE_INVALID','بيانات المركبة غير صحيحة.')
        result[key]=v.strip()
    if 'owned_by_kps' in result and type(result['owned_by_kps']) is not bool:
        raise db.error(422,'VEHICLE_INVALID','حدد هل المركبة تابعة لكبز.')
    for key,lo,hi in [('odometer_km',0,10000000),('model_year',1950,2100),('engine_cc',50,15000)]:
        if key in result and (key=='odometer_km' or result[key] is not None) and (type(result[key]) is not int or not lo<=result[key]<=hi):
            raise db.error(422,'VEHICLE_INVALID','سنة الموديل أو سعة المحرك أو قراءة العداد غير صحيحة.')
    if result.get('transmission') not in (None,'','manual','automatic'):
        raise db.error(422,'VEHICLE_INVALID','نوع القير غير صحيح.')
    if result.get('baseline_kml') is not None:
        v=result['baseline_kml']
        if type(v) not in (int,float) or not math.isfinite(v) or not 1<=v<=60:
            raise db.error(422,'VEHICLE_INVALID','معدل الوقود يجب أن يكون من 1 إلى 60 كم/لتر.')
        result['baseline_kml']=float(v)
    return result

def fuel_status(efficiency,baseline=10):
    if efficiency is None:return 'grey'
    value=Decimal(str(efficiency));base=Decimal(str(baseline or 10))
    return 'green' if value>=base*Decimal('.9') else 'orange' if value>=base*Decimal('.8') else 'red'
