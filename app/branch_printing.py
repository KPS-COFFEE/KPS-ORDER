"""Branch-bound outbound print queues. Spool submission is not proof of paper output."""
import io
import base64
import json
import secrets
import zipfile
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import auth, db, print_translations, invoice_profiles

router = APIRouter(prefix='/api/printing', tags=['branch-printing'])
operators = auth.require_roles('owner', 'delivery_manager')


def can_manage_printers(user):
    return user['role'] in {'owner','delivery_manager'} or auth.has_permission(user,'branch_printers_manage')


def printer_admin(user=Depends(auth.current_user)):
    if not can_manage_printers(user):
        raise db.error(403,'ROLE_FORBIDDEN','إدارة الطابعات تتطلب صلاحية يمنحها المالك.')
    return user



def scope(conn, user, branch_id):
    branch = conn.execute('SELECT * FROM branches WHERE id=%s AND brand_id=%s',
                          (branch_id, user['brand_id'])).fetchone()
    if not branch or (user['role'] != 'owner' and branch_id not in user.get('branch_ids', [])):
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


@router.get('/devices')
def devices(user=Depends(printer_admin)):
    with db.tx() as conn:
        rows = conn.execute('SELECT id,branch_id,label,printer_name,paper_mm,last_seen_at,revoked_at,approved_at,acceptance_job_id FROM branch_print_devices WHERE brand_id=%s ORDER BY created_at DESC',(user['brand_id'],)).fetchall()
        visible=[r for r in rows if user['role']=='owner' or r['branch_id'] in user.get('branch_ids',[])]
        for row in visible:
            row['connection_status']=connection_status(row)
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
    pair=pair_device(body,user)
    config={'pairing_code':pair['pairing_code'],'branch_name':branch['name'],'branch_id':body.branch_id}
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
                         (job_id,user['brand_id'],device['branch_id'],identifier,db.json({'language':'ar','kind':'test','lines':lines}),user['id']))
            conn.execute('UPDATE branch_print_devices SET acceptance_job_id=%s,approved_at=NULL,approved_by=NULL WHERE id=%s',(job_id,identifier))
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
        job=conn.execute("SELECT status FROM branch_print_jobs WHERE id=%s AND device_id=%s AND kind='test'",(body.job_id,identifier)).fetchone()
        if not body.paper_confirmed or body.job_id!=device['acceptance_job_id'] or not job or job['status']!='submitted':
            raise db.error(409,'PRINT_TEST_NOT_CONFIRMED','انتظر إرسال الاختبار وتأكد من خروج الورقة وصحة الشعار والعربية والمقاس.')
        if not device['approved_at']:
            conn.execute('UPDATE branch_print_devices SET approved_at=now(),approved_by=%s WHERE id=%s',(user['id'],identifier))
            db.audit(conn,user['id'],'printer.test.approved','printer',identifier,{'job_id':body.job_id,'physical_output_confirmed_by_operator':True})
        return {'approved':True}


def receipt(order, branch, kind, language, translations=None):
    ar = language=='ar'
    snap=order['snapshot']; lines=[]
    def add(text,bold=False,center=False,role='',label='',value=''):
        lines.append({'text':str(text),'bold':bold,'center':center,
                      'role':role,'label':label,'value':value})
    def row(label,value,bold=False):
        add(label+': '+value,bold,role='row',label=label,value=value)
    def amount(value): return f'{int(value or 0)/100:.2f} '+('ر.س' if ar else 'SAR')
    add('كبز قهوة مختصة' if ar else 'KPS COFFEE',True,True,role='brand')
    add(('فاتورة تحضير الطلب' if ar else 'Preparation receipt') if kind=='preparation' else ('فاتورة طلب' if ar else 'Order receipt'),True,True)
    add('رقم الطلب' if ar else 'Order number',False,True,role='caption')
    add(snap.get('order_reference') or order['order_no'],True,True,role='reference')
    add(branch['name'],False,True)
    local=order['created_at'].astimezone(ZoneInfo('Asia/Riyadh'))
    add(('التاريخ: ' if ar else 'Date: ')+local.strftime('%Y-%m-%d'),False,True)
    add(('الوقت: ' if ar else 'Time: ')+local.strftime('%H:%M')+(' — الرياض' if ar else ' — Riyadh'),False,True)
    add('────────────────────',False,True)
    for item in snap.get('items',[]):
        labels=(translations or {}).get(item.get('product_id'),{})
        def translated(name):
            return name if ar else (labels.get(name) or print_translations.DEFAULTS.get(name) or name)
        add(translated(item['name']),True,role='item')
        if kind=='order':
            row(('الكمية: ' if ar else 'Quantity: ')+str(item['quantity']),amount(item.get('line_total_halalas')))
        else:
            row('الكمية' if ar else 'Quantity',str(item['quantity']),True)
        for i,unit in enumerate(item.get('units',[]),1):
            for option in (unit.get('option_snapshot') or {}).values():
                choices=', '.join(translated(str(x.get('name',x.get('code','')))) for x in option.get('choices',[]))
                if choices: add(f"{i}: {translated(option.get('name',''))} — {choices}")
        if item.get('note'): add(('ملاحظة: ' if ar else 'Note: ')+item['note'],True)
        add('────────────────────',False,True)
    if order.get('notes') or snap.get('notes'):add(('ملاحظات الطلب: ' if ar else 'Order notes: ')+(order.get('notes') or snap['notes']),True)
    if kind=='order':
        for key,label,en in [('subtotal_halalas','الأصناف','Subtotal'),('discount_halalas','الخصم','Discount'),('delivery_fee_halalas','التوصيل','Delivery'),('total_halalas','الإجمالي','Total')]:
            value=order.get(key,snap.get(key,0))
            if key=='discount_halalas' and not value:continue
            row(label if ar else en,amount(value),key=='total_halalas')
        methods={'cash':('كاش','Cash'),'card':('شبكة','Card'),'split':('كاش وشبكة','Cash and card')}
        row('الدفع' if ar else 'Payment',methods.get(order['payment_method'],(order['payment_method'],)*2)[0 if ar else 1])
        statuses={'paid':('تم التحصيل','Paid'),'unpaid':('لم يتم التحصيل','Unpaid'),'partially_refunded':('مسترد جزئيًا','Partially refunded'),'refunded':('مسترد','Refunded')}
        row('حالة التحصيل' if ar else 'Collection',statuses.get(order['payment_status'],('راجع حالة التحصيل بالنظام','Check collection status in system'))[0 if ar else 1],True)
        add('────────────────────',False,True)
        add('فاتورة طلب — ليست فاتورة ضريبية' if ar else 'Order receipt — not a tax invoice',False,True,role='caption')
        if order.get('id'):
            qr=invoice_profiles.ordinary_qr(order)
            lines.append({'role':'qr','text':'','matrix':qr['matrix']})
    return {'language':language,'kind':kind,'lines':lines}


class JobBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    device_id:str
    kind:Literal['order','preparation','both']='order'
    language:Literal['ar','en']='en'
    reprint_reason:str=Field(default='',max_length=300)


@router.post('/orders/{order_id}')
def enqueue(order_id:str, body:JobBody, user=Depends(operators), idem:str|None=Header(None,alias='Idempotency-Key')):
    with db.tx() as conn:
        # Recheck authorization before replaying any saved result.
        order=conn.execute('SELECT * FROM orders WHERE id=%s AND brand_id=%s FOR UPDATE',(order_id,user['brand_id'])).fetchone()
        if not order:raise db.error(404,'ORDER_NOT_FOUND','الطلب غير موجود.')
        branch=scope(conn,user,order['branch_id'])
        device=conn.execute('SELECT * FROM branch_print_devices WHERE id=%s AND brand_id=%s AND revoked_at IS NULL AND token_hash IS NOT NULL FOR UPDATE',(body.device_id,user['brand_id'])).fetchone()
        if not device:raise db.error(409,'PRINT_NOT_PAIRED','اربط طابعة هذا الفرع أولًا.')
        destination=scope(conn,user,device['branch_id'])
        if not device['approved_at']:
            raise db.error(409,'PRINT_TEST_REQUIRED','اختبر الطابعة وأكد خروج الورقة قبل اعتماد طباعة الطلبات.')
        translations=print_translations.for_order(conn,order)
        def perform():
            identifiers=[]
            for kind in (('order','preparation') if body.kind=='both' else (body.kind,)):
                previous=conn.execute("SELECT id FROM branch_print_jobs WHERE order_id=%s AND kind=%s AND status<>'cancelled' LIMIT 1",(order_id,kind)).fetchone()
                if previous and not body.reprint_reason.strip():raise db.error(409,'PRINT_REPRINT_REASON','سبق إرسال هذه الفاتورة؛ تحقق من الطابعة وأدخل سبب إعادة الطباعة لمنع التكرار.')
                identifier=db.new_id('print_');payload=receipt(order,branch,kind,body.language,translations)
                if destination['id'] != branch['id']:
                    payload['lines'].insert(0,{'text':('وجهة الطباعة: ' if body.language=='ar' else 'Print destination: ')+destination['name'],'bold':True,'center':True})
                if previous:payload['lines'].insert(0,{'text':('إعادة طباعة' if body.language=='ar' else 'REPRINT'),'bold':True,'center':True})
                conn.execute('INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,order_id,kind,payload,requested_by,reprint_reason,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())',(identifier,user['brand_id'],device['branch_id'],body.device_id,order_id,kind,db.json(payload),user['id'],body.reprint_reason))
                db.audit(conn,user['id'],'printer.job.queued','print_job',identifier,{'order_id':order_id,'order_branch_id':order['branch_id'],'destination_branch_id':device['branch_id'],'device_id':body.device_id,'kind':kind,'reprint_reason':body.reprint_reason})
                identifiers.append(identifier)
            return {'id':identifiers[0],'ids':identifiers,'status':'queued'}
        return db.idempotent(conn,user['id'],idem,'printer.enqueue',{'order_id':order_id,**body.model_dump()},perform)


@router.get('/jobs')
def jobs(user=Depends(printer_admin)):
    with db.tx() as conn:
        rows=conn.execute('SELECT id,branch_id,device_id,order_id,kind,status,created_at,claimed_at,finished_at,reprint_reason FROM branch_print_jobs WHERE brand_id=%s ORDER BY created_at DESC LIMIT 100',(user['brand_id'],)).fetchall()
        return {'jobs':[r for r in rows if user['role']=='owner' or r['branch_id'] in user.get('branch_ids',[])]}


@router.post('/bridge/next')
def claim(device=Depends(bridge)):
    with db.tx() as conn:
        live=conn.execute('SELECT id,approved_at FROM branch_print_devices WHERE id=%s AND revoked_at IS NULL FOR UPDATE',(device['id'],)).fetchone()
        if not live:raise db.error(401,'PRINT_DEVICE_AUTH','ألغي ربط الجهاز.')
        conn.execute('UPDATE branch_print_devices SET last_seen_at=now() WHERE id=%s',(device['id'],))
        row=conn.execute("SELECT * FROM branch_print_jobs WHERE device_id=%s AND status='queued' AND (kind='test' OR %s) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1",(device['id'],bool(live['approved_at']))).fetchone()
        if not row:return {'job':None}
        conn.execute("UPDATE branch_print_jobs SET status='claimed',claimed_at=now() WHERE id=%s",(row['id'],))
        return {'job':{'id':row['id'],'payload':row['payload'],'paper_mm':device['paper_mm']}}


class AckBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status:Literal['submitted','uncertain']


@router.post('/bridge/jobs/{identifier}/ack')
def acknowledge(identifier:str,body:AckBody,device=Depends(bridge)):
    with db.tx() as conn:
        row=conn.execute('SELECT status FROM branch_print_jobs WHERE id=%s AND device_id=%s FOR UPDATE',(identifier,device['id'])).fetchone()
        if not row:raise db.error(404,'PRINT_JOB_NOT_FOUND','أمر الطباعة غير موجود.')
        if row['status'] not in {'claimed',body.status}:raise db.error(409,'PRINT_JOB_STATE','حالة أمر الطباعة لا تسمح بهذا الإجراء.')
        conn.execute('UPDATE branch_print_jobs SET status=%s,finished_at=coalesce(finished_at,now()) WHERE id=%s',(body.status,identifier))
        return {'id':identifier,'status':body.status}


@router.get('/windows-bridge.zip')
def download_bridge(user=Depends(printer_admin)):
    return Response(bridge_bundle(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="KPS-Windows-Print-Bridge.zip"','Cache-Control':'no-store'})


def bridge_bundle(setup=None):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as bundle:
        for name in ('KpsPrintBridge.cs','Start.cmd','README.txt'):
            bundle.write(db.ROOT/'tools'/'windows-print-bridge'/name,name)
        bundle.write(db.ROOT/'public'/'assets'/'kps-receipt-logo.png','logo.png')
        if setup:
            bundle.writestr('setup.json',json.dumps(setup,ensure_ascii=False))
    return output.getvalue()
