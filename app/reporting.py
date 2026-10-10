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
    brand=c.execute('SELECT name,settings FROM brands WHERE id=%s',(s['brand_id'],)).fetchone()
    cfg=brand['settings']
    settings=c.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(s['brand_id'],)).fetchone()
    cfg={**cfg,**(settings['settings'] if settings else {})};minutes,configured=day_start(cfg)
    snap=safe({'schema_version':1,'brand_name':brand['name'],'reference':sid,'kind':s['kind'],'timezone':'Asia/Riyadh','operational_day_configured':configured,'operational_date':(s['opened_at'].astimezone(ZoneInfo('Asia/Riyadh'))-timedelta(minutes=minutes)).date().isoformat(),'shift':s,'manager_user_id':manager_user_id,'prepared_by':s['user_id'],'approved_by':approved_by,'orders':orders,'tasks':tasks,'collections':collections,'refunds':refunds,'custody_events':events,'transfers':transfers,'linked_corrections':c.execute('SELECT * FROM report_corrections WHERE shift_id=ANY(%s)',(shift_ids,)).fetchall(),'manager_handovers':c.execute('SELECT * FROM manager_handovers WHERE from_shift_id=ANY(%s) OR to_shift_id=ANY(%s)',(shift_ids,shift_ids)).fetchall(),'retained_carryovers':c.execute('SELECT * FROM retained_carryovers WHERE from_shift_id=ANY(%s) OR to_shift_id=ANY(%s)',(shift_ids,shift_ids)).fetchall(),'driver_reports':[{'id':r['id'],'shift_id':r['shift_id'],'version':r['version'],'closure':r['snapshot']['shift']['submission']} for r in children], 'summary':{'unique_orders':len(orders),'completed_orders':len(completed),'cash_collected_halalas':cash,'card_collected_halalas':card,'cash_refunded_halalas':sum(r['amount_halalas'] for r in refunds if r['method']=='cash'),'card_refunded_halalas':sum(r['amount_halalas'] for r in refunds if r['method']=='card'),'gross_completed_halalas':sum(o['total_halalas'] for o in completed),'delivery_fees_halalas':sum(o['delivery_fee_halalas'] for o in completed),'net_collections_halalas':cash+card-sum(r['amount_halalas'] for r in refunds),'submission':s['submission']}})
    rid=uid();c.execute('INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES(%s,%s,%s,%s,%s,%s,%s)',(rid,sid,s['kind'],s['brand_id'],db.json(s['branch_ids']),s['user_id'],db.json(snap)))
    return rid

def cell(v):
    text=str(v if v is not None else '')
    if text.lstrip().startswith(('=','+','-','@')):text="'"+text
    return text

def make_csv(snap):
    out=io.StringIO();writer=csv.writer(out);writer.writerow(['نوع السجل','المرجع','الحالة/الطريقة','المبلغ بالهللات','الفرع/الشفت','الوقت'])
    for o in snap['orders']:writer.writerow([cell(v) for v in ['order_completed' if o.get('fulfilled_in_report') else 'order_history_only',o['id'],o['delivery_status'],o['total_halalas'],o['branch_id'],o['created_at']]])
    for name in ('collections','refunds'):
        for r in snap[name]:writer.writerow([cell(v) for v in [name,r['id'],r['method'],r['amount_halalas'],r['shift_id'],r['created_at']]])
    for r in snap['custody_events']:writer.writerow([cell(v) for v in ['custody',r['id'],r['kind'],r['amount_halalas'],r['shift_id'],r['created_at']]])
    for key,v in snap['summary']['submission'].items():writer.writerow(['closure',cell(key),'',cell(v),snap['shift']['id'],''])
    return ('\ufeff'+out.getvalue()).encode('utf-8')

def brand_title(snap):
    name=snap.get('brand_name')
    if isinstance(name,str) and name.strip():return name.strip()
    brand_id=str(snap.get('shift',{}).get('brand_id') or '')
    return 'KPS COFFEE' if brand_id=='kps' else brand_id

def make_pdf(snap):
    from datetime import datetime,timezone
    from decimal import Decimal
    from math import ceil
    import re
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import arabic_reshaper
    from bidi.algorithm import get_display
    font=os.getenv('REPORT_ARABIC_FONT','/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    if not os.path.isfile(font):raise RuntimeError('Arabic report font unavailable')
    if 'KPSArabic' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('KPSArabic',font))
    bold=font.replace('.ttf','-Bold.ttf')
    bold_name='KPSArabic'
    if os.path.isfile(bold):
        if 'KPSArabicBold' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('KPSArabicBold',bold))
        bold_name='KPSArabicBold'
    title=brand_title(snap)
    stream=io.BytesIO();cv=canvas.Canvas(stream,pagesize=(595,842));cv.setTitle(title+' - Operational closure')
    left,right=40,555
    y=792
    page_number=1
    def visual(text):return get_display(arabic_reshaper.reshape(str(text)))
    def footer():
        cv.setStrokeColorRGB(.82,.84,.85);cv.line(left,39,right,39)
        cv.setFillColorRGB(.4,.43,.45);cv.setFont('KPSArabic',8)
        cv.drawString(left,25,visual(title)+' | Asia/Riyadh')
        cv.drawRightString(right,25,visual('تقرير تشغيلي وتقفيلة - صفحة '+str(page_number)))
    def reserve(height):
        nonlocal y,page_number
        if y-height<55:
            footer();cv.showPage();page_number+=1;y=792
    def width(text,size=9.5,name='KPSArabic',rtl=True):
        return pdfmetrics.stringWidth(visual(text) if rtl else str(text),name,size)
    def wrap(text,max_width,size=9.5,rtl=True):
        # Measure actual glyph widths and wrap whole words. A long opaque token
        # is split into balanced pieces only when it cannot fit on a full line.
        lines=[];current=''
        for word in str(text).split():
            candidate=(current+' '+word).strip()
            if width(candidate,size,rtl=rtl)<=max_width:current=candidate;continue
            if current:lines.append(current);current=''
            if width(word,size,rtl=rtl)<=max_width:current=word;continue
            pieces=max(2,ceil(width(word,size,rtl=rtl)/max_width))
            while True:
                length=ceil(len(word)/pieces)
                chunks=[word[i:i+length] for i in range(0,len(word),length)]
                if all(width(chunk,size,rtl=rtl)<=max_width for chunk in chunks):break
                pieces+=1
            # Balance the final piece so a reference never strands one character.
            if len(chunks)>1 and len(chunks[-1])<4:
                joined=chunks[-2]+chunks[-1];middle=ceil(len(joined)/2)
                chunks[-2:]=[joined[:middle],joined[middle:]]
            lines.extend(chunks[:-1]);current=chunks[-1]
        if current or not lines:lines.append(current)
        return lines
    def text(text,size=9.5,color=(.14,.17,.19)):
        nonlocal y
        for row in wrap(text,right-left,size):
            reserve(15);cv.setFont('KPSArabic',size);cv.setFillColorRGB(*color)
            cv.drawRightString(right,y,visual(row));y-=15
    def section(title):
        nonlocal y
        reserve(58);y-=8
        cv.setFillColorRGB(.94,.95,.95);cv.roundRect(left,y-11,right-left,27,4,fill=1,stroke=0)
        cv.setFont(bold_name,11);cv.setFillColorRGB(.14,.22,.18)
        cv.drawRightString(right-10,y-2,visual(title));y-=33
    def kv(label,value,ltr=False):
        nonlocal y
        label_rows=wrap(label,160,9)
        value_rows=wrap(value,335,9.5,rtl=not ltr)
        rows=max(len(label_rows),len(value_rows));reserve(rows*15+2)
        for i in range(rows):
            reserve(15)
            cv.setFillColorRGB(.36,.4,.41);cv.setFont('KPSArabic',9)
            if i<len(label_rows):cv.drawRightString(right,y,visual(label_rows[i]))
            cv.setFillColorRGB(.1,.13,.15);cv.setFont('KPSArabic',9.5)
            if i<len(value_rows):
                if ltr:cv.drawString(left,y,value_rows[i])
                else:cv.drawRightString(375,y,visual(value_rows[i]))
            y-=15
        y-=2
    def money(value):return f'{Decimal(str(value))/100:,.2f}'
    def timestamp(label,value):
        if not value:kv(label,'غير مسجل');return
        try:
            parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
            if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
            local=parsed.astimezone(ZoneInfo('Asia/Riyadh'))
        except (TypeError,ValueError):kv(label,str(value),ltr=True);return
        kv(label+' - التاريخ',local.strftime('%Y-%m-%d'),ltr=True)
        kv(label+' - وقت الرياض',local.strftime('%H:%M:%S'),ltr=True)
    def divider():
        nonlocal y
        reserve(9);cv.setStrokeColorRGB(.88,.9,.9);cv.line(left,y,right,y);y-=12
    labels={'expected_halalas':'النقد المتوقع','counted_halalas':'النقد المعدود','handover_halalas':'التسليم النهائي المؤكد','retained_halalas':'العهدة المحتفظ بها','difference_halalas':'الفرق المعتمد','cash_collected_halalas':'التحصيل النقدي الصافي بعد الباقي','card_collected_halalas':'تحصيل الشبكة','cash_refunded_halalas':'استردادات الكاش','card_refunded_halalas':'استردادات الشبكة','net_collections_halalas':'صافي التحصيلات','delivery_fees_halalas':'رسوم الطلبات المكتملة'}
    text(title,15)
    text('تقرير تشغيلي وتقفيلة',13)
    kv('مرجع التقفيلة',snap['reference'],ltr=True)
    kv('التاريخ التشغيلي',snap['operational_date'],ltr=True)
    kv('المنطقة الزمنية','Asia/Riyadh',ltr=True)
    timestamp('بداية الشفت',snap['shift']['opened_at'])
    timestamp('نهاية الشفت',snap['shift']['closed_at'])
    kv('المعد',snap['prepared_by'],ltr=True);kv('المعتمد',snap['approved_by'],ltr=True)
    section('ملخص التقفيلة - جميع المبالغ بالريال السعودي')
    for key,value in snap['summary']['submission'].items():kv(labels.get(key,key),money(value),ltr=True)
    for key in ('cash_collected_halalas','card_collected_halalas','cash_refunded_halalas','card_refunded_halalas','net_collections_halalas','delivery_fees_halalas'):kv(labels[key],money(snap['summary'][key]),ltr=True)
    if snap['shift'].get('exception_reason'):kv('سبب الاستثناء المعتمد',snap['shift']['exception_reason'])
    section('الطلبات ونتائجها')
    if not snap['orders']:text('لا توجد طلبات ضمن هذه التقفيلة.')
    for o in snap['orders']:
        kv('مرجع الطلب',o['id'],ltr=True);kv('فرع التنفيذ',o['branch_id'],ltr=True)
        kv('التجهيز / التوصيل / الدفع',o['preparation_status']+' / '+o['delivery_status']+' / '+o['payment_status'],ltr=True)
        kv('الاحتساب','منفذ ضمن التقرير' if o.get('fulfilled_in_report') else 'سجل تحويل تاريخي دون احتساب مبيعات')
        kv('مبلغ الطلب (ر.س)',money(o['total_halalas']),ltr=True)
        for i in o['snapshot']['items']:
            kv('الصنف',i['name']);kv('الكمية / مبلغ الصنف (ر.س)',str(i['quantity'])+' / '+money(i['line_total_halalas']),ltr=True)
            for index,unit in enumerate(i.get('units',[]),1):
                descriptions=unit.get('option_snapshot') or {}
                if not descriptions and not unit.get('options'):continue
                kv('الوحدة '+str(index)+' - السعر (ر.س)',money(unit.get('price_halalas',i.get('unit_price_halalas',0))),ltr=True)
                if descriptions:
                    for group in descriptions.values():
                        kv(group.get('name','الخيار'),' / '.join(str(choice.get('name',choice.get('code',''))) for choice in group.get('choices',[])))
                else:
                    for group,choice in unit.get('options',{}).items():kv(str(group),str(choice),ltr=not bool(re.search(r'[\u0600-\u06ff]',str(choice))))
        divider()
    section('تحصيلات الشبكة والكاش ومراجعها')
    if not snap['collections']:text('لا توجد تحصيلات.')
    for r in snap['collections']:
        kv('مرجع التحصيل',r['id'],ltr=True);kv('وسيلة التحصيل',r['method'],ltr=True)
        kv('المبلغ / الباقي (ر.س)',money(r['amount_halalas'])+' / '+money(r['change_halalas']),ltr=True)
        kv('المرجع الفعلي',r['reference'] or 'غير مسجل',ltr=bool(r['reference'] and not re.search(r'[\u0600-\u06ff]',r['reference'])))
        divider()
    section('الاستردادات والعهد والسلف والتسليمات')
    if not snap['refunds'] and not snap['custody_events'] and not snap['transfers']:text('لا توجد أحداث عهدة أو استردادات.')
    for r in snap['refunds']:
        kv('مرجع الاسترداد',r['id'],ltr=True);kv('الوسيلة / المبلغ (ر.س)',r['method']+' / '+money(r['amount_halalas']),ltr=True)
        kv('سبب الاسترداد',r['reason']);divider()
    for r in snap['custody_events']:
        kv('مرجع حركة العهدة',r['id'],ltr=True);kv('نوع الحركة / المبلغ (ر.س)',r['kind']+' / '+money(r['amount_halalas']),ltr=True)
        kv('سبب الحركة / المرجع',r['reason']);divider()
    for r in snap['transfers']:
        kv('مرجع تسليم العهدة',r['id'],ltr=True);kv('الحالة / المبلغ (ر.س)',r['status']+' / '+money(r['amount_halalas']),ltr=True)
        kv('المستلم الفعلي',r['recipient_user_id'],ltr=True);kv('مرجع التسليم',r['reference'],ltr=not bool(re.search(r'[\u0600-\u06ff]',r['reference'])))
        divider()
    timed=[t for t in snap['tasks'] if t['reason'] or (t['picked_up_at'] and t['delivered_at'])]
    if timed:
        section('المهام والاستثناءات وأوقات التوصيل')
        for t in timed:
            kv('مرجع المهمة',t['id'],ltr=True)
            if t['reason']:kv('استثناء المهمة',t['reason'])
            if t['picked_up_at'] and t['delivered_at']:
                timestamp('الاستلام من الفرع',t['picked_up_at']);timestamp('تسليم العميل',t['delivered_at'])
            divider()
    section('التقفيلات التابعة - تشمل شفتات السائقين دون طلبات')
    if not snap['driver_reports']:text('لا توجد تقفيلات سائقين تابعة لهذا التقرير.')
    for r in snap['driver_reports']:
        kv('مرجع تقرير السائق',r['id'],ltr=True);kv('مرجع الشفت',r['shift_id'],ltr=True)
        for key in ('expected_halalas','counted_halalas','difference_halalas'):kv(labels[key]+' (ر.س)',money(r['closure'][key]),ltr=True)
        divider()
    footer();cv.save();return stream.getvalue()

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
    s=r['snapshot'];esc=lambda x:html.escape(str(x));title=esc(brand_title(s))
    rows=''.join('<tr><td>'+esc(o['id'])+'</td><td>'+esc(o['branch_id'])+'</td><td>'+esc(o['delivery_status'])+(' — منفذ ضمن التقرير' if o.get('fulfilled_in_report') else ' — سجل تحويل تاريخي')+'</td><td>'+f'{o["total_halalas"]/100:.2f}'+'</td></tr>' for o in s['orders'])
    summary=''.join('<p>'+esc(k)+': '+esc(v)+'</p>' for k,v in s['summary']['submission'].items())
    page='<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>تقفيلة '+title+'</title><style>body{font:16px sans-serif;max-width:1100px;margin:24px;background:white;color:#222}table{width:100%;border-collapse:collapse}td,th{border:1px solid #aaa;padding:8px;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere}@media print{button{display:none}}</style><h1>تقرير تشغيلي وتقفيلة '+title+'</h1><p>'+esc(s['reference'])+' — '+esc(s['operational_date'])+' — Asia/Riyadh</p><button type="button" data-print-report>طباعة</button><script src="/report.js" defer></script>'+summary+'<table><tr><th>الطلب</th><th>الفرع</th><th>الحالة</th><th>ر.س</th></tr>'+rows+'</table><h2>تفاصيل اللقطة النهائية المحفوظة</h2><pre>'+esc(__import__('json').dumps(s,ensure_ascii=False,indent=2))+'</pre></html>'
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
