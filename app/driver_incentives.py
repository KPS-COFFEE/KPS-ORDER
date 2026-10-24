"""Nonmonetary driver entitlement, sourced from canonical tasks and approved shifts."""
import calendar
import hashlib
import json
from collections import defaultdict
from datetime import date
from typing import Literal
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from . import auth, db, service_hours
from .operations import fail, safe, mutate, shift_access

router=APIRouter(prefix='/api/driver-incentives',tags=['driver-incentives'])
DAY="coalesce(s.reporting_operational_date,s.delivery_operational_date,(s.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '4 hours')::date)"
SOURCE="""SELECT o.id order_id,coalesce(o.snapshot->>'order_reference',o.order_no::text) order_reference,
 o.preparation_status,o.payment_status,o.brand_id,o.branch_id,
 t.id task_id,t.status task_status,t.driver_id,t.shift_id,
 s.status shift_status,s.branch_ids,s.manager_shift_id,u.display_name driver_name,
 """+DAY+""" operational_day,
 coalesce((SELECT array_agg(r.id ORDER BY r.id) FROM refunds r WHERE r.order_id=o.id AND r.amendment_id IS NULL),ARRAY[]::text[]) refund_ids,
 d.task_id decision_task_id,d.responsibility,d.reason,d.incident_key saved_incident_key,
 d.version decision_version,d.decided_by,d.decided_at
 FROM orders o
 JOIN LATERAL (SELECT * FROM driver_tasks t WHERE t.order_id=o.id
   AND t.status NOT IN ('transferred','reassigned')
   ORDER BY (t.status='delivered') DESC,t.created_at DESC,t.id DESC LIMIT 1) t ON true
 JOIN shifts s ON s.id=t.shift_id AND s.brand_id=o.brand_id
 JOIN users u ON u.id=t.driver_id
 LEFT JOIN driver_incentive_decisions d ON d.order_id=o.id
 WHERE o.brand_id=%s AND o.test_void IS NULL"""

def read_access(user=Depends(auth.current_user)):
    permitted=user['role'] in ('owner','driver','delivery_manager') or auth.has_permission(user,'accounting_reports') or (user['role']=='admin' and auth.has_permission(user,'shifts_view'))
    if not permitted:fail(403,'صلاحية عرض استحقاق السائقين غير متاحة')
    return user

def scoped(user,alias='s'):
    if user['role']=='driver':return f' AND {alias}.user_id=%s',[user['id']]
    if user['role']=='delivery_manager':return f' AND {alias}.manager_shift_id IN (SELECT id FROM shifts WHERE user_id=%s)',[user['id']]
    return '',[]

def source_rows(c,user,start=None,end=None,shift_id=None,order_id=None,driver_id='',branch_ids=None):
    query=SOURCE;args=[user['brand_id']]
    if start is not None:query+=' AND '+DAY+'>=%s AND '+DAY+'<=%s';args.extend([start,end])
    if shift_id:query+=' AND s.id=%s';args.append(shift_id)
    if order_id:query+=' AND o.id=%s';args.append(order_id)
    if driver_id:query+=' AND t.driver_id=%s';args.append(driver_id)
    scope,params=scoped(user);query+=scope;args+=params
    if user.get('_morning_manager_context'):
        query+=" AND s.manager_shift_id=%s AND o.snapshot->>'delivery_period'='morning'"
        args.append(user['_morning_manager_context']['shift_id'])
    if user['role']!='owner':
        query+=' AND s.branch_ids <@ %s::jsonb AND o.branch_id=ANY(%s)';args.extend([db.json(user.get('branch_ids',[])),user.get('branch_ids',[])])
    if branch_ids is not None:query+=' AND o.branch_id=ANY(%s)';args.append(branch_ids)
    return c.execute(query+' ORDER BY operational_day,t.driver_id,o.id',args).fetchall()

def incident(row):
    cancelled=row['preparation_status'] in ('cancelled','rejected') or row['task_status']=='cancelled'
    refs=sorted(row.get('refund_ids') or [])
    key=hashlib.sha256(json.dumps([row['task_id'],cancelled,refs],separators=(',',':')).encode()).hexdigest()
    return cancelled or bool(refs),key

def classify(row):
    exceptional,key=incident(row)
    valid=exceptional and row.get('decision_task_id')==row['task_id'] and row.get('saved_incident_key')==key
    if exceptional and not valid:state='pending_review'
    elif exceptional and row.get('responsibility')=='driver':state='excluded'
    elif row['shift_status']!='closed':state='awaiting_approval'
    elif exceptional or row['task_status']=='delivered' and row['payment_status']=='paid':state='eligible'
    else:state='incomplete'
    return {'order_id':row['order_id'],'order_reference':row['order_reference'],'task_id':row['task_id'],
      'shift_id':row['shift_id'],'driver_id':row['driver_id'],'driver_name':row['driver_name'],
      'day':row['operational_day'].isoformat(),'state':state,'exceptional':exceptional,'incident_key':key,
      'responsibility':row.get('responsibility') if valid else None,'reason':row.get('reason') if valid else None,
      'decision_version':row.get('decision_version') or 0,'decided_by':row.get('decided_by') if valid else None,
      'decided_at':row.get('decided_at') if valid else None,'shift_closed':row['shift_status']=='closed'}

def aggregate(entries,shift_days=()):
    rows={}
    def bucket(driver,name,day):
        return rows.setdefault((driver,day),{'driver_id':driver,'driver_name':name,'day':day,'label':name+' / '+day,
          'eligible':0,'excluded':0,'pending_review':0,'awaiting_approval':0,'incomplete':0,'orders':0,'open_shifts':0})
    for s in shift_days:
        b=bucket(s['user_id'],s['driver_name'],s['operational_day'].isoformat())
        b['open_shifts']+=int(s['status']!='closed')
    for e in entries:
        b=bucket(e['driver_id'],e['driver_name'],e['day']);b['orders']+=1;b[e['state']]+=1
    result=sorted(rows.values(),key=lambda b:(b['driver_name'],b['day'],b['driver_id']))
    cumulative=defaultdict(int)
    for b in result:
        n=b['eligible'];cumulative[b['driver_id']]+=n;b['cumulative']=cumulative[b['driver_id']]
        b.update(tier_1=min(n,20),tier_2=min(max(n-20,0),5),tier_3=min(max(n-25,0),5),tier_4=max(n-30,0))
    return result

def report_data(c,user,start,end,driver_id='',branch_ids=None):
    entries=[classify(row) for row in source_rows(c,user,start,end,driver_id=driver_id,branch_ids=branch_ids)]
    query='SELECT s.user_id,u.display_name driver_name,s.status,'+DAY+" operational_day FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.kind='driver' AND s.brand_id=%s AND "+DAY+'>=%s AND '+DAY+'<=%s'
    args=[user['brand_id'],start,end];scope,params=scoped(user);query+=scope;args+=params
    if driver_id:query+=' AND s.user_id=%s';args.append(driver_id)
    if user['role']!='owner':query+=' AND s.branch_ids <@ %s::jsonb';args.append(db.json(user.get('branch_ids',[])))
    if branch_ids is not None:query+=' AND s.branch_ids ?| %s';args.append(branch_ids)
    days=c.execute(query,args).fetchall()
    rows=aggregate(entries,days)
    if user['role']=='driver':entries=[]
    return safe({'rows':rows,'entries':entries,'eligible':sum(b['eligible'] for b in rows),
      'excluded':sum(b['excluded'] for b in rows),'pending_review':sum(b['pending_review'] for b in rows),
      'awaiting_approval':sum(b['awaiting_approval'] for b in rows),'open_shifts':sum(b['open_shifts'] for b in rows),
      'today':service_hours.delivery_operational_date(),'period':{'start':start,'end':end},
      'definitions':'الاستحقاق بعد اعتماد الشفت. الملغي والمرتجع بسبب العميل أو الفرع يحتسب؛ بسبب السائق يستبعد. الطلب يحتسب مرة واحدة. الشرائح يومية بلا معاملات مالية مفعلة.'})

@router.get('')
def achievement(month:str='',driver_id:str='',user=Depends(read_access)):
    try:
        today=service_hours.delivery_operational_date()
        first=date.fromisoformat((month or today.strftime('%Y-%m'))+'-01')
        last=first.replace(day=calendar.monthrange(first.year,first.month)[1])
    except (ValueError,TypeError):fail(422,'الشهر غير صالح')
    if user['role']=='driver' and driver_id and driver_id!=user['id']:fail(403,'العداد خاص بك فقط')
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        return report_data(c,user,first,last,driver_id)

@router.get('/shifts/{sid}')
def shift_review(sid:str,user=Depends(read_access)):
    with db.tx() as c:
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone();shift_access(c,user,s,capability='shifts_view')
        if user['role']=='driver':return {'entries':[]}
        return safe({'entries':[classify(r) for r in source_rows(c,user,shift_id=sid)]})

class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    task_id:str
    incident_key:str=Field(min_length=64,max_length=64)
    version:StrictInt=Field(ge=0)
    responsibility:Literal['driver','customer','branch']
    reason:str=Field(min_length=3,max_length=2000)

@router.post('/orders/{oid}/decision')
def decide(oid:str,body:Decision,request:Request,user=Depends(auth.current_user)):
    if user['role']!='delivery_manager':fail(403,'تحديد المسؤولية واعتمادها لمدير التوصيل فقط')
    with db.tx() as c:
        def perform():
            c.execute('SELECT id FROM orders WHERE id=%s FOR UPDATE',(oid,))
            rows=source_rows(c,user,order_id=oid)
            if not rows:fail(404,'الطلب خارج نطاق شفتاتك')
            row=rows[0];exceptional,key=incident(row)
            if not exceptional:fail(409,'هذا الطلب ليس ملغيًا أو مرتجعًا')
            if body.task_id!=row['task_id'] or body.incident_key!=key or body.version!=(row.get('decision_version') or 0):fail(409,'تغيّر الطلب أو القرار؛ حدّث القائمة قبل الاعتماد')
            version=body.version+1
            c.execute('INSERT INTO driver_incentive_decisions(order_id,task_id,brand_id,responsibility,reason,incident_key,version,decided_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(order_id) DO UPDATE SET task_id=excluded.task_id,responsibility=excluded.responsibility,reason=excluded.reason,incident_key=excluded.incident_key,version=excluded.version,decided_by=excluded.decided_by,decided_at=now()', (oid,body.task_id,user['brand_id'],body.responsibility,body.reason,key,version,user['id']))
            evidence={**body.model_dump(),'version':version,'driver_id':row['driver_id'],'shift_id':row['shift_id']}
            c.execute('INSERT INTO driver_incentive_decision_events(id,order_id,version,actor_id,decision) VALUES(%s,%s,%s,%s,%s)',(db.new_id('die_'),oid,version,user['id'],db.json(evidence)))
            db.audit(c,user['id'],'driver.incentive.decision','order',oid,evidence)
            return {'order_id':oid,'shift_id':row['shift_id'],'version':version}
        # Scope must also be checked before returning an idempotent replay.
        if not source_rows(c,user,order_id=oid):fail(404,'الطلب خارج نطاق شفتاتك')
        return mutate(c,user,request,'driver.incentive.decision:'+oid,body.model_dump(),perform)

def require_decisions(c,user,s):
    if s['kind']!='driver':return
    entries=[classify(row) for row in source_rows(c,user,shift_id=s['id'])]
    if any(e['state']=='pending_review' for e in entries):fail(409,'حدد سبب ومسؤولية الطلبات الملغية والمرتجعة في قسم استحقاق السائق قبل اعتماد التقفيلة')
