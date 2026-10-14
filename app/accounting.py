"""Scoped accounting read models over canonical cash, refund and closure records.

Reviews and month snapshots never post cash, edit invoices or deduct payroll.
Sales use completion time; collections/refunds use their actual event time.
"""
import calendar
import hashlib
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request, Response, UploadFile, File
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from . import auth, db, service_hours
from .operations import safe

router=APIRouter(prefix='/api/accounting',tags=['accounting'])
TZ=ZoneInfo('Asia/Riyadh')
READ='accounting_reports'
REVIEW='accounting_review'
FOLLOW='accounting_cases_followup'
KINDS={'order':('مخالفة طلب','Order incident'),'traffic':('مخالفة مرورية','Traffic violation'),'fault':('عطل','Fault'),'damage':('تلفيات','Damage'),'other':('أخرى','Other')}
STATES={'open':('مفتوح','Open'),'in_progress':('قيد المتابعة','In progress'),'resolved':('مغلق','Resolved')}
RESP={'pending':('لم تحدد المسؤولية','Responsibility pending'),'confirmed':('مسؤولية مثبتة','Responsibility confirmed'),'not_responsible':('غير مسؤول','Not responsible')}

def fail(code,message,status=422):raise db.error(status,code,message)
def access(user=Depends(auth.current_user)):
    if user['role']=='driver' or not auth.has_permission(user,READ):fail('ACCOUNTING_FORBIDDEN','صلاحية المحاسبة غير متاحة.',403)
    return user

def case_access(user=Depends(auth.current_user)):
    if not (auth.has_permission(user,READ) or auth.has_permission(user,FOLLOW) or user['role']=='delivery_manager'):
        fail('ACCOUNTING_FORBIDDEN','صلاحية متابعة الملاحظات غير متاحة.',403)
    return user

def branches(c,u,selected=''):
    rows=c.execute('SELECT id,name,branch_no FROM branches WHERE brand_id=%s ORDER BY branch_no',(u['brand_id'],)).fetchall()
    allowed={r['id'] for r in rows if u['role']=='owner' or r['id'] in u.get('branch_ids',[])}
    requested=set(filter(None,selected.split(','))) if selected else allowed
    if not requested or not requested<=allowed:fail('BRANCH_SCOPE','الفروع خارج نطاق الحساب.',403)
    return [r for r in rows if r['id'] in requested]

def person(c,u,identifier,bids):
    r=c.execute("SELECT id,display_name,role,branch_ids FROM users WHERE id=%s AND brand_id=%s AND role IN ('driver','delivery_manager')",(identifier,u['brand_id'])).fetchone()
    if not r or not set(r['branch_ids'])&set(bids):fail('PERSON_SCOPE','الموظف خارج النطاق.',404)
    return r

def period(start,end):
    today=service_hours.delivery_operational_date()
    try:lo=date.fromisoformat(start) if start else today;hi=date.fromisoformat(end) if end else lo
    except ValueError:fail('DATE_INVALID','التاريخ غير صالح.')
    if hi<lo or (hi-lo).days>366:fail('PERIOD_INVALID','اختر فترة لا تتجاوز سنة.')
    return lo,hi,datetime.combine(lo,time(16),TZ),datetime.combine(hi+timedelta(days=1),time(16),TZ)

def opday(value):return service_hours.delivery_operational_date(value).isoformat()
def in_period(value,lo,hi):return bool(value and lo<=value<hi)
def new_row(label):
    return dict(label=label,orders=0,correct=0,processed=0,cancelled=0,gross=0,cash=0,card=0,refund_cash=0,refund_card=0,refunds=0,total=0,net_sales=0,notes='')

def build(c,u,start='',end='',branch_ids='',driver_id='',product_id='',group='daily',lang='ar'):
    ds,de,lo,hi=period(start,end);bs=branches(c,u,branch_ids);bids=[b['id'] for b in bs];bn={b['id']:b['name'] for b in bs}
    if driver_id:person(c,u,driver_id,bids)
    if group in ('driver_incentives','driver_incentive_details'):
        from .driver_incentives import report_data
        if product_id:fail('INCENTIVE_FILTER','استحقاق السائق يحسب على مستوى الطلب؛ أزل فلتر الصنف.')
        data=report_data(c,u,ds,de,driver_id,bids)
        states={'eligible':('مستحق','Eligible'),'excluded':('مستبعد بسبب السائق','Excluded: driver responsibility'),'pending_review':('بانتظار تحديد المسؤولية','Responsibility pending'),'awaiting_approval':('بانتظار اعتماد الشفت','Awaiting shift approval'),'incomplete':('غير مكتمل','Incomplete')}
        responsibilities={'driver':('السائق','Driver'),'customer':('الزبون','Customer'),'branch':('الفرع','Branch')}
        result=data['rows']
        if group=='driver_incentive_details':
            names={r['id']:r['display_name'] for r in c.execute('SELECT id,display_name FROM users WHERE brand_id=%s',(u['brand_id'],)).fetchall()}
            result=[{**e,'label':e['order_reference'],'driver':e['driver_name'],'review':states[e['state']][lang=='en'],
              'responsibility':responsibilities.get(e['responsibility'],('—','—'))[lang=='en'],
              'reason':e['reason'] or '—','actor':names.get(e['decided_by'],'—')} for e in data['entries']]
        return safe({'rows':result,'group':group,'period':{'start':ds,'end':de,'timezone':'Asia/Riyadh'},'branches':bs,
          'definitions':data['definitions'] if lang=='ar' else 'Entitlement follows shift approval. Customer/branch-caused cancellations or refunds count; driver-caused ones do not. Each order counts once. Daily tiers have no financial rates yet.',
          'generated_at':datetime.now(TZ)})
    if product_id and not c.execute('SELECT 1 FROM products WHERE id=%s AND brand_id=%s',(product_id,u['brand_id'])).fetchone():fail('PRODUCT_SCOPE','الصنف غير متاح.',404)
    # Fetch qualifying orders once. Lateral task selection prevents split payment,
    # transfers, refunds or multiple tasks from multiplying order/item counts.
    q="""SELECT o.id,o.order_no,o.branch_id,o.created_at,o.completed_at,o.preparation_status,
      o.total_halalas,o.snapshot,o.payment_status,t.driver_id,u.display_name driver_name
      FROM orders o LEFT JOIN LATERAL (SELECT driver_id FROM driver_tasks WHERE order_id=o.id
      ORDER BY (status='delivered') DESC,created_at DESC,id DESC LIMIT 1) t ON true
      LEFT JOIN users u ON u.id=t.driver_id WHERE o.brand_id=%s AND o.branch_id=ANY(%s)
      AND (o.created_at>=%s AND o.created_at<%s OR o.completed_at>=%s AND o.completed_at<%s
      OR EXISTS(SELECT 1 FROM collections x WHERE x.order_id=o.id AND x.created_at>=%s AND x.created_at<%s)
      OR EXISTS(SELECT 1 FROM refunds x WHERE x.order_id=o.id AND x.created_at>=%s AND x.created_at<%s))"""
    args=[u['brand_id'],bids,lo,hi,lo,hi,lo,hi,lo,hi]
    if driver_id:q+=' AND t.driver_id=%s';args.append(driver_id)
    if product_id:q+=" AND EXISTS(SELECT 1 FROM jsonb_array_elements(o.snapshot->'items') x WHERE x->>'product_id'=%s)";args.append(product_id)
    orders=c.execute(q,args).fetchall();ids=[o['id'] for o in orders]
    cols=c.execute('SELECT * FROM collections WHERE order_id=ANY(%s) AND created_at<%s ORDER BY created_at,id',(ids,hi)).fetchall()
    refs=c.execute('SELECT r.*,u.display_name actor_name FROM refunds r JOIN users u ON u.id=r.authorized_by WHERE order_id=ANY(%s) AND r.created_at<%s ORDER BY r.created_at,r.id',(ids,hi)).fetchall()
    tickets=c.execute('SELECT t.*,u.display_name actor_name FROM support_tickets t JOIN users u ON u.id=t.created_by WHERE order_id=ANY(%s) AND t.created_at<%s',(ids,hi)).fetchall()
    events=c.execute('SELECT e.*,u.display_name actor_name FROM order_events e LEFT JOIN users u ON u.id=e.actor_id WHERE order_id=ANY(%s) AND e.created_at<%s ORDER BY e.created_at,e.id',(ids,hi)).fetchall()
    paid=defaultdict(int);refunded=defaultdict(int);treated={t['order_id'] for t in tickets}
    for x in cols:paid[x['order_id']]+=x['amount_halalas']
    for x in refs:
        if x.get('amendment_id'):paid[x['order_id']]-=x['amount_halalas']
        else:refunded[x['order_id']]+=x['amount_halalas'];treated.add(x['order_id'])
    rows={};items={};om={o['id']:o for o in orders}
    def bucket(o,at):
        d=opday(at)
        key=d if group=='daily' else o['branch_id'] if group=='branch' else (o['driver_id'] or 'unassigned') if group=='driver' else (o['driver_id'] or 'unassigned',d)
        label=d if group=='daily' else bn[o['branch_id']] if group=='branch' else o['driver_name'] or ('بدون سائق' if lang=='ar' else 'No driver')
        if group=='driver_daily':label=(o['driver_name'] or '—')+' / '+d
        if key not in rows:rows[key]={**new_row(label),'day':d,'driver_id':o['driver_id']}
        return rows[key]
    for o in orders:
        if in_period(o['created_at'],lo,hi):
            b=bucket(o,o['created_at']);b['orders']+=1
            if o['preparation_status'] in ('cancelled','rejected'):b['cancelled']+=1
        if in_period(o['completed_at'],lo,hi):
            b=bucket(o,o['completed_at']);b['gross']+=o['total_halalas']
            correct=paid[o['id']]>=o['total_halalas'] and not refunded[o['id']] and o['preparation_status'] not in ('cancelled','rejected') and o['id'] not in treated
            b['correct']+=int(correct);b['processed']+=int(not correct)
            seen=set()
            for x in o['snapshot'].get('items',[]):
                pid=x['product_id']
                if product_id and pid!=product_id:continue
                if pid not in items:items[pid]={'label':x.get('name') or pid,'quantity':0,'returned_quantity':0,'gross':0,'correct':0,'processed':0}
                it=items[pid];it['quantity']+=int(x['quantity']);it['gross']+=int(x.get('line_total_halalas',0))
                if pid not in seen:it['correct']+=int(correct);it['processed']+=int(not correct);seen.add(pid)
    for x in cols:
        if in_period(x['created_at'],lo,hi):bucket(om[x['order_id']],x['created_at'])[x['method']]+=x['amount_halalas']
    for x in refs:
        if not in_period(x['created_at'],lo,hi):continue
        b=bucket(om[x['order_id']],x['created_at']);b['refund_'+x['method']]+=x['amount_halalas'];b['refunds']+=x['amount_halalas']
        if x.get('amendment_id'):b['amendment_refunds']=b.get('amendment_refunds',0)+x['amount_halalas']
        for pid,qty in x['line_quantities'].items():
            if product_id and pid!=product_id:continue
            if pid not in items:
                name=next((i.get('name',pid) for i in om[x['order_id']]['snapshot'].get('items',[]) if i['product_id']==pid),pid)
                items[pid]={'label':name,'quantity':0,'returned_quantity':0,'gross':0,'correct':0,'processed':0}
            items[pid]['returned_quantity']+=qty
    for b in rows.values():b['total']=b['cash']+b['card']-b['refunds'];b['net_sales']=b['gross']-b['refunds']+b.get('amendment_refunds',0)
    if group=='daily':
        for n in range((de-ds).days+1):
            day=(ds+timedelta(days=n)).isoformat()
            rows.setdefault(day,{**new_row(day),'day':day})
    deficits=deficit_rows(c,u,lo,hi,bids,driver_id,lang)
    if group=='daily':
        for d in deficits:
            if d['day'] in rows:
                rows[d['day']]['notes']+=(d['driver']+' '+(('عجز' if d['shortage'] else 'فائض') if lang=='ar' else ('Shortage' if d['shortage'] else 'Surplus'))+f" {max(d['shortage'],d['surplus'])/100:.2f}; ")
    if group=='items':result=list(items.values())
    else:result=sorted(rows.values(),key=lambda x:(str(x.get('driver_id','')),x['label']))
    if group=='driver_daily':
        counts=defaultdict(int)
        for b in result:counts[b['driver_id']]+=b['correct'];b['cumulative']=counts[b['driver_id']]
    actions=[]
    for r in refs:
        if in_period(r['created_at'],lo,hi):
            o=om[r['order_id']];actions.append({'label':str(o['snapshot'].get('order_reference') or o['order_no']),'day':opday(r['created_at']),'kind':'مرتجع / Refund','amount':r['amount_halalas'],'method':r['method'],'reason':r['reason'],'actor':r['actor_name'],'action':'تم تسجيل رد المبلغ / Refund recorded','order_id':o['id']})
    for o in orders:
        if o['preparation_status'] in ('cancelled','rejected') and in_period(o['created_at'],lo,hi):
            ev=[e for e in events if e['order_id']==o['id']]
            relevant=[e for e in ev if any(w in str(e['details'])+e['event_type'] for w in ('cancel','reject'))]
            last=(relevant or ev or [{}])[-1]
            actions.append({'label':str(o['snapshot'].get('order_reference') or o['order_no']),'day':opday(last.get('created_at') or o['created_at']),'kind':'ملغي / Cancelled','amount':o['total_halalas'],'method':'—','reason':last.get('details',{}).get('reason','—'),'actor':last.get('actor_name') or '—','action':('مبلغ محصل ينتظر الرد / Collected balance awaiting refund' if paid[o['id']]>refunded[o['id']] else 'لا يوجد رصيد مسترد معلق / No remaining refund balance'),'order_id':o['id']})
    for t in tickets:
        if not in_period(t['created_at'],lo,hi):continue
        o=om[t['order_id']];actions.append({'label':str(o['snapshot'].get('order_reference') or o['order_no']),'day':opday(t['created_at']),'kind':'معالجة / Treatment','amount':0,'method':'—','reason':t['note'],'actor':t['actor_name'],'action':t['status']+' / '+t['resolution'],'order_id':o['id']})
    if group=='actions':result=actions
    if group=='deficits':result=deficits
    return safe({'rows':result,'group':group,'period':{'start':ds,'end':de,'from':lo,'until':hi,'timezone':'Asia/Riyadh','start_time':'16:00'},'branches':bs,
      'definitions':('المبيعات بتاريخ الإكمال؛ التحصيل والمرتجعات بتاريخ الحركة. الطلبات والإلغاءات حسب تاريخ إنشاء الطلب. الصحيح مكتمل ومحصّل بلا مرتجع أو معالجة. مبالغ فلتر الصنف تخص الطلبات المحتوية عليه، وتقرير الأصناف يفصل قيمة الصنف. صافي التحصيل قد يختلف عن صافي المبيعات. العجز حسب تقفيلة السائق كاملة ولا يوزّع افتراضيًا على الفروع.' if lang=='ar' else 'Sales use completion time; collections and refunds use transaction time. Orders and cancellations use order creation date. Correct means completed and fully collected without refund or treatment. Product-filtered payment totals cover containing orders; item sales show item values. Net receipts can differ from net sales. A shortage belongs to the full driver closure and is not allocated to branches.'),
      'generated_at':datetime.now(TZ)})

def deficit_rows(c,u,lo,hi,bids,driver_id,lang):
    rows=c.execute("SELECT r.*,v.id review_id,v.note review_note,v.created_at reviewed_at,u.display_name driver_name,coalesce(s.delivery_operational_date,(s.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '16 hours')::date) accounting_day FROM closure_reports r JOIN shifts s ON s.id=r.shift_id JOIN users u ON u.id=s.user_id LEFT JOIN accounting_reviews v ON v.report_id=r.id WHERE r.brand_id=%s AND r.kind='driver' AND coalesce(s.delivery_operational_date,(s.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '16 hours')::date)>=%s AND coalesce(s.delivery_operational_date,(s.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '16 hours')::date)<%s ORDER BY accounting_day,r.created_at,r.id",(u['brand_id'],lo.date(),hi.date())).fetchall()
    output=[]
    for r in rows:
        subject=r['snapshot']['shift']['user_id']
        if not set(r['branch_ids'])&set(bids) or (u['role']!='owner' and not set(r['branch_ids'])<=set(u.get('branch_ids',[]))) or driver_id and subject!=driver_id:continue
        sub=r['snapshot']['shift'].get('submission') or {};difference=int(sub.get('difference_halalas',0))
        if not difference:continue
        s=r['snapshot']['summary'];output.append({'id':r['id'],'label':r['snapshot'].get('closure_label') or r['id'],'day':r['accounting_day'].isoformat(),'review_id':r['review_id'],'driver':r['driver_name'],'driver_id':r['user_id'],'orders':s.get('completed_orders',0),'cash':s.get('cash_collected_halalas',0),'card':s.get('card_collected_halalas',0),'total':s.get('net_collections_halalas',0),'shortage':max(0,-difference),'surplus':max(0,difference),'approved_shortage':max(0,-difference) if r['review_id'] else 0,'review':('معتمد / Reviewed' if r['review_id'] else 'بانتظار المحاسبة / Pending accounting'),'notes':r['review_note'] or sub.get('notes','')})
    return output

@router.get('/options')
def options(user=Depends(case_access)):
    with db.tx() as c:
        bs=branches(c,user);bids={b['id'] for b in bs}
        people=c.execute("SELECT id,display_name,role,branch_ids,vehicle FROM users WHERE brand_id=%s AND role IN ('driver','delivery_manager') ORDER BY display_name",(user['brand_id'],)).fetchall()
        return {'branches':bs,'people':[p for p in people if set(p['branch_ids'])&bids],'products':c.execute('SELECT id,name FROM products WHERE brand_id=%s ORDER BY name',(user['brand_id'],)).fetchall(),'today':service_hours.delivery_operational_date(),'can_review':auth.has_permission(user,REVIEW),'can_report':auth.has_permission(user,READ),'can_follow':can_follow(c,user),'categories':KINDS,'states':STATES,'responsibilities':RESP,'manager_followup':(c.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(user['brand_id'],)).fetchone() or {}).get('settings',{}).get('accounting_manager_followup',True)}

Group=Literal['daily','branch','items','driver','driver_daily','driver_incentives','driver_incentive_details','deficits','actions']
@router.get('/reports')
def reports(start:str='',end:str='',branch_ids:str='',driver_id:str='',product_id:str='',group:Group='daily',lang:Literal['ar','en']='ar',format:Literal['json','pdf','xlsx']='json',user=Depends(access)):
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        data=build(c,user,start,end,branch_ids,driver_id,product_id,group,lang)
    return export(data,format,lang)

def export(data,format,lang):
    if format=='json':
        from .accounting_export import contract
        return contract(data,lang)
    from .accounting_export import render
    return Response(render(data,format,lang),media_type='application/pdf' if format=='pdf' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="accounting-{data["group"]}.{format}"','Cache-Control':'no-store'})

class Note(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    note:str=Field(min_length=3,max_length=2000)

@router.post('/reviews/{report_id}')
def review(report_id:str,body:Note,request:Request,user=Depends(auth.require_permission(REVIEW))):
    with db.tx() as c:
        r=c.execute("SELECT * FROM closure_reports WHERE id=%s AND brand_id=%s AND kind='driver' FOR UPDATE",(report_id,user['brand_id'])).fetchone()
        if not r or (user['role']!='owner' and not set(r['branch_ids'])<=set(user.get('branch_ids',[]))):fail('REPORT_SCOPE','التقفيلة غير متاحة.',404)
        def perform():
            if c.execute('SELECT 1 FROM accounting_reviews WHERE report_id=%s',(report_id,)).fetchone():fail('ALREADY_REVIEWED','تم اعتماد المراجعة سابقًا.',409)
            row=c.execute('INSERT INTO accounting_reviews(id,brand_id,report_id,reviewer_id,note) VALUES(%s,%s,%s,%s,%s) RETURNING *',(db.new_id('acr_'),user['brand_id'],report_id,user['id'],body.note)).fetchone()
            db.audit(c,user['id'],'accounting.review','report',report_id,{'note':body.note,'no_payroll_deduction':True});return safe(row)
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.review:'+report_id,body.model_dump(),perform)

def can_follow(c,u):
    settings=c.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(u['brand_id'],)).fetchone()
    manager_default=(settings or {}).get('settings',{}).get('accounting_manager_followup',True)
    return auth.has_permission(u,FOLLOW) and (u['role']!='delivery_manager' or manager_default)

def case_row(c,u,identifier,lock=False):
    row=c.execute('SELECT * FROM accounting_cases WHERE id=%s AND brand_id=%s'+(' FOR UPDATE' if lock else ''),(identifier,u['brand_id'])).fetchone()
    if not row or row['branch_id'] not in [b['id'] for b in branches(c,u)]:fail('CASE_SCOPE','الملاحظة غير متاحة.',404)
    if u['role']=='delivery_manager' and not can_follow(c,u):fail('FOLLOW_FORBIDDEN','صلاحية المتابعة مسحوبة.',403)
    return row

class CaseBody(Note):
    branch_id:str
    subject_id:str
    order_id:str|None=None
    vehicle_report_id:str|None=None
    category:Literal['order','traffic','fault','damage','other']
    occurred_on:date
    vehicle_plate:str=Field(default='',max_length=40)
    amount_halalas:StrictInt=Field(default=0,ge=0,le=100000000)

@router.post('/cases')
def create_case(body:CaseBody,request:Request,user=Depends(auth.require_permission(REVIEW))):
    with db.tx() as c:
        bids=[x['id'] for x in branches(c,user,body.branch_id)];p=person(c,user,body.subject_id,bids)
        if body.occurred_on>datetime.now(TZ).date():fail('DATE_INVALID','تاريخ الواقعة لا يكون في المستقبل.')
        if body.order_id and not c.execute('SELECT 1 FROM orders WHERE id=%s AND brand_id=%s AND branch_id=%s',(body.order_id,user['brand_id'],body.branch_id)).fetchone():fail('ORDER_SCOPE','الطلب خارج النطاق.',404)
        if body.vehicle_report_id and not c.execute('SELECT 1 FROM vehicle_reports WHERE id=%s AND brand_id=%s AND branch_id=%s AND driver_id=%s',(body.vehicle_report_id,user['brand_id'],body.branch_id,p['id'])).fetchone():fail('VEHICLE_SCOPE','بلاغ المركبة خارج النطاق.',404)
        def perform():
            row=c.execute('INSERT INTO accounting_cases(id,brand_id,branch_id,subject_id,order_id,vehicle_report_id,category,occurred_on,vehicle_plate,description,amount_halalas,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',(db.new_id('acc_'),user['brand_id'],body.branch_id,body.subject_id,body.order_id,body.vehicle_report_id,body.category,body.occurred_on,body.vehicle_plate,body.note,body.amount_halalas,user['id'])).fetchone()
            case_event(c,user,row,{'event':'created','note':body.note});return safe(row)
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.case.create',safe(body.model_dump()),perform)

def case_event(c,u,row,details):
    c.execute('INSERT INTO accounting_case_events(id,case_id,actor_id,details) VALUES(%s,%s,%s,%s)',(db.new_id('ace_'),row['id'],u['id'],db.json(details)))
    db.audit(c,u['id'],'accounting.case','case',row['id'],details)

class CaseUpdate(Note):
    version:StrictInt=Field(ge=1)
    status:Literal['open','in_progress','resolved']
    responsibility:Literal['pending','confirmed','not_responsible']

@router.post('/cases/{identifier}/followup')
def followup(identifier:str,body:CaseUpdate,request:Request,user=Depends(case_access)):
    with db.tx() as c:
        row=case_row(c,user,identifier,True)
        if not can_follow(c,user):fail('FOLLOW_FORBIDDEN','المتابعة لمسؤولها المخول فقط.',403)
        def perform():
            if row['version']!=body.version:fail('CASE_CHANGED','حدّث الملاحظة قبل الحفظ.',409)
            updated=c.execute('UPDATE accounting_cases SET status=%s,responsibility=%s,action=%s,version=version+1,updated_at=now() WHERE id=%s RETURNING *',(body.status,body.responsibility,body.note,identifier)).fetchone()
            case_event(c,user,row,body.model_dump());return safe(updated)
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.case.followup:'+identifier,body.model_dump(),perform)

@router.get('/cases')
def cases(start:str='',end:str='',branch_ids:str='',subject_id:str='',status:str='',category:str='',lang:Literal['ar','en']='ar',format:Literal['json','pdf','xlsx']='json',user=Depends(case_access)):
    ds,de,_,_=period(start,end)
    with db.tx() as c:
        bids=[x['id'] for x in branches(c,user,branch_ids)]
        if user['role']=='delivery_manager' and not can_follow(c,user):fail('FOLLOW_FORBIDDEN','صلاحية المتابعة مسحوبة.',403)
        rows=c.execute('SELECT a.*,u.display_name subject,b.name branch FROM accounting_cases a JOIN users u ON u.id=a.subject_id JOIN branches b ON b.id=a.branch_id WHERE a.brand_id=%s AND a.branch_id=ANY(%s) AND occurred_on BETWEEN %s AND %s ORDER BY occurred_on DESC,case_no DESC',(user['brand_id'],bids,ds,de)).fetchall()
        rows=[r for r in rows if (not subject_id or r['subject_id']==subject_id) and (not status or r['status']==status) and (not category or r['category']==category)]
        if format=='json':return safe({'rows':rows,'can_follow':can_follow(c,user)})
        values=[dict(label=str(r['case_no']),day=r['occurred_on'],branch=r['branch'],driver=r['subject'],kind=KINDS[r['category']][lang=='en'],vehicle=r['vehicle_plate'],amount=r['amount_halalas'],review=STATES[r['status']][lang=='en'],responsibility=RESP[r['responsibility']][lang=='en'],reason=r['description'],action=r['action']) for r in rows]
        return export(safe({'rows':values,'group':'cases','period':{'start':ds,'end':de},'definitions':'المبلغ تقديري وليس خصمًا أو إثبات مسؤولية. / Estimated amounts do not create deductions or establish liability.'}),format,lang)

@router.get('/cases/{identifier}')
def detail(identifier:str,user=Depends(case_access)):
    with db.tx() as c:
        row=case_row(c,user,identifier)
        events=c.execute('SELECT e.details,e.created_at,u.display_name actor FROM accounting_case_events e JOIN users u ON u.id=e.actor_id WHERE case_id=%s ORDER BY e.created_at,e.id',(identifier,)).fetchall()
        files=c.execute('SELECT id,content_type,created_at FROM accounting_case_files WHERE case_id=%s ORDER BY created_at',(identifier,)).fetchall()
        return safe({'case':row,'events':events,'files':files,'can_follow':can_follow(c,user)})

@router.post('/cases/{identifier}/files')
async def upload(identifier:str,request:Request,file:UploadFile=File(...),user=Depends(case_access)):
    import io
    from PIL import Image,ImageOps
    data=await file.read(5*1024*1024+1)
    if len(data)>5*1024*1024 or not data:fail('FILE_SIZE','الحد الأقصى 5 MB.')
    try:
        im=Image.open(io.BytesIO(data))
        if im.format not in ('JPEG','PNG','WEBP'):fail('IMAGE_INVALID','الصيغ المسموحة JPEG وPNG وWebP.')
        im.verify();im=Image.open(io.BytesIO(data))
        if im.width*im.height>25000000:fail('IMAGE_SIZE','الصورة كبيرة جدًا.')
        im=ImageOps.exif_transpose(im).convert('RGB');out=io.BytesIO();im.save(out,'JPEG',quality=90);data=out.getvalue()
    except (OSError,ValueError,Image.DecompressionBombError):fail('IMAGE_INVALID','أرفق صورة مستند بصيغة JPEG أو PNG أو WebP.')
    with db.tx() as c:
        row=case_row(c,user,identifier,True)
        if not (auth.has_permission(user,REVIEW) or can_follow(c,user)):fail('UPLOAD_FORBIDDEN','غير مخول بالإرفاق.',403)
        digest=hashlib.sha256(data).hexdigest()
        def perform():
            fid=db.new_id('acf_');c.execute('INSERT INTO accounting_case_files(id,case_id,uploader_id,content_type,data,sha256) VALUES(%s,%s,%s,%s,%s,%s)',(fid,identifier,user['id'],'image/jpeg',data,digest));case_event(c,user,row,{'event':'attachment','file_id':fid});return {'id':fid}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.file:'+identifier,{'sha256':digest},perform)

@router.get('/cases/{identifier}/files/{fid}')
def download(identifier:str,fid:str,user=Depends(case_access)):
    with db.tx() as c:
        case_row(c,user,identifier);r=c.execute('SELECT * FROM accounting_case_files WHERE id=%s AND case_id=%s',(fid,identifier)).fetchone()
        if not r:fail('FILE_NOT_FOUND','المرفق غير موجود.',404)
        return Response(bytes(r['data']),media_type=r['content_type'],headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

class FollowSettings(BaseModel):
    enabled:bool
    reason:str=Field(min_length=3,max_length=1000)

@router.post('/followup-policy')
def policy(body:FollowSettings,user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        auth.require_owner_step_up(c,user)
        c.execute("UPDATE app_settings SET settings=jsonb_set(settings,'{accounting_manager_followup}',%s) WHERE brand_id=%s",(db.json(body.enabled),user['brand_id']))
        db.audit(c,user['id'],'accounting.followup_policy','brand',user['brand_id'],body.model_dump());return {'enabled':body.enabled}

class MonthBody(Note):
    month:str=Field(pattern=r'^\d{4}-\d{2}$')
    branch_ids:str=''

@router.post('/months')
def close_month(body:MonthBody,request:Request,user=Depends(auth.require_permission(REVIEW))):
    try:ds=date.fromisoformat(body.month+'-01')
    except ValueError:fail('MONTH_INVALID','الشهر غير صالح.')
    de=ds.replace(day=calendar.monthrange(ds.year,ds.month)[1])
    if de>=service_hours.delivery_operational_date():fail('MONTH_OPEN','لا يقفل الشهر قبل انتهاء آخر يوم تشغيلي.',409)
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        bs=branches(c,user,body.branch_ids);bids=sorted(b['id'] for b in bs);h=hashlib.sha256(','.join(bids).encode()).hexdigest()
        _,_,lo,hi=period(ds.isoformat(),de.isoformat())
        if c.execute("SELECT 1 FROM shifts WHERE brand_id=%s AND status<>'closed' AND opened_at<%s AND coalesce(submitted_at,now())>=%s AND branch_ids ?| %s",(user['brand_id'],hi,lo,bids)).fetchone():fail('OPEN_SHIFTS','توجد شفتات غير مقفلة في الشهر.',409)
        if c.execute("SELECT 1 FROM closure_reports r WHERE r.brand_id=%s AND r.kind='manager' AND r.created_at>=%s AND r.created_at<%s AND r.branch_ids ?| %s AND r.snapshot->'shift'->'submission'->>'operational_approved'='true' AND NOT EXISTS(SELECT 1 FROM manager_financial_matches m WHERE m.report_id=r.id AND m.status='matched') LIMIT 1",(user['brand_id'],lo,hi,bids)).fetchone():
            fail('PENDING_MANAGER_MATCHES','ثبّت البنك والكاش لتقفيلات المدير قبل إقفال الشهر.',409)
        from .driver_incentives import report_data
        entitlements=report_data(c,user,ds,de,branch_ids=bids)
        if entitlements['pending_review'] or entitlements['awaiting_approval'] or any(r['incomplete'] for r in entitlements['rows']):fail('PENDING_INCENTIVES','راجع أسباب الطلبات الملغية والمرتجعة واعتمادات الشفتات قبل إقفال الشهر.',409)
        deficits=deficit_rows(c,user,lo,hi,bids,'','ar')
        if any(r['review'].startswith('بانتظار') for r in deficits):fail('PENDING_REVIEWS','اعتمد مراجعات العجز والفائض قبل إقفال الشهر.',409)
        def perform():
            if c.execute('SELECT 1 FROM accounting_months WHERE brand_id=%s AND month=%s AND scope_hash=%s',(user['brand_id'],ds,h)).fetchone():fail('MONTH_CLOSED','هذا النطاق مقفل مسبقًا.',409)
            snapshot={g:build(c,user,ds.isoformat(),de.isoformat(),','.join(bids),group=g) for g in ('daily','branch','items','driver','driver_daily','driver_incentives','driver_incentive_details','deficits','actions')}
            mid=db.new_id('acm_');c.execute('INSERT INTO accounting_months(id,brand_id,month,branch_ids,scope_hash,snapshot,closed_by,reason) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(mid,user['brand_id'],ds,db.json(bids),h,db.json(snapshot),user['id'],body.note));db.audit(c,user['id'],'accounting.month.closed','accounting_month',mid,{'month':body.month,'branches':bids,'reason':body.note});return {'id':mid}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.month',body.model_dump(),perform)

@router.get('/months')
def months(user=Depends(access)):
    with db.tx() as c:
        bids={b['id'] for b in branches(c,user)}
        return safe({'rows':[r for r in c.execute('SELECT id,month,branch_ids,reason,created_at FROM accounting_months WHERE brand_id=%s ORDER BY month DESC',(user['brand_id'],)).fetchall() if set(r['branch_ids'])<=bids]})

@router.get('/months/{identifier}')
def month(identifier:str,group:Group='driver_daily',format:Literal['json','pdf','xlsx']='json',lang:Literal['ar','en']='ar',user=Depends(access)):
    with db.tx() as c:
        r=c.execute('SELECT * FROM accounting_months WHERE id=%s AND brand_id=%s',(identifier,user['brand_id'])).fetchone()
        if not r or not set(r['branch_ids'])<={b['id'] for b in branches(c,user)}:fail('MONTH_SCOPE','الكشف غير متاح.',404)
        if group not in r['snapshot']:fail('SNAPSHOT_GROUP_UNAVAILABLE','هذا التقرير غير موجود في الكشف التاريخي المقفل.',409)
        data=r['snapshot'][group];data['frozen']=True;data['closed_at']=r['created_at'].isoformat()
        return export(data,format,lang)


class FinancialMatch(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    bank_halalas:StrictInt=Field(ge=0,le=100000000000)
    cash_halalas:StrictInt=Field(ge=0,le=100000000000)
    bank_reference:str=Field(min_length=1,max_length=200)
    cash_reference:str=Field(min_length=1,max_length=200)
    note:str=Field(default='',max_length=2000)
    expected_revision:StrictInt=Field(ge=0)


def matching_expected(snapshot):
    summary=snapshot.get('summary') or {}
    submission=summary.get('submission') or (snapshot.get('shift') or {}).get('submission') or {}
    return (int(summary.get('card_collected_halalas') or 0)-int(summary.get('card_refunded_halalas') or 0),
            int(submission.get('handover_halalas',0)))


def matching_report_scope(user,row):
    if not row or row['brand_id']!=user['brand_id'] or row['kind']!='manager':
        fail('REPORT_SCOPE','التقفيلة غير متاحة.',404)
    if user['role']!='owner' and not set(row['branch_ids'])<=set(user.get('branch_ids',[])):
        fail('REPORT_SCOPE','التقفيلة غير متاحة.',404)
    if row['user_id']==user['id']:fail('SELF_MATCH','المطابقة المالية من حساب محاسب مستقل.',403)


@router.get('/manager-closures')
def manager_closures(user=Depends(access)):
    with db.tx() as c:
        rows=c.execute("""SELECT r.id,r.brand_id,r.branch_ids,r.user_id,r.kind,r.snapshot,r.created_at,
          u.display_name manager_name,m.revision,m.status financial_status,m.bank_halalas,m.cash_halalas,
          m.bank_reference,m.cash_reference,m.note,m.created_at matched_at
          FROM closure_reports r JOIN users u ON u.id=r.user_id
          LEFT JOIN LATERAL (SELECT * FROM manager_financial_matches WHERE report_id=r.id ORDER BY revision DESC LIMIT 1) m ON true
          WHERE r.brand_id=%s AND r.kind='manager'
          ORDER BY CASE WHEN m.status='matched' THEN 1 ELSE 0 END,r.created_at DESC LIMIT 500""",(user['brand_id'],)).fetchall()
        result=[]
        for r in rows:
            if user['role']!='owner' and not set(r['branch_ids'])<=set(user.get('branch_ids',[])):continue
            snap=r.pop('snapshot');bank,cash=matching_expected(snap)
            r.update(expected_bank_halalas=bank,expected_cash_halalas=cash,
                     label=snap.get('closure_label') or snap.get('shift',{}).get('closure_label') or snap.get('reference') or r['id'],day=snap.get('operational_date'),revision=r['revision'] or 0)
            result.append(r)
        return safe({'closures':result})


@router.post('/manager-closures/{report_id}/match')
def match_manager_closure(report_id:str,body:FinancialMatch,request:Request,user=Depends(auth.require_permission(REVIEW))):
    with db.tx() as c:
        r=c.execute("SELECT * FROM closure_reports WHERE id=%s AND brand_id=%s FOR UPDATE",(report_id,user['brand_id'])).fetchone()
        matching_report_scope(user,r)
        def perform():
            previous=c.execute('SELECT * FROM manager_financial_matches WHERE report_id=%s ORDER BY revision DESC LIMIT 1',(report_id,)).fetchone()
            revision=previous['revision'] if previous else 0
            if previous and previous['status']=='matched':fail('ALREADY_MATCHED','تمت المطابقة المالية سابقًا.',409)
            if revision!=body.expected_revision:fail('MATCH_CHANGED','حدثت مراجعة أحدث؛ حدّث القائمة.',409)
            bank,cash=matching_expected(r['snapshot'])
            matched=body.bank_halalas==bank and body.cash_halalas==cash
            if not matched and not body.note:fail('DIFFERENCE_NOTE','اكتب ملاحظة توضح فرق البنك أو الكاش.')
            row=c.execute("""INSERT INTO manager_financial_matches
              (id,report_id,brand_id,revision,expected_bank_halalas,expected_cash_halalas,bank_halalas,cash_halalas,bank_reference,cash_reference,note,status,reviewer_id)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
              (db.new_id('mfm_'),report_id,user['brand_id'],revision+1,bank,cash,body.bank_halalas,body.cash_halalas,body.bank_reference,body.cash_reference,body.note,'matched' if matched else 'difference',user['id'])).fetchone()
            db.audit(c,user['id'],'accounting.manager_match','report',report_id,{'match_id':row['id'],'status':row['status'],'revision':revision+1})
            return safe(row)
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.manager_match:'+report_id,body.model_dump(),perform)
