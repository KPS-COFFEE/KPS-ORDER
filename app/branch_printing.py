"""Branch-bound outbound print queues. Spool submission is not proof of paper output."""
import io
import secrets
import zipfile
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from . import auth, db

router = APIRouter(prefix='/api/printing', tags=['branch-printing'])
operators = auth.require_roles('owner', 'delivery_manager')


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
def pair_device(body: PairBody, user=Depends(operators)):
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
        if not creator or creator['role'] not in {'owner','delivery_manager'}:
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
def devices(user=Depends(operators)):
    with db.tx() as conn:
        rows = conn.execute('SELECT id,branch_id,label,printer_name,paper_mm,last_seen_at,revoked_at FROM branch_print_devices WHERE brand_id=%s ORDER BY created_at DESC',(user['brand_id'],)).fetchall()
        return {'devices':[r for r in rows if user['role']=='owner' or r['branch_id'] in user.get('branch_ids',[])]}


class ChangeBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(min_length=1, max_length=300)


@router.post('/devices/{identifier}/revoke')
def revoke(identifier: str, body: ChangeBody, user=Depends(operators)):
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


def receipt(order, branch, kind, language):
    ar = language=='ar'
    snap=order['snapshot']; lines=[]
    def add(text,bold=False,center=False):
        lines.append({'text':str(text),'bold':bold,'center':center})
    def amount(value): return f'{int(value or 0)/100:.2f} '+('ر.س' if ar else 'SAR')
    add('كبز قهوة مختصة' if ar else 'KPS COFFEE',True,True)
    add(('فاتورة تحضير الطلب' if ar else 'Preparation receipt') if kind=='preparation' else ('فاتورة طلب' if ar else 'Order receipt'),True,True)
    add(snap.get('order_reference') or order['order_no'],True,True)
    add(branch['name'],False,True)
    add(order['created_at'].astimezone(ZoneInfo('Asia/Riyadh')).strftime('%Y-%m-%d %H:%M'),False,True)
    for item in snap.get('items',[]):
        add(f"{item['quantity']} × {item['name']}"+(('  '+amount(item.get('line_total_halalas'))) if kind=='order' else ''),True)
        for i,unit in enumerate(item.get('units',[]),1):
            for option in (unit.get('option_snapshot') or {}).values():
                choices=', '.join(str(x.get('name',x.get('code',''))) for x in option.get('choices',[]))
                if choices: add(f"{i}: {option.get('name','')} — {choices}")
        if item.get('note'): add(item['note'],True)
    if order.get('notes') or snap.get('notes'):add(order.get('notes') or snap['notes'],True)
    if kind=='order':
        for key,label,en in [('subtotal_halalas','الأصناف','Subtotal'),('discount_halalas','الخصم','Discount'),('delivery_fee_halalas','التوصيل','Delivery'),('total_halalas','الإجمالي','Total')]:
            value=order.get(key,snap.get(key,0))
            if key=='discount_halalas' and not value:continue
            add((label if ar else en)+': '+amount(value),key=='total_halalas')
        methods={'cash':('كاش','Cash'),'card':('شبكة','Card'),'split':('كاش وشبكة','Cash and card')}
        add(('الدفع: ' if ar else 'Payment: ')+methods.get(order['payment_method'],(order['payment_method'],)*2)[0 if ar else 1])
        add(('محصل' if ar else 'Paid') if order['payment_status']=='paid' else ('راجع حالة التحصيل: ' if ar else 'Collection status: ')+order['payment_status'])
        add('فاتورة طلب — ليست فاتورة ضريبية' if ar else 'Order receipt — not a tax invoice',False,True)
    return {'language':language,'kind':kind,'lines':lines}


class JobBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    device_id:str
    kind:Literal['order','preparation']='order'
    language:Literal['ar','en']='ar'
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
        def perform():
            previous=conn.execute("SELECT id FROM branch_print_jobs WHERE order_id=%s AND kind=%s AND status<>'cancelled' LIMIT 1",(order_id,body.kind)).fetchone()
            if previous and not body.reprint_reason.strip():raise db.error(409,'PRINT_REPRINT_REASON','سبق إرسال هذه الفاتورة؛ تحقق من الطابعة وأدخل سبب إعادة الطباعة لمنع التكرار.')
            identifier=db.new_id('print_');payload=receipt(order,branch,body.kind,body.language)
            if destination['id'] != branch['id']:
                payload['lines'].insert(0,{'text':('وجهة الطباعة: ' if body.language=='ar' else 'Print destination: ')+destination['name'],'bold':True,'center':True})
            if previous:payload['lines'].insert(0,{'text':'إعادة طباعة / REPRINT','bold':True,'center':True})
            conn.execute('INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,order_id,kind,payload,requested_by,reprint_reason) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',(identifier,user['brand_id'],device['branch_id'],body.device_id,order_id,body.kind,db.json(payload),user['id'],body.reprint_reason))
            db.audit(conn,user['id'],'printer.job.queued','print_job',identifier,{'order_id':order_id,'order_branch_id':order['branch_id'],'destination_branch_id':device['branch_id'],'device_id':body.device_id,'kind':body.kind,'reprint_reason':body.reprint_reason})
            return {'id':identifier,'status':'queued'}
        return db.idempotent(conn,user['id'],idem,'printer.enqueue',{'order_id':order_id,**body.model_dump()},perform)


@router.get('/jobs')
def jobs(user=Depends(operators)):
    with db.tx() as conn:
        rows=conn.execute('SELECT id,branch_id,device_id,order_id,kind,status,created_at,claimed_at,finished_at,reprint_reason FROM branch_print_jobs WHERE brand_id=%s ORDER BY created_at DESC LIMIT 100',(user['brand_id'],)).fetchall()
        return {'jobs':[r for r in rows if user['role']=='owner' or r['branch_id'] in user.get('branch_ids',[])]}


@router.post('/bridge/next')
def claim(device=Depends(bridge)):
    with db.tx() as conn:
        live=conn.execute('SELECT id FROM branch_print_devices WHERE id=%s AND revoked_at IS NULL FOR UPDATE',(device['id'],)).fetchone()
        if not live:raise db.error(401,'PRINT_DEVICE_AUTH','ألغي ربط الجهاز.')
        conn.execute('UPDATE branch_print_devices SET last_seen_at=now() WHERE id=%s',(device['id'],))
        row=conn.execute("SELECT * FROM branch_print_jobs WHERE device_id=%s AND status='queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1",(device['id'],)).fetchone()
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
def download_bridge(user=Depends(operators)):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as bundle:
        for name in ('KpsPrintBridge.cs','Start.cmd','README.txt'):
            bundle.write(db.ROOT/'tools'/'windows-print-bridge'/name,name)
        bundle.write(db.ROOT/'public'/'assets'/'kps-receipt-logo.png','logo.png')
    return Response(output.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="KPS-Windows-Print-Bridge.zip"'})
