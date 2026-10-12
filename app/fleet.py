"""Permanent vehicle cards and persisted per-shift operating history.

Canonical shifts hold inspections/fuel; the ledger stores references and a
versioned metric snapshot. Closed corrections append overlays, never edit shifts.
No GPS or route distance is invented where KPS ORDER has no measured source.
"""
from datetime import date
from decimal import Decimal
from typing import Literal
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from . import auth, db, accounting_export, service_hours

router=APIRouter(prefix='/api/fleet',tags=['fleet'])
PERMISSION='vehicle_maintenance_manage'
Color=Literal['green','orange','red','grey']
Metric=Literal['vehicles','actual_distance','liters','efficiency','faults','exceptions','green','orange','red','grey']
COLUMNS=[('date','التاريخ','Date'),('operating_day','اليوم التشغيلي','Operating day'),('plate','السيارة','Vehicle'),
 ('driver_name','السائق','Driver'),('branches','الفرع','Branch'),('received_at','وقت الاستلام','Received'),('returned_at','وقت التسليم','Returned'),
 ('start_odometer','عداد البداية','Start odometer'),('end_odometer','عداد النهاية','End odometer'),('actual_distance','المسافة الفعلية KM','Actual KM'),
 ('gps_distance','مسافة GPS','GPS KM'),('expected_distance','مسافة الطلبات المتوقعة','Expected order KM'),('distance_variance','فرق المسافة','Variance KM'),
 ('liters','التعبئة L','Liters'),('efficiency','كفاءة KM/L','KM/L'),('full_start','فل عند الاستلام','Full at start'),('full_end','فل عند التسليم','Full at end'),
 ('fuel_status','حالة البنزين','Fuel status'),('orders','عدد الطلبات','Orders'),('faults','عدد الأعطال','Faults'),('exception','استثناء','Exception'),
 ('notes','ملاحظات','Notes'),('record_status','حالة السجل','Record status')]
DEFINITIONS=('KM/L: full-to-full measured distance / filled liters. Grey: open or incomplete readings, not full-to-full, or zero fuel/distance. '
 'Provisional baseline 10 KM/L: Green >=9; Orange >=8 and <9; Red <8. This is a review indicator, not driver liability. '
 'GPS and expected route distance remain blank until measured canonical data is available. '
 'Average KM/L uses only comparable full-to-full rows (sum KM / sum liters), not a mean of ratios. '
 'One row per vehicle shift. Multiple shifts in one day remain separate. Operational day starts 16:00 Asia/Riyadh. '
 'الفترة يوم تشغيلي بتوقيت الرياض. الكفاءة من فل إلى فل فقط؛ الرمادي بيانات ناقصة. الألوان للمراجعة ولا تثبت مسؤولية السائق.')

def access(user=Depends(auth.require_permission(PERMISSION))):return user

def scoped(user):
    clauses=['s.brand_id=%s'];args=[user['brand_id']]
    if user['role']!='owner':
        # A multi-branch record is visible only when ALL its branches are authorized.
        clauses.append('s.branch_ids <@ %s::jsonb AND jsonb_array_length(s.branch_ids)>0');args.append(db.json(user.get('branch_ids') or []))
    return clauses,args

BASE=""" FROM vehicle_operating_logs l JOIN shifts s ON s.id=l.shift_id
 JOIN fleet_vehicles v ON v.id=l.vehicle_id AND v.brand_id=s.brand_id JOIN users u ON u.id=s.user_id
 LEFT JOIN LATERAL(SELECT after_metrics FROM vehicle_log_corrections WHERE shift_id=l.shift_id ORDER BY revision DESC LIMIT 1) correction ON true """
METRICS='coalesce(correction.after_metrics,l.metrics)'
SELECT=f"""SELECT l.shift_id,l.vehicle_id,l.operating_day,l.revision,v.plate,v.details,u.id driver_id,u.display_name driver_name,
 s.branch_ids,s.opened_at received_at,coalesce((s.vehicle_log->'end'->>'recorded_at')::timestamptz,(s.vehicle_log->'end_exception'->>'recorded_at')::timestamptz,s.submitted_at) returned_at,
 {METRICS} metrics,
 (SELECT count(DISTINCT t.order_id) FROM driver_tasks t WHERE t.shift_id=s.id) orders,
 GREATEST((SELECT count(*) FROM vehicle_reports r WHERE r.brand_id=s.brand_id AND r.driver_id=s.user_id
 AND r.branch_id IN (SELECT jsonb_array_elements_text(s.branch_ids))
 AND regexp_replace(upper(r.vehicle_plate),'[[:space:]]','','g')=v.plate_key
 AND r.created_at>=s.opened_at AND r.created_at<coalesce(s.submitted_at,s.closed_at,now())),
 ((EXISTS(SELECT 1 FROM jsonb_each_text(coalesce(s.vehicle_log->'start'->'checks','{{}}')) check_start WHERE check_start.value='issue')
 OR EXISTS(SELECT 1 FROM jsonb_each_text(coalesce(s.vehicle_log->'end'->'checks','{{}}')) check_end WHERE check_end.value='issue'))::int)) faults
 """

def filters(user,start=None,end=None,vehicle_id=None,driver_id=None,branch_id=None,fuel_status=None,faults_only=False,exceptions_only=False,metric=None):
    lo=start or service_hours.delivery_operational_date();hi=end or lo
    if hi<lo or (hi-lo).days>366:raise db.error(422,'FLEET_PERIOD_INVALID','اختر فترة صحيحة لا تتجاوز سنة.')
    clauses,args=scoped(user);clauses+=['l.operating_day BETWEEN %s AND %s'];args +=[lo,hi]
    for name,value in [('l.vehicle_id',vehicle_id),('s.user_id',driver_id)]:
        if value:clauses.append(name+'=%s');args.append(value)
    if branch_id:clauses.append('s.branch_ids ? %s');args.append(branch_id)
    if fuel_status:clauses.append(f"{METRICS}->>'fuel_status'=%s");args.append(fuel_status)
    if metric in ('green','orange','red','grey'):
        clauses.append(f"{METRICS}->>'fuel_status'=%s");args.append(metric)
    if exceptions_only or metric=='exceptions':clauses.append(f"({METRICS}->>'exception')::boolean")
    # Faults and numeric metrics are applied to a fully scoped inner result.
    outer=[]
    if faults_only or metric=='faults':outer.append('faults>0')
    if metric in ('actual_distance','liters','efficiency'):
        outer.append("metrics->>'"+metric+"' IS NOT NULL"+(" AND (metrics->>'liters')::numeric>0" if metric=='liters' else ''))
    return lo,hi,clauses,args,outer

def query_statement(user,**kwargs):
    lo,hi,clauses,args,outer=filters(user,**kwargs)
    statement='SELECT * FROM ('+SELECT+BASE+' WHERE '+' AND '.join(clauses)+') q'
    if outer:statement+=' WHERE '+' AND '.join(outer)
    return lo,hi,statement,args

def present_rows(c,user,rows):
    names={b['id']:b['name'] for b in c.execute('SELECT id,name FROM branches WHERE brand_id=%s',(user['brand_id'],)).fetchall()}
    for r in rows:
        r.update(r.pop('metrics'));r['date']=r['received_at'].astimezone(service_hours.RIYADH).date().isoformat()
        r['branches']=' / '.join(names.get(b,'—') for b in r.pop('branch_ids'))
    return rows

def query_rows(c,user,limit=None,offset=0,**kwargs):
    lo,hi,statement,args=query_statement(user,**kwargs)
    statement+=' ORDER BY operating_day DESC,received_at DESC,shift_id DESC'
    if limit is not None:statement+=' LIMIT %s OFFSET %s';args +=[limit,offset]
    return lo,hi,present_rows(c,user,c.execute(statement,args).fetchall())

def query_summary(c,user,**kwargs):
    lo,hi,statement,args=query_statement(user,**kwargs)
    stats=c.execute("""SELECT count(*) records,count(DISTINCT vehicle_id) vehicles,
      coalesce(sum((metrics->>'actual_distance')::numeric),0) actual_distance,
      coalesce(sum((metrics->>'liters')::numeric),0) liters,
      round(sum((metrics->>'actual_distance')::numeric) FILTER (WHERE metrics->>'efficiency' IS NOT NULL)
       / nullif(sum((metrics->>'liters')::numeric) FILTER (WHERE metrics->>'efficiency' IS NOT NULL),0),3) efficiency,
      count(*) FILTER(WHERE metrics->>'fuel_status'='green') green,
      count(*) FILTER(WHERE metrics->>'fuel_status'='orange') orange,
      count(*) FILTER(WHERE metrics->>'fuel_status'='red') red,
      count(*) FILTER(WHERE metrics->>'fuel_status'='grey') grey,
      coalesce(sum(faults),0) faults,count(*) FILTER(WHERE (metrics->>'exception')::boolean) exceptions
      FROM ("""+statement+') scoped_records',args).fetchone()
    return stats

def summary(rows):
    eligible=[r for r in rows if r.get('efficiency') is not None]
    liters=sum(float(r['liters']) for r in eligible)
    return dict(vehicles=len({r['vehicle_id'] for r in rows}),actual_distance=round(sum(float(r['actual_distance'] or 0) for r in rows),3),
      liters=round(sum(float(r['liters'] or 0) for r in rows),3),efficiency=round(sum(float(r['actual_distance']) for r in eligible)/liters,3) if liters else None,
      **{color:sum(r['fuel_status']==color for r in rows) for color in ('green','orange','red','grey')},
      faults=sum(r['faults'] for r in rows),exceptions=sum(bool(r['exception']) for r in rows),records=len(rows))

@router.get('/vehicles')
def vehicles(user=Depends(access)):
    with db.tx() as c:
        clauses,args=scoped(user)
        current_scope='u.brand_id=%s AND u.role=\'driver\''
        current_args=[user['brand_id']]
        if user['role']!='owner':
            current_scope+=' AND u.branch_ids <@ %s::jsonb AND jsonb_array_length(u.branch_ids)>0';current_args.append(db.json(user.get('branch_ids') or []))
        rows=c.execute('SELECT DISTINCT v.id,v.plate,v.details'+BASE+' WHERE '+' AND '.join(clauses)+
         ' UNION SELECT v.id,v.plate,v.details FROM fleet_vehicles v JOIN users u ON u.brand_id=v.brand_id AND v.plate_key=regexp_replace(upper(u.vehicle->>\'plate\'),\'[[:space:]]\',\'\',\'g\') WHERE '+current_scope+' ORDER BY plate,id',args+current_args).fetchall()
        return {'vehicles':rows}

@router.get('/options')
def options(user=Depends(access)):
    with db.tx() as c:
        clauses,args=scoped(user)
        people=c.execute('SELECT DISTINCT u.id,u.display_name'+BASE+' WHERE '+' AND '.join(clauses)+' ORDER BY u.display_name,u.id',args).fetchall()
        branches=c.execute('SELECT id,name,branch_no FROM branches WHERE brand_id=%s ORDER BY branch_no',(user['brand_id'],)).fetchall()
        return {'drivers':people,'branches':[b for b in branches if user['role']=='owner' or b['id'] in user.get('branch_ids',[])],
                'definitions':DEFINITIONS,'today':service_hours.delivery_operational_date(),'columns':[{'key':k,'ar':a,'en':e} for k,a,e in COLUMNS]}

@router.get('/report')
def report(start:date|None=None,end:date|None=None,vehicle_id:str|None=None,driver_id:str|None=None,branch_id:str|None=None,
           fuel_status:Color|None=None,faults_only:bool=False,exceptions_only:bool=False,metric:Metric|None=None,
           limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0,le=1000000),user=Depends(access)):
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        selected=dict(start=start,end=end,vehicle_id=vehicle_id,driver_id=driver_id,branch_id=branch_id,fuel_status=fuel_status,faults_only=faults_only,exceptions_only=exceptions_only,metric=metric)
        lo,hi,rows=query_rows(c,user,limit=limit,offset=offset,**selected)
        totals=query_summary(c,user,**selected)
        return {'rows':rows,'total':totals['records'],'summary':totals,'period':{'start':lo,'end':hi},'limit':limit,'offset':offset}

@router.get('/vehicles/{vid}')
def vehicle(vid:str,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0,le=1000000),user=Depends(access)):
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        clauses,args=scoped(user);clauses.append('l.vehicle_id=%s');args.append(vid)
        card=c.execute('SELECT DISTINCT v.id,v.plate,v.details'+BASE+' WHERE '+' AND '.join(clauses),args).fetchone()
        if not card:
            current_clause="u.brand_id=%s AND u.role='driver' AND v.id=%s";current_args=[user['brand_id'],vid]
            if user['role']!='owner':
                current_clause+=' AND u.branch_ids <@ %s::jsonb AND jsonb_array_length(u.branch_ids)>0';current_args.append(db.json(user.get('branch_ids') or []))
            card=c.execute("SELECT DISTINCT v.id,v.plate,v.details FROM fleet_vehicles v JOIN users u ON u.brand_id=v.brand_id AND v.plate_key=regexp_replace(upper(u.vehicle->>'plate'),'[[:space:]]','','g') WHERE "+current_clause,current_args).fetchone()
            if not card:raise db.error(404,'VEHICLE_NOT_FOUND','السيارة غير موجودة ضمن نطاقك.')
        where=' WHERE '+' AND '.join(clauses)
        total=c.execute('SELECT count(*) n'+BASE+where,args).fetchone()['n']
        rows=c.execute(SELECT+BASE+where+' ORDER BY l.operating_day DESC,s.opened_at DESC,l.shift_id DESC LIMIT %s OFFSET %s',args+[limit,offset]).fetchall()
        return {'vehicle':card,'rows':present_rows(c,user,rows),'total':total,'limit':limit,'offset':offset}

@router.get('/export')
def export(format:Literal['pdf','xlsx'],lang:Literal['ar','en']='ar',start:date|None=None,end:date|None=None,
           vehicle_id:str|None=None,driver_id:str|None=None,branch_id:str|None=None,fuel_status:Color|None=None,
           faults_only:bool=False,exceptions_only:bool=False,metric:Metric|None=None,user=Depends(access)):
    with db.tx() as c:
        lo,hi,rows=query_rows(c,user,start=start,end=end,vehicle_id=vehicle_id,driver_id=driver_id,branch_id=branch_id,fuel_status=fuel_status,faults_only=faults_only,exceptions_only=exceptions_only,metric=metric)
        db.audit(c,user['id'],'fleet.export','fleet',user['brand_id'],{'format':format,'start':str(lo),'end':str(hi),'rows':len(rows)})
    keys=[k for k,_,_ in COLUMNS];headers=[e if lang=='en' else a for _,a,e in COLUMNS]
    from datetime import datetime
    def value(v):
        if v is None:return '—'
        if type(v) is bool:return ('Yes' if v else 'No') if lang=='en' else ('نعم' if v else 'لا')
        if isinstance(v,datetime):return v.astimezone(service_hours.RIYADH).strftime('%Y-%m-%d %H:%M')
        if isinstance(v,(int,float)):return v
        translations={'green':'أخضر','orange':'برتقالي','red':'أحمر','grey':'رمادي','closed':'مغلق','open':'مفتوح','exception':'استثناء'}
        return translations.get(str(v),str(v)) if lang=='ar' else str(v)
    values=[[value(r.get(k)) for k in keys] for r in rows]
    title='تقرير الأسطول / Fleet daily report';meta=f'{lo} - {hi} | Asia/Riyadh'
    totals=summary(rows)
    labels={'vehicles':('السيارات المستخدمة','Vehicles used'),'actual_distance':('إجمالي KM','Total KM'),'liters':('إجمالي L','Total liters'),
      'efficiency':('متوسط KM/L','Average KM/L'),'green':('أخضر','Green'),'orange':('برتقالي','Orange'),'red':('أحمر','Red'),'grey':('رمادي','Grey'),
      'faults':('الأعطال','Faults'),'exceptions':('الاستثناءات','Exceptions')}
    meta+='\n'+' | '.join(labels[k][lang=='en']+': '+('—' if totals[k] is None else str(totals[k])) for k in labels)
    definitions=DEFINITIONS
    if format=='xlsx':payload=accounting_export.xlsx([headers,*values],keys,[title,meta,definitions],lang)
    else:
        # Split 23 columns into readable landscape pages, retain row identity
        # in both sections using matching row numbers. All fields remain in the PDF.
        payload=make_pdf(rows,lang,title,meta,definitions)
    return Response(payload,media_type='application/pdf' if format=='pdf' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="fleet-{lo}-{hi}.{format}"','Cache-Control':'no-store'})

def make_pdf(rows,lang,title,meta,definitions):
    from datetime import datetime
    translations={'green':'أخضر','orange':'برتقالي','red':'أحمر','grey':'رمادي','closed':'مغلق','open':'مفتوح','exception':'استثناء'}
    def value(v):
        if v is None:return '—'
        if type(v) is bool:return ('Yes' if v else 'No') if lang=='en' else ('نعم' if v else 'لا')
        if isinstance(v,datetime):return v.astimezone(service_hours.RIYADH).strftime('%Y-%m-%d %H:%M')
        if isinstance(v,(int,float)):return v
        return translations.get(str(v),str(v)) if lang=='ar' else str(v)
    definitions_map={k:e if lang=='en' else a for k,a,e in COLUMNS}
    sections=[]
    for title_part,keys in [
       (('الحركة والمسافات','Movement and distances'),['date','operating_day','plate','driver_name','branches','received_at','returned_at','start_odometer','end_odometer','actual_distance','gps_distance','expected_distance','distance_variance']),
       (('الوقود والأعطال والاستثناءات','Fuel, faults and exceptions'),['operating_day','plate','driver_name','liters','efficiency','full_start','full_end','fuel_status','orders','faults','exception','notes','record_status'])]:
        headers=[('الصف' if lang=='ar' else 'Row'),*[definitions_map[k] for k in keys]]
        values=[[i,*[value(r.get(k)) for k in keys]] for i,r in enumerate(rows,1)]
        sections.append({'title':title_part[lang=='en'],'keys':['row_number',*keys],'rows':[headers,*values]})
    return accounting_export.pdf([],[],title,meta,definitions,lang,sections=sections)

class Correction(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    revision:StrictInt=Field(ge=1)
    reason:str=Field(min_length=1,max_length=2000)
    start_odometer:StrictInt=Field(ge=0,le=9999999)
    end_odometer:StrictInt=Field(ge=0,le=9999999)
    liters:Decimal=Field(ge=0,le=10000,max_digits=9,decimal_places=2)
    full_start:bool
    full_end:bool
    notes:str=Field(default='',max_length=1500)
    @field_validator('reason','notes')
    @classmethod
    def safe_text(cls,v):
        if '\x00' in v:raise ValueError('Invalid text')
        return v

@router.get('/vehicles/{vid}/logs/{sid}')
def detail(vid:str,sid:str,user=Depends(access)):
    with db.tx() as c:
        row=log_access(c,user,vid,sid)
        events=c.execute('SELECT e.*,u.display_name actor_name FROM vehicle_log_corrections e JOIN users u ON u.id=e.actor_id WHERE shift_id=%s ORDER BY e.revision',(sid,)).fetchall()
        return {'log':row,'corrections':events}

def log_access(c,user,vid,sid,lock=False):
    clauses,args=scoped(user);clauses+=['l.vehicle_id=%s','l.shift_id=%s'];args +=[vid,sid]
    row=c.execute('SELECT l.*,s.status shift_status,'+METRICS+' effective_metrics'+BASE+' WHERE '+' AND '.join(clauses)+(' FOR UPDATE OF l' if lock else ''),args).fetchone()
    if not row:raise db.error(404,'VEHICLE_LOG_NOT_FOUND','السجل غير موجود ضمن نطاقك.')
    return row

@router.post('/vehicles/{vid}/logs/{sid}/corrections')
def correct(vid:str,sid:str,body:Correction,request:Request,user=Depends(access)):
    with db.tx() as c:
        row=log_access(c,user,vid,sid,True)
        def perform():
            if row['shift_status']!='closed':raise db.error(409,'VEHICLE_LOG_OPEN','السجل مفتوح؛ أكمل بياناته من الشفت.')
            if row['revision']!=body.revision:raise db.error(409,'VEHICLE_LOG_CHANGED','حدّث السجل قبل التصحيح.')
            if body.end_odometer<body.start_odometer:raise db.error(422,'ODOMETER_DECREASE','قراءة النهاية أقل من البداية.')
            before=row['effective_metrics'];after=dict(before)
            after.update(body.model_dump(mode='json',exclude={'reason','revision'}));after['liters']=float(body.liters)
            after['actual_distance']=body.end_odometer-body.start_odometer
            efficiency=after['actual_distance']/float(body.liters) if body.full_start and body.full_end and body.liters>0 and after['actual_distance']>0 else None
            after['efficiency']=round(efficiency,3) if efficiency is not None else None
            after['fuel_status']='grey' if efficiency is None else 'green' if efficiency>=9 else 'orange' if efficiency>=8 else 'red'
            after['distance_variance']=after['actual_distance']-after['expected_distance'] if after.get('expected_distance') is not None else None
            after['exception']=True;after['record_status']='exception'
            revision=row['revision']+1
            c.execute('INSERT INTO vehicle_log_corrections(id,shift_id,revision,actor_id,reason,before_metrics,after_metrics) VALUES(%s,%s,%s,%s,%s,%s,%s)',(db.new_id('vlc_'),sid,revision,user['id'],body.reason,db.json(before),db.json(after)))
            c.execute('UPDATE vehicle_operating_logs SET revision=%s WHERE shift_id=%s',(revision,sid))
            db.audit(c,user['id'],'vehicle_log.correct','vehicle_log',sid,{'vehicle_id':vid,'revision':revision,'reason':body.reason,'before':before,'after':after})
            return {'shift_id':sid,'revision':revision,'metrics':after}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'vehicle_log.correct:'+sid,body.model_dump(mode='json'),perform)
