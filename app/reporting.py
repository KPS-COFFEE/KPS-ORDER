"""Immutable closure snapshots with durable CSV/PDF files and visible retry state."""
import csv
import html
import io
import os
from datetime import timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,Response,Request
from . import auth,db
from .operations import uid,safe,fail,roles,mutate,day_start

router=APIRouter(prefix='/api/reports',tags=['reports'])

def can_read(user,row):
    if user['role']=='owner':return user['brand_id']==row['brand_id']
    if user['brand_id']!=row['brand_id'] or not set(row['branch_ids'])<=set(user.get('branch_ids',[])):return False
    if user['id']==row['user_id']:return True
    return user['role']=='delivery_manager' and row['snapshot'].get('manager_user_id')==user['id']

def report(c,user,rid):
    r=c.execute('SELECT * FROM closure_reports WHERE id=%s',(rid,)).fetchone()
    if not r or not can_read(user,r):fail(404,'التقرير غير موجود')
    return r

def create_snapshot(c,sid,approved_by):
    s=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone()
    shift_ids=[sid]
    children=[]
    if s['kind']=='manager':
        children=c.execute('SELECT r.id,r.shift_id,r.version,r.snapshot FROM closure_reports r JOIN shifts s ON s.id=r.shift_id WHERE s.manager_shift_id=%s ORDER BY r.created_at',(sid,)).fetchall()
        shift_ids += [r['shift_id'] for r in children]
    manager_user_id=s['user_id'] if s['kind']=='manager' else c.execute('SELECT user_id FROM shifts WHERE id=%s',(s['manager_shift_id'],)).fetchone()['user_id']
    orders=c.execute("SELECT DISTINCT o.id,o.branch_id,o.service,o.preparation_status,o.delivery_status,o.payment_status,o.subtotal_halalas,o.delivery_fee_halalas,o.total_halalas,o.snapshot,o.created_at,o.completed_at FROM orders o JOIN driver_tasks t ON t.order_id=o.id WHERE t.shift_id=ANY(%s) ORDER BY o.id",(shift_ids,)).fetchall()
    tasks=c.execute('SELECT * FROM driver_tasks WHERE shift_id=ANY(%s) ORDER BY created_at',(shift_ids,)).fetchall()
    collections=c.execute('SELECT * FROM collections WHERE shift_id=ANY(%s) ORDER BY created_at',(shift_ids,)).fetchall()
    refunds=c.execute('SELECT * FROM refunds WHERE shift_id=ANY(%s) ORDER BY created_at',(shift_ids,)).fetchall()
    events=c.execute('SELECT * FROM custody_events WHERE shift_id=ANY(%s) ORDER BY created_at',(shift_ids,)).fetchall()
    transfers=c.execute('SELECT * FROM custody_transfers WHERE from_shift_id=ANY(%s) OR to_shift_id=ANY(%s) ORDER BY created_at',(shift_ids,shift_ids)).fetchall()
    if s['kind']=='manager':
        # Financial/outcome history comes from approved child snapshots, not live
        # orders that may later be refunded or fulfilled by another manager.
        def merge(name,extra):
            records={r['id']:r for child in children for r in child['snapshot'][name]}
            for r in extra:records.setdefault(r['id'],r)
            return list(records.values())
        child_orders={}
        for child in children:
            for o in child['snapshot']['orders']:
                if o['id'] not in child_orders or o.get('fulfilled_in_report'):child_orders[o['id']]=o
        orders=list(child_orders.values())
        tasks=merge('tasks',[])
        collections=merge('collections',c.execute('SELECT * FROM collections WHERE shift_id=%s',(sid,)).fetchall())
        refunds=merge('refunds',c.execute('SELECT * FROM refunds WHERE shift_id=%s',(sid,)).fetchall())
        events=merge('custody_events',c.execute('SELECT * FROM custody_events WHERE shift_id=%s',(sid,)).fetchall())
        transfers=merge('transfers',c.execute('SELECT * FROM custody_transfers WHERE from_shift_id=%s OR to_shift_id=%s',(sid,sid)).fetchall())
    for order in orders:
        order['snapshot']=safe(order['snapshot']);order['snapshot'].pop('notes',None);order['snapshot'].pop('note',None)
        for item in order['snapshot'].get('items',[]):item.pop('note',None)
    fulfilled={t['order_id'] for t in tasks if t['status']=='delivered' and t.get('delivered_at')}
    for o in orders:o['fulfilled_in_report']=o.get('fulfilled_in_report',o['id'] in fulfilled)
    cash=sum(r['amount_halalas'] for r in collections if r['method']=='cash')
    card=sum(r['amount_halalas'] for r in collections if r['method']=='card')
    completed=[o for o in orders if o['fulfilled_in_report']]
    cfg=c.execute('SELECT settings FROM brands WHERE id=%s',(s['brand_id'],)).fetchone()['settings']
    settings=c.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(s['brand_id'],)).fetchone()
    cfg={**cfg,**(settings['settings'] if settings else {})};minutes,configured=day_start(cfg)
    snap=safe({'schema_version':1,'reference':sid,'kind':s['kind'],'timezone':'Asia/Riyadh','operational_day_configured':configured,'operational_date':(s['opened_at'].astimezone(ZoneInfo('Asia/Riyadh'))-timedelta(minutes=minutes)).date().isoformat(),'shift':s,'manager_user_id':manager_user_id,'prepared_by':s['user_id'],'approved_by':approved_by,'orders':orders,'tasks':tasks,'collections':collections,'refunds':refunds,'custody_events':events,'transfers':transfers,'linked_corrections':c.execute('SELECT * FROM report_corrections WHERE shift_id=ANY(%s)',(shift_ids,)).fetchall(),'manager_handovers':c.execute('SELECT * FROM manager_handovers WHERE from_shift_id=ANY(%s) OR to_shift_id=ANY(%s)',(shift_ids,shift_ids)).fetchall(),'retained_carryovers':c.execute('SELECT * FROM retained_carryovers WHERE from_shift_id=ANY(%s) OR to_shift_id=ANY(%s)',(shift_ids,shift_ids)).fetchall(),'driver_reports':[{'id':r['id'],'shift_id':r['shift_id'],'version':r['version'],'closure':r['snapshot']['shift']['submission']} for r in children], 'summary':{'unique_orders':len(orders),'completed_orders':len(completed),'cash_collected_halalas':cash,'card_collected_halalas':card,'cash_refunded_halalas':sum(r['amount_halalas'] for r in refunds if r['method']=='cash'),'card_refunded_halalas':sum(r['amount_halalas'] for r in refunds if r['method']=='card'),'gross_completed_halalas':sum(o['total_halalas'] for o in completed),'delivery_fees_halalas':sum(o['delivery_fee_halalas'] for o in completed),'net_collections_halalas':cash+card-sum(r['amount_halalas'] for r in refunds),'submission':s['submission']}})
    rid=uid();c.execute('INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES(%s,%s,%s,%s,%s,%s,%s)',(rid,sid,s['kind'],s['brand_id'],db.json(s['branch_ids']),s['user_id'],db.json(snap)))
    return rid

def cell(v):
    text=str(v if v is not None else '')
    if text.lstrip().startswith(('=','+','-','@')):text="'"+text
    return text

def make_csv(snap):
    out=io.StringIO();writer=csv.writer(out);writer.writerow(['نوع السجل','المرجع','الحالة/الطريقة','المبلغ بالهللات','الفرع/الشفت','الوقت'])
    for o in snap['orders']:writer.writerow([cell(v) for v in ['order',o['id'],o['delivery_status'],o['total_halalas'],o['branch_id'],o['created_at']]])
    for name in ('collections','refunds'):
        for r in snap[name]:writer.writerow([cell(v) for v in [name,r['id'],r['method'],r['amount_halalas'],r['shift_id'],r['created_at']]])
    for r in snap['custody_events']:writer.writerow([cell(v) for v in ['custody',r['id'],r['kind'],r['amount_halalas'],r['shift_id'],r['created_at']]])
    for key,v in snap['summary']['submission'].items():writer.writerow(['closure',cell(key),'',cell(v),snap['shift']['id'],''])
    return ('\ufeff'+out.getvalue()).encode('utf-8')

def make_pdf(snap):
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import arabic_reshaper
    from bidi.algorithm import get_display
    font=os.getenv('REPORT_ARABIC_FONT','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    if not os.path.isfile(font):raise RuntimeError('Arabic report font unavailable')
    if 'KPSArabic' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('KPSArabic',font))
    stream=io.BytesIO();cv=canvas.Canvas(stream,pagesize=(595,842));cv.setTitle('KPS COFFEE — Operational closure')
    y=795
    def line(text):
        nonlocal y
        if y<45:cv.showPage();y=795
        cv.setFont('KPSArabic',10)
        text=str(text)
        # Wrap to a safe character width and retain all financial content.
        for start in range(0,len(text),82):
            if y<45:cv.showPage();cv.setFont('KPSArabic',10);y=795
            cv.drawRightString(555,y,get_display(arabic_reshaper.reshape(text[start:start+82])));y-=18
    line('KPS COFFEE — تقرير تشغيلي وتقفيلة')
    line('مرجع التقفيلة: '+snap['reference'])
    line('التاريخ التشغيلي: '+snap['operational_date']+' | المنطقة الزمنية: Asia/Riyadh')
    line('البداية: '+snap['shift']['opened_at']+' | النهاية: '+str(snap['shift']['closed_at']))
    line('المعد: '+snap['prepared_by']+' | المعتمد: '+snap['approved_by'])
    labels={'expected_halalas':'النقد المتوقع','counted_halalas':'النقد المعدود','handover_halalas':'التسليم النهائي المؤكد','retained_halalas':'العهدة المحتفظ بها','difference_halalas':'الفرق المعتمد','cash_collected_halalas':'التحصيل النقدي الصافي بعد الباقي','card_collected_halalas':'تحصيل الشبكة','cash_refunded_halalas':'استردادات الكاش','card_refunded_halalas':'استردادات الشبكة','net_collections_halalas':'صافي التحصيلات','delivery_fees_halalas':'رسوم الطلبات المكتملة'}
    for key,value in snap['summary']['submission'].items():line(labels.get(key,key)+': '+f'{value/100:.2f} ر.س')
    for key in ('cash_collected_halalas','card_collected_halalas','cash_refunded_halalas','card_refunded_halalas','net_collections_halalas','delivery_fees_halalas'):line(labels[key]+': '+f'{snap["summary"][key]/100:.2f} ر.س')
    if snap['shift'].get('exception_reason'):line('سبب الاستثناء: '+snap['shift']['exception_reason'])
    line('الطلبات ونتائجها — كل مبلغ بالريال السعودي')
    for o in snap['orders']:
        line(o['id']+' | '+o['branch_id']+' | '+o['preparation_status']+' / '+o['delivery_status']+' / '+o['payment_status']+(' | منفذ ضمن التقرير' if o.get('fulfilled_in_report') else ' | سجل تحويل تاريخي دون احتساب مبيعات')+' | '+f'{o["total_halalas"]/100:.2f}')
        for i in o['snapshot']['items']:line('  '+i['name']+' × '+str(i['quantity'])+' | '+f'{i["line_total_halalas"]/100:.2f}')
    line('تحصيلات الشبكة والكاش ومراجعها')
    for r in snap['collections']:line(r['id']+' | '+r['method']+' | '+f'{r["amount_halalas"]/100:.2f}'+' | المرجع: '+str(r['reference'])+' | الباقي: '+f'{r["change_halalas"]/100:.2f}')
    line('الاستردادات والعهد والسلف والتسليمات')
    for r in snap['refunds']:line(r['id']+' | استرداد '+r['method']+' | '+f'{r["amount_halalas"]/100:.2f}'+' | '+r['reason'])
    for r in snap['custody_events']:line(r['id']+' | '+r['kind']+' | '+f'{r["amount_halalas"]/100:.2f}'+' | '+r['reason'])
    for r in snap['transfers']:line(r['id']+' | '+r['status']+' | '+f'{r["amount_halalas"]/100:.2f}'+' | المستلم: '+r['recipient_user_id']+' | '+r['reference'])
    for t in snap['tasks']:
        if t['reason']:line('استثناء المهمة '+t['id']+': '+t['reason'])
        if t['picked_up_at'] and t['delivered_at']:line('زمن التوصيل '+t['id']+': '+str(t['picked_up_at'])+' إلى '+str(t['delivered_at']))
    line('التقفيلات التابعة، تشمل شفتات السائقين دون طلبات')
    for r in snap['driver_reports']:line(r['id']+' | الشفت '+r['shift_id']+' | المتوقع '+str(r['closure']['expected_halalas'])+' هللة | المعدود '+str(r['closure']['counted_halalas'])+' هللة | الفرق '+str(r['closure']['difference_halalas']))
    cv.save();return stream.getvalue()

def generate_files(rid):
    # Files are generated after the closure transaction has committed. Failure never reopens it.
    with db.tx() as c:
        r=c.execute('SELECT snapshot,pdf_status FROM closure_reports WHERE id=%s',(rid,)).fetchone()
    if not r or r['pdf_status']=='ready':return
    csv_data=make_csv(r['snapshot'])
    try:
        pdf=make_pdf(r['snapshot'])
        with db.tx() as c:c.execute("UPDATE closure_reports SET pdf_data=%s,csv_data=%s,pdf_status='ready',pdf_error=NULL WHERE id=%s",(pdf,csv_data,rid))
    except Exception as exc:
        # Store a bounded type/message rather than local paths, secrets or a traceback.
        with db.tx() as c:c.execute("UPDATE closure_reports SET csv_data=%s,pdf_status='failed',pdf_error=%s WHERE id=%s",(csv_data,type(exc).__name__+': report generation failed; retry is available',rid))

@router.get('')
def report_list(offset:int=0,user=Depends(auth.current_user)):
    roles(user,'owner','driver','delivery_manager')
    if offset<0 or offset>100000:fail(422,'صفحة غير صالحة')
    with db.tx() as c:
        clause='brand_id=%s' if user['role']=='owner' else "brand_id=%s AND (user_id=%s OR snapshot->>'manager_user_id'=%s)"
        p=[user['brand_id']] if user['role']=='owner' else [user['brand_id'],user['id'],user['id'] if user['role']=='delivery_manager' else '']
        rows=c.execute('SELECT id,shift_id,kind,brand_id,branch_ids,user_id,version,pdf_status,pdf_error,created_at FROM closure_reports WHERE '+clause+' ORDER BY created_at DESC LIMIT 50 OFFSET %s',[*p,offset]).fetchall()
        if user['role']!='owner':rows=[r for r in rows if set(r['branch_ids'])<=set(user.get('branch_ids',[]))]
        return safe({'reports':rows,'offset':offset,'limit':50})

@router.get('/{rid}')
def report_detail(rid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        r=report(c,user,rid);r.pop('pdf_data');r.pop('csv_data');corrections=c.execute('SELECT x.*,n.id resulting_report_id FROM report_corrections x LEFT JOIN closure_reports n ON n.shift_id=x.shift_id WHERE x.report_id=%s ORDER BY correction_version',(rid,)).fetchall();return safe({'report':r,'corrections':corrections})

@router.get('/{rid}/print')
def print_report(rid:str,user=Depends(auth.current_user)):
    with db.tx() as c:r=report(c,user,rid)
    s=r['snapshot'];esc=lambda x:html.escape(str(x))
    rows=''.join('<tr><td>'+esc(o['id'])+'</td><td>'+esc(o['branch_id'])+'</td><td>'+esc(o['delivery_status'])+'</td><td>'+f'{o["total_halalas"]/100:.2f}'+'</td></tr>' for o in s['orders'])
    summary=''.join('<p>'+esc(k)+': '+esc(v)+'</p>' for k,v in s['summary']['submission'].items())
    page='<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>تقفيلة KPS</title><style>body{font:16px sans-serif;max-width:1100px;margin:24px;background:white;color:#222}table{width:100%;border-collapse:collapse}td,th{border:1px solid #aaa;padding:8px;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere}@media print{button{display:none}}</style><h1>تقرير تشغيلي وتقفيلة KPS</h1><p>'+esc(s['reference'])+' — '+esc(s['operational_date'])+' — Asia/Riyadh</p><button type="button" data-print-report>طباعة</button><script src="/report.js" defer></script>'+summary+'<table><tr><th>الطلب</th><th>الفرع</th><th>الحالة</th><th>ر.س</th></tr>'+rows+'</table><h2>تفاصيل اللقطة النهائية المحفوظة</h2><pre>'+esc(__import__('json').dumps(s,ensure_ascii=False,indent=2))+'</pre></html>'
    return Response(page,media_type='text/html',headers={'Cache-Control':'private, no-store','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; script-src 'self'"})

@router.get('/{rid}/pdf')
@router.get('/{rid}/print.pdf')
def download_pdf(rid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        r=report(c,user,rid)
        if r['pdf_status']!='ready' or not r['pdf_data']:fail(409,'ملف PDF غير جاهز؛ راجع حالة التوليد وأعد المحاولة')
        return Response(bytes(r['pdf_data']),media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="KPS-closure-{rid}.pdf"','Cache-Control':'private, no-store'})

@router.get('/{rid}/csv')
def download_csv(rid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        r=report(c,user,rid)
        if not r['csv_data']:fail(409,'التصدير قيد التوليد؛ أعد المحاولة')
        return Response(bytes(r['csv_data']),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="KPS-closure-{rid}.csv"','Cache-Control':'private, no-store'})

@router.post('/{rid}/pdf/retry')
def retry(rid:str,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        report(c,user,rid);db.audit(c,user['id'],'report.pdf.retry','report',rid)
    generate_files(rid)
    with db.tx() as c:
        r=report(c,user,rid);return {'report_id':rid,'pdf_status':r['pdf_status'],'pdf_error':r['pdf_error']}
