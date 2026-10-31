"""Persistent support and task-scoped driver contact; no general driver inbox."""
import base64
import hashlib
import io
import re
from typing import Literal
from PIL import Image, UnidentifiedImageError
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field, ConfigDict, model_validator
from . import auth,db,customer_message_routing as routing
from .operations import uid,safe,fail,roles,mutate

router=APIRouter(prefix='/api',tags=['chat'])
CATEGORIES={'help','suggestion','complaint','change','refund','delivery_problem','privacy','other'}

def attachment_url(aid,user):
    context='customer' if user['role']=='customer' else 'work'
    return '/api/chat-attachments/'+aid+'?context='+context

def route_for(category, service, details=''):
    if category=='privacy':return 'privacy'
    if category=='refund':return 'review'
    if category=='delivery_problem' or service=='delivery' or re.search(r'توصيل|سائق|سايق|تأخير|delivery|driver|delay',details,re.I):return 'delivery'
    return 'branch'

def reference(row):
    return 'CS-'+str(row['followup_no']).zfill(6)

def customer_conversation_view(conn, row):
    result = dict(row)
    result['reference']=reference(row)
    for key in ('assignee_id','route'):
        result.pop(key,None)
    if row.get('order_id'):
        order = conn.execute('SELECT snapshot FROM orders WHERE id=%s', (row['order_id'],)).fetchone()
        routing = (order['snapshot'] if order else {}).get('internal_routing')
        if routing:
            selected = routing['selected_branch']
            result.update({key: selected[key] for key in ('branch_id', 'branch_name', 'branch_no')})
    return result


def allowed(user,row,conn=None,*,supervision=False):
    if user.get('_morning_manager_context'):
        if conn is None:
            return False
        from .morning_manager_context import order_allowed
        if row.get('order_id'):
            if not order_allowed(user,conn.execute('SELECT * FROM orders WHERE id=%s',(row['order_id'],)).fetchone(),conn):return False
        else:
            duty=conn.execute('SELECT opened_at FROM shifts WHERE id=%s',(user['_morning_manager_context']['shift_id'],)).fetchone()
            if not duty or row.get('context_service')!='delivery' or not row.get('created_at') or row['created_at']<duty['opened_at']:return False
    if user['role']=='owner':return user['brand_id']==row['brand_id'] and (not user.get('home_brand_id') or row['branch_id'] in user.get('branch_ids',[]) or (row['branch_id'] is None and conn is not None and routing.owner_branchless(conn,user)))
    if user['role']=='customer':return row['customer_id'] in auth.customer_ids(user) and row['brand_id']==user['brand_id']
    if (supervision and auth.has_admin_read_grant(user,'chat_view')
            and user['brand_id']==row['brand_id'] and row['branch_id'] in user.get('branch_ids',[])):
        return True
    if conn is not None:
        configured = routing.access(conn, user, row)
        if configured is not None:return configured
    if row['route'] in ('privacy','review'):return False
    if user['brand_id']!=row['brand_id'] or row['branch_id'] not in user.get('branch_ids',[]):return False
    if row['route']=='delivery':return user['role'] in ('delivery_manager','delivery_staff')
    return user['role']=='branch_manager'

def conversation(c,user,cid,lock=False,*,supervision=True):
    # Locked paths change shared conversation state. A supervisory read grant
    # never extends an employee's existing routed reply/manage authority.
    read_supervision=supervision and not lock
    if lock:
        initial=c.execute('SELECT * FROM conversations WHERE id=%s',(cid,)).fetchone()
        if not initial or not allowed(user,initial,c):fail(404,'المحادثة غير موجودة')
        if initial['order_id']:c.execute('SELECT id FROM orders WHERE id=%s FOR UPDATE',(initial['order_id'],)).fetchone()
    row=c.execute('SELECT * FROM conversations WHERE id=%s'+(' FOR UPDATE' if lock else ''),(cid,)).fetchone()
    if not row or not allowed(user,row,c,supervision=read_supervision):fail(404,'المحادثة غير موجودة')
    if row['order_id']:
        order=c.execute('SELECT order_no,snapshot->>\'order_reference\' order_reference FROM orders WHERE id=%s AND brand_id=%s',(row['order_id'],row['brand_id'])).fetchone()
        row['order_no']=order['order_no'] if order else None
        row['order_reference']=order['order_reference'] if order else None
    return row

class CreateConversation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    order_id:str|None=Field(None,min_length=1,max_length=100)
    branch_id:str|None=Field(None,min_length=1,max_length=100)
    service:Literal['delivery','pickup','car']|None=None
    category:Literal['help','suggestion','complaint','change','refund','delivery_problem','privacy','other']='help'
    details:str=Field('',max_length=4000)

@router.post('/conversations')
def create_conversation(body:CreateConversation,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','customer','admin','delivery_manager','delivery_staff','branch_manager')
    if body.order_id is None:roles(user,'customer')
    if body.category=='other' and not body.details.strip():fail(422,'اكتب التفاصيل عند اختيار أخرى')
    with db.tx() as c:
        def perform():
            if body.order_id is not None:
                o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(body.order_id,)).fetchone()
                if not o or not auth.can_access_order(user,o,c):fail(404,'الطلب غير موجود')
                if (body.branch_id is not None and body.branch_id!=o['branch_id']) or (body.service is not None and body.service!=o['service']):fail(422,'سياق المحادثة يجب أن يطابق الطلب')
                existing=c.execute('SELECT * FROM conversations WHERE order_id=%s AND category=%s AND driver_task_id IS NULL',(o['id'],body.category)).fetchone()
                order_id=o['id'];branch_id=o['branch_id'];customer_id=o['customer_id'];brand_id=o['brand_id'];service=o['service']
                linked=c.execute('SELECT customer_id FROM customer_identity_links WHERE source_id=%s AND brand_id=%s',(customer_id,brand_id)).fetchone()
                if linked:customer_id=linked['customer_id']
            else:
                # Customer/guest ownership comes from the revocable server session;
                # staff/owner accounts cannot fabricate a customer's context.
                roles(user,'customer')
                if body.category not in ('privacy','refund') and (body.branch_id is None or body.service is None):fail(422,'حدد نوع الخدمة والفرع لبدء المحادثة')
                branch=c.execute('SELECT id,brand_id FROM branches WHERE id=%s AND brand_id=%s',(body.branch_id,user['brand_id'])).fetchone()
                if body.branch_id and not branch:fail(404,'الفرع غير موجود')
                order_id=None;branch_id=branch['id'] if branch else None;customer_id=user['id'];brand_id=user['brand_id'];service=body.service or 'pickup'
                existing=c.execute("SELECT * FROM conversations WHERE order_id IS NULL AND customer_id=%s AND branch_id IS NOT DISTINCT FROM %s AND context_service=%s AND category=%s AND status<>'resolved' ORDER BY created_at DESC LIMIT 1 FOR UPDATE",(customer_id,branch_id,service,body.category)).fetchone()
            if body.details.strip() or 'category' in body.model_fields_set:existing=None
            if existing:
                if not allowed(user,existing,c):fail(404,'المحادثة غير متاحة')
                return {'conversation':existing}
            if not db.rate_limit(c,'contact-create:'+user['id'],10,600):raise db.error(429,'CONTACT_RATE_LIMIT','رسائل كثيرة. حاول بعد قليل.')
            route=route_for(body.category,service,body.details)
            row={'customer_id':customer_id,'brand_id':brand_id,'branch_id':branch_id,'route':route,'order_id':order_id,
                 'context_service':service,'category':body.category,'created_at':c.execute('SELECT now() AS n').fetchone()['n']}
            if not allowed(user,row,c):fail(403,'المحادثة مخصصة لفريق المتابعة المختص')
            cid=uid();c.execute('INSERT INTO conversations(id,order_id,brand_id,branch_id,customer_id,route,context_service,category) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(cid,order_id,brand_id,branch_id,customer_id,route,service,body.category))
            if body.details.strip():
                c.execute('INSERT INTO chat_messages(id,conversation_id,sender_id,text) VALUES(%s,%s,%s,%s)',(uid(),cid,user['id'],body.details.strip()))
            db.audit(c,user['id'],'chat.create','conversation',cid,{'order_id':order_id,'service':service,'branch_id':branch_id,'route':route})
            return {'conversation':c.execute('SELECT * FROM conversations WHERE id=%s',(cid,)).fetchone()}
        result = mutate(c,user,request,'chat.create',body.model_dump(),perform)
        if user['role'] == 'customer':
            result = dict(result, conversation=customer_conversation_view(c, result['conversation']))
        return result

@router.get('/conversations')
def inbox(status:str|None=None,offset:int=0,order_id:str|None=None,user=Depends(auth.current_user)):
    roles(user,'owner','customer','admin','delivery_manager','delivery_staff','branch_manager')
    if offset<0 or offset>100000:fail(422,'صفحة غير صالحة')
    with db.tx() as c:
        if user['role']=='owner':
            scope='v.brand_id=%s';p=[user['brand_id']]
            if user.get('home_brand_id'):
                scope+=' AND (v.branch_id=ANY(%s) OR (v.branch_id IS NULL AND %s))';p.extend([user.get('branch_ids',[]),routing.owner_branchless(c,user)])
        elif user['role']=='customer':scope='v.customer_id=ANY(%s) AND v.brand_id=%s';p=[auth.customer_ids(user),user['brand_id']]
        elif user['role']=='admin':scope='FALSE';p=[]
        else:scope='v.brand_id=%s AND v.branch_id=ANY(%s) AND v.route=%s';p=[user['brand_id'],user.get('branch_ids',[]),'branch' if user['role']=='branch_manager' else 'delivery']
        configured = routing.scope(c, user)
        if configured is not None:scope,p=configured
        if auth.has_admin_read_grant(user,'chat_view'):
            # Retain already granted response-team scopes, including their
            # branchless inbox, while adding only scoped supervisory reads.
            scope='(('+scope+') OR (v.brand_id=%s AND v.branch_id=ANY(%s)))'
            p.extend([user['brand_id'],user.get('branch_ids',[])])
        from .morning_manager_context import conversation_filter
        morning_clause,morning_args=conversation_filter(user)
        scope+=' AND '+morning_clause;p.extend(morning_args)
        if order_id:
            if len(order_id)>100:fail(422,'مرجع الطلب غير صالح')
            scope+=' AND v.order_id=%s';p.append(order_id)
        if status:
            if status not in ('open','in_progress','resolved'):fail(422,'حالة غير صالحة')
            scope+=' AND v.status=%s';p.append(status)
        rows=c.execute('SELECT v.*,o.order_no,o.snapshot->>\'order_reference\' order_reference,coalesce(o.service,v.context_service) service,b.name branch_name,b.branch_no,coalesce(o.customer->>\'name\',cu.display_name) customer_name,last.text last_message,last.created_at last_message_at,(SELECT count(*) FROM chat_messages m WHERE m.conversation_id=v.id AND m.sender_id<>%s AND m.seq>coalesce((SELECT through_seq FROM conversation_reads r WHERE r.conversation_id=v.id AND r.user_id=%s),0)) unread_count FROM conversations v LEFT JOIN orders o ON o.id=v.order_id LEFT JOIN branches b ON b.id=v.branch_id LEFT JOIN users cu ON cu.id=v.customer_id AND cu.brand_id=v.brand_id LEFT JOIN LATERAL (SELECT text,created_at FROM chat_messages WHERE conversation_id=v.id ORDER BY seq DESC LIMIT 1) last ON true WHERE '+scope+' ORDER BY v.created_at DESC,v.id DESC LIMIT 51 OFFSET %s',[user['id'],user['id'],*p,offset]).fetchall()
        next_offset=offset+50 if len(rows)>50 else None
        rows=rows[:50]
        if user['role'] == 'customer':
            rows = [customer_conversation_view(c, row) for row in rows]
        elif routing.config(c, user['brand_id']) is not None:
            rows = [dict(row, response_team=routing.team(row)) for row in rows]
        return safe({'conversations':rows,'offset':offset,'limit':50,'next_offset':next_offset})

@router.get('/conversations/alerts')
def reply_alerts(user=Depends(auth.current_user)):
    """Reading/claiming/resolving is not a reply. Scope matches the inbox."""
    roles(user,'owner','admin','delivery_manager','delivery_staff','branch_manager')
    if user['role']=='owner':
        scope='v.brand_id=%s';params=[user['brand_id']]
        if user.get('home_brand_id'):
            scope+=' AND v.branch_id=ANY(%s)';params.append(user.get('branch_ids',[]))
    elif user['role']=='admin':scope='FALSE';params=[]
    else:
        scope='v.brand_id=%s AND v.branch_id=ANY(%s) AND v.route=%s'
        params=[user['brand_id'],user.get('branch_ids',[]),
                'branch' if user['role']=='branch_manager' else 'delivery']
    with db.tx() as c:
        if user['role']=='owner' and user.get('home_brand_id') and routing.owner_branchless(c,user):
            scope='v.brand_id=%s';params=[user['brand_id']]
        configured = routing.scope(c, user, alerts=True)
        if configured is not None:
            if user['role']=='owner':
                scope+=' AND ('+configured[0]+')';params.extend(configured[1])
            else:scope,params=configured
        from .morning_manager_context import conversation_filter
        morning_clause,morning_args=conversation_filter(user)
        scope+=' AND '+morning_clause;params.extend(morning_args)
        rows=c.execute('''SELECT v.id,v.order_id,v.branch_id,last.seq,o.order_no,
            o.snapshot->>'order_reference' order_reference,b.name branch_name,
            u.display_name sender_name,u.role sender_role,last.text last_message,
            last.created_at last_message_at
            FROM conversations v
            LEFT JOIN orders o ON o.id=v.order_id
            LEFT JOIN branches b ON b.id=v.branch_id
            JOIN LATERAL (SELECT m.seq,m.sender_id,m.text,m.created_at FROM chat_messages m
                WHERE m.conversation_id=v.id ORDER BY m.seq DESC LIMIT 1) last ON true
            LEFT JOIN users u ON u.id=last.sender_id
            WHERE '''+scope+''' AND last.sender_id=v.customer_id
            ORDER BY last.created_at,v.id LIMIT 100''',params).fetchall()
        return safe({'awaiting_reply':rows})


@router.get('/conversations/{cid}/messages')
def messages(cid:str,after:int=0,user=Depends(auth.current_user)):
    if after<0:fail(422,'مرجع غير صالح')
    with db.tx() as c:
        v=conversation(c,user,cid)
        rows=c.execute('SELECT m.*,u.display_name sender FROM chat_messages m JOIN users u ON u.id=m.sender_id WHERE m.conversation_id=%s AND m.seq>%s ORDER BY m.seq LIMIT 100',(cid,after)).fetchall()
        for r in rows:r['attachment_url']=attachment_url(r['attachment_id'],user) if r['attachment_id'] else None
        transfers=c.execute('SELECT * FROM conversation_transfers WHERE conversation_id=%s ORDER BY created_at',(cid,)).fetchall()
        from .order_support import STATES,CATEGORIES
        tickets=c.execute('SELECT ticket_no,category,status FROM support_tickets WHERE conversation_id=%s ORDER BY ticket_no',(cid,)).fetchall()
        if user['role'] == 'customer':
            v = customer_conversation_view(c, v)
            transfers=[]
        elif routing.config(c, user['brand_id']) is not None:
            v = dict(v, response_team=routing.team(v))
        if user['role']!='customer':
            write_scope=allowed(user,v,c)
            v=dict(v,can_reply=bool(write_scope and auth.has_permission(user,'chat_reply')),
                   can_manage=bool(write_scope and auth.has_permission(user,'chat_manage')))
        return safe({'conversation':v,'messages':rows,'transfers':transfers,'tickets':[{'reference':'TKT-'+str(t['ticket_no']).zfill(6),'status':t['status'],'status_label':STATES[t['status']],'category_label':CATEGORIES[t['category']]} for t in tickets]})

class Message(BaseModel):
    text:str=Field('',max_length=4000)
    attachment_id:str|None=None

@router.post('/conversations/{cid}/messages')
def send(cid:str,body:Message,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        def perform():
            v=conversation(c,user,cid,True)
            if v['route']=='branch' and auth.customer_phone_required(c,user['brand_id']) and route_for(v['category'],v['context_service'],body.text)=='delivery':
                c.execute("INSERT INTO conversation_transfers(id,conversation_id,from_route,to_route,reason,actor_id) VALUES(%s,%s,'branch','delivery',%s,%s)",(uid(),cid,'تصنيف تلقائي لمشكلة توصيل',user['id']))
                c.execute("UPDATE conversations SET route='delivery',assignee_id=NULL WHERE id=%s",(cid,))
            if not body.text.strip() and not body.attachment_id:fail(422,'الرسالة فارغة')
            if not db.rate_limit(c,'chat:'+user['id'],60,60):fail(429,'أرسل الرسائل بوتيرة أبطأ')
            if body.attachment_id:
                a=c.execute('SELECT * FROM chat_attachments WHERE id=%s AND conversation_id=%s AND uploader_id=%s',(body.attachment_id,cid,user['id'])).fetchone()
                if not a:fail(404,'المرفق غير موجود')
            mid=uid();c.execute('INSERT INTO chat_messages(id,conversation_id,sender_id,text,attachment_id) VALUES(%s,%s,%s,%s,%s)',(mid,cid,user['id'],body.text,body.attachment_id))
            c.execute("UPDATE conversations SET status=CASE WHEN status='resolved' THEN 'open' ELSE status END WHERE id=%s",(cid,))
            db.audit(c,user['id'],'chat.send','conversation',cid,{'message_id':mid})
            result=c.execute('SELECT * FROM chat_messages WHERE id=%s',(mid,)).fetchone();result['attachment_url']=attachment_url(body.attachment_id,user) if body.attachment_id else None
            return {'message':result}
        # A configured team's revoked access must reject cached replies too.
        # Legacy transfer receipts remain replayable after their route changes.
        if user['role']=='admin' or routing.config(c,user['brand_id']) is not None:conversation(c,user,cid,supervision=False)
        return mutate(c,user,request,'chat.send:'+cid,body.model_dump(),perform)

class Attachment(BaseModel):
    data_base64:str=Field(max_length=6990510)
    content_type:str

def validated_image(body:Attachment):
    expected={'image/png':'PNG','image/jpeg':'JPEG','image/webp':'WEBP'}
    if body.content_type not in expected:fail(422,'نوع صورة غير مسموح')
    try:
        data=base64.b64decode(body.data_base64,validate=True)
        if not 0<len(data)<=5*1024*1024:fail(413,'الصورة أكبر من 5 ميغابايت')
        with Image.open(io.BytesIO(data)) as img:
            if img.format!=expected[body.content_type] or img.width*img.height>16000000:fail(422,'ملف الصورة غير صالح أو كبير')
            img.verify()
    except (ValueError,UnidentifiedImageError,Image.DecompressionBombError,OSError):fail(422,'ملف الصورة غير صالح')
    return data

@router.post('/conversations/{cid}/attachments')
def upload(cid:str,body:Attachment,request:Request,user=Depends(auth.current_user)):
    data=validated_image(body)
    digest=hashlib.sha256(data).hexdigest()
    with db.tx() as c:
        def perform():
            conversation(c,user,cid,True)
            if not db.rate_limit(c,'attachment:'+user['id'],12,60):fail(429,'أرسل الصور بوتيرة أبطأ')
            aid=uid();c.execute('INSERT INTO chat_attachments(id,conversation_id,uploader_id,content_type,size_bytes,sha256,data) VALUES(%s,%s,%s,%s,%s,%s,%s)',(aid,cid,user['id'],body.content_type,len(data),digest,data))
            db.audit(c,user['id'],'chat.attachment','conversation',cid,{'attachment_id':aid,'size_bytes':len(data)})
            return {'id':aid,'url':attachment_url(aid,user)}
        # A configured team's revoked access must reject cached replies too.
        # Legacy transfer receipts remain replayable after their route changes.
        if user['role']=='admin' or routing.config(c,user['brand_id']) is not None:conversation(c,user,cid,supervision=False)
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
    roles(user,'owner','admin','branch_manager','delivery_manager','delivery_staff')
    with db.tx() as c:
        def perform():
            v=conversation(c,user,cid,True)
            o=c.execute('SELECT service FROM orders WHERE id=%s',(v['order_id'],)).fetchone() if v['order_id'] else None
            service=o['service'] if o else v['context_service']
            if v['route'] in ('privacy','review') or (routing.config(c,user['brand_id']) is not None and routing.team(v)=='care'):
                fail(403,'المحادثة مخصصة لفريق متابعة الشكاوى والخصوصية')
            if body.target not in ('delivery','branch') or body.target==v['route']:fail(422,'جهة تحويل غير صالحة')
            if body.target=='branch' and (service=='delivery' or v['route']=='delivery'):fail(409,'شكاوى التوصيل تظل لدى التوصيل')
            tid=uid();c.execute('INSERT INTO conversation_transfers(id,conversation_id,from_route,to_route,reason,actor_id) VALUES(%s,%s,%s,%s,%s,%s)',(tid,cid,v['route'],body.target,body.reason,user['id']))
            c.execute("UPDATE conversations SET route=%s,assignee_id=NULL,status='open' WHERE id=%s",(body.target,cid))
            db.audit(c,user['id'],'chat.transfer','conversation',cid,{'from':v['route'],'to':body.target,'reason':body.reason})
            return {'conversation_id':cid,'route':body.target,'transfer_id':tid}
        # A configured team's revoked access must reject cached replies too.
        # Legacy transfer receipts remain replayable after their route changes.
        if user['role']=='admin' or routing.config(c,user['brand_id']) is not None:conversation(c,user,cid,supervision=False)
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
    roles(user,'owner','admin','branch_manager','delivery_manager','delivery_staff')
    with db.tx() as c:
        def perform():
            v=conversation(c,user,cid,True)
            if v['assignee_id'] and v['assignee_id']!=user['id']:
                previous=c.execute('SELECT * FROM users WHERE id=%s',(v['assignee_id'],)).fetchone()
                if previous and previous['active'] and allowed(previous,v,c) and user['role']!='owner':fail(409,'المحادثة لدى موظف آخر؛ يراجع المدير نقل المسؤولية')
            c.execute("UPDATE conversations SET assignee_id=%s,status='in_progress' WHERE id=%s",(user['id'],cid))
            db.audit(c,user['id'],'chat.claim','conversation',cid)
            return {'assignee_id':user['id'],'status':'in_progress'}
        # A configured team's revoked access must reject cached replies too.
        # Legacy transfer receipts remain replayable after their route changes.
        if user['role']=='admin' or routing.config(c,user['brand_id']) is not None:conversation(c,user,cid,supervision=False)
        return mutate(c,user,request,'chat.claim:'+cid,{},perform)

class Status(BaseModel):status:str

@router.post('/conversations/{cid}/status')
def status(cid:str,body:Status,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','admin','branch_manager','delivery_manager','delivery_staff')
    with db.tx() as c:
        def perform():
            v=conversation(c,user,cid,True)
            if body.status=='resolved' and v['order_id']:
                from .order_support import next_cleanup
                c.execute('UPDATE delivery_photos SET expires_at=greatest(expires_at,%s) WHERE order_id=%s AND data IS NOT NULL',(next_cleanup(),v['order_id']))
            if body.status not in ('open','in_progress','resolved'):fail(422,'حالة غير صالحة')
            c.execute('UPDATE conversations SET status=%s WHERE id=%s',(body.status,cid));db.audit(c,user['id'],'chat.status','conversation',cid,body.model_dump())
            return {'status':body.status}
        # A configured team's revoked access must reject cached replies too.
        # Legacy transfer receipts remain replayable after their route changes.
        if user['role']=='admin' or routing.config(c,user['brand_id']) is not None:conversation(c,user,cid,supervision=False)
        return mutate(c,user,request,'chat.status:'+cid,body.model_dump(),perform)


DRIVER_ACTIVE_TASKS={'assigned','accepted','picked_up','problem'}

def driver_contact_scope(c,user,tid,*,lock=True):
    roles(user,'driver')
    task=c.execute('SELECT * FROM driver_tasks WHERE id=%s',(tid,)).fetchone()
    if not task:fail(404,'المهمة غير متاحة للتواصل')
    # Mutations match task-transition lock order. The polling GET instead owns
    # a read-only repeatable snapshot: contact scope and messages must describe
    # the same assignment without locking rows needed by GPS/task writes.
    suffix=' FOR UPDATE' if lock else ''
    # Contact never changes order keys. Exclude concurrent order mutations but
    # allow the KEY SHARE check of GPS order_events inserts while GPS owns the
    # shift lock. FOR UPDATE here would recreate the order/shift deadlock.
    order_suffix=' FOR NO KEY UPDATE' if lock else ''
    order=c.execute('SELECT * FROM orders WHERE id=%s'+order_suffix,(task['order_id'],)).fetchone()
    task=c.execute('SELECT * FROM driver_tasks WHERE id=%s'+suffix,(tid,)).fetchone()
    if (not task or not order or task['driver_id']!=user['id'] or task['status'] not in DRIVER_ACTIVE_TASKS
        or not auth.can_access_order(user,order,c)):
        fail(404,'المهمة غير متاحة للتواصل')
    shift=c.execute('SELECT user_id,status FROM shifts WHERE id=%s'+suffix,(task['shift_id'],)).fetchone()
    if not shift or shift['user_id']!=user['id'] or shift['status']!='open':fail(409,'التواصل متاح أثناء الشفت المفتوح فقط')
    return task,order


def whatsapp_contact(phone):
    try:
        normalized=auth.normalize_phone(str(phone or ''))
    except Exception:
        return None
    return 'https://wa.me/'+normalized.lstrip('+')


@router.get('/tasks/{tid}/customer-chat')
def driver_customer_messages(tid:str,after:int=0,user=Depends(auth.current_user)):
    if not auth.has_permission(user,'tasks_view'):fail(403,'صلاحية التواصل غير متاحة')
    if after<0:fail(422,'مرجع غير صالح')
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        task,order=driver_contact_scope(c,user,tid,lock=False)
        conv=c.execute('SELECT * FROM conversations WHERE driver_task_id=%s',(tid,)).fetchone()
        rows=[]
        if conv:
            rows=c.execute('SELECT m.seq,m.text,m.attachment_id,m.created_at,u.display_name sender FROM chat_messages m JOIN users u ON u.id=m.sender_id WHERE m.conversation_id=%s AND m.seq>%s ORDER BY m.seq LIMIT 100',(conv['id'],after)).fetchall()
        for row in rows:
            aid=row.pop('attachment_id',None)
            row['attachment_url']='/api/tasks/'+tid+'/customer-chat/attachments/'+aid if aid else None
        return safe({'messages':rows,'whatsapp_url':whatsapp_contact((order.get('customer') or {}).get('phone')),'has_more':len(rows)==100})


class DriverCustomerMessage(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    text:str=Field(default='',max_length=2000)
    attachment_id:str|None=Field(default=None,min_length=1,max_length=100)

    @model_validator(mode='after')
    def content_required(self):
        if not self.text and not self.attachment_id:raise ValueError('أدخل رسالة أو صورة')
        return self


def driver_conversation(c,tid,order):
    conv=c.execute('SELECT * FROM conversations WHERE driver_task_id=%s',(tid,)).fetchone()
    if not conv:
        customer_id=order['customer_id']
        linked=c.execute('SELECT customer_id FROM customer_identity_links WHERE source_id=%s AND brand_id=%s',(customer_id,order['brand_id'])).fetchone()
        if linked:customer_id=linked['customer_id']
        cid=uid()
        c.execute("INSERT INTO conversations(id,order_id,brand_id,branch_id,customer_id,route,context_service,category,driver_task_id) VALUES(%s,%s,%s,%s,%s,'delivery','delivery','help',%s)",
            (cid,order['id'],order['brand_id'],order['branch_id'],customer_id,tid))
    else:cid=conv['id']
    return cid


def authorize_driver_contact_replay(c,user,operation,result):
    """A driver's own task receipt never grants the general chat inbox."""
    if not auth.has_permission(user,'tasks_update'):fail(403,'صلاحية الإرسال غير متاحة')
    kind,tid=operation.split(':',1)
    task,order=driver_contact_scope(c,user,tid)
    if kind=='driver.customer_message':
        saved=c.execute('SELECT 1 FROM chat_messages m JOIN conversations v ON v.id=m.conversation_id '
            'WHERE m.id=%s AND m.sender_id=%s AND m.conversation_id=%s AND v.driver_task_id=%s '
            'AND v.order_id=%s AND v.brand_id=%s AND v.branch_id=%s',
            (result.get('message_id'),user['id'],result.get('conversation_id'),task['id'],
             order['id'],order['brand_id'],order['branch_id'])).fetchone()
    else:
        saved=c.execute('SELECT 1 FROM chat_attachments a JOIN conversations v ON v.id=a.conversation_id '
            'WHERE a.id=%s AND a.uploader_id=%s AND v.driver_task_id=%s '
            'AND v.order_id=%s AND v.brand_id=%s AND v.branch_id=%s',
            (result.get('id'),user['id'],task['id'],order['id'],order['brand_id'],order['branch_id'])).fetchone()
    if not saved:fail(404,'السجل خارج الصلاحية الحالية')


@router.post('/tasks/{tid}/customer-chat')
def driver_customer_send(tid:str,body:DriverCustomerMessage,request:Request,user=Depends(auth.current_user)):
    if not auth.has_permission(user,'tasks_update'):fail(403,'صلاحية الإرسال غير متاحة')
    with db.tx() as c:
        def perform():
            # mutate owns the brand lane before taking order/task/shift locks.
            # Replays use the same live scope guard in authorize_replay.
            task,order=driver_contact_scope(c,user,tid)
            if not db.rate_limit(c,'driver-chat:'+user['id'],30,60):fail(429,'أرسل الرسائل بوتيرة أبطأ')
            cid=driver_conversation(c,tid,order)
            if body.attachment_id:
                a=c.execute('SELECT id FROM chat_attachments WHERE id=%s AND conversation_id=%s AND uploader_id=%s',(body.attachment_id,cid,user['id'])).fetchone()
                if not a:fail(404,'المرفق غير موجود')
            mid=uid()
            c.execute('INSERT INTO chat_messages(id,conversation_id,sender_id,text,attachment_id) VALUES(%s,%s,%s,%s,%s)',(mid,cid,user['id'],body.text,body.attachment_id))
            c.execute("UPDATE conversations SET status='open' WHERE id=%s AND status='resolved'",(cid,))
            db.audit(c,user['id'],'driver.customer_message','conversation',cid,{'message_id':mid,'task_id':tid})
            return {'message_id':mid,'conversation_id':cid}
        return mutate(c,user,request,'driver.customer_message:'+tid,body.model_dump(),perform)


@router.post('/tasks/{tid}/customer-chat/attachments')
def driver_customer_upload(tid:str,body:Attachment,request:Request,user=Depends(auth.current_user)):
    if not auth.has_permission(user,'tasks_update'):fail(403,'صلاحية الإرسال غير متاحة')
    data=validated_image(body)
    digest=hashlib.sha256(data).hexdigest()
    with db.tx() as c:
        def perform():
            task,order=driver_contact_scope(c,user,tid)
            if not db.rate_limit(c,'attachment:'+user['id'],12,60):fail(429,'أرسل الصور بوتيرة أبطأ')
            cid=driver_conversation(c,tid,order)
            aid=uid()
            c.execute('INSERT INTO chat_attachments(id,conversation_id,uploader_id,content_type,size_bytes,sha256,data) VALUES(%s,%s,%s,%s,%s,%s,%s)',(aid,cid,user['id'],body.content_type,len(data),digest,data))
            db.audit(c,user['id'],'chat.attachment','conversation',cid,{'attachment_id':aid,'size_bytes':len(data),'task_id':tid})
            return {'id':aid}
        return mutate(c,user,request,'driver.customer_attachment:'+tid,{'sha256':digest,'content_type':body.content_type},perform)


@router.get('/tasks/{tid}/customer-chat/attachments/{aid}')
def driver_customer_download(tid:str,aid:str,user=Depends(auth.current_user)):
    if not auth.has_permission(user,'tasks_view'):fail(403,'صلاحية التواصل غير متاحة')
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        driver_contact_scope(c,user,tid,lock=False)
        a=c.execute('SELECT a.* FROM chat_attachments a JOIN conversations v ON v.id=a.conversation_id WHERE a.id=%s AND v.driver_task_id=%s',(aid,tid)).fetchone()
        if not a:fail(404,'المرفق غير موجود')
        return Response(bytes(a['data']),media_type=a['content_type'],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Disposition':'inline; filename="image.'+{'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[a['content_type']]+'"'})
