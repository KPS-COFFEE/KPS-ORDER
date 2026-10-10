"""Persistent order or service-context support; drivers have no customer inbox."""
import base64
import hashlib
import io
from typing import Literal
from PIL import Image, UnidentifiedImageError
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, ConfigDict
from . import auth,db
from .operations import uid,safe,fail,roles,mutate

router=APIRouter(prefix='/api',tags=['chat'])

def allowed(user,row):
    if user['role']=='owner':return user['brand_id']==row['brand_id']
    if user['role']=='customer':return row['customer_id']==user['id'] and row['brand_id']==user['brand_id']
    if user['brand_id']!=row['brand_id'] or row['branch_id'] not in user.get('branch_ids',[]):return False
    if row['route']=='delivery':return user['role'] in ('delivery_manager','delivery_staff')
    return user['role']=='branch_manager'

def conversation(c,user,cid,lock=False):
    row=c.execute('SELECT * FROM conversations WHERE id=%s'+(' FOR UPDATE' if lock else ''),(cid,)).fetchone()
    if not row or not allowed(user,row):fail(404,'المحادثة غير موجودة')
    return row

class CreateConversation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    order_id:str|None=Field(None,min_length=1,max_length=100)
    branch_id:str|None=Field(None,min_length=1,max_length=100)
    service:Literal['delivery','pickup','car']|None=None

@router.post('/conversations')
def create_conversation(body:CreateConversation,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','customer','delivery_manager','delivery_staff','branch_manager')
    if body.order_id is None:roles(user,'customer')
    with db.tx() as c:
        def perform():
            if body.order_id is not None:
                o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(body.order_id,)).fetchone()
                if not o or not auth.can_access_order(user,o,c):fail(404,'الطلب غير موجود')
                if (body.branch_id is not None and body.branch_id!=o['branch_id']) or (body.service is not None and body.service!=o['service']):fail(422,'سياق المحادثة يجب أن يطابق الطلب')
                existing=c.execute('SELECT * FROM conversations WHERE order_id=%s',(o['id'],)).fetchone()
                order_id=o['id'];branch_id=o['branch_id'];customer_id=o['customer_id'];brand_id=o['brand_id'];service=o['service']
            else:
                # Real customer sessions are issued only after provider-backed OTP;
                # staff/owner accounts cannot fabricate an unverified customer context.
                roles(user,'customer')
                if body.branch_id is None or body.service is None:fail(422,'حدد نوع الخدمة والفرع لبدء المحادثة')
                branch=c.execute('SELECT id,brand_id FROM branches WHERE id=%s AND brand_id=%s',(body.branch_id,user['brand_id'])).fetchone()
                if not branch:fail(404,'الفرع غير موجود')
                order_id=None;branch_id=branch['id'];customer_id=user['id'];brand_id=user['brand_id'];service=body.service
                existing=c.execute("SELECT * FROM conversations WHERE order_id IS NULL AND customer_id=%s AND branch_id=%s AND context_service=%s AND status<>'resolved' ORDER BY created_at DESC LIMIT 1 FOR UPDATE",(customer_id,branch_id,service)).fetchone()
            if existing:
                if not allowed(user,existing):fail(404,'المحادثة غير متاحة')
                return {'conversation':existing}
            route='delivery' if service=='delivery' else 'branch'
            row={'customer_id':customer_id,'brand_id':brand_id,'branch_id':branch_id,'route':route}
            if not allowed(user,row):fail(403,'المحادثة مخصصة لفريق المتابعة المختص')
            cid=uid();c.execute('INSERT INTO conversations(id,order_id,brand_id,branch_id,customer_id,route,context_service) VALUES(%s,%s,%s,%s,%s,%s,%s)',(cid,order_id,brand_id,branch_id,customer_id,route,service))
            db.audit(c,user['id'],'chat.create','conversation',cid,{'order_id':order_id,'service':service,'branch_id':branch_id,'route':route})
            return {'conversation':c.execute('SELECT * FROM conversations WHERE id=%s',(cid,)).fetchone()}
        return mutate(c,user,request,'chat.create',body.model_dump(),perform)

@router.get('/conversations')
def inbox(status:str|None=None,offset:int=0,user=Depends(auth.current_user)):
    roles(user,'owner','customer','delivery_manager','delivery_staff','branch_manager')
    if offset<0 or offset>100000:fail(422,'صفحة غير صالحة')
    with db.tx() as c:
        if user['role']=='owner':scope='v.brand_id=%s';p=[user['brand_id']]
        elif user['role']=='customer':scope='v.customer_id=%s AND v.brand_id=%s';p=[user['id'],user['brand_id']]
        else:scope='v.brand_id=%s AND v.branch_id=ANY(%s) AND v.route=%s';p=[user['brand_id'],user.get('branch_ids',[]),'branch' if user['role']=='branch_manager' else 'delivery']
        if status:
            if status not in ('open','in_progress','resolved'):fail(422,'حالة غير صالحة')
            scope+=' AND v.status=%s';p.append(status)
        rows=c.execute('SELECT v.*,coalesce(o.service,v.context_service) service,b.name branch_name,(SELECT count(*) FROM chat_messages m WHERE m.conversation_id=v.id AND m.sender_id<>%s AND m.seq>coalesce((SELECT through_seq FROM conversation_reads r WHERE r.conversation_id=v.id AND r.user_id=%s),0)) unread_count FROM conversations v LEFT JOIN orders o ON o.id=v.order_id JOIN branches b ON b.id=v.branch_id WHERE '+scope+' ORDER BY v.created_at DESC LIMIT 50 OFFSET %s',[user['id'],user['id'],*p,offset]).fetchall()
        return safe({'conversations':rows,'offset':offset,'limit':50})

@router.get('/conversations/{cid}/messages')
def messages(cid:str,after:int=0,user=Depends(auth.current_user)):
    if after<0:fail(422,'مرجع غير صالح')
    with db.tx() as c:
        v=conversation(c,user,cid)
        rows=c.execute('SELECT m.*,u.display_name sender FROM chat_messages m JOIN users u ON u.id=m.sender_id WHERE m.conversation_id=%s AND m.seq>%s ORDER BY m.seq LIMIT 100',(cid,after)).fetchall()
        for r in rows:r['attachment_url']='/api/chat-attachments/'+r['attachment_id'] if r['attachment_id'] else None
        transfers=c.execute('SELECT * FROM conversation_transfers WHERE conversation_id=%s ORDER BY created_at',(cid,)).fetchall()
        return safe({'conversation':v,'messages':rows,'transfers':transfers})

class Message(BaseModel):
    text:str=Field('',max_length=4000)
    attachment_id:str|None=None

@router.post('/conversations/{cid}/messages')
def send(cid:str,body:Message,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        def perform():
            v=conversation(c,user,cid,True)
            if not body.text.strip() and not body.attachment_id:fail(422,'الرسالة فارغة')
            if not db.rate_limit(c,'chat:'+user['id'],60,60):fail(429,'أرسل الرسائل بوتيرة أبطأ')
            if body.attachment_id:
                a=c.execute('SELECT * FROM chat_attachments WHERE id=%s AND conversation_id=%s AND uploader_id=%s',(body.attachment_id,cid,user['id'])).fetchone()
                if not a:fail(404,'المرفق غير موجود')
            mid=uid();c.execute('INSERT INTO chat_messages(id,conversation_id,sender_id,text,attachment_id) VALUES(%s,%s,%s,%s,%s)',(mid,cid,user['id'],body.text,body.attachment_id))
            c.execute("UPDATE conversations SET status=CASE WHEN status='resolved' THEN 'open' ELSE status END WHERE id=%s",(cid,))
            db.audit(c,user['id'],'chat.send','conversation',cid,{'message_id':mid})
            result=c.execute('SELECT * FROM chat_messages WHERE id=%s',(mid,)).fetchone();result['attachment_url']='/api/chat-attachments/'+body.attachment_id if body.attachment_id else None
            return {'message':result}
        return mutate(c,user,request,'chat.send:'+cid,body.model_dump(),perform)

class Attachment(BaseModel):
    data_base64:str=Field(max_length=6990510)
    content_type:str

@router.post('/conversations/{cid}/attachments')
def upload(cid:str,body:Attachment,request:Request,user=Depends(auth.current_user)):
    expected={'image/png':'PNG','image/jpeg':'JPEG','image/webp':'WEBP'}
    if body.content_type not in expected:fail(422,'نوع صورة غير مسموح')
    try:
        data=base64.b64decode(body.data_base64,validate=True)
        if not 0<len(data)<=5*1024*1024:fail(413,'الصورة أكبر من 5 ميغابايت')
        with Image.open(io.BytesIO(data)) as img:
            if img.format!=expected[body.content_type] or img.width*img.height>16000000:fail(422,'ملف الصورة غير صالح أو كبير')
            img.verify()
    except (ValueError,UnidentifiedImageError,Image.DecompressionBombError,OSError):fail(422,'ملف الصورة غير صالح')
    digest=hashlib.sha256(data).hexdigest()
    with db.tx() as c:
        def perform():
            conversation(c,user,cid,True)
            if not db.rate_limit(c,'attachment:'+user['id'],12,60):fail(429,'أرسل الصور بوتيرة أبطأ')
            aid=uid();c.execute('INSERT INTO chat_attachments(id,conversation_id,uploader_id,content_type,size_bytes,sha256,data) VALUES(%s,%s,%s,%s,%s,%s,%s)',(aid,cid,user['id'],body.content_type,len(data),digest,data))
            db.audit(c,user['id'],'chat.attachment','conversation',cid,{'attachment_id':aid,'size_bytes':len(data)})
            return {'id':aid,'url':'/api/chat-attachments/'+aid}
        return mutate(c,user,request,'chat.attachment:'+cid,{'sha256':digest,'content_type':body.content_type},perform)

@router.get('/chat-attachments/{aid}')
def download(aid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        a=c.execute('SELECT * FROM chat_attachments WHERE id=%s',(aid,)).fetchone()
        if not a:fail(404,'المرفق غير موجود')
        conversation(c,user,a['conversation_id'])
        return Response(bytes(a['data']),media_type=a['content_type'],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Disposition':'inline; filename="image.'+{'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[a['content_type']]+'"'})

class Transfer(BaseModel):
    target:str
    reason:str=Field(min_length=1,max_length=2000)

@router.post('/conversations/{cid}/transfer')
def transfer(cid:str,body:Transfer,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','branch_manager','delivery_manager','delivery_staff')
    with db.tx() as c:
        def perform():
            v=conversation(c,user,cid,True)
            o=c.execute('SELECT service FROM orders WHERE id=%s',(v['order_id'],)).fetchone() if v['order_id'] else None
            service=o['service'] if o else v['context_service']
            if body.target not in ('delivery','branch') or body.target==v['route']:fail(422,'جهة تحويل غير صالحة')
            if body.target=='branch' and (service=='delivery' or v['route']=='delivery'):fail(409,'شكاوى التوصيل تظل لدى التوصيل')
            tid=uid();c.execute('INSERT INTO conversation_transfers(id,conversation_id,from_route,to_route,reason,actor_id) VALUES(%s,%s,%s,%s,%s,%s)',(tid,cid,v['route'],body.target,body.reason,user['id']))
            c.execute("UPDATE conversations SET route=%s,assignee_id=NULL,status='open' WHERE id=%s",(body.target,cid))
            db.audit(c,user['id'],'chat.transfer','conversation',cid,{'from':v['route'],'to':body.target,'reason':body.reason})
            return {'conversation_id':cid,'route':body.target,'transfer_id':tid}
        return mutate(c,user,request,'chat.transfer:'+cid,body.model_dump(),perform)

class Read(BaseModel):through_seq:int=Field(ge=0)

@router.post('/conversations/{cid}/read')
def mark_read(cid:str,body:Read,user=Depends(auth.current_user)):
    with db.tx() as c:
        conversation(c,user,cid)
        last=c.execute('SELECT coalesce(max(seq),0) n FROM chat_messages WHERE conversation_id=%s',(cid,)).fetchone()['n']
        through=min(last,body.through_seq)
        c.execute('INSERT INTO conversation_reads(conversation_id,user_id,through_seq) VALUES(%s,%s,%s) ON CONFLICT(conversation_id,user_id) DO UPDATE SET through_seq=greatest(conversation_reads.through_seq,EXCLUDED.through_seq)',(cid,user['id'],through))
        return {'through_seq':through}

@router.post('/conversations/{cid}/claim')
def claim(cid:str,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','branch_manager','delivery_manager','delivery_staff')
    with db.tx() as c:
        def perform():
            v=conversation(c,user,cid,True)
            if v['assignee_id'] and v['assignee_id']!=user['id']:
                previous=c.execute('SELECT active FROM users WHERE id=%s',(v['assignee_id'],)).fetchone()
                if previous and previous['active'] and user['role']!='owner':fail(409,'المحادثة لدى موظف آخر؛ يراجع المدير نقل المسؤولية')
            c.execute("UPDATE conversations SET assignee_id=%s,status='in_progress' WHERE id=%s",(user['id'],cid))
            db.audit(c,user['id'],'chat.claim','conversation',cid)
            return {'assignee_id':user['id'],'status':'in_progress'}
        return mutate(c,user,request,'chat.claim:'+cid,{},perform)

class Status(BaseModel):status:str

@router.post('/conversations/{cid}/status')
def status(cid:str,body:Status,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','branch_manager','delivery_manager','delivery_staff')
    with db.tx() as c:
        def perform():
            conversation(c,user,cid,True)
            if body.status not in ('open','in_progress','resolved'):fail(422,'حالة غير صالحة')
            c.execute('UPDATE conversations SET status=%s WHERE id=%s',(body.status,cid));db.audit(c,user['id'],'chat.status','conversation',cid,body.model_dump())
            return {'status':body.status}
        return mutate(c,user,request,'chat.status:'+cid,body.model_dump(),perform)
