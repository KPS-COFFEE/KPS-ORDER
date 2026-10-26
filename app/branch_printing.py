"""Branch-bound outbound print queues. Spool submission is not proof of paper output."""
import io
import base64
import json
import os
from urllib.parse import urlsplit
import secrets
import zipfile
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from . import auth, db, print_translations, invoice_profiles, catalog

router = APIRouter(prefix='/api/printing', tags=['branch-printing'])
operators = auth.require_roles('owner', 'delivery_manager')


def can_manage_printers(user):
    return user['role'] != 'customer' and auth.has_permission(user,'branch_printers_manage')


def printer_admin(user=Depends(auth.current_user)):
    if not can_manage_printers(user):
        raise db.error(403,'ROLE_FORBIDDEN','إدارة الطابعات تتطلب صلاحية يمنحها المالك.')
    return user



def can_access_branch(user, branch_id):
    # Home-brand owners retain their existing full scope. A projected owner
    # has only the branches in the current explicit additional-brand grant.
    return ((user['role'] == 'owner' and not user.get('home_brand_id'))
            or branch_id in user.get('branch_ids', []))


def scope(conn, user, branch_id):
    branch = conn.execute('SELECT * FROM branches WHERE id=%s AND brand_id=%s',
                          (branch_id, user['brand_id'])).fetchone()
    if not branch or not can_access_branch(user, branch_id):
        raise db.error(404, 'PRINT_BRANCH_SCOPE', 'الفرع غير متاح ضمن صلاحياتك.')
    return branch


class PairBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    branch_id: str
    label: str = Field(min_length=1, max_length=100)
    paper_mm: Literal[58,80] = 80
    reason: str = Field(default="", max_length=300)


@router.post('/devices')
def pair_device(body: PairBody, user=Depends(printer_admin)):
    code = secrets.token_urlsafe(24)
    with db.tx() as conn:
        scope(conn, user, body.branch_id)
        existing = conn.execute("SELECT id FROM branch_print_devices WHERE branch_id=%s LIMIT 1", (body.branch_id,)).fetchone()
        if existing and not body.reason.strip():
            raise db.error(422, "PRINT_CHANGE_REASON", "سبب تعديل ربط الطابعة مطلوب.")
        identifier = db.new_id('printer_')
        conn.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,pair_hash,pair_expires_at,paper_mm,created_by) VALUES(%s,%s,%s,%s,%s,now()+interval '10 minutes',%s,%s)",
                     (identifier,user['brand_id'],body.branch_id,body.label,auth.token_hash(code),body.paper_mm,user['id']))
        db.audit(conn,user['id'],'printer.pair.created','printer',identifier,{'branch_id':body.branch_id,'reason':body.reason.strip()})
    return {'id':identifier,'pairing_code':code,'expires_in_seconds':600}


class RedeemBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pairing_code: str = Field(min_length=20,max_length=100)
    printer_name: str = Field(min_length=1,max_length=200)


@router.post('/bridge/pair')
def redeem(body: RedeemBody, request: Request):
    with db.tx() as conn:
        allowed = db.rate_limit(conn,'printer-pair:'+str(request.client.host if request.client else ''),20,600)
    if not allowed:
        raise db.error(429,'PRINT_PAIR_LIMIT','محاولات ربط كثيرة؛ انتظر قليلًا.')
    with db.tx() as conn:
        device = conn.execute('SELECT * FROM branch_print_devices WHERE pair_hash=%s AND pair_expires_at>now() AND revoked_at IS NULL FOR UPDATE', (auth.token_hash(body.pairing_code),)).fetchone()
        if not device:
            raise db.error(401,'PRINT_PAIR_INVALID','رمز الربط غير صالح أو مستخدم أو منتهي.')
        creator = conn.execute('SELECT * FROM users WHERE id=%s AND active',(device['created_by'],)).fetchone()
        if creator and creator['brand_id'] != device['brand_id']:
            from . import multibrand
            creator = multibrand.project(conn, creator, device['brand_id'])
        if not creator or not can_manage_printers(creator):
            raise db.error(403,'PRINT_PAIR_REVOKED','صلاحية منشئ الربط غير سارية.')
        scope(conn,creator,device['branch_id'])
        token = secrets.token_urlsafe(32)
        conn.execute('UPDATE branch_print_devices SET token_hash=%s,pair_hash=NULL,pair_expires_at=NULL,printer_name=%s,last_seen_at=now() WHERE id=%s',
                     (auth.token_hash(token),body.printer_name,device['id']))
        db.audit(conn,creator['id'],'printer.paired','printer',device['id'])
        return {'device_id':device['id'],'token':token,'paper_mm':device['paper_mm']}


def bridge(request: Request):
    authorization = request.headers.get('authorization','')
    token = authorization.removeprefix('Bearer ')
    if not authorization.startswith('Bearer ') or not 40<=len(token)<=100:
        raise db.error(401,'PRINT_DEVICE_AUTH','تسجيل جهاز الطباعة مطلوب.')
    with db.tx() as conn:
        device = conn.execute('SELECT d.* FROM branch_print_devices d JOIN brands b ON b.id=d.brand_id WHERE d.token_hash=%s AND d.revoked_at IS NULL AND b.active', (auth.token_hash(token),)).fetchone()
    if not device:
        raise db.error(401,'PRINT_DEVICE_AUTH','جهاز الطباعة غير مخول أو ألغي ربطه.')
    return device


def same_print_city(source, destination):
    # Match branch geography independently of invoice numbering or prefixes.
    left=(source.get('city') or '').strip()
    right=(destination.get('city') or '').strip()
    return bool(left and right) and catalog.normalize_city(left)==catalog.normalize_city(right)


@router.get('/devices')
def devices(user=Depends(printer_admin), order_id: str | None = None):
    with db.tx() as conn:
        source=None
        if order_id:
            order=conn.execute('SELECT branch_id FROM orders WHERE id=%s AND brand_id=%s',(order_id,user['brand_id'])).fetchone()
            if not order:raise db.error(404,'ORDER_NOT_FOUND','الطلب غير موجود.')
            source=scope(conn,user,order['branch_id'])
        rows = conn.execute('SELECT id,branch_id,label,printer_name,paper_mm,last_seen_at,revoked_at,approved_at,acceptance_job_id FROM branch_print_devices WHERE brand_id=%s ORDER BY created_at DESC',(user['brand_id'],)).fetchall()
        visible=[r for r in rows if can_access_branch(user, r['branch_id'])]
        if source:
            branches={b['id']:b for b in conn.execute('SELECT id,city FROM branches WHERE brand_id=%s',(user['brand_id'],)).fetchall()}
            visible=[r for r in visible if same_print_city(source,branches.get(r['branch_id'],{}))]
        for row in visible:
            row['connection_status']=connection_status(row)
        return {'devices':visible}


@router.get('/operational-devices')
def operational_devices(order_id: str, user=Depends(operators)):
    with db.tx() as conn:
        order=conn.execute('SELECT * FROM orders WHERE id=%s AND brand_id=%s',
                           (order_id,user['brand_id'])).fetchone()
        if not order or not auth.can_access_order(user,order,conn):
            raise db.error(404,'ORDER_NOT_FOUND','الطلب غير موجود ضمن صلاحياتك.')
        source=scope(conn,user,order['branch_id'])
        rows=conn.execute('SELECT d.id,d.branch_id,d.label,d.printer_name,d.paper_mm,b.city '
                          'FROM branch_print_devices d JOIN branches b ON b.id=d.branch_id '
                          'WHERE d.brand_id=%s AND d.revoked_at IS NULL AND d.approved_at IS NOT NULL '
                          'AND d.token_hash IS NOT NULL ORDER BY d.created_at DESC',
                          (user['brand_id'],)).fetchall()
        visible=[{k:v for k,v in row.items() if k!='city'} for row in rows
                 if can_access_branch(user, row['branch_id'])
                 and same_print_city(source,row)]
        return {'devices':visible}


def connection_status(row):
    if row['revoked_at']: return 'revoked'
    if not row['printer_name']: return 'pending'
    if not row['last_seen_at'] or (datetime.now(timezone.utc)-row['last_seen_at']).total_seconds()>45:
        return 'offline'
    return 'online'


@router.post('/setup')
def guided_setup(body: PairBody, response: Response, user=Depends(printer_admin)):
    # The setup secret is short-lived and branch-bound, never placed in a URL.
    with db.tx() as conn:
        branch=scope(conn,user,body.branch_id)
    origin=bridge_origin()
    pair=pair_device(body,user)
    config={'pairing_code':pair['pairing_code'],'branch_name':branch['name'],'branch_id':body.branch_id,'origin':origin}
    response.headers['Cache-Control']='no-store'
    return {'id':pair['id'],'bundle':base64.b64encode(bridge_bundle(config)).decode('ascii')}


class ChangeBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=1, max_length=300)


@router.post('/devices/{identifier}/revoke')
def revoke(identifier: str, body: ChangeBody, user=Depends(printer_admin)):
    if not body.reason.strip():
        raise db.error(422, 'PRINT_CHANGE_REASON', 'سبب تعديل ربط الطابعة مطلوب.')
    with db.tx() as conn:
        row=conn.execute('SELECT * FROM branch_print_devices WHERE id=%s AND brand_id=%s FOR UPDATE',(identifier,user['brand_id'])).fetchone()
        if not row: raise db.error(404,'PRINT_DEVICE_NOT_FOUND','الجهاز غير موجود.')
        scope(conn,user,row['branch_id'])
        conn.execute('UPDATE branch_print_devices SET revoked_at=now(),token_hash=NULL,pair_hash=NULL WHERE id=%s',(identifier,))
        conn.execute("UPDATE branch_print_jobs SET status='cancelled',finished_at=now() WHERE device_id=%s AND status='queued'",(identifier,))
        db.audit(conn,user['id'],'printer.revoked','printer',identifier,{'reason':body.reason.strip()})
        return {'revoked':True}



def managed_device(conn, user, identifier):
    device=conn.execute('SELECT * FROM branch_print_devices WHERE id=%s AND brand_id=%s AND revoked_at IS NULL AND token_hash IS NOT NULL FOR UPDATE',(identifier,user['brand_id'])).fetchone()
    if not device:
        raise db.error(404,'PRINT_DEVICE_NOT_FOUND','الطابعة غير مربوطة أو غير متاحة.')
    branch=scope(conn,user,device['branch_id'])
    return device,branch


@router.post('/devices/{identifier}/test')
def test_printer(identifier:str,user=Depends(printer_admin),idem:str|None=Header(None,alias='Idempotency-Key')):
    with db.tx() as conn:
        device,branch=managed_device(conn,user,identifier)
        def perform():
            job_id=db.new_id('print_')
            lines=[{'text':text,'bold':True,'center':True} for text in (
                'اختبار الطباعة / PRINT TEST',branch['name'],device['label'],
                str(device['paper_mm'])+' mm', 'قهوة عربية — 1234567890',
                'Arabic / English — check logo and margins',
                'ليست فاتورة طلب / NOT AN ORDER',job_id)]
            conn.execute("INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,kind,payload,requested_by) VALUES(%s,%s,%s,%s,'test',%s,%s)",
                         (job_id,user['brand_id'],device['branch_id'],identifier,db.json({'language':'ar','kind':'test','lines':lines,'brand_id':device['brand_id']}),user['id']))
            conn.execute('UPDATE branch_print_devices SET acceptance_job_id=%s,approved_at=NULL,approved_by=NULL,approved_api_host=NULL WHERE id=%s',(job_id,identifier))
            db.audit(conn,user['id'],'printer.test.queued','printer',identifier,{'job_id':job_id})
            return {'id':job_id,'status':'queued'}
        return db.idempotent(conn,user['id'],idem,'printer.test',{'device_id':identifier},perform)


class AcceptBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id:str
    paper_confirmed:StrictBool


@router.post('/devices/{identifier}/approve')
def approve_printer(identifier:str,body:AcceptBody,user=Depends(printer_admin)):
    with db.tx() as conn:
        device,branch=managed_device(conn,user,identifier)
        job=conn.execute("SELECT status,ack_api_host FROM branch_print_jobs WHERE id=%s AND device_id=%s AND kind='test'",(body.job_id,identifier)).fetchone()
        if not body.paper_confirmed or body.job_id!=device['acceptance_job_id'] or not job or job['status']!='submitted':
            raise db.error(409,'PRINT_TEST_NOT_CONFIRMED','انتظر إرسال الاختبار وتأكد من خروج الورقة وصحة الشعار والعربية والمقاس.')
        if not device['approved_at']:
            conn.execute('UPDATE branch_print_devices SET approved_at=now(),approved_by=%s,approved_api_host=%s WHERE id=%s',(user['id'],job['ack_api_host'],identifier))
            db.audit(conn,user['id'],'printer.test.approved','printer',identifier,{'job_id':body.job_id,'physical_output_confirmed_by_operator':True})
        return {'approved':True}


def receipt(order, branch, kind, language, translations=None):
    ar = language=='ar'
    snap=order['snapshot']; lines=[]
    fiscal = order.get('fiscal_document') if kind == 'order' else None
    if fiscal:
        translations=fiscal.get('print_labels_en',{})
        branch={**branch,'name':fiscal.get('branch_name') or branch['name']}
    def add(text,bold=False,center=False,role='',label='',value=''):
        lines.append({'text':str(text),'bold':bold,'center':center,
                      'role':role,'label':label,'value':value})
    def row(label,value,bold=False):
        add(label+': '+value,bold,role='row',label=label,value=value)
    if order.get('amendment_revision',0):
        add(('نسخة معدلة رقم ' if ar else 'REVISED COPY ')+str(order['amendment_revision']+1),True,True)
        add('تلغي نسخة الطلب السابقة' if ar else 'Replaces previous order copy',True,True)
    def amount(value): return f'{int(value or 0)/100:.2f} '+('ر.س' if ar else 'SAR')
    free=snap.get('complimentary')
    if free:
        from .complimentary import KINDS
        add(KINDS[free['kind']][0 if ar else 1],True,True)
    occasion_style = {'font': 'Arial', 'size': 18, 'bold': True, 'align': 'center'} | (snap.get('receipt_occasion_style') or {})
    if kind == 'order':
        for occasion_line in (snap.get('receipt_occasion_text') or '').splitlines():
            add(occasion_line,occasion_style['bold'],occasion_style['align']=='center',role='occasion')
            lines[-1]['align'] = occasion_style['align']
    add(('عنبيشن' if ar else 'ENBISHAN') if order.get('brand_id')=='enbishan' else ('كبز قهوة مختصة' if ar else 'KPS COFFEE'),True,True,role='brand')
    if fiscal:
        add(('إشعار دائن مبسط' if ar else 'Simplified credit note') if fiscal['kind']=='credit_note' else ('فاتورة ضريبية مبسطة' if ar else 'Simplified tax invoice'),True,True)
        if fiscal.get('status')!='issued':
            add('معاينة فقط — غير صالحة للإصدار الرسمي' if ar else 'PREVIEW ONLY — not officially issued',True,True)
        add('VAT: '+fiscal['seller']['vat_number'],False,True)
        add(fiscal['seller']['address'].replace('حفر الباطن,حفر الباطن,', 'حفر الباطن,'),False,True)
        if fiscal.get('original_number'): add(fiscal['original_number'],False,True)
    else:
        add(('فاتورة تحضير الطلب' if ar else 'Preparation receipt') if kind=='preparation' else ('فاتورة طلب' if ar else 'Order receipt'),True,True)
    add(('رقم الفاتورة' if ar else 'Invoice number') if fiscal else ('رقم الطلب' if ar else 'Order number'),False,True,role='caption')
    add(snap.get('order_reference') or order['order_no'],True,True,role='reference')
    add(('مركز التوصيل' if ar else 'Delivery Center') if kind!='preparation' and snap.get('order_source')=='delivery_center' else branch['name'],False,True)
    local=order['created_at'].astimezone(ZoneInfo('Asia/Riyadh'))
    add(('التاريخ: ' if ar else 'Date: ')+local.strftime('%Y-%m-%d'),False,True)
    add(('الوقت: ' if ar else 'Time: ')+local.strftime('%H:%M:%S' if fiscal else '%H:%M'),False,True)
    if order.get('service') == 'car':
        car = (order.get('customer') or {}).get('car') or {}
        for key, arabic, english in [('model', 'السيارة', 'Vehicle'),
                                     ('color', 'لون السيارة', 'Car color'),
                                     ('plate', 'لوحة السيارة', 'License plate')]:
            if car.get(key):
                row(arabic if ar else english, str(car[key]), True)
            elif key == 'plate':
                row(arabic if ar else english, 'لم يُدخل رقم اللوحة' if ar else 'No plate entered', True)
    add('────────────────────',False,True)
    for item in snap.get('items',[]):
        labels=(translations or {}).get(item.get('product_id'),{})
        def translated(name):
            return name if ar else (labels.get(name) or print_translations.DEFAULTS.get(name) or name)
        add(translated(item['name']),True,role='item')
        if kind=='order' and not free:
            row(('الكمية: ' if ar else 'Quantity: ')+str(item['quantity']),amount(item.get('line_total_halalas')))
        else:
            row('الكمية' if ar else 'Quantity',str(item['quantity']),True)
        for i,unit in enumerate(item.get('units',[]),1):
            for option in (unit.get('option_snapshot') or {}).values():
                choices=', '.join(translated(str(x.get('name',x.get('code','')))) for x in option.get('choices',[]))
                if choices: add(f"{i}: {translated(option.get('name',''))} — {choices}")
        if item.get('note'): add(('ملاحظة: ' if ar else 'Note: ')+item['note'],True)
        add('────────────────────',False,True)
    if kind=='preparation' and not free and (order.get('notes') or snap.get('notes')):add(('ملاحظات الطلب: ' if ar else 'Order notes: ')+(order.get('notes') or snap['notes']),True)
    if kind=='order' and not free:
        for key,label,en in [('subtotal_halalas','الأصناف','Subtotal'),('discount_halalas','الخصم','Discount'),('delivery_fee_halalas','التوصيل','Delivery'),('total_halalas','الإجمالي','Total')]:
            value=order.get(key,snap.get(key,0))
            if key=='discount_halalas' and not value:continue
            row(label if ar else en,amount(value),key=='total_halalas')
        if fiscal:
            row('الصافي قبل الضريبة' if ar else 'Net before VAT',amount(fiscal['net_halalas']))
            row('ضريبة القيمة المضافة 15%' if ar else 'VAT 15%',amount(fiscal['tax_halalas']))
        methods={'cash':('كاش','Cash'),'card':('شبكة','Card'),'split':('كاش وشبكة','Cash and card')}
        row('الدفع' if ar else 'Payment',('بدون تحصيل' if ar else 'No collection') if free else methods.get(order['payment_method'],(order['payment_method'],)*2)[0 if ar else 1])
        statuses={'not_required':('بدون تحصيل','No collection'),'paid':('تم التحصيل','Paid'),'unpaid':('لم يتم التحصيل','Unpaid'),'partially_refunded':('مسترد جزئيًا','Partially refunded'),'refunded':('مسترد','Refunded')}
        row('حالة التحصيل' if ar else 'Collection',statuses.get(order['payment_status'],('راجع حالة التحصيل بالنظام','Check collection status in system'))[0 if ar else 1],True)
        add('────────────────────',False,True)
        add(('معاينة فقط — غير صالحة للإصدار الرسمي' if ar else 'PREVIEW ONLY — not officially issued') if fiscal and fiscal.get('status')!='issued' else ('نسخة محفوظة' if ar else 'Archived invoice') if fiscal else ('فاتورة طلب — ليست فاتورة ضريبية' if ar else 'Order receipt — not a tax invoice'),False,True,role='caption')
        if order.get('id'):
            if fiscal:
                from .fiscal import qr as fiscal_qr
                qr=fiscal_qr(fiscal)
            else:
                qr=invoice_profiles.ordinary_qr(order)
            lines.append({'role':'qr','text':'','matrix':qr['matrix']})
    return {'language':language,'kind':kind,'lines':lines,'occasion_style':occasion_style,'brand_id':order.get('brand_id','kps')}


class JobBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    device_id:str
    kind:Literal['order','preparation','both']='order'
    language:Literal['ar','en']='en'
    reprint_reason:str=Field(default='',max_length=300)


def morning_preparer_branch(conn, user, order):
    """The approved two-action preparer workflow is limited to morning Branch 2."""
    if (user.get('role') != 'branch_manager' or order.get('brand_id') != 'kps'
            or order.get('service') != 'delivery'
            or (order.get('snapshot') or {}).get('delivery_period') != 'morning'):
        return None
    from . import morning_delivery
    morning_delivery.authorize_order(conn, user, order, preparation=True)
    branch = conn.execute('SELECT * FROM branches WHERE id=%s AND brand_id=%s',
                          (order['branch_id'], order['brand_id'])).fetchone()
    return branch if branch and branch['branch_no'] == 2 else None


def enqueue_morning_preparer_receipt(conn, user, order, branch, *, source='morning_preparer_acceptance'):
    """Called under the preparation transaction's order lock; never reprint.

    Queueing does not assert physical paper output. An ambiguous or unapproved
    destination fails acceptance atomically instead of choosing another branch.
    """
    from .order_duplicates import guard as duplicate_guard
    duplicate_guard(conn, order)
    previous = conn.execute("SELECT id FROM branch_print_jobs WHERE order_id=%s "
                            "AND kind='order' AND status<>'cancelled' LIMIT 1",
                            (order['id'],)).fetchone()
    if previous:
        return {'id': previous['id'], 'status': 'already_requested'}
    devices = conn.execute('SELECT * FROM branch_print_devices WHERE brand_id=%s AND branch_id=%s '
                           'AND revoked_at IS NULL AND token_hash IS NOT NULL AND approved_at IS NOT NULL '
                           'ORDER BY id FOR UPDATE', (order['brand_id'], branch['id'])).fetchall()
    if len(devices) != 1:
        raise db.error(409, 'MORNING_PRINTER_NOT_READY',
                       'يلزم وجود طابعة واحدة مرتبطة ومعتمدة في فرع 2 قبل قبول الطلب الصباحي.')
    device = devices[0]
    from .fiscal_issuance import original
    from .fiscal import print_order
    doc = original(conn, order['id'])
    printable = {**order, **print_order(doc, order)} if doc else order
    payload = receipt(printable, branch, 'order', 'en', print_translations.for_order(conn, order))
    payload['order_revision'] = order.get('amendment_revision', 0)
    identifier = db.new_id('print_')
    conn.execute('INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,order_id,kind,payload,requested_by,reprint_reason,created_at) '
                 'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())',
                 (identifier, order['brand_id'], branch['id'], device['id'], order['id'], 'order',
                  db.json(payload), user['id'], ''))
    db.audit(conn, user['id'], 'printer.job.queued', 'print_job', identifier,
             {'order_id': order['id'], 'order_branch_id': branch['id'], 'destination_branch_id': branch['id'],
              'device_id': device['id'], 'kind': 'order', 'source': source})
    return {'id': identifier, 'status': 'queued'}


@router.post('/orders/{order_id}')
def enqueue(order_id:str, body:JobBody, user=Depends(operators), idem:str|None=Header(None,alias='Idempotency-Key')):
    with db.tx() as conn:
        # Recheck authorization before replaying any saved result.
        order=conn.execute('SELECT * FROM orders WHERE id=%s AND brand_id=%s FOR UPDATE',(order_id,user['brand_id'])).fetchone()
        if not order or not auth.can_access_order(user,order,conn):
            raise db.error(404,'ORDER_NOT_FOUND','الطلب غير موجود ضمن صلاحياتك.')
        from .order_duplicates import guard as duplicate_guard
        duplicate_guard(conn, order)
        branch=scope(conn,user,order['branch_id'])
        device=conn.execute('SELECT * FROM branch_print_devices WHERE id=%s AND brand_id=%s AND revoked_at IS NULL AND token_hash IS NOT NULL FOR UPDATE',(body.device_id,user['brand_id'])).fetchone()
        if not device:raise db.error(409,'PRINT_NOT_PAIRED','اربط طابعة هذا الفرع أولًا.')
        destination=scope(conn,user,device['branch_id'])
        if not same_print_city(branch,destination):
            raise db.error(403,'PRINT_CITY_MISMATCH','اختر طابعة ضمن محافظة الطلب فقط.')
        if not device['approved_at']:
            raise db.error(409,'PRINT_TEST_REQUIRED','اختبر الطابعة وأكد خروج الورقة قبل اعتماد طباعة الطلبات.')
        translations=print_translations.for_order(conn,order)
        def perform():
            identifiers=[]
            for kind in (('order','preparation') if body.kind=='both' else (body.kind,)):
                previous=conn.execute("SELECT id FROM branch_print_jobs WHERE order_id=%s AND kind=%s AND status<>'cancelled' LIMIT 1",(order_id,kind)).fetchone()
                if previous and not body.reprint_reason.strip():raise db.error(409,'PRINT_REPRINT_REASON','سبق إرسال هذه الفاتورة؛ تحقق من الطابعة وأدخل سبب إعادة الطباعة لمنع التكرار.')
                identifier=db.new_id('print_')
                printable=order
                if kind=='order':
                    from .fiscal_issuance import original
                    from .fiscal import print_order
                    doc=original(conn,order_id)
                    if doc: printable={**order,**print_order(doc,order)}
                payload=receipt(printable,branch,kind,body.language,translations)
                payload['order_revision']=order.get('amendment_revision',0)
                if destination['id'] != branch['id']:
                    payload['lines'].insert(0,{'text':('وجهة الطباعة: ' if body.language=='ar' else 'Print destination: ')+destination['name'],'bold':True,'center':True})
                if previous:payload['lines'].insert(0,{'text':('إعادة طباعة' if body.language=='ar' else 'REPRINT'),'bold':True,'center':True})
                conn.execute('INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,order_id,kind,payload,requested_by,reprint_reason,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())',(identifier,user['brand_id'],device['branch_id'],body.device_id,order_id,kind,db.json(payload),user['id'],body.reprint_reason))
                db.audit(conn,user['id'],'printer.job.queued','print_job',identifier,{'order_id':order_id,'order_branch_id':order['branch_id'],'destination_branch_id':device['branch_id'],'device_id':body.device_id,'kind':kind,'reprint_reason':body.reprint_reason})
                identifiers.append(identifier)
            return {'id':identifiers[0],'ids':identifiers,'status':'queued'}
        return db.idempotent(conn,user['id'],idem,'printer.enqueue',{'order_id':order_id,**body.model_dump()},perform)


def output_operator(user=Depends(auth.require_roles('owner','delivery_manager','branch_manager'))):
    permission='orders_prepare' if user['role']=='branch_manager' else 'orders_print'
    if not auth.has_permission(user,permission):
        raise db.error(403,'PRINT_OUTPUT_PERMISSION','تأكيد طباعة الطلب غير مصرح.')
    return user


@router.get('/orders/{order_id}/output')
def order_output_status(order_id:str,user=Depends(output_operator)):
    from .orders import scoped_order
    with db.tx() as conn:
        order=scoped_order(conn,order_id,user)
        rows=conn.execute("""SELECT j.id,j.status,j.created_at,j.finished_at,
            (SELECT e.created_at FROM order_events e WHERE e.order_id=j.order_id
               AND e.event_type='order.print_output_confirmed' AND e.details->>'job_id'=j.id
               ORDER BY e.created_at LIMIT 1) confirmed_at
            FROM branch_print_jobs j WHERE j.order_id=%s AND j.branch_id=%s AND j.kind='order'
              AND coalesce((j.payload->>'order_revision')::int,0)=%s
            ORDER BY j.created_at DESC LIMIT 10""",(order_id,order['branch_id'],order.get('amendment_revision',0))).fetchall()
        return {'jobs':rows,'version':order['version'],'branch_id':order['branch_id']}


class OutputConfirmation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id:str=Field(min_length=1,max_length=100)
    version:StrictInt=Field(ge=1)
    paper_confirmed:StrictBool


@router.post('/orders/{order_id}/output')
def confirm_order_output(order_id:str,body:OutputConfirmation,user=Depends(output_operator),
                         idem:str|None=Header(None,alias='Idempotency-Key')):
    from .orders import scoped_order
    with db.tx() as conn:
        scoped_order(conn,order_id,user)
        def perform():
            order=scoped_order(conn,order_id,user,lock=True)
            if not body.paper_confirmed:
                raise db.error(422,'PRINT_OUTPUT_CONFIRMATION','أكد خروج فاتورة الطلب من طابعة الفرع فعليًا.')
            if order['completed_at'] or order['preparation_status'] in ('cancelled','rejected','completed') or order['delivery_status'] in ('cancelled','delivered'):
                raise db.error(409,'PRINT_OUTPUT_CLOSED','الطلب منتهٍ؛ لا تغيّر سجل تأكيد الطباعة.')
            if order['version']!=body.version:
                raise db.error(409,'ORDER_VERSION_CONFLICT','تغير الطلب؛ حدّث التفاصيل قبل تأكيد الطباعة.')
            job=conn.execute('SELECT * FROM branch_print_jobs WHERE id=%s AND order_id=%s FOR UPDATE',(body.job_id,order_id)).fetchone()
            if not job or job['brand_id']!=order['brand_id'] or job['branch_id']!=order['branch_id']:
                raise db.error(404,'PRINT_OUTPUT_JOB','فاتورة فرع تنفيذ الطلب غير متاحة.')
            if job['kind']!='order' or job['status']!='submitted' or job['payload'].get('order_revision',0)!=order.get('amendment_revision',0):
                raise db.error(409,'PRINT_OUTPUT_NOT_SUBMITTED','انتظر إرسال فاتورة النسخة الحالية إلى طابعة فرع التنفيذ، ثم أكد خروج الورقة.')
            if not conn.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='order.print_output_confirmed' AND details->>'job_id'=%s",(order_id,body.job_id)).fetchone():
                details={'job_id':body.job_id,'branch_id':order['branch_id'],
                         'revision':order.get('amendment_revision',0),'source':'operator_paper_confirmation'}
                db.event(conn,order_id,user['id'],'order.print_output_confirmed',details)
                db.audit(conn,user['id'],'printer.order_output_confirmed','order',order_id,details)
            return {'confirmed':True,'job_id':body.job_id}
        return db.idempotent(conn,user['id'],idem,'printer.output:'+order_id,body.model_dump(),perform)


class AlertAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['confirmed', 'reprint', 'escalated']
    reason: str = Field(min_length=1, max_length=300)
    paper_checked: StrictBool = False


@router.post('/jobs/{identifier}/resolve-alert')
def resolve_print_alert(identifier: str, body: AlertAction, user=Depends(operators),
                        idem: str | None = Header(None, alias='Idempotency-Key')):
    if not auth.has_permission(user, 'orders_print'):
        raise db.error(403, 'PRINT_ALERT_PERMISSION', 'معالجة تنبيه الطباعة غير مصرح بها.')
    if not body.reason.strip():
        raise db.error(422, 'PRINT_ALERT_REASON', 'سبب الإجراء مطلوب.')
    if body.action in ('confirmed', 'reprint') and not body.paper_checked:
        raise db.error(422, 'PRINT_ALERT_CHECK', 'تحقق من خروج الورقة لدى الفرع أولًا.')
    with db.tx() as conn:
        job = conn.execute('SELECT * FROM branch_print_jobs WHERE id=%s AND brand_id=%s',
                           (identifier, user['brand_id'])).fetchone()
        if not job:
            raise db.error(404, 'PRINT_JOB_NOT_FOUND', 'أمر الطباعة غير موجود.')
        scope(conn, user, job['branch_id'])
        order = None
        if job['order_id']:
            from .orders import scoped_order
            order = scoped_order(conn, job['order_id'], user, lock=True)
        def perform():
            current = conn.execute('SELECT * FROM branch_print_jobs WHERE id=%s FOR UPDATE', (identifier,)).fetchone()
            resolution = current['alert_resolution']
            if resolution.get('action') in ('confirmed', 'reprint'):
                raise db.error(409, 'PRINT_ALERT_RESOLVED', 'تمت معالجة التنبيه؛ حدّث الصفحة.')
            if current['status'] == 'cancelled':
                raise db.error(409, 'PRINT_ALERT_CANCELLED', 'مهمة الطباعة ملغاة.')
            result = {'action': body.action, 'reason': body.reason.strip(), 'actor_id': user['id'],
                      'actor_name': user.get('display_name', ''), 'at': datetime.now(timezone.utc).isoformat()}
            if body.action == 'reprint':
                if order:
                    from .order_duplicates import guard as duplicate_guard
                    duplicate_guard(conn,order)
                if current['status'] == 'claimed':
                    raise db.error(409, 'PRINT_JOB_IN_FLIGHT', 'المهمة ما زالت لدى برنامج الطباعة؛ تحقق من انتهائها قبل إعادة الطباعة.')
                if not order or order['completed_at'] or order['preparation_status'] in ('cancelled','rejected','completed') or order['delivery_status'] in ('cancelled','delivered'):
                    raise db.error(409, 'PRINT_ORDER_CLOSED', 'لا يمكن إعادة طباعة هذه المهمة؛ راجع الطلب أو اختبار الطابعة.')
                if current['payload'].get('order_revision', 0) != order.get('amendment_revision', 0):
                    raise db.error(409, 'PRINT_OLD_REVISION', 'تغير الطلب؛ اطبع النسخة الحالية من تفاصيله.')
                device = conn.execute('SELECT * FROM branch_print_devices WHERE id=%s AND brand_id=%s AND revoked_at IS NULL AND approved_at IS NOT NULL AND token_hash IS NOT NULL FOR UPDATE', (current['device_id'], user['brand_id'])).fetchone()
                if not device:
                    raise db.error(409, 'PRINT_NOT_PAIRED', 'راجع ربط واعتماد طابعة الفرع أولًا.')
                payload = dict(current['payload'])
                payload['lines'] = [{'text': 'إعادة طباعة' if payload.get('language') == 'ar' else 'REPRINT', 'bold': True, 'center': True}] + payload['lines']
                replacement = db.new_id('print_')
                conn.execute('INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,order_id,kind,payload,requested_by,reprint_reason,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())', (replacement,current['brand_id'],current['branch_id'],current['device_id'],current['order_id'],current['kind'],db.json(payload),user['id'],body.reason.strip()))
                result['replacement_id'] = replacement
            if body.action in ('confirmed','reprint') and current['status'] == 'queued':
                conn.execute("UPDATE branch_print_jobs SET status='cancelled',finished_at=now() WHERE id=%s", (identifier,))
            from .employee_ticket_workflow import enabled as factual_workflow_enabled
            if body.action=='escalated' and factual_workflow_enabled():
                from .employee_tickets import create_linked_followup
                result['followup_ticket_id']=create_linked_followup(conn,user,'print_job',identifier,body.reason.strip())
            conn.execute('UPDATE branch_print_jobs SET alert_resolution=%s WHERE id=%s', (db.json(result),identifier))
            db.audit(conn,user['id'],'printer.alert.'+body.action,'print_job',identifier,result)
            if order:
                db.event(conn,order['id'],user['id'],'printer.alert.'+body.action,{'job_id':identifier,**result})
            return result
        return db.idempotent(conn,user['id'],idem,'printer.alert:'+identifier,body.model_dump(),perform)


@router.get('/jobs')
def jobs(user=Depends(printer_admin)):
    with db.tx() as conn:
        rows=conn.execute('SELECT id,branch_id,device_id,order_id,kind,status,created_at,claimed_at,finished_at,reprint_reason FROM branch_print_jobs WHERE brand_id=%s ORDER BY created_at DESC LIMIT 100',(user['brand_id'],)).fetchall()
        rows=[r for r in rows if can_access_branch(user, r['branch_id'])]
        if user.get('_morning_manager_context'):
            from .morning_manager_context import order_allowed
            duty=conn.execute('SELECT opened_at FROM shifts WHERE id=%s',(user['_morning_manager_context']['shift_id'],)).fetchone()
            rows=[r for r in rows if (order_allowed(user,conn.execute('SELECT * FROM orders WHERE id=%s',(r['order_id'],)).fetchone(),conn)
                                     if r.get('order_id') else r['created_at']>=duty['opened_at'])]
        return {'jobs':rows}


def require_brand_logo_capability(device, request):
    # KPS and legacy KPS jobs keep their existing protocol. Enbishan must never
    # reach an older executable that would print the KPS logo unconditionally.
    capabilities = (request.headers.get('X-KPS-Print-Capabilities', '') if request else '').split(',')
    if device.get('brand_id') == 'enbishan' and 'brand-logo-v1' not in {c.strip() for c in capabilities}:
        raise db.error(409, 'PRINT_BRIDGE_UPDATE_REQUIRED', 'حدّث برنامج الطباعة من إعداد الفرع لطباعة شعار عنبيشن الصحيح.')


@router.post('/bridge/next')
def claim(request: Request = None, device=Depends(bridge)):
    require_brand_logo_capability(device, request)
    with db.tx() as conn:
        live=conn.execute('SELECT id,approved_at FROM branch_print_devices WHERE id=%s AND revoked_at IS NULL FOR UPDATE',(device['id'],)).fetchone()
        if not live:raise db.error(401,'PRINT_DEVICE_AUTH','ألغي ربط الجهاز.')
        conn.execute('UPDATE branch_print_devices SET last_seen_at=now(),last_api_host=coalesce(%s,last_api_host),bridge_version=coalesce(%s,bridge_version) WHERE id=%s',
                     (request.url.hostname if request else None,request.headers.get('X-KPS-Bridge-Version','')[:60] if request else None,device['id']))
        rows=conn.execute("SELECT * FROM branch_print_jobs WHERE device_id=%s AND status='queued' AND (kind='test' OR %s) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 100",(device['id'],bool(live['approved_at']))).fetchall()
        for row in rows:
            if row['order_id']:
                # Enqueue locks order before device. Skip an occupied order here
                # rather than invert that lock order and wait on it.
                order=conn.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE SKIP LOCKED',(row['order_id'],)).fetchone()
                if not order or order['preparation_status'] in ('cancelled','rejected'):
                    continue
                from .order_duplicates import review as duplicate_review
                if duplicate_review(conn,order)['status']=='pending':
                    continue
            conn.execute("UPDATE branch_print_jobs SET status='claimed',claimed_at=now() WHERE id=%s",(row['id'],))
            return {'job':{'id':row['id'],'payload':{**row['payload'],'brand_id':row['brand_id']},'paper_mm':device['paper_mm']}}
        return {'job':None}


class AckBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status:Literal['submitted','uncertain']


@router.post('/bridge/jobs/{identifier}/ack')
def acknowledge(identifier:str,body:AckBody,request: Request = None,device=Depends(bridge)):
    with db.tx() as conn:
        row=conn.execute('SELECT status FROM branch_print_jobs WHERE id=%s AND device_id=%s FOR UPDATE',(identifier,device['id'])).fetchone()
        if not row:raise db.error(404,'PRINT_JOB_NOT_FOUND','أمر الطباعة غير موجود.')
        if row['status'] not in {'claimed',body.status}:raise db.error(409,'PRINT_JOB_STATE','حالة أمر الطباعة لا تسمح بهذا الإجراء.')
        # Capture the first acknowledgement endpoint only. A replay through a
        # different host must not re-label historic paper/acceptance evidence,
        # including legacy jobs whose provenance is unknown (NULL).
        conn.execute("UPDATE branch_print_jobs SET ack_api_host=CASE WHEN status='claimed' THEN %s ELSE ack_api_host END,status=%s,finished_at=coalesce(finished_at,now()) WHERE id=%s",
                     (request.url.hostname if request else None,body.status,identifier))
        return {'id':identifier,'status':body.status}


@router.get('/windows-bridge.zip')
def download_bridge(user=Depends(printer_admin)):
    return Response(bridge_bundle(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="KPS-Windows-Print-Bridge.zip"','Cache-Control':'no-store'})


def bridge_origin():
    # Trusted deployment configuration only; never derive pairing endpoints from
    # request Host, Origin or forwarded headers. Never downgrade bridge secrets.
    value=os.getenv('PRINT_BRIDGE_ORIGIN','https://order.kps.sa').strip()
    try:
        parsed=urlsplit(value)
        valid=(parsed.scheme=='https' and parsed.hostname and not parsed.username
               and not parsed.password and parsed.port in (None,443)
               and parsed.path in ('','/') and not parsed.query and not parsed.fragment)
    except ValueError:
        valid=False
    if not valid:
        raise db.error(503,'PRINT_ORIGIN_INVALID','عنوان برنامج الطباعة يجب أن يكون دومين HTTPS رسميًا دون مسارات.')
    return value.rstrip('/')


@router.get('/migration-status')
def migration_status(user=Depends(printer_admin)):
    target=urlsplit(bridge_origin()).hostname
    with db.tx() as conn:
        branches=conn.execute('SELECT id,name FROM branches WHERE brand_id=%s ORDER BY name',
                              (user['brand_id'],)).fetchall()
        rows=conn.execute('SELECT id,branch_id,label,printer_name,last_seen_at,revoked_at,approved_at,approved_api_host,last_api_host,bridge_version '
                          'FROM branch_print_devices WHERE brand_id=%s ORDER BY created_at DESC',
                          (user['brand_id'],)).fetchall()
    branches=[b for b in branches if can_access_branch(user, b['id'])]
    result=[]
    for branch in branches:
        active=[r for r in rows if r['branch_id']==branch['id'] and not r['revoked_at']]
        connected=[r for r in active if connection_status(r)=='online']
        moved=[r for r in connected if r['last_api_host']==target and r['approved_api_host']==target and r['approved_at']]
        # Unknown/old/offline registrations remain blockers until an operator
        # verifies/retires them explicitly. New pairing never revokes old devices.
        remaining=[r for r in active if r['last_api_host']!=target or r['approved_api_host']!=target or not r['approved_at'] or connection_status(r)!='online']
        result.append({**branch,'migrated':bool(moved) and not remaining,
                       'connected_devices':len(connected),
                       'approved_devices':sum(bool(r['approved_at']) for r in active),
                       'active_devices':len(active),'needs_attention':not moved or bool(remaining),
                       'devices':[{**r,'connection_status':connection_status(r)} for r in active]})
    return {'target_origin':bridge_origin(),'branches':result,
            'migrated_branches':sum(b['migrated'] for b in result),
            'remaining_branches':sum(not b['migrated'] for b in result),
            'public_render_shutdown_authorized':False}


def bridge_bundle(setup=None):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as bundle:
        for name in ('KpsPrintBridge.cs','Start.cmd','README.txt'):
            bundle.write(db.ROOT/'tools'/'windows-print-bridge'/name,name)
        bundle.write(db.ROOT/'public'/'assets'/'kps-receipt-logo.png','logo.png')
        bundle.write(db.ROOT/'public'/'assets'/'enbishan-original.jpeg','enbishan-logo.jpeg')
        bundle.writestr('endpoint.json',json.dumps({'origin':bridge_origin()},ensure_ascii=False))
        if setup:
            bundle.writestr('setup.json',json.dumps(setup,ensure_ascii=False))
    return output.getvalue()
