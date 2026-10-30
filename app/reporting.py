"""Immutable closure snapshots with durable CSV/PDF files and visible retry state."""
import csv
import html
import io
import os
from datetime import timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,Response,Request
from . import auth,db
from .complimentary import operational_view,contains_complimentary
from .operations import uid,safe,fail,roles,mutate,day_start

router=APIRouter(prefix='/api/reports',tags=['reports'])

def can_read(user,row):
    if user.get('_morning_manager_context'):
        from .morning_manager_context import shift_allowed
        if not shift_allowed(user,(row.get('snapshot') or {}).get('shift')):return False
    if user['role']=='driver':return False
    if user['role']=='owner':return user['brand_id']==row['brand_id'] and (not user.get('home_brand_id') or set(row['branch_ids'])<=set(user.get('branch_ids',[])))
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
    orders=c.execute("SELECT DISTINCT o.id,o.order_no,o.performance_snapshot,jsonb_build_object('neighborhood',o.customer->>'neighborhood') customer,o.branch_id,o.service,o.preparation_status,o.delivery_status,o.payment_status,o.subtotal_halalas,o.delivery_fee_halalas,o.total_halalas,o.snapshot,o.test_void,o.created_at,o.completed_at FROM orders o JOIN effective_driver_tasks t ON t.order_id=o.id WHERE t.shift_id=ANY(%s) ORDER BY o.id",(shift_ids,)).fetchall()
    tasks=c.execute('SELECT * FROM effective_driver_tasks WHERE shift_id=ANY(%s) ORDER BY created_at',(shift_ids,)).fetchall()
    collections=c.execute('SELECT * FROM effective_collections WHERE shift_id=ANY(%s) ORDER BY created_at',(shift_ids,)).fetchall()
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
        collections=merge('collections',c.execute('SELECT * FROM effective_collections WHERE shift_id=%s',(sid,)).fetchall())
        refunds=merge('refunds',c.execute('SELECT * FROM refunds WHERE shift_id=%s',(sid,)).fetchall())
        events=merge('custody_events',c.execute('SELECT * FROM custody_events WHERE shift_id=%s',(sid,)).fetchall())
        transfers=merge('transfers',c.execute('SELECT * FROM custody_transfers WHERE from_shift_id=%s OR to_shift_id=%s',(sid,sid)).fetchall())
    if s['kind']=='driver':
        from .performance import enrich_orders
        enrich_orders(c,orders)
    for order in orders:
        order['snapshot']=safe(order['snapshot']);order['snapshot'].pop('notes',None);order['snapshot'].pop('note',None)
        for item in order['snapshot'].get('items',[]):item.pop('note',None)
    attestations=c.execute('SELECT id,order_id,task_id,shift_id,actor_id,reason,operational_date,recorded_at FROM legacy_delivery_attestations WHERE shift_id=ANY(%s) ORDER BY recorded_at,id',(shift_ids,)).fetchall()
    imports=c.execute('SELECT id,order_id,invoice_reference,invoice_issued_at,recorded_at,original_shift_id,manager_shift_id,total_halalas,tax_halalas,cash_halalas,card_halalas,reason FROM legacy_invoice_imports WHERE ledger_shift_id=ANY(%s) ORDER BY recorded_at,id',(shift_ids,)).fetchall()
    attested={a['order_id'] for a in attestations} | {r['order_id'] for r in imports}
    fulfilled={t['order_id'] for t in tasks if t['status']=='delivered' and (t.get('delivered_at') or t['order_id'] in attested)}
    for o in orders:o['fulfilled_in_report']=not o.get('test_void') and o.get('fulfilled_in_report',o['id'] in fulfilled)
    cash=sum(r['amount_halalas'] for r in collections if r['method']=='cash')-sum(r['amount_halalas'] for r in refunds if r.get('record_kind')=='test_void')
    card=sum(r['amount_halalas'] for r in collections if r['method']=='card')
    completed=[o for o in orders if o['fulfilled_in_report']]
    brand=c.execute('SELECT name,settings FROM brands WHERE id=%s',(s['brand_id'],)).fetchone()
    cfg=brand['settings']
    settings=c.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(s['brand_id'],)).fetchone()
    cfg={**cfg,**(settings['settings'] if settings else {})};minutes,configured=day_start(cfg)
    if s.get('delivery_operational_date'):
        configured=True
    snap=safe({'schema_version':1,'brand_name':brand['name'],'reference':sid,'kind':s['kind'],'timezone':'Asia/Riyadh','operational_day_configured':configured,'operational_date':(s['opened_at'].astimezone(ZoneInfo('Asia/Riyadh'))-timedelta(minutes=minutes)).date().isoformat(),'shift':s,'manager_user_id':manager_user_id,'prepared_by':s['user_id'],'approved_by':approved_by,'orders':orders,'tasks':tasks,'collections':collections,'refunds':refunds,'custody_events':events,'transfers':transfers,'linked_corrections':c.execute('SELECT * FROM report_corrections WHERE shift_id=ANY(%s)',(shift_ids,)).fetchall(),'manager_handovers':c.execute('SELECT * FROM manager_handovers WHERE from_shift_id=ANY(%s) OR to_shift_id=ANY(%s)',(shift_ids,shift_ids)).fetchall(),'retained_carryovers':c.execute('SELECT * FROM retained_carryovers WHERE from_shift_id=ANY(%s) OR to_shift_id=ANY(%s)',(shift_ids,shift_ids)).fetchall(),'driver_reports':[{'id':r['id'],'shift_id':r['shift_id'],'version':r['version'],'closure':r['snapshot']['shift']['submission']} for r in children], 'summary':{'test_void_halalas':sum(r['amount_halalas'] for r in refunds if r.get('record_kind')=='test_void'),'unique_orders':len(orders),'completed_orders':len(completed),'cash_collected_halalas':cash,'card_collected_halalas':card,'cash_refunded_halalas':sum(r['amount_halalas'] for r in refunds if r['method']=='cash' and r.get('record_kind')!='test_void'),'card_refunded_halalas':sum(r['amount_halalas'] for r in refunds if r['method']=='card' and r.get('record_kind')!='test_void'),'gross_completed_halalas':sum(o['total_halalas'] for o in completed),'delivery_fees_halalas':sum(o['delivery_fee_halalas'] for o in completed),'net_collections_halalas':cash+card-sum(r['amount_halalas'] for r in refunds if r.get('record_kind')!='test_void'),'submission':s['submission']}})
    if s.get('delivery_operational_date'):
        snap['operational_date']=(s.get('reporting_operational_date') or s['delivery_operational_date']).isoformat()
        snap['delivery_window']=s.get('delivery_window_snapshot')
        snap['delivery_operational_day_start']='04:00'
    from .collection_tender_corrections import snapshot_evidence
    snap['collection_tender_corrections']=snapshot_evidence(c,[p['id'] for p in collections])
    snap['driver_attribution_corrections']=safe(c.execute('SELECT * FROM order_driver_attribution_corrections WHERE source_shift_id=ANY(%s) OR target_shift_id=ANY(%s) ORDER BY created_at,id',(shift_ids,shift_ids)).fetchall())
    snap['legacy_delivery_attestations']=safe(attestations)
    snap['legacy_invoice_imports']=safe(imports)
    # Display metadata is captured only for this new snapshot, never backfilled.
    snap['schema_version']=2;snap['presentation_template_version']=TEMPLATE_VERSION
    snap['display_users']={k:v for child in children for k,v in child['snapshot'].get('display_users',{}).items()}
    user_ids={s['user_id'],approved_by,manager_user_id}
    for record in [*tasks,*collections,*refunds,*events,*transfers]:
        for key in ('driver_id','collector_id','authorized_by','actor_id','recipient_user_id','confirmed_by','created_by','assigned_by'):
            if record.get(key):user_ids.add(record[key])
    for row in c.execute('SELECT id,display_name FROM users WHERE id=ANY(%s)',(list(user_ids),)).fetchall():snap['display_users'].setdefault(row['id'],row['display_name'] or row['id'])
    snap['display_branches']={b['id']:{'name':b['name'],'branch_no':b['branch_no'],'city':b['city']} for b in c.execute('SELECT id,name,branch_no,city FROM branches WHERE id=ANY(%s)',(list(set(s['branch_ids'])|{o['branch_id'] for o in orders}),)).fetchall()}
    snap['closure_label']=display_shift_label(s)
    if s.get('supplement_of_shift_id'):
        original=c.execute('SELECT * FROM shifts WHERE id=%s',(s['supplement_of_shift_id'],)).fetchone()
        snap['closure_label']='ملحق / Supplement '+display_shift_label(original)+' · '+display_shift_label(s)
        snap['supplement_of_shift_id']=original['id']
        snap['supplement_note']='تسجيل طلبات سابقة؛ استلام المدير للنقد غير مؤكد / Prior deliveries; manager cash receipt not confirmed'
        snap['shift']['submission']['notes']=(snap['shift']['submission'].get('notes') or '')+' — '+snap['supplement_note']
    snap['driver_reports']=[{'id':r['id'],'shift_id':r['shift_id'],'version':r['version'],'shift_no':r['snapshot']['shift'].get('shift_no'),'closure_label':r['snapshot'].get('closure_label') or display_shift_label(r['snapshot']['shift']),'user_id':r['snapshot']['shift']['user_id'],'summary':r['snapshot']['summary'],'closure':r['snapshot']['shift']['submission']} for r in children]
    if s['kind']=='driver':
        external=s.get('external_enbishan') or {}
        snap['external_enbishan']=[{**external,'driver_id':s['user_id'],'shift_id':sid}] if external else []
    else:
        snap['external_enbishan']=[row for child in children for row in child['snapshot'].get('external_enbishan',[])]
    snap['external_enbishan_totals']={key:sum(row.get(key,0) for row in snap['external_enbishan']) for key in ('order_count','cash_halalas','card_halalas','total_halalas')}
    order_ids=[o['id'] for o in orders]
    snap['conversation_exceptions']=safe(c.execute('SELECT t.*,v.order_id FROM conversation_transfers t JOIN conversations v ON v.id=t.conversation_id WHERE v.order_id=ANY(%s) ORDER BY t.created_at',(order_ids,)).fetchall())
    if s['kind']=='manager':
        snap['photo_capture_evidence']=safe(c.execute('''SELECT p.order_id,p.stage,p.created_at,p.used_at,
            p.capture_location,u.display_name driver_name FROM delivery_photos p
            JOIN orders o ON o.id=p.order_id JOIN users u ON u.id=p.uploader_id
            WHERE p.order_id=ANY(%s) AND o.brand_id=%s AND o.branch_id=ANY(%s)
            AND p.used_at IS NOT NULL ORDER BY p.used_at,p.id''',
            (order_ids,s['brand_id'],s['branch_ids'])).fetchall())
    if s['kind']=='manager':
        from .order_support import report_tickets
        snap['support_tickets']=safe(report_tickets(c,s['brand_id'],s['branch_ids'],s['opened_at'],s['closed_at']))
    rid=uid();c.execute('INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES(%s,%s,%s,%s,%s,%s,%s)',(rid,sid,s['kind'],s['brand_id'],db.json(s['branch_ids']),s['user_id'],db.json(snap)))
    queue_v5_renderings(c,rid,snap)
    if s['kind']=='manager' or s.get('submission',{}).get('closure_exception'):
        from .report_notifications import enqueue_manager_closure
        enqueue_manager_closure(c,rid)
    return rid

def cell(v):
    text=str(v if v is not None else '')
    if text.lstrip().startswith(('=','+','-','@')):text="'"+text
    return text

def make_csv(snap):
    out=io.StringIO();writer=csv.writer(out);writer.writerow(['نوع السجل','المرجع','الحالة/الطريقة','المبلغ بالهللات','الفرع/الشفت','الوقت'])
    for o in snap['orders']:writer.writerow([cell(v) for v in ['order_completed' if o.get('fulfilled_in_report') else 'order_history_only',o['id'],o['delivery_status'],o['total_halalas'],o['branch_id'],o['created_at']]])
    for name in ('collections','refunds'):
        for r in snap[name]:writer.writerow([cell(v) for v in ['test_void_no_cash_disbursed' if r.get('record_kind')=='test_void' else name,r['id'],r['method'],r['amount_halalas'],r['shift_id'],r['created_at']]])
    for r in snap['custody_events']:writer.writerow([cell(v) for v in ['custody',r['id'],r['kind'],r['amount_halalas'],r['shift_id'],r['created_at']]])
    for key,v in snap['summary']['submission'].items():writer.writerow(['closure',cell(key),'',cell(v),snap['shift']['id'],''])
    if snap.get('external_enbishan'):
        writer.writerow([])
        writer.writerow(['طلبات عنبيشن — ساباد','السائق','عدد الطلبات','كاش بالهللات','شبكة بالهللات','الإجمالي بالهللات'])
    for row in snap.get('external_enbishan',[]):
        writer.writerow(['طلبات عنبيشن — ساباد',cell(row.get('driver_id')),row.get('order_count',0),row.get('cash_halalas',0),row.get('card_halalas',0),row.get('total_halalas',0)])
    return ('\ufeff'+out.getvalue()).encode('utf-8')

def corrected_snapshot(c, snap):
    from .operational_day_policy import report_snapshot
    return report_snapshot(c, snap)

def brand_title(snap):
    name=snap.get('brand_name')
    if isinstance(name,str) and name.strip():return name.strip()
    brand_id=str(snap.get('shift',{}).get('brand_id') or '')
    return 'KPS COFFEE' if brand_id=='kps' else brand_id

def make_print_html(snap):
    """Present only the saved closure snapshot; rendering never changes its ledger."""
    if snap.get("schema_version",1)>=2:return make_v5_print_html(snap)
    from datetime import datetime,timezone
    from decimal import Decimal

    esc=lambda value:html.escape(str(value),quote=True)
    labels={
        'terminal_total_halalas':'موازنة جهاز الشبكة','expected_card_halalas':'الشبكة حسب النظام','card_difference_halalas':'فرق الشبكة','expected_halalas':'النقد المتوقع','counted_halalas':'النقد المعدود',
        'handover_halalas':'التسليم النهائي المؤكد','retained_halalas':'العهدة المحتفظ بها',
        'difference_halalas':'الفرق المعتمد','cash_collected_halalas':'التحصيل النقدي الصافي بعد الباقي',
        'card_collected_halalas':'تحصيل الشبكة','cash_refunded_halalas':'استردادات الكاش',
        'card_refunded_halalas':'استردادات الشبكة','gross_completed_halalas':'قيمة الطلبات المكتملة',
        'net_collections_halalas':'صافي التحصيلات','delivery_fees_halalas':'رسوم الطلبات المكتملة',
    }
    names={
        'driver':'سائق','manager':'مدير توصيل','delivery':'توصيل','pickup':'استلام من الفرع','car':'استلام بالسيارة',
        'cash':'كاش','card':'شبكة','split':'كاش وشبكة','open':'مفتوح','closing':'قيد التقفيل','submitted':'مقدم للاعتماد','closed':'مقفل',
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
        refund_records.append(record(('إلغاء تجريبي — لا صرف نقدي ' if r.get('record_kind')=='test_void' else 'استرداد ')+str(len(refund_records)+1),[
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
    *{box-sizing:border-box}html{background:#eef1ef;color:#172620}body{margin:0;background:#eef1ef;font:15px/1.55 Arial,"DejaVu Sans",sans-serif}
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
    @media print{html{background:#fff}body{background:#fff;font-size:10px;line-height:1.45}main{max-width:none;margin:0;padding:0}button{display:none}.brand{font-size:14px}h1{font-size:20px}h2{font-size:15px;margin-top:16px;break-after:avoid}h3{font-size:12px;break-after:avoid}h4,h5{font-size:11px;break-after:avoid}.record{padding:10px;border-radius:0;break-inside:avoid}.fields{grid-template-columns:repeat(2,minmax(0,1fr));gap:5px 15px}.field{grid-template-columns:105px minmax(0,1fr);gap:6px;break-inside:avoid}dt,.reference{font-size:10px}.timestamp,.currency,.note,footer{font-size:9px}.item,.unit{break-inside:avoid}header{break-inside:avoid}footer{break-inside:avoid}}
    '''
    return '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>تقفيلة '+esc(title)+'</title><style>'+css+'</style><script src="/report.js" defer></script></head><body><main><header><p class="brand">'+esc(title)+'</p><h1>تقرير تشغيلي وتقفيلة</h1><button type="button" data-print-report>طباعة التقرير</button>'+intro+'<p class="note">القيم والنتائج أدناه من اللقطة النهائية المحفوظة عند اعتماد التقفيلة. المراجع معروضة كاملة، والأوقات بتوقيت الرياض. أحداث العهدة تسجل حركة النقد؛ لا تضاف مرة أخرى إلى تحصيلات الطلبات.</p></header>'+''.join(sections)+'<footer>نهاية التقرير المحفوظ · مرجع التقفيلة '+ref(snap.get('reference'))+' · جميع المبالغ بالريال السعودي</footer></main></body></html>'

def make_pdf(snap):
    if snap.get('external_enbishan'):return make_v5_pdf(snap)

    if snap.get("schema_version",1)>=2:return make_v5_pdf(snap)
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
    labels={'terminal_total_halalas':'موازنة جهاز الشبكة','expected_card_halalas':'الشبكة حسب النظام','card_difference_halalas':'فرق الشبكة','expected_halalas':'النقد المتوقع','counted_halalas':'النقد المعدود','handover_halalas':'التسليم النهائي المؤكد','retained_halalas':'العهدة المحتفظ بها','difference_halalas':'الفرق المعتمد','cash_collected_halalas':'التحصيل النقدي الصافي بعد الباقي','card_collected_halalas':'تحصيل الشبكة','cash_refunded_halalas':'استردادات الكاش','card_refunded_halalas':'استردادات الشبكة','net_collections_halalas':'صافي التحصيلات','delivery_fees_halalas':'رسوم الطلبات المكتملة'}
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
    if not r:return
    if r['pdf_status']=='ready':
        generate_v5_files(rid)
        return
    csv_data=make_csv(r['snapshot'])
    try:
        pdf=make_pdf(r['snapshot'])
        with db.tx() as c:c.execute("UPDATE closure_reports SET pdf_data=%s,csv_data=%s,pdf_status='ready',pdf_error=NULL WHERE id=%s AND pdf_status<>'ready'",(pdf,csv_data,rid))
    except Exception as exc:
        # Store a bounded type/message rather than local paths, secrets or a traceback.
        with db.tx() as c:c.execute("UPDATE closure_reports SET csv_data=%s,pdf_status='failed',pdf_error=%s WHERE id=%s AND pdf_status<>'ready'",(csv_data,type(exc).__name__+': report generation failed; retry is available',rid))
    generate_v5_files(rid)

@router.get('')
def report_list(offset:int=0,user=Depends(auth.current_user)):
    if user['role']=='driver':return {'reports':[],'offset':offset,'limit':50}
    roles(user,'owner','driver','delivery_manager')
    if offset<0 or offset>100000:fail(422,'صفحة غير صالحة')
    with db.tx() as c:
        clause='brand_id=%s' if user['role']=='owner' else "brand_id=%s AND (user_id=%s OR snapshot->>'manager_user_id'=%s)"
        p=[user['brand_id']] if user['role']=='owner' else [user['brand_id'],user['id'],user['id'] if user['role']=='delivery_manager' else '']
        if user['role']=='owner' and user.get('home_brand_id'):
            clause+=' AND branch_ids <@ %s::jsonb';p.append(db.json(user.get('branch_ids',[])))
        if user.get('_morning_manager_context'):
            clause+=" AND snapshot->'shift'->'delivery_window_snapshot'->>'period'='morning' AND (shift_id=%s OR snapshot->'shift'->>'manager_shift_id'=%s)"
            p.extend([user['_morning_manager_context']['shift_id']]*2)
        rows=c.execute("SELECT id,shift_id,kind,brand_id,branch_ids,user_id,version,pdf_status,pdf_error,created_at,snapshot->'display_users'->>(snapshot->'shift'->>'user_id') driver_name,coalesce((SELECT reporting_operational_date::text FROM shifts WHERE shifts.id=closure_reports.shift_id),snapshot->>'operational_date') operational_date,(SELECT shift_no FROM shifts WHERE shifts.id=closure_reports.shift_id) shift_no FROM closure_reports WHERE "+clause+' ORDER BY created_at DESC LIMIT 50 OFFSET %s',[*p,offset]).fetchall()
        if user['role']!='owner':rows=[r for r in rows if set(r['branch_ids'])<=set(user.get('branch_ids',[]))]
        return safe({'reports':rows,'offset':offset,'limit':50})

@router.get('/{rid}')
def report_detail(rid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        r=report(c,user,rid);r['snapshot']=corrected_snapshot(c,r['snapshot']);r.pop('pdf_data');r.pop('csv_data');r['renderings']=c.execute('SELECT template_version,view_kind,status,error,attempts,source_sha256,generated_at FROM report_renderings WHERE report_id=%s ORDER BY template_version,view_kind',(rid,)).fetchall();corrections=c.execute('SELECT x.*,n.id resulting_report_id FROM report_corrections x LEFT JOIN closure_reports n ON n.shift_id=x.shift_id WHERE x.report_id=%s ORDER BY correction_version',(rid,)).fetchall();return safe(operational_view({'report':r,'corrections':corrections}))

@router.get('/{rid}/print')
def print_report(rid:str,user=Depends(auth.current_user)):
    with db.tx() as c:r=report(c,user,rid)
    return v5_download(rid,'print',user)

@router.get('/{rid}/pdf')
@router.get('/{rid}/print.pdf')
def download_pdf(rid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        r=report(c,user,rid)
        projected=corrected_snapshot(c,r['snapshot'])
        if projected.get('operational_date_correction'):
            return Response(make_v5_pdf(projected),media_type='application/pdf',headers={'Cache-Control':'private, no-store'})
        if contains_complimentary(r['snapshot']) and user['role']!='owner':
            return Response(make_v5_pdf(r['snapshot']),media_type='application/pdf',headers={'Cache-Control':'private, no-store'})
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


# V5 keeps every approved V4 snapshot/file intact. New presentations are versioned
# separately and derived solely from the source snapshot (never live money).
TEMPLATE_VERSION = 5

V5_NAMES = {
    'driver':'سائق','manager':'مدير التوصيل','closed':'معتمد نهائيا','submitted':'بانتظار الاعتماد',
    'delivery':'توصيل','pickup':'استلام من الفرع','car':'استلام بالسيارة','cash':'كاش','card':'شبكة','split':'كاش وشبكة',
    'delivered':'مكتمل','cancelled':'ملغى','transferred':'محول','problem':'مشكلة',
    'confirmed':'مؤكد','pending':'بانتظار التأكيد','refunded':'مرتجع','partially_refunded':'مرتجع جزئي',
    'collection':'تحصيل','refund':'رد نقدي','transfer_in':'عهدة مستلمة','transfer_out':'عهدة مسلمة',
    'expense':'مصروف/سلفة','direct_receipt':'استلام مباشر','adjustment':'تسوية معتمدة',
}

def v5_money(value):
    from decimal import Decimal
    return 'غير مسجل' if value is None else f'{Decimal(str(value))/100:,.2f}'

def v5_stamp(value):
    from datetime import datetime,timezone
    if not value:return 'غير مسجل'
    try:
        stamp=value if isinstance(value,datetime) else datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(ZoneInfo('Asia/Riyadh')).strftime('%Y-%m-%d %H:%M')
    except (TypeError,ValueError):return str(value)

def display_shift_label(shift):
    """Human reference only; the raw database IDs and financial snapshot stay intact."""
    prefix='M' if shift.get('kind')=='manager' else 'D' if shift.get('kind')=='driver' else 'S'
    if shift.get('shift_no') is not None:
        return f"{prefix}-{int(shift['shift_no']):06d}"
    value=shift.get('opened_at') or shift.get('created_at')
    date=str(value or '')[:10].replace('-','')
    tail=str(shift.get('id') or '').replace('-','')[-8:].upper() or '—'
    return prefix+'-'+(date+'-' if date else '')+tail


def display_order_label(order):
    return str((order.get('snapshot') or {}).get('order_reference') or order.get('order_reference') or order.get('order_no') or ('طلب '+str(order.get('id') or order.get('order_id') or '').replace('-','')[-8:].upper()))


def v5_tables(snap,view='compact'):
    """One presentation data model shared by HTML/PDF/CSV. No live DB reads."""
    shift=snap.get('shift',{});summary=snap.get('summary',{});closure=summary.get('submission') or shift.get('submission') or {}
    users=snap.get('display_users',{});branches=snap.get('display_branches',{})
    name=lambda uid:users.get(uid) or str(uid or 'غير مسجل')
    def branch(bid):
        b=branches.get(bid,{})
        return (str(b['branch_no'])+' · ' if b.get('branch_no') is not None else '')+(b.get('name') or str(bid or 'غير مسجل'))
    order_names={o.get('id'):display_order_label(o) for o in snap.get('orders',[])}
    order_name=lambda identifier:order_names.get(identifier) or ('طلب '+str(identifier or '').replace('-','')[-8:].upper())
    shift_names={shift.get('id'):snap.get('closure_label') or display_shift_label(shift)}
    shift_names.update({r.get('shift_id'):r.get('closure_label') or display_shift_label({'id':r.get('shift_id'),'shift_no':r.get('shift_no'),'kind':'driver'}) for r in snap.get('driver_reports',[])})
    shift_name=lambda identifier:shift_names.get(identifier) or display_shift_label({'id':identifier})
    label=lambda value:('معتمدة تشغيليًا — المطابقة المالية لدى المحاسبة' if value=='closed' and (shift.get('submission') or {}).get('operational_approved') else V5_NAMES.get(value,str(value or 'غير مسجل')))
    tables=[]
    def table(key,title,columns,rows,total=None):
        if not rows and not total:return
        tables.append({'key':key,'title':title,'columns':columns,'rows':rows,'total':total})
    table('shift','الشفت والاعتماد',['البيان','القيمة'],[
        ['تقفيلة السائق' if shift.get('kind')=='driver' else 'صاحب الشفت',name(shift.get('user_id'))],['يوم التشغيل',shift.get('delivery_operational_date') or snap.get('operational_date','غير مسجل')],
        *([['تصحيح يوم التشغيل — التاريخ الأصلي',snap['operational_date_correction'].get('original_day')],['سبب التصحيح','جمع الشفت الصباحي ثم المسائي؛ المبالغ الأصلية محفوظة']] if snap.get('operational_date_correction') else []),
        ['بداية الدوام - الرياض',v5_stamp(shift.get('opened_at'))],
        ['نهاية الدوام - الرياض',v5_stamp((shift.get('vehicle_log') or {}).get('end',{}).get('recorded_at') or shift.get('submitted_at')) if shift.get('kind')=='driver' else v5_stamp(shift.get('closed_at'))],
        ['الفروع', ' / '.join(branch(bid) for bid in shift.get('branch_ids',[])) or 'لا توجد'],
        ['مرجع الشفت / حالة الاعتماد',(snap.get('closure_label') or display_shift_label(shift))+' / '+label(shift.get('status'))],
        ['المعد / المعتمد',name(snap.get('prepared_by'))+' / '+name(snap.get('approved_by'))],
    ])
    orders=snap.get('orders',[]);tasks=snap.get('tasks',[]);refunds=snap.get('refunds',[])
    from .complimentary import KINDS
    table('complimentary','الضيافة والتعويض',['الطلب','النوع','الأصناف','الحالة'],[
        [display_order_label(o),KINDS[o['snapshot']['complimentary']['kind']][0],
         ' / '.join(str(i['quantity'])+' × '+i['name'] for i in o['snapshot'].get('items',[])),
         label(o.get('delivery_status'))]
        for o in orders if (o.get('snapshot') or {}).get('complimentary',{}).get('kind') in KINDS])
    cancelled=len({t.get('order_id') for t in tasks if t.get('status')=='cancelled'}|{o['id'] for o in orders if o.get('preparation_status')=='cancelled'})
    table('legacy-source-invoices','فواتير ساباد الأصلية — ملحق مستقل',['الطلب','فاتورة المصدر','وقت إصدار الفاتورة؛ ليس وقت التسليم','وقت التسجيل','سبب الملحق'],[[order_name(r['order_id']),r['invoice_reference'],v5_stamp(r['invoice_issued_at']),v5_stamp(r['recorded_at']),r['reason']+' — وقت التسليم والتحصيل الفعلي غير معلوم؛ لا يثبت استلام المدير للكاش.'] for r in snap.get('legacy_invoice_imports',[])])
    table('outcomes','نتائج الطلبات',['مكتمل','ملغى','مرتجع/جزئي','قيمة المكتمل (ر.س)'],[[
        summary.get('completed_orders',0),cancelled,len({r.get('order_id') for r in refunds}),v5_money(summary.get('gross_completed_halalas',0))]])
    if snap.get('kind')=='manager':
        rows=[]
        driver_summaries=[]
        for child in snap.get('driver_reports',[]):
            cs=child.get('summary')
            if not cs:
                child_tasks=[t for t in tasks if t.get('shift_id')==child.get('shift_id')]
                cs={'completed_orders':len({t.get('order_id') for t in child_tasks if t.get('status')=='delivered' and t.get('delivered_at')}),**{k+'_collected_halalas':sum(r.get('amount_halalas',0) for r in snap.get('collections',[]) if r.get('shift_id')==child.get('shift_id') and r.get('method')==method) for k,method in [('cash','cash'),('card','card')]}}
            driver_summaries.append(cs);sub=child.get('closure') or {}
            rows.append([name(child.get('user_id') or child.get('driver_id')),
                cs.get('completed_orders',child.get('completed_orders','غير مسجل')),
                v5_money(cs.get('cash_collected_halalas')),v5_money(cs.get('card_collected_halalas')),
                v5_money(sub.get('expected_halalas')),v5_money(sub.get('handover_halalas')),
                v5_money(sub.get('difference_halalas')),'معتمد / '+(child.get('closure_label') or display_shift_label({'id':child.get('shift_id'),'shift_no':child.get('shift_no'),'kind':'driver'}))])
        # Footer sums child money only, separately from the manager custody balance.
        amount_keys=['cash_collected_halalas','card_collected_halalas']
        totals=['إجمالي السائقين',sum(r.get('completed_orders',0) for r in driver_summaries)]
        totals += [v5_money(sum(r.get(k,0) for r in driver_summaries)) for k in amount_keys]
        totals += [v5_money(sum((r.get('closure') or {}).get(k,0) for r in snap.get('driver_reports',[]))) for k in ['expected_halalas','handover_halalas','difference_halalas']]
        totals += [str(len(rows))+' سائق']
        table('drivers','تقفيلات السائقين - تشمل شفتات بلا طلبات',['السائق','عدد','كاش محصل','شبكة','متوقع','تسليم مستلم','فرق','حالة/مرجع'],rows,totals)
    external=snap.get('external_enbishan',[])
    if external:
        keys=('order_count','cash_halalas','card_halalas','total_halalas')
        totals={k:sum(r.get(k,0) for r in external) for k in keys}
        table('external-enbishan','طلبات عنبيشن — ساباد (ملخص مؤقت مستقل عن مبيعات كبز)',
              ['السائق / الشفت','عدد الطلبات','كاش','شبكة','الإجمالي','سجّل بواسطة / الوقت'],
              [[name(r.get('driver_id'))+' / '+shift_name(r.get('shift_id')),r.get('order_count',0),
                v5_money(r.get('cash_halalas')),v5_money(r.get('card_halalas')),v5_money(r.get('total_halalas')),
                str(r.get('recorded_name') or name(r.get('recorded_by')))+' / '+v5_stamp(r.get('recorded_at'))] for r in external],
              ['إجمالي عنبيشن',totals['order_count'],v5_money(totals['cash_halalas']),v5_money(totals['card_halalas']),v5_money(totals['total_halalas']),'مدرج في التسوية؛ لا يمثل فواتير جديدة'])
    own_events=[r for r in snap.get('custody_events',[]) if r.get('shift_id')==shift.get('id')]
    received_custody=sum(r.get('amount_halalas',0) for r in own_events if r.get('kind')=='transfer_in')
    driver_shift_ids={r.get('shift_id') for r in snap.get('driver_reports',[])}
    driver_receipt_ids={r.get('id') for r in snap.get('transfers',[]) if r.get('from_shift_id') in driver_shift_ids and r.get('to_shift_id')==shift.get('id') and r.get('status')=='confirmed'}
    driver_receipts=sum(r.get('amount_halalas',0) for r in own_events if r.get('kind')=='transfer_in' and r.get('reference_id') in driver_receipt_ids)
    other_receipts=received_custody-driver_receipts
    direct_receipts=sum(r.get('amount_halalas',0) for r in own_events if r.get('kind')=='direct_receipt')
    expenses=-sum(r.get('amount_halalas',0) for r in own_events if r.get('kind') in ('expense','refund'))
    previous=-sum(r.get('amount_halalas',0) for r in own_events if r.get('kind')=='transfer_out' and not any(t.get('closure') and t.get('id')==r.get('reference_id') for t in snap.get('transfers',[])))
    adjustment=sum(r.get('amount_halalas',0) for r in own_events if r.get('kind')=='adjustment')
    finance=[['كاش محصل فعليا بعد الباقي',v5_money(summary.get('cash_collected_halalas',0)),'شبكة محصلة',v5_money(summary.get('card_collected_halalas',0))],
        ['عهدة افتتاحية',v5_money(shift.get('opening_halalas',0)),'نقد مستلم من السائقين' if snap.get('kind')=='manager' else 'عهدة نقدية مستلمة',v5_money(driver_receipts if snap.get('kind')=='manager' else received_custody)],
        ['مصروفات / سلف / ردود نقدية مسجلة',v5_money(expenses),'تسليمات نقدية سابقة',v5_money(previous)],
        ['النقد المتوقع',v5_money(closure.get('expected_halalas')),'النقد المعدود',v5_money(closure.get('counted_halalas'))],
        ['الكاش المقرر تسليمه للمطابقة' if closure.get('operational_approved') else 'التسليم النهائي المؤكد',v5_money(closure.get('handover_halalas')),'العهدة المتبقية',v5_money(closure.get('retained_halalas'))],
        ['الفرق المعتمد',v5_money(closure.get('difference_halalas')),'صافي التحصيلات - كاش وشبكة',v5_money(summary.get('net_collections_halalas',0))]]
    if adjustment:finance.append(['تسويات معتمدة بإشارتها',v5_money(adjustment),'استردادات الشبكة',v5_money(summary.get('card_refunded_halalas',0))])
    elif summary.get('card_refunded_halalas'):finance.append(['استردادات الشبكة',v5_money(summary['card_refunded_halalas']),'رسوم الطلبات المكتملة',v5_money(summary.get('delivery_fees_halalas',0))])
    if not expenses and not previous:finance.pop(2)
    if snap.get('kind')=='manager' and other_receipts:finance.insert(2,['عهدة مستلمة من شفتات أخرى',v5_money(other_receipts),'—','—'])
    if direct_receipts:finance.insert(2,['استلامات نقدية مباشرة مسجلة',v5_money(direct_receipts),'—','—'])
    table('finance','النقد والشبكة والعهدة - جميع المبالغ بالريال',['البيان','المبلغ','البيان','المبلغ'],finance)
    difference=closure.get('difference_halalas',0) or 0
    if closure.get('terminal_total_halalas') is not None:
        table('card-reconciliation','موازنة جهاز الشبكة',['حسب النظام','موازنة الجهاز','فرق الشبكة'],[[
            v5_money(closure.get('expected_card_halalas')),v5_money(closure['terminal_total_halalas']),v5_money(closure.get('card_difference_halalas'))]])
    table('reconciliation','نتيجة المطابقة',['الحالة','الفرق بالريال'],[['عجز' if difference<0 else 'فائض' if difference>0 else 'متوافق',v5_money(difference)]])
    table('legacy-deliveries','تسليم سابق بإقرار المالك — لا يثبت التحصيل',['الطلب','يوم التشغيل','سبب الإقرار','وقت تسجيل الإقرار؛ وقت التسليم الفعلي غير موثق'],[
        [order_name(a['order_id']),a.get('operational_date'),a.get('reason'),v5_stamp(a.get('recorded_at'))]
        for a in snap.get('legacy_delivery_attestations',[])])
    if snap.get('kind')=='driver':
        terminal=closure.get('payment_terminal') or {}
        table('payment-terminal','جهاز الشبكة المسند للسائق',['البيان','القيمة'],[
            ['المزود',terminal.get('provider') or 'غير مسجل'],['رقم الجهاز',terminal.get('terminal_id') or 'غير مسجل'],
            ['الرقم التسلسلي',terminal.get('serial_number') or 'غير مسجل'],['الطراز',terminal.get('model') or 'غير مسجل'],['بيانات إضافية',terminal.get('notes') or '—']])
    receipt_rows=[]
    for t in snap.get('transfers',[]):
        if t.get('from_shift_id')==shift.get('id') and t.get('closure'):
            destination=(snap.get('display_branches',{}).get(t.get('receiving_branch_id')) or {}).get('name') if t.get('receiving_branch_id') else None
            receipt_rows.append([name(t.get('confirmed_by') or t.get('recipient_user_id'))+(' / '+destination if destination else ''),str(t.get('reference') or 'غير مسجل'),label(t.get('status'))])
    if not receipt_rows:receipt_rows=[['لا تسليم نقدي مطلوب' if closure.get('handover_halalas')==0 else 'غير مسجل','—','—']]
    table('approval','الاستلام والاعتماد',['المستلم','المرجع','التأكيد'],receipt_rows)
    if shift.get('submission',{}).get('closure_exception'):table('delegated-closure','إقفال استثنائي',['الحالة','المعتمد'],[['مقفل باستثناء من مدير التوصيل',name(snap.get('approved_by'))]])
    if shift.get('exception_reason'):table('exception','الاستثناء المعتمد',['السبب','المعتمد'],[[shift['exception_reason'],name(snap.get('approved_by'))]])
    log=shift.get('vehicle_log') or {}
    if log:
        from .shift_vehicle import summary as vehicle_summary, CHECKS
        vehicle=vehicle_summary(log);begin=log.get('start') or {};end=log.get('end') or {}
        table('vehicle','المركبة والمسافة والبنزين',['البيان','القيمة'],[
            ['لوحة المركبة',(log.get('vehicle') or {}).get('plate','غير مسجلة')],
            ['عداد البداية (كم)',begin.get('odometer_km','غير مسجل')],['عداد النهاية (كم)',end.get('odometer_km','غير مسجل')],
            ['المسافة المقطوعة (كم)',vehicle['distance_km'] if vehicle['distance_km'] is not None else 'غير متاحة'],
            ['بنزين البداية %',begin.get('fuel_percent','غير مسجل')],['بنزين النهاية %',end.get('fuel_percent','غير مسجل')],
            ['إجمالي التعبئة (لتر)',vehicle['fuel_liters']],['عدد التعبئات',vehicle['refuel_count']]])
        status=lambda v:'سليم' if v=='ok' else 'يحتاج متابعة' if v=='issue' else 'غير مسجل'
        table('inspections','الفحص اليومي للمركبة',['البند','بداية الشفت','نهاية الشفت'],[[label,status(begin.get('checks',{}).get(key)),status(end.get('checks',{}).get(key))] for key,label in CHECKS.items()]+[['ملاحظات',begin.get('notes',''),end.get('notes','')]])
        parts=[*log.get('previous_segments',[]),log]
        if log.get('previous_segments'):
            table('vehicle-handovers','المركبات خلال الشفت',['المركبة','عداد الاستلام','عداد التسليم','بنزين الاستلام %','بنزين التسليم %','سبب التبديل'],[[(part.get('vehicle') or {}).get('plate',''),part.get('start',{}).get('odometer_km'),part.get('end',{}).get('odometer_km'),part.get('start',{}).get('fuel_percent'),part.get('end',{}).get('fuel_percent'),part.get('handover',{}).get('reason','')] for part in parts])
        table('refuels','سجل تعبئة البنزين',['الوقت - الرياض','المركبة','لتر','العداد (كم)','ملاحظات'],[[v5_stamp(r['recorded_at']),part.get('vehicle',{}).get('plate',''),r['liters'],r['odometer_km'],r.get('notes','')] for part in parts for r in part.get('refuels',[])])
    if snap.get('support_tickets'):
        from .order_support import CATEGORIES,STATES
        table('support-tickets','تذاكر المتابعة والمشكلات',['التذكرة','الطلب','الفرع','التصنيف','الحالة','الملاحظة / الحل'],[[t['reference'],t['order_reference'],branch(t['branch_id']),CATEGORIES.get(t['category'],t['category']),STATES.get(t['status'],t['status']),t['note']+' / '+t.get('resolution','')] for t in snap['support_tickets']])
    # Included in compact and owner output; metadata survives image retention.
    photo_labels={'acceptable':'دقة مقبولة','weak':'دقة ضعيفة','missing':'الموقع غير متاح',
                  'stale':'قراءة قديمة','invalid_clock':'وقت الجهاز غير موثوق',
                  'capture_time_unverified':'وقت الالتقاط غير متحقق'}
    photo_rows=[]
    for p in snap.get('photo_capture_evidence',[]):
        loc=p.get('capture_location') or {}
        coords=(str(loc['lat'])+', '+str(loc['lng'])) if loc.get('lat') is not None and loc.get('lng') is not None else '—'
        photo_rows.append([order_name(p['order_id']),p.get('driver_name') or '—',
            'استلام الفرع' if p['stage']=='picked_up' else 'تسليم العميل',
            v5_stamp(p.get('created_at')),v5_stamp(loc.get('captured_at')),
            photo_labels.get(loc.get('status'),'غير مسجل'),
            str(loc['accuracy_m'])+' m' if loc.get('accuracy_m') is not None else '—',coords])
    table('photo_capture_evidence','توثيق الصور — موقع ووقت يبلغهما الجهاز؛ لا يثبتان التسليم وحدهما',
        ['الطلب','السائق','الخطوة','وقت الرفع','وقت الجهاز','حالة الموقع','هامش الدقة','الإحداثيات'],photo_rows)
    if view!='owner':return tables
    by_branch={};by_item={};collection_branches={o['id']:o.get('branch_id') for o in orders}
    for o in orders:
        bid=o.get('branch_id');g=by_branch.setdefault(bid,{'orders':0,'gross':0,'cash':0,'card':0,'refund':0})
        if not o.get('fulfilled_in_report'):continue
        g['orders']+=1;g['gross']+=o.get('total_halalas',0)
        for item in (o.get('snapshot') or {}).get('items',[]):
            key=item.get('product_id') or item.get('source_id') or item.get('name')
            a=by_item.setdefault(key,{'name':item.get('name',key),'qty':0,'returns':0,'gross':0})
            a['qty']+=item.get('quantity',0);a['gross']+=item.get('line_total_halalas',0)
    for r in snap.get('collections',[]):
        bid=collection_branches.get(r.get('order_id'))
        if bid in by_branch:by_branch[bid][r['method']]+=r.get('amount_halalas',0)
    for r in refunds:
        bid=collection_branches.get(r.get('order_id'))
        if bid in by_branch:by_branch[bid]['refund']+=r.get('amount_halalas',0)
        for product,quantity in (r.get('line_quantities') or {}).items():
            if product in by_item:by_item[product]['returns']+=quantity
    table('branches','أداء الفروع من اللقطة المحفوظة',['الفرع','مكتمل','قيمة الطلبات','كاش','شبكة','ردود','صافي التحصيل'],[[
        branch(bid),a['orders'],v5_money(a['gross']),v5_money(a['cash']),v5_money(a['card']),v5_money(a['refund']),v5_money(a['cash']+a['card']-a['refund'])] for bid,a in by_branch.items()])
    table('items','الأصناف المنفذة - المبلغ قبل الاسترداد؛ الكمية الصافية مستقلة',['الصنف','الكمية','كمية معادة','صافي الكمية','القيمة قبل الرد'],[[
        a['name'],a['qty'],a['returns'],a['qty']-a['returns'],v5_money(a['gross'])] for a in sorted(by_item.values(),key=lambda a:a['qty']-a['returns'],reverse=True)])
    table('orders','تفاصيل الطلبات - المراجع تفتح التفاصيل المحفوظة',['الطلب','الفرع','النتيجة','الاحتساب','القيمة','وقت التأكيد'],[[
        display_order_label(o),branch(o.get('branch_id')),label(o.get('delivery_status')),'منفذ' if o.get('fulfilled_in_report') else 'تاريخي فقط',v5_money(o.get('total_halalas')),v5_stamp(o.get('created_at'))] for o in orders])
    item_details=[]
    for order in orders:
        for item in (order.get('snapshot') or {}).get('items',[]):
            units=item.get('units') or [{}]
            for index,unit in enumerate(units,1):
                opts=[]
                for group in (unit.get('option_snapshot') or {}).values():
                    choices=[]
                    for x in group.get('choices',[]):
                        choice=str(x.get('name') or x.get('code',''))
                        if x.get('price_delta_halalas') is not None:choice+=' (تعديل السعر '+v5_money(x['price_delta_halalas'])+')'
                        choices.append(choice)
                    opts.append(str(group.get('name','خيار'))+': '+' / '.join(choices))
                if not opts:
                    for key,value in (unit.get('options') or {}).items():opts.append(str(key)+': '+(' / '.join(map(str,value)) if isinstance(value,list) else str(value)))
                item_details.append([display_order_label(order),item.get('name'),item.get('quantity') if index==1 else '—',v5_money(item.get('line_total_halalas')) if index==1 else '—',('الوحدة '+str(index)+' - ' if unit else '')+(' / '.join(opts) or 'لا خيارات')])
    table('order_items','تفاصيل الأصناف والخيارات المحفوظة - قيمة السطر تظهر مرة واحدة',['الطلب','الصنف','الكمية','قيمة السطر','الخيارات لكل وحدة'],item_details)
    table('collections','التحصيلات ومراجع الشبكة - صافي الكاش بعد الباقي',['التحصيل / الطلب','الطريقة','المبلغ','الباقي','مرجع الدفع','وقت التحصيل'],[[
        str(r.get('id'))+' / '+order_name(r.get('order_id')),label(r.get('method')),v5_money(r.get('amount_halalas')),v5_money(r.get('change_halalas',0)),r.get('reference') or '—',v5_stamp(r.get('created_at'))] for r in snap.get('collections',[])])
    table('refunds','الإلغاءات والاستردادات المعتمدة',['الرد / الطلب','الطريقة','المبلغ','السبب','المخول','الوقت'],[[
        str(r.get('id'))+' / '+order_name(r.get('order_id')),label(r.get('method')),v5_money(r.get('amount_halalas')),r.get('reason') or '—',name(r.get('authorized_by')),v5_stamp(r.get('created_at'))] for r in refunds if r.get('record_kind')!='test_void'])
    table('test-voids','إلغاء تجريبي بأمر المالك — لا صرف نقدي',['مرجع التصحيح / الطلب','المبلغ المسجل المعكوس','السبب','المالك','الوقت'],[[str(r.get('id'))+' / '+order_name(r.get('order_id')),v5_money(r.get('amount_halalas')),r.get('reason') or '—',name(r.get('authorized_by')),v5_stamp(r.get('created_at'))] for r in refunds if r.get('record_kind')=='test_void'])
    table('custody','حركات العهدة - ليست مبيعات جديدة',['الحركة / الشفت','النوع','المبلغ بإشارته','السبب','المرجع'],[[
        str(r.get('id'))+' / '+shift_name(r.get('shift_id')),label(r.get('kind')),v5_money(r.get('amount_halalas')),r.get('reason') or '—',r.get('reference_id') or '—'] for r in snap.get('custody_events',[])])
    table('transfers','التسليمات والمستلمون',['المرجع','المبلغ','المستلم','الحالة','مرجع الاستلام'],[[
        r.get('id'),v5_money(r.get('amount_halalas')),name(r.get('confirmed_by') or r.get('recipient_user_id')),label(r.get('status')),r.get('reference') or '—'] for r in snap.get('transfers',[])])
    table('exceptions','مشكلات المهام والشكاوى/تحويلات المحادثات',['المهمة / الطلب','الحالة','السبب / السياق','المسؤول'],[[
        str(t.get('id'))+' / '+order_name(t.get('order_id')),label(t.get('status')),t.get('reason'),name(t.get('driver_id'))] for t in tasks if t.get('reason')]+[[
        str(x.get('id'))+' / '+order_name(x.get('order_id')),str(x.get('from_route'))+' ← '+str(x.get('to_route')),x.get('reason'),name(x.get('actor_id'))] for x in snap.get('conversation_exceptions',[])])
    timing_rows=[]
    for o in orders:
        p=o.get('performance') or o.get('timing_snapshot') or {};stages={key:value.get('seconds') for key,value in o.get('timings',{}).items()}
        policy=(o.get('performance_snapshot') or {}).get('policies',{}).get('delivery') or p.get('policy') or {}
        def duration(value):
            if value is None:return 'مرحلة لم تبدأ/بيانات ناقصة'
            secs=max(0,int(value));return f'{secs//60}:{secs%60:02d}'
        task=next((t for t in tasks if t.get('order_id')==o.get('id') and t.get('status')=='delivered'),None)
        road=stages.get('delivery')
        if road is None and task and task.get('picked_up_at') and task.get('delivered_at'):
            from datetime import datetime
            road=(datetime.fromisoformat(task['delivered_at'].replace('Z','+00:00'))-datetime.fromisoformat(task['picked_up_at'].replace('Z','+00:00'))).total_seconds()
        timing_rows.append([display_order_label(o),duration(stages.get('preparation')),duration(stages.get('driver_wait')),duration(road),
            str(p.get('label') or 'المعيار غير محدد'),str(policy.get('basis') or p.get('basis') or 'من استلام السائق إلى التسليم')+' / '+str(policy.get('version') or p.get('policy_version') or 'غير محدد')])
    table('timings','الأزمنة المحفوظة بالدقائق:الثواني - لا يعاد تقييم التاريخ',['الطلب','التجهيز','انتظار السائق','التوصيل','التصنيف','الأساس/الإصدار'],timing_rows)
    for key,title in [('linked_corrections','التصحيحات المالية المرتبطة'),('retained_carryovers','العهد المرحلة'),('manager_handovers','تسليم المناوبات')]:
        rows=[]
        for r in snap.get(key,[]):
            rows.append([r.get('id'),v5_money(r.get('amount_halalas')) if 'amount_halalas' in r else 'نقل مسؤولية',r.get('reason') or '—',name(r.get('authorized_by') or r.get('approved_by') or r.get('accepted_by')),r.get('report_id') or r.get('from_shift_id') or '—'])
        table(key,title,['المرجع','المبلغ/الإجراء','السبب','المعتمد','المرجع المرتبط'],rows)
    return tables

V5_CSS = '''
@font-face{font-family:"Noto Kufi Arabic";src:url("/assets/fonts/NotoKufiArabic-Regular.ttf") format("truetype");font-weight:400;font-style:normal;font-display:swap}
@font-face{font-family:"Noto Kufi Arabic";src:url("/assets/fonts/NotoKufiArabic-SemiBold.ttf") format("truetype");font-weight:600;font-style:normal;font-display:swap}
@font-face{font-family:"Noto Kufi Arabic";src:url("/assets/fonts/NotoKufiArabic-Bold.ttf") format("truetype");font-weight:700;font-style:normal;font-display:swap}
*{box-sizing:border-box;font-synthesis:none}body{margin:0;background:#f8f7f4;color:#20241f;font:17px/1.8 "Noto Kufi Arabic",sans-serif}main{max-width:1220px;margin:20px auto;background:white;padding:22px}header{border-bottom:2px solid #bc510f;padding-bottom:10px}.brand{font-size:19px;font-weight:700}h1{font-size:25px;margin:0}h2{font-size:21px;margin:18px 0 8px;font-weight:700}p{margin:5px 0}.meta,.foot{font-size:14px;color:#545b53}.test-label{color:#8a3106;font-weight:700}button{font:inherit;background:#aa4208;color:white;border:0;padding:8px 18px;cursor:pointer;border-radius:6px}.report-actions{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.report-actions a{font:inherit;background:#aa4208;color:white;padding:8px 18px;border-radius:6px;text-decoration:none}.report-actions :focus-visible{outline:3px solid #20241f;outline-offset:3px}.table-wrap{max-width:100%;overflow:auto}table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:15px;line-height:1.8}th,td{text-align:right;vertical-align:top;border:1px solid #dde1da;padding:6px 8px;overflow-wrap:anywhere}th{background:#f3f4f0;font-weight:700}tfoot td{background:#fff1e5;font-weight:700}.empty{text-align:center;color:#62675e}.num{font-variant-numeric:tabular-nums;unicode-bidi:plaintext}a{color:#943905}details{margin:12px 0;border:1px solid #dce0d8;padding:10px}summary{cursor:pointer;font-weight:600}.foot{border-top:1px solid #dce0d8;padding-top:10px;margin-top:15px}
@media(max-width:768px){main{margin:0;padding:14px}h1{font-size:23px}h2{font-size:21px}.wide table{min-width:850px}.meta{overflow-wrap:anywhere}}
@page{size:A4;margin:11mm}
@media print{body{background:white;font-size:11pt;line-height:1.65}main{padding:0;margin:0;max-width:none}.brand{font-size:14pt}h1{font-size:16pt}h2{font-size:14pt;margin:11px 0 5px;break-after:avoid}.meta,.foot{font-size:9pt}button,.screen-only,.drilldowns{display:none}.table-wrap{overflow:visible}table,.wide table{min-width:0;font-size:11pt;line-height:1.6}th,td{padding:4px 5px}thead{display:table-header-group}tfoot{display:table-row-group}tr{break-inside:avoid;page-break-inside:avoid}header{break-inside:avoid}}
/* Compact sections use page area before reducing readable type. */
main[data-report-view="compact"]{max-width:794px;font-size:13px;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 18px}
main[data-report-view="compact"]>header,main[data-report-view="compact"]>footer,main[data-report-view="compact"]>#shift,main[data-report-view="compact"]>#finance,main[data-report-view="compact"]>#drivers,main[data-report-view="compact"]>#support-tickets,main[data-report-view="compact"]>#refuels{grid-column:1/-1}
main[data-report-view="compact"] table{font-size:12px;line-height:1.5}main[data-report-view="compact"] th,main[data-report-view="compact"] td{padding:4px 6px}main[data-report-view="compact"] h2{font-size:15px;margin:8px 0 5px}main[data-report-view="compact"] h1{font-size:21px}main>section{min-width:0}h2{color:#873a12}tbody tr:nth-child(even){background:#faf9f6}
@media screen and (max-width:700px){main[data-report-view="compact"]{display:block}}
@media print{html,body{width:auto!important;min-width:0!important;margin:0!important}main[data-report-view="compact"]{width:100%;max-width:100%;min-width:0;margin:0;padding:0 2mm;gap:5px 12px}main[data-report-view="compact"] .table-wrap{width:100%;min-width:0}main[data-report-view="compact"] table{width:100%;min-width:0!important;font-size:9.5pt;line-height:1.45}main[data-report-view="compact"] th,main[data-report-view="compact"] td{padding:2px 4px}main[data-report-view="compact"] h2{font-size:10pt;line-height:1.4;margin:5px 0 3px}main[data-report-view="compact"] .meta,main[data-report-view="compact"] .foot{font-size:8pt;line-height:1.4}main[data-report-view="compact"] h1{font-size:14pt}main[data-report-view="compact"] header{padding-bottom:4px}main[data-report-view="compact"] section{break-inside:avoid}main[data-report-view="compact"] #drivers,main[data-report-view="compact"] #refuels,main[data-report-view="compact"] #support-tickets{break-inside:auto}}

'''

def output_tables(snap,view,part):
    tables=v5_tables(snap,view)
    if part=='drivers':return [t for t in tables if t['key'] in ('drivers','external-enbishan')]
    if part=='main':return [t for t in tables if t['key']!='drivers']
    return tables

def make_v5_print_html(snap,view='compact',report_id=None,part='all'):
    esc=lambda value:html.escape(str(value if value is not None else '—'),quote=True)
    tables=output_tables(snap,view,part)
    title='تقرير المالك المفصل' if view=='owner' else 'تقفيلة السائق' if snap.get('kind')=='driver' else 'تقفيلة مدير التوصيل'
    if part=='drivers':title='تقفيلات السائقين'
    sections=[]
    for table in tables:
        def row(values,tag='td',target=None):
            return '<tr'+(' data-drill-target="'+esc(target)+'"' if target else '')+'>'+''.join('<'+tag+' class="num">'+esc(v)+'</'+tag+'>' for v in values)+'</tr>'
        rows=''.join(row(values,target='order-'+snap['orders'][index]['id'] if table['key']=='orders' else None) for index,values in enumerate(table['rows'])) or '<tr><td class="empty" colspan="'+str(len(table['columns']))+'">لا توجد سجلات في اللقطة</td></tr>'
        footer='<tfoot>'+row(table['total'])+'</tfoot>' if table.get('total') else ''
        sections.append('<section id="'+esc(table['key'])+'"><h2>'+esc(table['title'])+'</h2><div class="table-wrap '+('wide' if len(table['columns'])>4 else '')+'"><table><thead>'+row(table['columns'],'th')+'</thead><tbody>'+rows+'</tbody>'+footer+'</table></div></section>')
    drill=[]
    if view=='owner' and part!='drivers':
        for o in snap.get('orders',[]):
            itemrows=[]
            for item in (o.get('snapshot') or {}).get('items',[]):
                options=[]
                for index,unit in enumerate(item.get('units',[]),1):
                    parts=[]
                    for group in (unit.get('option_snapshot') or {}).values():
                        parts.append(str(group.get('name','خيار'))+': '+ ' / '.join(str(x.get('name') or x.get('code','')) for x in group.get('choices',[])))
                    if not parts:
                        parts=[str(k)+': '+', '.join(v) if isinstance(v,list) else str(k)+': '+str(v) for k,v in (unit.get('options') or {}).items()]
                    if parts:options.append('الوحدة '+str(index)+' - '+'; '.join(parts))
                itemrows.append('<tr><td>'+esc(item.get('name'))+'</td><td>'+esc(item.get('quantity'))+'</td><td>'+esc(v5_money(item.get('line_total_halalas')))+'</td><td>'+esc(' / '.join(options) or 'لا خيارات')+'</td></tr>')
            drill.append('<details id="order-'+esc(o['id'])+'"><summary>طلب '+esc(display_order_label(o))+' - تفاصيل الأصناف المحفوظة</summary><div class="table-wrap"><table><thead><tr><th>الصنف</th><th>الكمية</th><th>المبلغ</th><th>الخيارات</th></tr></thead><tbody>'+''.join(itemrows)+'</tbody></table></div></details>')
    testing=snap.get('test_data') or str(snap.get('brand_name','')).startswith(('اختبار','تجريبي','TEST'))
    test='<p class="test-label">بيانات اختبار فقط - ليست تشغيلًا حقيقيًا</p>' if testing else ''
    actions='<button type="button" data-print-report>طباعة التقرير</button>'
    if report_id:
        from urllib.parse import quote
        prefix='/api/reports/'+quote(str(report_id),safe='')+'/renderings/v5/'
        actions+='<a href="'+esc(prefix+'pdf')+'">تنزيل PDF المحفوظ</a><a href="'+esc(prefix+'csv')+'">تنزيل CSV المحفوظ</a>'
    if report_id:
        actions+='<a href="/api/reports/'+quote(str(report_id),safe='')+'/formatted/pdf?part='+esc(part)+'&amp;view='+esc(view)+'">تنزيل PDF المنسق</a>'
        if snap.get('kind')=='manager':actions+='<a href="/api/reports/'+quote(str(report_id),safe='')+'/formatted/print?part=drivers">ورقة تقفيلات السائقين</a>'
    actions='<nav class="report-actions screen-only" aria-label="ملفات التقرير">'+actions+'</nav>'
    return '<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+esc(title)+' - '+esc(brand_title(snap))+'</title><style>'+V5_CSS+'</style><script src="/report.js" defer></script></head><body><main data-report-template="5" data-report-view="'+esc(view)+'"><header><div class="brand">'+esc(brand_title(snap))+'</div><h1>'+esc(title)+'</h1>'+test+'<p class="meta">قالب 5 · بيانات '+esc(snap.get('schema_version',1))+' · '+esc(snap.get('timezone','Asia/Riyadh'))+' · تقرير '+esc(snap.get('closure_label') or display_shift_label(snap.get('shift',{})))+'</p>'+actions+'<p class="meta">اللقطة المعتمدة ثابتة. تسليم العهدة لا يُحسب بيعًا ثانيًا.</p></header>'+''.join(sections)+'<section class="drilldowns screen-only"><h2>الطلبات - عرض التفاصيل</h2>'+''.join(drill)+'</section><footer class="foot">مرجع الشفت '+esc(snap.get('closure_label') or display_shift_label(snap.get('shift',{})))+' · النسخة المعتمدة محفوظة · جميع المبالغ بالريال السعودي</footer></main></body></html>'

def make_v5_csv(snap,view='compact'):
    # Complete source rows remain available even when the staff print is compact.
    base=make_csv(snap).decode('utf-8-sig');out=io.StringIO();out.write(base)
    writer=csv.writer(out);writer.writerow(['presentation_template',TEMPLATE_VERSION,'data_version',snap.get('schema_version',1),'timezone','Asia/Riyadh'])
    for table in v5_tables(snap,view):
        writer.writerow(['table',table['key'],cell(table['title'])]);writer.writerow([cell(v) for v in table['columns']])
        for row in table['rows']:writer.writerow([cell(v) for v in row])
        if table.get('total'):writer.writerow([cell(v) for v in table['total']])
    return ('\ufeff'+out.getvalue()).encode('utf-8')

def make_v5_pdf(snap,view='compact',part='all'):
    """Readable A4 tables; repeat real headers and keep every row across pages."""
    from pathlib import Path
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Table,TableStyle,Spacer,KeepTogether,Flowable
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import arabic_reshaper
    from bidi.algorithm import get_display
    font_root=Path(__file__).resolve().parents[1]/'public'/'assets'/'fonts'
    for weight,name in [('Regular','KPSKufi400'),('SemiBold','KPSKufi600'),('Bold','KPSKufi700')]:
        if name not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(name,str(font_root/('NotoKufiArabic-'+weight+'.ttf'))))
    pdfmetrics.registerFontFamily('KPSKufi400',normal='KPSKufi400',bold='KPSKufi700',italic='KPSKufi400',boldItalic='KPSKufi700')
    normal=ParagraphStyle('KufiBody',fontName='KPSKufi400',fontSize=9 if view=='compact' else 11,leading=13 if view=='compact' else 19,alignment=2,wordWrap='RTL')
    bold=ParagraphStyle('KufiHead',parent=normal,fontName='KPSKufi700',fontSize=9 if view=='compact' else 11,leading=13 if view=='compact' else 19)
    heading=ParagraphStyle('KufiTitle',parent=bold,fontSize=15,leading=23,spaceAfter=5,keepWithNext=True)
    section=ParagraphStyle('KufiSection',parent=bold,fontSize=10 if view=='compact' else 14,leading=15 if view=='compact' else 22,spaceBefore=4,spaceAfter=3,keepWithNext=True)
    meta=ParagraphStyle('KufiMeta',parent=normal,fontSize=9,leading=16)
    class RTLText(Flowable):
        # Wrap the logical Arabic text first, then shape each complete line.
        # Shaping before wrapping would reverse the order of wrapped words.
        def __init__(self,value,style):
            Flowable.__init__(self);self.value=str(value if value is not None else '—');self.style=style
            self.keepWithNext=getattr(style,'keepWithNext',False);self.spaceBefore=style.spaceBefore;self.spaceAfter=style.spaceAfter
        def wrap(self,avail_width,avail_height):
            def measured(value):return pdfmetrics.stringWidth(get_display(arabic_reshaper.reshape(value)),self.style.fontName,self.style.fontSize)
            self.lines=[]
            for paragraph in self.value.split('\n'):
                current=''
                for word in paragraph.split():
                    candidate=(current+' '+word).strip()
                    if measured(candidate)<=avail_width:current=candidate;continue
                    if current:self.lines.append(current);current=''
                    if measured(word)<=avail_width:current=word;continue
                    part=''
                    for char in word:
                        if part and measured(part+char)>avail_width:self.lines.append(part);part=''
                        part+=char
                    current=part
                self.lines.append(current)
            self.width=avail_width;self.height=len(self.lines)*self.style.leading
            return self.width,self.height
        def split(self,avail_width,avail_height):
            # Table._splitCell passes the full column width, including padding,
            # whereas its original wrap uses the narrower content width. Keep
            # that measured width: broadening it here can turn three lines into
            # two and return a single flowable to a two-part cell split.
            measured_width=self.width
            self.wrap(min(avail_width,measured_width) if measured_width else avail_width,avail_height)
            count=int(avail_height//self.style.leading)
            if count<1:return []
            if count>=len(self.lines):return [self]
            return [RTLText('\n'.join(self.lines[:count]),self.style),RTLText('\n'.join(self.lines[count:]),self.style)]
        def draw(self):
            self.canv.setFont(self.style.fontName,self.style.fontSize);self.canv.setFillColor(colors.HexColor('#20241f'))
            for index,line in enumerate(self.lines):
                self.canv.drawRightString(self.width,self.height-(index+1)*self.style.leading+4,get_display(arabic_reshaper.reshape(line)))
    def p(value,style=normal):return RTLText(value,style)
    stream=io.BytesIO();doc=SimpleDocTemplate(stream,pagesize=A4,rightMargin=30,leftMargin=30,topMargin=28,bottomMargin=37,title=brand_title(snap)+' - Closure V5',invariant=1)
    title='تقرير المالك المفصل' if view=='owner' else 'تقفيلة السائق' if snap.get('kind')=='driver' else 'تقفيلة مدير التوصيل'
    if part=='drivers':title='تقفيلات السائقين'
    body=[p(brand_title(snap)+' - '+title,heading),p('قالب 5 · بيانات '+str(snap.get('schema_version',1))+' · التاريخ '+str(snap.get('operational_date',''))+' · وقت الرياض',meta)]
    if snap.get('test_data') or str(snap.get('brand_name','')).startswith(('اختبار','تجريبي','TEST')):body.append(p('بيانات اختبار فقط - ليست تشغيلًا حقيقيًا',bold))
    for table in output_tables(snap,view,part):
        # Pack short key/value sections into paired fields, preserving all values.
        field_table=view=='compact' and table['columns']==['البيان','القيمة']
        if field_table and len(table['rows'])>1:
            source=table['rows']
            if table['key']=='shift':
                # Keep both explicitly labelled times in one compact PDF field;
                # HTML keeps separate rows. Do not shrink text or remove data.
                source=[*source[:2],['بداية / نهاية الدوام - الرياض',
                    source[2][1]+' ← '+source[3][1]],*source[4:]]
            table={**table,'columns':['البيان','القيمة','البيان','القيمة'],
                   'rows':[source[i]+(source[i+1] if i+1<len(source) else ['','']) for i in range(0,len(source),2)]}
        # PDF rightmost column is the first semantic column, matching the RTL HTML.
        rows=[[p(table['title'],section)]+['' for _ in table['columns'][1:]]]
        if not field_table:rows.append([p(v,bold) for v in reversed(table['columns'])])
        for row in table['rows']:rows.append([p(v) for v in reversed(row)])
        if not table['rows']:rows.append([p('لا توجد سجلات')]+[p('—') for _ in table['columns'][1:]])
        if table.get('total'):rows.append([p(v,bold) for v in reversed(table['total'])])
        n=len(table['columns']);weights=[1]*n
        if n==4 and table['key'] in ('shift','payment-terminal','vehicle'):weights=[1.4,1,1.4,1]
        if n==2:weights=[1.9,1.1]
        if table['key']=='finance':weights=[.55,1.45,.55,1.45]
        if table['key']=='drivers':weights=[1.5,.6,.8,.8,.8,.8,.5,1.3]
        widths=[doc.width*w/sum(weights) for w in weights]
        rendered=Table(rows,colWidths=widths,repeatRows=1 if field_table else 2,hAlign='RIGHT',splitByRow=1,splitInRow=1)
        # Compact staff closures keep the same readable type and line spacing.
        # Use less empty cell padding so the added terminal/receipt metadata does
        # not force an otherwise small closure onto a nearly empty second page.
        vertical_padding=1 if view=='compact' else 3
        cmds=[('SPAN',(0,0),(-1,0)),('BACKGROUND',(0,1),(-1,1),colors.HexColor('#f1f3ee')),('GRID',(0,1),(-1,-1),.35,colors.HexColor('#d6ddd3')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),vertical_padding),('BOTTOMPADDING',(0,0),(-1,-1),vertical_padding)]
        if table.get('total'):cmds.append(('BACKGROUND',(0,-1),(-1,-1),colors.HexColor('#fff0e3')))
        rendered.setStyle(TableStyle(cmds));body += [rendered]
    def footer(cv,document):
        cv.saveState();cv.setStrokeColor(colors.HexColor('#d6ddd3'));cv.line(30,29,A4[0]-30,29);cv.setFont('KPSKufi400',9)
        cv.drawRightString(A4[0]-30,14,get_display(arabic_reshaper.reshape('صفحة '+str(document.page)+' · قالب 5 · وقت الرياض')))
        cv.drawString(30,14,str(snap.get('closure_label') or display_shift_label(snap.get('shift',{}))));cv.restoreState()
    doc.build(body,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()


def queue_v5_renderings(c,rid,snap=None):
    if snap is None:
        row=c.execute('SELECT snapshot,version FROM closure_reports WHERE id=%s',(rid,)).fetchone()
        if not row:return
        snap=row['snapshot']
    digest=db.digest(snap)
    for view in ['compact']+(['owner'] if snap.get('kind')=='manager' else []):
        c.execute('INSERT INTO report_renderings(report_id,template_version,view_kind,data_version,source_sha256) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',(rid,TEMPLATE_VERSION,view,snap.get('schema_version',1),digest))


def generate_v5_files(rid,view=None,force_retry=False):
    views=[view] if view else ['compact','owner']
    for variant in views:
        with db.tx() as c:
            row=c.execute('SELECT g.*,r.snapshot FROM report_renderings g JOIN closure_reports r ON r.id=g.report_id WHERE g.report_id=%s AND g.template_version=%s AND g.view_kind=%s FOR UPDATE OF g SKIP LOCKED',(rid,TEMPLATE_VERSION,variant)).fetchone()
            if not row or row['status']=='ready':continue
            if not force_retry:
                due=c.execute('SELECT %s::timestamptz<=now() due',(row['next_attempt_at'],)).fetchone()['due']
                if not due:continue
            try:
                if db.digest(row['snapshot'])!=row['source_sha256']:raise ValueError('Snapshot integrity mismatch')
                page=make_v5_print_html(row['snapshot'],variant,rid);pdf=make_v5_pdf(row['snapshot'],variant);csv_data=make_v5_csv(row['snapshot'],variant)
                c.execute("UPDATE report_renderings SET status='ready',html_data=%s,pdf_data=%s,csv_data=%s,error=NULL,attempts=attempts+1,generated_at=now() WHERE report_id=%s AND template_version=%s AND view_kind=%s",(page,pdf,csv_data,rid,TEMPLATE_VERSION,variant))
            except Exception as exc:
                delay=min(3600,30*(2**min(row['attempts'],7)))
                c.execute("UPDATE report_renderings SET status='failed',error=%s,attempts=attempts+1,next_attempt_at=now()+(%s*interval '1 second') WHERE report_id=%s AND template_version=%s AND view_kind=%s",(type(exc).__name__+': generation failed; retry available',delay,rid,TEMPLATE_VERSION,variant))


def current_view(user,r):return 'owner' if user['role']=='owner' and r['kind']=='manager' else 'compact'

def saved_rendering(c,user,rid,view=None):
    r=report(c,user,rid);variant=view or current_view(user,r)
    if variant=='owner' and user['role']!='owner':fail(404,'التقرير غير موجود')
    row=c.execute('SELECT * FROM report_renderings WHERE report_id=%s AND template_version=%s AND view_kind=%s',(rid,TEMPLATE_VERSION,variant)).fetchone()
    if not row:queue_v5_renderings(c,rid,r['snapshot'])
    return r,row,variant

@router.get('/{rid}/original/pdf')
def original_pdf(rid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        r=report(c,user,rid)
        if contains_complimentary(r['snapshot']) and user['role']!='owner':
            return Response(make_v5_pdf(r['snapshot']),media_type='application/pdf',headers={'Cache-Control':'private, no-store'})
        if r['pdf_status']!='ready' or not r['pdf_data']:fail(409,'النسخة الأصلية قيد التوليد')
        return Response(bytes(r['pdf_data']),media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="KPS-original-{rid}.pdf"','Cache-Control':'private, no-store'})

@router.get('/{rid}/formatted/{format}')
def formatted_download(rid:str,format:str,view:str='compact',part:str='main',user=Depends(auth.current_user)):
    if format not in ('print','pdf'):fail(404,'الملف غير موجود')
    if view not in ('compact','owner') or part not in ('main','drivers','all'):fail(422,'عرض غير صالح')
    with db.tx() as c:
        r=report(c,user,rid)
        if view=='owner' and current_view(user,r)!='owner':fail(403,'التقرير المفصل للمالك فقط')
        variant=view
        r['snapshot']=corrected_snapshot(c,r['snapshot'])
    data=make_v5_print_html(r['snapshot'],variant,rid,part) if format=='print' else make_v5_pdf(r['snapshot'],variant,part)
    headers={'Cache-Control':'private, no-store'}
    if format=='pdf':headers['Content-Disposition']=f'attachment; filename="KPS-formatted-{rid}.pdf"'
    return Response(data,media_type='text/html' if format=='print' else 'application/pdf',headers=headers)

@router.get('/{rid}/renderings/v5/{format}')
def v5_download(rid:str,format:str,user=Depends(auth.current_user)):
    if format not in ('print','pdf','csv'):fail(404,'الملف غير موجود')
    with db.tx() as c:
        r,row,variant=saved_rendering(c,user,rid)
        r['snapshot']=corrected_snapshot(c,r['snapshot'])
    if r['snapshot'].get('operational_date_correction') or contains_complimentary(r['snapshot']):
        value=make_v5_print_html(r['snapshot'],variant,rid) if format=='print' else make_v5_pdf(r['snapshot'],variant) if format=='pdf' else make_v5_csv(r['snapshot'],variant)
        return Response(value,media_type={'print':'text/html','pdf':'application/pdf','csv':'text/csv; charset=utf-8'}[format],headers={'Cache-Control':'private, no-store'})
    if not row or row['status']!='ready':
        generate_v5_files(rid,variant)
        with db.tx() as c:r,row,variant=saved_rendering(c,user,rid)
    if not row or row['status']!='ready':fail(409,'القالب الجديد قيد التوليد؛ إعادة المحاولة متاحة')
    data=row['html_data'] if format=='print' else bytes(row[format+'_data'])
    media={'print':'text/html','pdf':'application/pdf','csv':'text/csv; charset=utf-8'}[format]
    headers={'Cache-Control':'private, no-store'}
    if format!='print':headers['Content-Disposition']=f'attachment; filename="KPS-{variant}-v5-{rid}.{format}"'
    return Response(data,media_type=media,headers=headers)

@router.post('/{rid}/renderings/v5/retry')
def v5_retry(rid:str,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        r,row,variant=saved_rendering(c,user,rid)
        db.audit(c,user['id'],'report.rendering.retry','report',rid,{'template_version':5,'view':variant})
    generate_v5_files(rid,variant,force_retry=True)
    with db.tx() as c:
        row=c.execute('SELECT status,error,attempts FROM report_renderings WHERE report_id=%s AND template_version=5 AND view_kind=%s',(rid,variant)).fetchone()
    return {'report_id':rid,'template_version':5,'view':variant,**row}
