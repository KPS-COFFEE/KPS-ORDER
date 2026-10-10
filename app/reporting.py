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

def make_print_html(snap):
    """Present only the saved closure snapshot; rendering never changes its ledger."""
    from datetime import datetime,timezone
    from decimal import Decimal

    esc=lambda value:html.escape(str(value),quote=True)
    labels={
        'expected_halalas':'النقد المتوقع','counted_halalas':'النقد المعدود',
        'handover_halalas':'التسليم النهائي المؤكد','retained_halalas':'العهدة المحتفظ بها',
        'difference_halalas':'الفرق المعتمد','cash_collected_halalas':'التحصيل النقدي الصافي بعد الباقي',
        'card_collected_halalas':'تحصيل الشبكة','cash_refunded_halalas':'استردادات الكاش',
        'card_refunded_halalas':'استردادات الشبكة','gross_completed_halalas':'قيمة الطلبات المكتملة',
        'net_collections_halalas':'صافي التحصيلات','delivery_fees_halalas':'رسوم الطلبات المكتملة',
    }
    names={
        'driver':'سائق','manager':'مدير توصيل','delivery':'توصيل','pickup':'استلام من الفرع','car':'استلام بالسيارة',
        'cash':'كاش','card':'شبكة','open':'مفتوح','closing':'قيد التقفيل','submitted':'مقدم للاعتماد','closed':'مقفل',
        'new':'جديد','accepted':'مقبول','preparing':'قيد التجهيز','ready':'جاهز','handed_over':'سُلّم من الفرع',
        'completed':'مكتمل','pending':'بانتظار التأكيد','assigned':'مسند','picked_up':'مستلم من الفرع',
        'problem':'مشكلة مسجلة','delivered':'تم التسليم','cancelled':'ملغى','transferred':'محول',
        'not_applicable':'لا ينطبق','unpaid':'غير مدفوع','paid':'مدفوع','partially_refunded':'مسترد جزئيا',
        'refunded':'مسترد بالكامل','confirmed':'مؤكد من المستلم','rejected':'مرفوض',
        'collection':'تحصيل نقدي','refund':'استرداد نقدي','transfer_in':'عهدة مستلمة','transfer_out':'عهدة مسلمة',
        'expense':'مصروف أو سلفة','direct_receipt':'استلام نقد مباشر','adjustment':'تسوية معتمدة',
    }
    def present(value):return value is not None and value!=''
    def text(value):return esc(value) if present(value) else 'غير مسجل'
    def ref(value):return '<bdi class="reference" dir="ltr">'+esc(value)+'</bdi>' if present(value) else 'غير مسجل'
    def money(value):
        if not present(value):return 'غير مسجل'
        return '<bdi class="money" dir="ltr">'+f'{Decimal(str(value))/100:,.2f}'+'</bdi> <span class="currency">ر.س</span>'
    def named(value):return text(names.get(value,value))
    def refs(values):return '<span class="references">'+''.join(ref(v) for v in values)+'</span>' if values else 'لا توجد'
    def stamp(value):
        if not present(value):return 'غير مسجل'
        try:
            parsed=value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace('Z','+00:00'))
            if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=timezone.utc)
            local=parsed.astimezone(ZoneInfo('Asia/Riyadh'))
        except (TypeError,ValueError):return ref(value)
        return '<span class="timestamp"><span>التاريخ '+ref(local.strftime('%Y-%m-%d'))+'</span><span>وقت الرياض '+ref(local.strftime('%H:%M:%S'))+'</span></span>'
    def fields(rows):
        return '<dl class="fields">'+''.join('<div class="field"><dt>'+esc(label)+'</dt><dd>'+value+'</dd></div>' for label,value in rows)+'</dl>'
    def record(title,rows,extra=''):
        return '<article class="record"><h3>'+esc(title)+'</h3>'+fields(rows)+extra+'</article>'
    def section(key,title,records,empty):
        return '<section id="'+key+'"><h2>'+esc(title)+'</h2>'+(''.join(records) if records else '<p class="empty">'+esc(empty)+'</p>')+'</section>'
    def closure_rows(closure):
        return [(labels[key],money(closure.get(key))) for key in ('expected_halalas','counted_halalas','handover_halalas','retained_halalas','difference_halalas')]

    shift=snap.get('shift',{});summary=snap.get('summary',{});title=brand_title(snap)
    intro=fields([
        ('مرجع التقفيلة',ref(snap.get('reference'))),('نوع التقفيلة',named(snap.get('kind'))),
        ('التاريخ التشغيلي',ref(snap.get('operational_date'))),('المنطقة الزمنية',ref(snap.get('timezone'))),
        ('حدود اليوم',text('يوم تشغيلي حسب الإعداد المحفوظ' if snap.get('operational_day_configured') else 'يوم تقويمي؛ بداية اليوم التشغيلي غير محددة')),
        ('مرجع الشفت',ref(shift.get('id'))),('صاحب الشفت',ref(shift.get('user_id'))),('حالة الشفت',named(shift.get('status'))),
        ('الفروع المشمولة',refs(shift.get('branch_ids',[]))),('البراند',ref(shift.get('brand_id'))),
        ('شفت المدير المسؤول',ref(shift.get('manager_shift_id'))),('المدير المسؤول',ref(snap.get('manager_user_id'))),
        ('بداية الشفت',stamp(shift.get('opened_at'))),('تقديم التقفيلة',stamp(shift.get('submitted_at'))),
        ('اعتماد نهاية الشفت',stamp(shift.get('closed_at'))),('المعد',ref(snap.get('prepared_by'))),
        ('المعتمد',ref(snap.get('approved_by'))),
    ])
    financial=[('عهدة افتتاح الشفت',money(shift.get('opening_halalas'))),*closure_rows(summary.get('submission') or shift.get('submission') or {})]
    financial += [(labels[key],money(summary.get(key))) for key in ('cash_collected_halalas','card_collected_halalas','cash_refunded_halalas','card_refunded_halalas','gross_completed_halalas','delivery_fees_halalas','net_collections_halalas')]
    financial += [('عدد الطلبات في السجل',ref(summary.get('unique_orders'))),('الطلبات المنفذة ضمن التقرير',ref(summary.get('completed_orders')))]
    if shift.get('exception_reason'):financial.append(('سبب الاستثناء المعتمد',text(shift['exception_reason'])))
    sections=[section('financial','ملخص التقفيلة والتحصيلات', [record('جميع المبالغ بالريال السعودي',financial)],'')]

    order_records=[]
    for order in snap.get('orders',[]):
        saved=order.get('snapshot') or {};items=[]
        for index,item in enumerate(saved.get('items',[]),1):
            item_rows=[('الصنف',text(item.get('name'))),('مرجع المنتج',ref(item.get('product_id'))),('الكمية',ref(item.get('quantity'))),('سعر الوحدة',money(item.get('unit_price_halalas'))),('مبلغ الصنف',money(item.get('line_total_halalas')))]
            if item.get('source_id'):item_rows.append(('مرجع المنتج في المصدر',ref(item['source_id'])))
            units=[]
            for unit_index,unit in enumerate(item.get('units',[]),1):
                unit_rows=[('سعر الوحدة المحفوظ',money(unit.get('price_halalas')))]
                descriptions=unit.get('option_snapshot') or {}
                if descriptions:
                    for group_code,group in descriptions.items():
                        choices=[]
                        for choice in group.get('choices',[]):
                            choice_text=text(choice.get('name') or choice.get('code'))
                            if choice.get('code'):choice_text+=' <span class="muted">('+ref(choice['code'])+')</span>'
                            if choice.get('price_delta_halalas') is not None:choice_text+=' — تعديل السعر '+money(choice['price_delta_halalas'])
                            choices.append(choice_text)
                        unit_rows.append((str(group.get('name') or group_code),' / '.join(choices) or 'لا توجد خيارات'))
                else:
                    for group_code,choices in (unit.get('options') or {}).items():
                        unit_rows.append(('الخيار '+str(group_code),refs(choices) if isinstance(choices,list) else ref(choices)))
                units.append('<div class="unit"><h5>الوحدة '+str(unit_index)+'</h5>'+fields(unit_rows)+'</div>')
            items.append('<div class="item"><h4>الصنف '+str(index)+'</h4>'+fields(item_rows)+''.join(units)+'</div>')
        order_rows=[('مرجع الطلب',ref(order.get('id'))),('الخدمة',named(order.get('service'))),('مرجع الفرع',ref(order.get('branch_id'))),('اسم الفرع المحفوظ',text(saved.get('branch_name'))),('حالة التجهيز',named(order.get('preparation_status'))),('حالة التوصيل',named(order.get('delivery_status'))),('حالة الدفع',named(order.get('payment_status'))),('الاحتساب',text('منفذ ضمن التقرير' if order.get('fulfilled_in_report') else 'سجل تاريخي؛ لا يحتسب ضمن المبيعات المكتملة')),('قيمة الأصناف',money(order.get('subtotal_halalas'))),('رسوم التوصيل',money(order.get('delivery_fee_halalas'))),('إجمالي الطلب',money(order.get('total_halalas'))),('إنشاء الطلب',stamp(order.get('created_at'))),('اكتمال الطلب',stamp(order.get('completed_at')))]
        if saved.get('branch_city'):order_rows.append(('مدينة الفرع',text(saved['branch_city'])))
        if saved.get('payment_method'):order_rows.append(('طريقة الدفع المختارة',named(saved['payment_method'])))
        order_records.append(record('طلب '+str(len(order_records)+1),order_rows,''.join(items)))
    sections.append(section('orders','الطلبات والأصناف والخيارات المحفوظة',order_records,'لا توجد طلبات ضمن هذه التقفيلة.'))

    collection_records=[]
    for r in snap.get('collections',[]):
        collection_records.append(record('تحصيل '+str(len(collection_records)+1),[
            ('مرجع التحصيل',ref(r.get('id'))),('مرجع الطلب',ref(r.get('order_id'))),('شفت التحصيل',ref(r.get('shift_id'))),('المحصل',ref(r.get('collector_id'))),
            ('وسيلة التحصيل',named(r.get('method'))),('المبلغ المحصل الصافي',money(r.get('amount_halalas'))),
            ('النقد المسلم من العميل',money(r.get('tendered_halalas'))),('الباقي للعميل',money(r.get('change_halalas'))),
            ('مرجع الدفع الفعلي',ref(r.get('reference'))),('وقت التحصيل',stamp(r.get('created_at'))),
        ]))
    sections.append(section('collections','تحصيلات الكاش والشبكة ومراجعها',collection_records,'لا توجد تحصيلات.'))

    refund_records=[]
    for r in snap.get('refunds',[]):
        returned=''.join('<div class="unit">'+fields([('مرجع المنتج',ref(product)),('الكمية المعادة',ref(quantity))])+'</div>' for product,quantity in (r.get('line_quantities') or {}).items())
        refund_records.append(record('استرداد '+str(len(refund_records)+1),[
            ('مرجع الاسترداد',ref(r.get('id'))),('مرجع التحصيل الأصلي',ref(r.get('collection_id'))),('مرجع الطلب',ref(r.get('order_id'))),('شفت الاسترداد',ref(r.get('shift_id'))),
            ('وسيلة الاسترداد',named(r.get('method'))),('مبلغ الاسترداد',money(r.get('amount_halalas'))),('سبب الاسترداد',text(r.get('reason'))),('المخول بالاسترداد',ref(r.get('authorized_by'))),('وقت الاسترداد',stamp(r.get('created_at'))),
        ],'<h4>الكميات المعادة</h4>'+ (returned or '<p class="empty">لم تسجل كميات أصناف معادة لهذا الاسترداد.</p>')))
    sections.append(section('refunds','الاستردادات المعتمدة والكميات المعادة',refund_records,'لا توجد استردادات.'))

    event_records=[record('حركة عهدة '+str(index),[
        ('مرجع الحركة',ref(r.get('id'))),('الشفت',ref(r.get('shift_id'))),('نوع الحركة',named(r.get('kind'))),('مبلغ الحركة بإشارته',money(r.get('amount_halalas'))),('المرجع المرتبط',ref(r.get('reference_id'))),('منفذ الحركة',ref(r.get('actor_id'))),('سبب الحركة',text(r.get('reason'))),('وقت الحركة',stamp(r.get('created_at'))),
    ]) for index,r in enumerate(snap.get('custody_events',[]),1)]
    sections.append(section('custody-events','حركات العهدة والمصروفات والسلف',event_records,'لا توجد حركات عهدة.'))

    transfers=[record('تسليم عهدة '+str(index),[
        ('مرجع التسليم',ref(r.get('id'))),('من شفت',ref(r.get('from_shift_id'))),('إلى شفت',ref(r.get('to_shift_id'))),('المستلم المحدد',ref(r.get('recipient_user_id'))),('مبلغ التسليم',money(r.get('amount_halalas'))),('نوع التسليم',text('تسليم التقفيلة النهائي' if r.get('closure') else 'تسليم جزئي أو أثناء الشفت')),('حالة التأكيد',named(r.get('status'))),('مرجع التسليم الفعلي',ref(r.get('reference'))),('منشئ التسليم',ref(r.get('created_by'))),('المستلم الذي أكد',ref(r.get('confirmed_by'))),('إنشاء التسليم',stamp(r.get('created_at'))),('تأكيد الاستلام',stamp(r.get('confirmed_at'))),
    ]) for index,r in enumerate(snap.get('transfers',[]),1)]
    sections.append(section('custody-transfers','تسليمات العهدة وتأكيد المستلم',transfers,'لا توجد تسليمات عهدة.'))

    tasks=[record('مهمة '+str(index),[
        ('مرجع المهمة',ref(r.get('id'))),('مرجع الطلب',ref(r.get('order_id'))),('السائق',ref(r.get('driver_id'))),('شفت السائق',ref(r.get('shift_id'))),('حالة المهمة',named(r.get('status'))),('المسند',ref(r.get('assigned_by'))),('وقت الإسناد',stamp(r.get('created_at'))),('قبول المهمة',stamp(r.get('accepted_at'))),('الاستلام من الفرع',stamp(r.get('picked_up_at'))),('تسليم العميل',stamp(r.get('delivered_at'))),('دليل التسليم المسجل',text(r.get('proof'))),('المشكلة أو سبب الاستثناء',text(r.get('reason'))),('شفت التحويل المطلوب',ref(r.get('transfer_shift_id'))),('طالب تحويل المهمة',ref(r.get('transfer_requested_by'))),
    ]) for index,r in enumerate(snap.get('tasks',[]),1)]
    sections.append(section('tasks','المهام والتحويلات والاستثناءات وأوقات التوصيل',tasks,'لا توجد مهام ضمن هذه التقفيلة.'))

    children=[record('تقفيلة سائق '+str(index),[
        ('مرجع تقرير السائق',ref(r.get('id'))),('مرجع الشفت',ref(r.get('shift_id'))),('إصدار التقرير',ref(r.get('version'))),*closure_rows(r.get('closure') or {}),
    ]) for index,r in enumerate(snap.get('driver_reports',[]),1)]
    sections.append(section('driver-closures','تقفيلات السائقين التابعة، بما فيها شفتات دون طلبات',children,'لا توجد تقفيلات سائقين تابعة لهذا التقرير.'))

    corrections=[record('تصحيح مرتبط '+str(index),[
        ('مرجع التصحيح',ref(r.get('id'))),('التقرير الأصلي',ref(r.get('report_id'))),('إصدار التصحيح',ref(r.get('correction_version'))),('شفت التصحيح',ref(r.get('shift_id'))),('مرجع حركة التسوية',ref(r.get('event_reference_id'))),('مبلغ التصحيح بإشارته',money(r.get('amount_halalas'))),('سبب التصحيح',text(r.get('reason'))),('المخول بالتصحيح',ref(r.get('authorized_by'))),('وقت التصحيح',stamp(r.get('created_at'))),
    ]) for index,r in enumerate(snap.get('linked_corrections',[]),1)]
    sections.append(section('corrections','التصحيحات المرتبطة المحفوظة وقت الاعتماد',corrections,'لا توجد تصحيحات مرتبطة في هذه اللقطة.'))

    handovers=[record('تسليم مناوبة '+str(index),[
        ('مرجع تسليم المسؤولية',ref(r.get('id'))),('شفت المدير المسلم',ref(r.get('from_shift_id'))),('شفت المدير المستلم',ref(r.get('to_shift_id'))),('الفروع المنقولة',refs(r.get('branch_ids',[]))),('شفتات السائقين المنقولة',refs(r.get('driver_shift_ids',[]))),('حالة القبول',named(r.get('status'))),('سبب تسليم المناوبة',text(r.get('reason'))),('طالب التسليم',ref(r.get('requested_by'))),('المدير الذي قبل',ref(r.get('accepted_by'))),('طلب التسليم',stamp(r.get('created_at'))),('قبول المسؤولية',stamp(r.get('accepted_at'))),
    ]) for index,r in enumerate(snap.get('manager_handovers',[]),1)]
    sections.append(section('manager-handovers','تسليم مسؤولية مناوبة مديري التوصيل',handovers,'لا توجد تسليمات مسؤولية في هذه اللقطة.'))

    carryovers=[record('عهدة مرحلة '+str(index),[
        ('مرجع الترحيل',ref(r.get('id'))),('الشفت السابق',ref(r.get('from_shift_id'))),('صاحب العهدة',ref(r.get('user_id'))),('مبلغ العهدة المحتفظ بها',money(r.get('amount_halalas'))),('المعتمد',ref(r.get('approved_by'))),('سبب الإبقاء',text(r.get('reason'))),('الشفت الذي رحلت إليه',ref(r.get('to_shift_id'))),('اعتماد الإبقاء',stamp(r.get('created_at'))),('وقت الترحيل',stamp(r.get('carried_at'))),
    ]) for index,r in enumerate(snap.get('retained_carryovers',[]),1)]
    sections.append(section('carryovers','العهد المحتفظ بها وترحيلها بين الشفتات',carryovers,'لا توجد عهد مرحلة محفوظة في هذه اللقطة.'))

    css='''
    *{box-sizing:border-box}html{background:#eef1ef;color:#172620}body{margin:0;font:15px/1.55 Arial,"DejaVu Sans",sans-serif}
    main{max-width:1080px;margin:24px auto;padding:32px;background:#fff}header{border-bottom:3px solid #234d3b;padding-bottom:18px}
    .brand{font-size:18px;font-weight:700;color:#234d3b;margin:0}h1{font-size:27px;margin:4px 0 12px}h2{font-size:20px;border-bottom:1px solid #b9c9c0;padding:8px 0;margin:25px 0 12px;color:#234d3b}
    h3{font-size:16px;margin:0 0 10px}h4,h5{font-size:14px;margin:8px 0}p{margin:8px 0}.note,.empty,.muted{color:#57655e}.note{font-size:13px}
    button{font:inherit;background:#234d3b;color:#fff;border:0;border-radius:5px;padding:9px 22px;cursor:pointer;margin:6px 0 14px}
    .record{border:1px solid #d0dad4;border-radius:6px;padding:15px;margin:10px 0;background:#fff;min-width:0}
    .fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 24px;margin:0}
    .field{display:grid;grid-template-columns:135px minmax(0,1fr);gap:9px;min-width:0;align-items:start}
    dt{font-size:13px;color:#57655e}dd{margin:0;min-width:0;overflow-wrap:anywhere;white-space:pre-wrap}
    .reference{font-family:Arial,"DejaVu Sans",sans-serif;font-size:13px;unicode-bidi:isolate;overflow-wrap:anywhere;max-width:100%}
    .references{display:flex;flex-direction:column;align-items:flex-start;gap:3px}.money{font-variant-numeric:tabular-nums;white-space:nowrap;font-weight:600}.currency{white-space:nowrap;font-size:12px}
    .timestamp{display:flex;flex-direction:column;gap:2px;font-size:12px}.item{border-top:1px solid #dce4df;padding:8px 0 0;margin-top:12px}
    .unit{padding:6px 10px;border-right:2px solid #c0d0c6;margin-top:8px}footer{border-top:1px solid #b9c9c0;margin-top:24px;padding-top:10px;font-size:12px;color:#57655e}
    @media(max-width:720px){main{margin:0;padding:18px 14px}.fields{grid-template-columns:1fr;gap:10px}.field{grid-template-columns:1fr;gap:2px}.record{padding:12px}h1{font-size:23px}.reference{font-size:13px}}
    @page{size:A4;margin:14mm}
    @media print{html{background:#fff}body{font-size:10px;line-height:1.45}main{max-width:none;margin:0;padding:0}button{display:none}.brand{font-size:14px}h1{font-size:20px}h2{font-size:15px;margin-top:16px;break-after:avoid}h3{font-size:12px;break-after:avoid}h4,h5{font-size:11px;break-after:avoid}.record{padding:10px;border-radius:0;break-inside:avoid}.fields{grid-template-columns:repeat(2,minmax(0,1fr));gap:5px 15px}.field{grid-template-columns:105px minmax(0,1fr);gap:6px;break-inside:avoid}dt,.reference{font-size:10px}.timestamp,.currency,.note,footer{font-size:9px}.item,.unit{break-inside:avoid}header{break-inside:avoid}footer{break-inside:avoid}}
    '''
    return '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>تقفيلة '+esc(title)+'</title><style>'+css+'</style><script src="/report.js" defer></script></head><body><main><header><p class="brand">'+esc(title)+'</p><h1>تقرير تشغيلي وتقفيلة</h1><button type="button" data-print-report>طباعة التقرير</button>'+intro+'<p class="note">القيم والنتائج أدناه من اللقطة النهائية المحفوظة عند اعتماد التقفيلة. المراجع معروضة كاملة، والأوقات بتوقيت الرياض. أحداث العهدة تسجل حركة النقد؛ لا تضاف مرة أخرى إلى تحصيلات الطلبات.</p></header>'+''.join(sections)+'<footer>نهاية التقرير المحفوظ · مرجع التقفيلة '+ref(snap.get('reference'))+' · جميع المبالغ بالريال السعودي</footer></main></body></html>'

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
    page=make_print_html(r['snapshot'])
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
