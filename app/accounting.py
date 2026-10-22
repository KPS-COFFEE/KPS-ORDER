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
    if group=='driver_statement':
        if product_id:fail('STATEMENT_FILTER','كشف السائق يشمل التقفيلة كاملة؛ أزل فلتر الصنف.')
        from .accounting_statements import statement
        return statement(c,u,ds,de,bids,bs,driver_id,lang)
    if group=='complimentary':
        return complimentary_report(c,u,ds,de,lo,hi,bids,bs,driver_id,product_id,lang)
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
    q+=" AND NOT (o.snapshot ? 'complimentary')"
    if group!='actions':q+=" AND o.test_void IS NULL"
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
            o=om[r['order_id']];test_void=r.get('record_kind')=='test_void'
            actions.append({'label':str(o['snapshot'].get('order_reference') or o['order_no']),'day':opday(r['created_at']),'kind':'إلغاء تجريبي / Test void' if test_void else 'مرتجع / Refund','amount':r['amount_halalas'],'method':r['method'],'reason':r['reason'],'actor':r['actor_name'],'action':'عكس تسجيل تجريبي — لا صرف نقدي / Test correction — no cash disbursed' if test_void else 'تم تسجيل رد المبلغ / Refund recorded','order_id':o['id']})
    for o in orders:
        if not o.get('test_void') and o['preparation_status'] in ('cancelled','rejected') and in_period(o['created_at'],lo,hi):
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
    latest={e['report_id']:e for e in c.execute('SELECT DISTINCT ON(report_id) * FROM driver_accounting_settlements WHERE report_id=ANY(%s) ORDER BY report_id,revision DESC',([r['id'] for r in rows],)).fetchall()}
    output=[]
    for r in rows:
        subject=r['snapshot']['shift']['user_id']
        if not set(r['branch_ids'])&set(bids) or (u['role']!='owner' and not set(r['branch_ids'])<=set(u.get('branch_ids',[]))) or driver_id and subject!=driver_id:continue
        sub=r['snapshot']['shift'].get('submission') or {};difference=int(sub.get('difference_halalas',0))
        event=latest.get(r['id'])
        if event:
            values=driver_settlement_values(r['snapshot'],event)
            shortage,surplus=values['shortage_halalas'],values['surplus_halalas']
            if values['stage']=='cash_received':r['review_id']=event['id'];r['review_note']=event['note']
        else:shortage,surplus=max(0,-difference),max(0,difference)
        if not shortage and not surplus:continue
        s=r['snapshot']['summary'];output.append({'id':r['id'],'label':r['snapshot'].get('closure_label') or r['id'],'day':r['accounting_day'].isoformat(),'review_id':r['review_id'],'driver':r['driver_name'],'driver_id':r['user_id'],'orders':s.get('completed_orders',0),'cash':s.get('cash_collected_halalas',0),'card':s.get('card_collected_halalas',0),'total':s.get('net_collections_halalas',0),'shortage':shortage,'surplus':surplus,'approved_shortage':shortage if r['review_id'] else 0,'review':('معتمد / Reviewed' if r['review_id'] else 'بانتظار المحاسبة / Pending accounting'),'notes':r['review_note'] or sub.get('notes','')})
    return output


def complimentary_report(c,u,ds,de,lo,hi,bids,bs,driver_id,product_id,lang):
    from .complimentary import KINDS
    en=lang=='en'
    rows=c.execute("""SELECT o.*,b.name branch_name,creator.display_name creator_name,
      t.driver_id,driver.display_name driver_name,e.details cancel_details,e.created_at cancelled_at
      FROM orders o JOIN branches b ON b.id=o.branch_id
      LEFT JOIN users creator ON creator.id=o.snapshot->>'created_by_staff_id'
      LEFT JOIN LATERAL (SELECT driver_id FROM driver_tasks WHERE order_id=o.id ORDER BY created_at DESC,id DESC LIMIT 1) t ON true
      LEFT JOIN users driver ON driver.id=t.driver_id
      LEFT JOIN LATERAL (SELECT details,created_at FROM order_events WHERE order_id=o.id
         AND event_type IN ('preparation.cancelled','task.cancelled') ORDER BY created_at DESC,id DESC LIMIT 1) e ON true
      WHERE o.brand_id=%s AND o.branch_id=ANY(%s)
        AND (o.snapshot ? 'complimentary' OR o.preparation_status IN ('cancelled','rejected') OR o.delivery_status='cancelled')
        AND ((o.created_at>=%s AND o.created_at<%s) OR (e.created_at>=%s AND e.created_at<%s))
      ORDER BY o.created_at DESC,o.id""",(u['brand_id'],bids,lo,hi,lo,hi)).fetchall()
    output=[]
    for o in rows:
        if driver_id and o['driver_id']!=driver_id:continue
        if product_id and not any(i.get('product_id')==product_id for i in o['snapshot'].get('items',[])):continue
        free=o['snapshot'].get('complimentary') or {}
        output.append(dict(day=opday(o['created_at']),label=str(o['snapshot'].get('order_reference') or o['order_no']),
            branch=o['branch_name'],driver=o['driver_name'] or '—',kind=KINDS.get(free.get('kind'),('طلب ملغى','Cancelled order'))[en],
            amount=free.get('catalog_value_halalas',o['total_halalas']),reason=free.get('reason') or (o['cancel_details'] or {}).get('reason','—'),
            actor=o['creator_name'] or (o['cancel_details'] or {}).get('actor_name','—'),
            action=o['delivery_status'] if o['preparation_status'] not in ('cancelled','rejected') else o['preparation_status'],
            notes=' / '.join(str(i['quantity'])+' × '+i['name'] for i in o['snapshot'].get('items',[]))))
    if not driver_id and not product_id:
        reprints=c.execute("""SELECT j.*,o.order_no,o.snapshot,u.display_name actor_name,b.name branch_name
          FROM branch_print_jobs j JOIN orders o ON o.id=j.order_id JOIN users u ON u.id=j.requested_by JOIN branches b ON b.id=j.branch_id
          WHERE j.brand_id=%s AND j.branch_id=ANY(%s) AND o.branch_id=ANY(%s)
          AND j.created_at>=%s AND j.created_at<%s AND length(trim(j.reprint_reason))>0 ORDER BY j.created_at DESC""",(u['brand_id'],bids,bids,lo,hi)).fetchall()
        for p in reprints:
            output.append(dict(day=opday(p['created_at']),label=str(p['snapshot'].get('order_reference') or p['order_no']),
                branch=p['branch_name'],driver='—',kind='Reprint' if en else 'إعادة طباعة',amount=0,
                reason=p['reprint_reason'],actor=p['actor_name'],action=p['status'],notes='No new order or charge' if en else 'بدون طلب أو مبلغ إضافي'))
    return safe({'rows':output,'group':'complimentary','branches':bs,'period':{'start':ds,'end':de,'timezone':'Asia/Riyadh'},
        'definitions':'Reference menu values, not sales or actual product costs. Reprints add no order or charge.' if en else 'القيمة مرجعية من المنيو وليست مبيعات أو تكلفة الأصناف الفعلية. إعادة الطباعة لا تضيف طلبًا أو مبلغًا.',
        'generated_at':datetime.now(TZ)})

@router.get('/options')
def options(user=Depends(case_access)):
    with db.tx() as c:
        bs=branches(c,user);bids={b['id'] for b in bs}
        people=c.execute("SELECT id,display_name,role,branch_ids,vehicle FROM users WHERE brand_id=%s AND role IN ('driver','delivery_manager') ORDER BY display_name",(user['brand_id'],)).fetchall()
        return {'branches':bs,'people':[p for p in people if set(p['branch_ids'])&bids],'products':c.execute('SELECT id,name FROM products WHERE brand_id=%s ORDER BY name',(user['brand_id'],)).fetchall(),'today':service_hours.delivery_operational_date(),'can_review':auth.has_permission(user,REVIEW),'can_report':auth.has_permission(user,READ),'can_follow':can_follow(c,user),'categories':KINDS,'states':STATES,'responsibilities':RESP,'manager_followup':(c.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(user['brand_id'],)).fetchone() or {}).get('settings',{}).get('accounting_manager_followup',True)}

Group=Literal['daily','branch','items','driver','driver_daily','driver_incentives','driver_incentive_details','deficits','actions','complimentary','driver_statement']
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
        if c.execute("SELECT 1 FROM closure_reports r WHERE r.brand_id=%s AND r.kind='manager' AND r.created_at>=%s AND r.created_at<%s AND r.branch_ids ?| %s AND r.snapshot->'shift'->'submission'->>'operational_approved'='true' AND NOT EXISTS(SELECT 1 FROM manager_financial_matches m WHERE m.report_id=r.id AND m.status='matched') AND NOT EXISTS(SELECT 1 FROM driver_accounting_settlements d WHERE d.report_id=r.id AND d.stage='cash_received' AND d.balance_approved) AND NOT EXISTS(SELECT 1 FROM accounting_daily_closures a WHERE a.manager_shift_id=r.shift_id) LIMIT 1",(user['brand_id'],lo,hi,bids)).fetchone():
            fail('PENDING_MANAGER_MATCHES','ثبّت البنك والكاش لتقفيلات المدير قبل إقفال الشهر.',409)
        from .accounting_statements import source_rows,histories
        driver_reports=source_rows(c,user,bids,ds,de);driver_events=histories(c,user,driver_reports)
        if any(driver_settlement_values(r['snapshot'],driver_events[r['id']][-1] if driver_events[r['id']] else None)['stage']!='cash_received' for r in driver_reports):
            fail('PENDING_SETTLEMENTS','أكمل تسويات السائقين قبل إقفال الشهر.',409)
        from .driver_incentives import report_data
        entitlements=report_data(c,user,ds,de,branch_ids=bids)
        if entitlements['pending_review'] or entitlements['awaiting_approval'] or any(r['incomplete'] for r in entitlements['rows']):fail('PENDING_INCENTIVES','راجع أسباب الطلبات الملغية والمرتجعة واعتمادات الشفتات قبل إقفال الشهر.',409)
        deficits=deficit_rows(c,user,lo,hi,bids,'','ar')
        if any(r['review'].startswith('بانتظار') for r in deficits):fail('PENDING_REVIEWS','اعتمد مراجعات العجز والفائض قبل إقفال الشهر.',409)
        def perform():
            if c.execute('SELECT 1 FROM accounting_months WHERE brand_id=%s AND month=%s AND scope_hash=%s',(user['brand_id'],ds,h)).fetchone():fail('MONTH_CLOSED','هذا النطاق مقفل مسبقًا.',409)
            snapshot={g:build(c,user,ds.isoformat(),de.isoformat(),','.join(bids),group=g) for g in ('daily','branch','items','driver','driver_daily','driver_incentives','driver_incentive_details','deficits','actions','driver_statement')}
            mid=db.new_id('acm_');c.execute('INSERT INTO accounting_months(id,brand_id,month,branch_ids,scope_hash,snapshot,closed_by,reason) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(mid,user['brand_id'],ds,db.json(bids),h,db.json(snapshot),user['id'],body.note));db.audit(c,user['id'],'accounting.month.closed','accounting_month',mid,{'month':body.month,'branches':bids,'reason':body.note});return {'id':mid}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.month',body.model_dump(),perform)

@router.get('/months')
def months(user=Depends(access)):
    with db.tx() as c:
        bids={b['id'] for b in branches(c,user)}
        return safe({'rows':[r for r in c.execute('SELECT id,month,branch_ids,reason,created_at FROM accounting_months WHERE brand_id=%s ORDER BY month DESC',(user['brand_id'],)).fetchall() if set(r['branch_ids'])<=bids]})

@router.get('/months/{identifier}')
def month(identifier:str,group:Group='driver_daily',driver_id:str='',format:Literal['json','pdf','xlsx']='json',lang:Literal['ar','en']='ar',user=Depends(access)):
    with db.tx() as c:
        r=c.execute('SELECT * FROM accounting_months WHERE id=%s AND brand_id=%s',(identifier,user['brand_id'])).fetchone()
        if not r or not set(r['branch_ids'])<={b['id'] for b in branches(c,user)}:fail('MONTH_SCOPE','الكشف غير متاح.',404)
        if group not in r['snapshot']:fail('SNAPSHOT_GROUP_UNAVAILABLE','هذا التقرير غير موجود في الكشف التاريخي المقفل.',409)
        data=r['snapshot'][group];data['frozen']=True;data['closed_at']=r['created_at'].isoformat()
        if driver_id and group=='driver_statement':
            for key in ('rows','drivers','settlements','adjustment_history','incidents'):
                data[key]=[row for row in data.get(key,[]) if row['driver_id']==driver_id]
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


class DriverBalanceApproval(Note):
    expected_revision: StrictInt = Field(ge=0)
    bank_halalas: StrictInt = Field(ge=0, le=100000000000)
    cash_due_halalas: StrictInt = Field(ge=0, le=100000000000)


class DriverCashReceipt(Note):
    expected_revision: StrictInt = Field(ge=0)
    cash_received_halalas: StrictInt = Field(ge=0, le=100000000000)
    received_on: date
    receipt_reference: str = Field(min_length=1, max_length=200)


def driver_settlement_values(snapshot, latest=None):
    sub = (snapshot.get('shift') or {}).get('submission') or {}
    summary = snapshot.get('summary') or {}
    submitted = sub.get('driver_original_submission') or sub
    net_card = int(summary.get('card_collected_halalas', 0))-int(summary.get('card_refunded_halalas', 0))
    original_bank = int(submitted.get('terminal_total_halalas', net_card) or 0)
    original_cash = int(submitted.get('handover_halalas') or 0)
    approved = bool(latest and latest.get('balance_approved',True))
    cash_due = latest['cash_due_halalas'] if latest else int(sub.get('handover_halalas') or 0)
    received = latest['cash_received_halalas'] if latest else None
    stage = ('cash_received' if approved else 'cash_pending_balance') if received is not None else ('balance_approved' if approved else 'pending_balance')
    expected_cash = int(sub.get('expected_halalas',original_cash-int(sub.get('difference_halalas',0))) or 0)-int(sub.get('retained_halalas',0) or 0)
    expected_bank = int(sub.get('expected_card_halalas',net_card if 'card_collected_halalas' in summary else original_bank) or 0)
    bank = latest['bank_halalas'] if latest else int(sub.get('terminal_total_halalas',net_card) or 0)
    cash_difference = received-expected_cash if received is not None else None
    bank_difference = bank-expected_bank if approved else None
    return dict(original_bank_halalas=original_bank, original_cash_halalas=original_cash,
                system_bank_halalas=expected_bank,system_cash_halalas=expected_cash,
                bank_halalas=bank,balance_approved=approved,cash_recorded=received is not None,
                cash_due_halalas=cash_due, cash_received_halalas=received,
                cash_difference_halalas=received-cash_due if received is not None else None,
                cash_variance_halalas=cash_difference,bank_difference_halalas=bank_difference,
                shortage_halalas=sum(max(0,-v) for v in (cash_difference,bank_difference) if v is not None),
                surplus_halalas=sum(max(0,v) for v in (cash_difference,bank_difference) if v is not None),
                pending_cash_halalas=cash_due if received is None else 0,
                stage=stage, revision=latest['revision'] if latest else 0)


def driver_settlement_scope(c, user, report_id):
    # Lock the parent to serialize first approval, amendments and final receipt.
    row = c.execute("SELECT * FROM closure_reports WHERE id=%s AND brand_id=%s AND kind IN ('driver','manager') FOR UPDATE",
                    (report_id, user['brand_id'])).fetchone()
    if not row or (user['role'] != 'owner' and not set(row['branch_ids']) <= set(user.get('branch_ids', []))):
        fail('REPORT_SCOPE', 'التقفيلة غير متاحة.', 404)
    if row['user_id'] == user['id']:
        fail('SELF_SETTLEMENT', 'اعتماد التسوية من حساب محاسب مستقل.', 403)
    if row['kind']=='driver':
        shift = c.execute('SELECT manager_shift_id FROM shifts WHERE id=%s', (row['shift_id'],)).fetchone()
        manager = c.execute("SELECT id FROM shifts WHERE id=%s AND kind='manager' AND status='closed'",
                            (shift['manager_shift_id'],)).fetchone()
        if not manager:
            fail('MANAGER_CLOSURE_REQUIRED', 'تنتقل الموازنة للمحاسب بعد إقفال شفت مدير التوصيل.', 409)
    return row


@router.get('/driver-settlements/{report_id}/print')
def settlement_original_report(report_id:str,user=Depends(access)):
    with db.tx() as c:
        row=driver_settlement_scope(c,user,report_id)
        from .reporting import make_print_html
        return Response(make_print_html(row['snapshot']),media_type='text/html',headers={'Cache-Control':'no-store'})


@router.get('/driver-settlements')
def driver_settlements(kind:Literal['driver','manager']='driver',user=Depends(access)):
    with db.tx() as c:
        # No date restriction: cash from an older operational day remains visible.
        rows = c.execute("""SELECT r.*,u.display_name driver_name,
          s.manager_shift_id,manager_user.display_name manager_name,
          coalesce(s.delivery_operational_date,(s.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '16 hours')::date) accounting_day
          FROM closure_reports r JOIN users u ON u.id=r.user_id JOIN shifts s ON s.id=r.shift_id
          LEFT JOIN shifts manager ON manager.id=s.manager_shift_id
          LEFT JOIN users manager_user ON manager_user.id=manager.user_id
          WHERE r.brand_id=%s AND r.kind=%s AND (r.kind='manager' OR manager.status='closed')
          ORDER BY r.created_at DESC,r.id""", (user['brand_id'],kind)).fetchall()
        events = c.execute("""SELECT e.*,u.display_name actor_name FROM driver_accounting_settlements e
          JOIN users u ON u.id=e.actor_id WHERE e.brand_id=%s ORDER BY e.report_id,e.revision""", (user['brand_id'],)).fetchall()
        history = defaultdict(list)
        for event in events: history[event['report_id']].append(event)
        result = []
        for row in rows:
            if user['role'] != 'owner' and not set(row['branch_ids']) <= set(user.get('branch_ids', [])): continue
            changes = history[row['id']]
            latest = changes[-1] if changes else None
            if row['kind']=='manager' and not latest:
                legacy=c.execute("SELECT * FROM manager_financial_matches WHERE report_id=%s AND status='matched' ORDER BY revision DESC LIMIT 1",(row['id'],)).fetchone()
                if legacy:
                    latest=dict(stage='cash_received',revision=0,bank_halalas=legacy['bank_halalas'],cash_due_halalas=legacy['expected_cash_halalas'],cash_received_halalas=legacy['cash_halalas'],received_on=legacy['created_at'].date())
            approved = next((e for e in changes if e['stage'] == 'balance_approved'), None)
            from .catalog import normalize_city
            local = any(normalize_city(b['city']) == 'حفر الباطن' for b in c.execute(
                'SELECT city FROM branches WHERE id=ANY(%s)', (row['branch_ids'],)).fetchall())
            due_on = row['accounting_day'] + timedelta(days=1) if local else None
            result.append(dict(id=row['id'], user_id=row['user_id'], driver_name=row['driver_name'],kind=row['kind'],
                branch_ids=row['branch_ids'], day=row['accounting_day'],manager_shift_id=row['manager_shift_id'],manager_name=row['manager_name'],
                label=row['snapshot'].get('closure_label') or row['id'],
                due_on=due_on, overdue=bool(due_on and datetime.now(TZ).date()>due_on and driver_settlement_values(row['snapshot'],latest)['stage']!='cash_received'),
                waiting_days=max(0, (datetime.now(TZ).date()-approved['created_at'].astimezone(TZ).date()).days) if approved and not latest.get('received_on') else 0,
                history=changes, **driver_settlement_values(row['snapshot'], latest)))
        result.sort(key=lambda r: r['stage'] == 'cash_received')
        return safe({'settlements': result})


def save_driver_settlement(c, user, report_id, body, receipt=False):
    row = driver_settlement_scope(c, user, report_id)
    if row['kind']=='manager' and c.execute("SELECT 1 FROM manager_financial_matches WHERE report_id=%s AND status='matched'",(report_id,)).fetchone():
        fail('SETTLEMENT_FINAL','التقفيلة معتمدة ماليًا مسبقًا.',409)
    previous = c.execute('SELECT * FROM driver_accounting_settlements WHERE report_id=%s ORDER BY revision DESC LIMIT 1', (report_id,)).fetchone()
    revision = previous['revision'] if previous else 0
    if previous and previous['cash_received_halalas'] is not None and previous.get('balance_approved',True):
        fail('SETTLEMENT_FINAL', 'تم اعتماد استلام الكاش والتسوية نهائيًا.', 409)
    if revision != body.expected_revision: fail('SETTLEMENT_CHANGED', 'حدثت مراجعة أحدث؛ حدّث القائمة.', 409)
    if receipt:
        if previous and previous['cash_received_halalas'] is not None:fail('CASH_ALREADY_RECEIVED','تم تسجيل استلام الكاش سابقًا.',409)
        # Actual receipt date is independent of operational day and approval time.
        shift = c.execute('SELECT opened_at FROM shifts WHERE id=%s', (row['shift_id'],)).fetchone()
        if body.received_on > datetime.now(TZ).date() or body.received_on < shift['opened_at'].astimezone(TZ).date():
            fail('RECEIPT_DATE', 'تاريخ الاستلام يجب أن يكون بين بداية الشفت واليوم.')
        values=driver_settlement_values(row['snapshot'],previous)
        bank, due = values['bank_halalas'], values['cash_due_halalas']
    else: bank, due = body.bank_halalas, body.cash_due_halalas
    approved=bool(not receipt or previous and previous.get('balance_approved',True))
    received=body.cash_received_halalas if receipt else previous['cash_received_halalas'] if previous else None
    received_on=body.received_on if receipt else previous['received_on'] if previous else None
    reference=body.receipt_reference if receipt else previous['receipt_reference'] if previous else None
    event = c.execute("""INSERT INTO driver_accounting_settlements
      (id,report_id,brand_id,revision,stage,bank_halalas,cash_due_halalas,cash_received_halalas,received_on,receipt_reference,note,actor_id,balance_approved)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
      (db.new_id('das_'),report_id,user['brand_id'],revision+1,'cash_received' if received is not None else 'balance_approved',
       bank,due,received,received_on,reference,body.note,user['id'],approved)).fetchone()
    db.audit(c,user['id'],'accounting.driver_settlement.'+event['stage'],'report',report_id,
             {'event_id':event['id'],'revision':event['revision'],'reason':body.note,
              'before':driver_settlement_values(row['snapshot'],previous),'after':driver_settlement_values(row['snapshot'],event)})
    return safe(event)


@router.post('/driver-settlements/{report_id}/approve-balance')
def approve_driver_balance(report_id:str,body:DriverBalanceApproval,request:Request,user=Depends(access)):
    if not auth.has_permission(user, REVIEW): fail('ACCOUNTING_FORBIDDEN','صلاحية الاعتماد غير متاحة.',403)
    with db.tx() as c:
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.driver_balance:'+report_id,
            body.model_dump(),lambda:save_driver_settlement(c,user,report_id,body))


@router.post('/driver-settlements/{report_id}/receive-cash')
def receive_driver_cash(report_id:str,body:DriverCashReceipt,request:Request,user=Depends(access)):
    if not auth.has_permission(user, REVIEW): fail('ACCOUNTING_FORBIDDEN','صلاحية الاعتماد غير متاحة.',403)
    with db.tx() as c:
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.driver_receipt:'+report_id,
            body.model_dump(mode='json'),lambda:save_driver_settlement(c,user,report_id,body,receipt=True))


@router.get('/manager-cards')
def accounting_manager_cards(user=Depends(access)):
    from .accounting_statements import manager_cards
    with db.tx() as c:
        return manager_cards(c,user,[b['id'] for b in branches(c,user)])


@router.post('/driver-settlements/{report_id}/extend-deadline')
def extend_accounting_deadline(report_id:str,body:Note,request:Request,user=Depends(access)):
    if not auth.has_permission(user,REVIEW):fail('ACCOUNTING_FORBIDDEN','صلاحية الاعتماد غير متاحة.',403)
    from .accounting_statements import extend_deadline
    with db.tx() as c:
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.deadline:'+report_id,
            body.model_dump(),lambda:extend_deadline(c,user,report_id,body.note))


@router.post('/manager-cards/{manager_shift_id}/approve-day')
def approve_accounting_day(manager_shift_id:str,body:Note,request:Request,user=Depends(access)):
    if not auth.has_permission(user,REVIEW):fail('ACCOUNTING_FORBIDDEN','صلاحية الاعتماد غير متاحة.',403)
    from .accounting_statements import approve_day
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'accounting.day:'+manager_shift_id,
            body.model_dump(),lambda:approve_day(c,user,manager_shift_id,body.note))


@router.get('/days/{identifier}')
def accounting_day_report(identifier:str,format:Literal['json','pdf','xlsx']='json',lang:Literal['ar','en']='ar',user=Depends(access)):
    with db.tx() as c:
        row=c.execute('SELECT * FROM accounting_daily_closures WHERE id=%s AND brand_id=%s',(identifier,user['brand_id'])).fetchone()
        if not row or not set(row['branch_ids'])<={b['id'] for b in branches(c,user)}:fail('DAY_SCOPE','الكشف غير متاح.',404)
        data=row['snapshot'];data.update(frozen=True,closed_at=row['created_at'].isoformat(),approved_by=row['approved_by'],approval_note=row['note'])
        return export(data,format,lang)
