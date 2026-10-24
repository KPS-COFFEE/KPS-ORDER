"""Order-bound support tickets and short-lived, access-controlled delivery evidence."""
import asyncio
import base64
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Literal
from PIL import Image, UnidentifiedImageError
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from . import auth, db, service_hours
from .operations import roles, fail, safe, uid, mutate

router=APIRouter(prefix='/api',tags=['order-support'])
CATEGORIES={'complaint':'شكوى','compensation':'مطالبة تعويض','missing_item':'نقص بالطلب','wrong_item':'خطأ بالطلب','delay':'تأخير','quality':'جودة المنتج','payment':'مشكلة دفع','driver':'مشكلة مع سائق','other':'أخرى'}
STATES={'received':'تم استلام التذكرة','in_progress':'قيد المعالجة','awaiting_customer':'بانتظار معلومات من العميل','resolved':'تم الحل','closed':'مغلق نهائيًا'}

def order_access(c,user,oid,lock=False):
    o=c.execute('SELECT * FROM orders WHERE id=%s'+(' FOR UPDATE' if lock else ''),(oid,)).fetchone()
    if not o or not auth.can_access_order(user,o,c):fail(404,'الطلب غير متاح')
    return o

def next_cleanup(now=None):
    now=service_hours.local_time(now)
    at=now.replace(hour=15,minute=55,second=0,microsecond=0)
    return at if now<at else at+timedelta(days=1)

def ticket_rows(c,user,oid=None):
    query='o.brand_id=%s';args=[user['brand_id']]
    from .morning_manager_context import order_filter
    morning_clause,morning_args=order_filter(user);query+=' AND '+morning_clause;args.extend(morning_args)
    if user['role']=='customer':query+=' AND o.customer_id=ANY(%s)';args.append(auth.customer_ids(user))
    elif user['role']!='owner':query+=' AND o.branch_id=ANY(%s)';args.append(user.get('branch_ids',[]))
    if oid:query+=' AND t.order_id=%s';args.append(oid)
    return c.execute("SELECT t.*, 'TKT-'||lpad(t.ticket_no::text,greatest(6,length(t.ticket_no::text)),'0') reference,o.snapshot->>'order_reference' order_reference,o.branch_id FROM support_tickets t JOIN orders o ON o.id=t.order_id WHERE "+query+' ORDER BY t.created_at DESC LIMIT 500',args).fetchall()

@router.get('/support-tickets')
def tickets(order_id:str|None=None,user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager','customer')
    with db.tx() as c:
        if order_id:order_access(c,user,order_id)
        return safe({'tickets':ticket_rows(c,user,order_id),'categories':CATEGORIES,'states':STATES})

class TicketBody(BaseModel):
    model_config=ConfigDict(extra='forbid')
    order_id:str
    category:Literal['complaint','compensation','missing_item','wrong_item','delay','quality','payment','driver','other']
    note:str=Field(min_length=1,max_length=2000)

def announce(c,t,user,status,note):
    reference='TKT-'+str(t['ticket_no']).zfill(6)
    order=c.execute("SELECT snapshot->>'order_reference' ref,order_no FROM orders WHERE id=%s",(t['order_id'],)).fetchone()
    text=f"تذكرة {reference} | Ticket {reference}\nالطلب / Order: {order['ref'] or order['order_no']}\n{CATEGORIES[t['category']]}\n{STATES[status]} / {status.replace('_',' ')}\n{note}"
    c.execute('INSERT INTO chat_messages(id,conversation_id,sender_id,text) VALUES(%s,%s,%s,%s)',(uid(),t['conversation_id'],user['id'],text))
    c.execute('INSERT INTO support_ticket_events(id,ticket_id,actor_id,status,note) VALUES(%s,%s,%s,%s,%s)',(uid(),t['id'],user['id'],status,note))
    db.audit(c,user['id'],'ticket.'+status,'ticket',t['id'],{'order_id':t['order_id']})

@router.post('/support-tickets')
def create_ticket(body:TicketBody,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager')
    if not body.note.strip():fail(422,'اكتب وصف المشكلة')
    with db.tx() as c:
        def perform():
            o=order_access(c,user,body.order_id,True)
            v=c.execute("SELECT * FROM conversations WHERE order_id=%s AND category='help' FOR UPDATE",(o['id'],)).fetchone()
            if not v:
                cid=uid();c.execute("INSERT INTO conversations(id,order_id,brand_id,branch_id,customer_id,route,context_service) VALUES(%s,%s,%s,%s,%s,'delivery',%s)",(cid,o['id'],o['brand_id'],o['branch_id'],o['customer_id'],o['service']))
            else:
                cid=v['id']
                if v['route']!='delivery':
                    c.execute("INSERT INTO conversation_transfers(id,conversation_id,from_route,to_route,reason,actor_id) VALUES(%s,%s,%s,'delivery',%s,%s)",(uid(),cid,v['route'],'رفع تذكرة للمتابعة لدى مدير التوصيل',user['id']))
            c.execute("UPDATE conversations SET route='delivery',status='open',assignee_id=%s WHERE id=%s",(user['id'],cid))
            t=c.execute('INSERT INTO support_tickets(id,order_id,conversation_id,category,note,created_by) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *',(uid(),o['id'],cid,body.category,body.note.strip(),user['id'])).fetchone()
            announce(c,t,user,'received',body.note.strip())
            return safe({'ticket':t,'reference':'TKT-'+str(t['ticket_no']).zfill(6)})
        return mutate(c,user,request,'ticket.create',body.model_dump(),perform)

class UpdateTicket(BaseModel):
    model_config=ConfigDict(extra="forbid")
    status:Literal['received','in_progress','awaiting_customer','resolved','closed']
    note:str=Field(min_length=1,max_length=2000)

@router.post('/support-tickets/{tid}')
def update_ticket(tid:str,body:UpdateTicket,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager')
    if not body.note.strip():fail(422,'اكتب تحديثًا واضحًا للعميل')
    with db.tx() as c:
        def perform():
            t=c.execute('SELECT * FROM support_tickets WHERE id=%s',(tid,)).fetchone()
            if not t:fail(404,'التذكرة غير موجودة')
            order_access(c,user,t['order_id'],True)
            t=c.execute('SELECT * FROM support_tickets WHERE id=%s FOR UPDATE',(tid,)).fetchone()
            if body.status=='closed' and t['status']!='resolved':fail(409,'سجل الحل أولاً ثم أغلق الإجراء نهائيًا')
            if t['status']=='closed' and body.status!='in_progress':fail(409,'أعد فتح التذكرة قيد المعالجة أولاً')
            c.execute("UPDATE support_tickets SET status=%s,resolution=CASE WHEN %s='resolved' THEN %s ELSE resolution END,updated_at=now(),closed_at=CASE WHEN %s='closed' THEN now() ELSE NULL END WHERE id=%s",(body.status,body.status,body.note.strip(),body.status,tid))
            announce(c,t,user,body.status,body.note.strip())
            if body.status=='closed':
                c.execute('UPDATE delivery_photos SET expires_at=greatest(expires_at,%s) WHERE order_id=%s AND data IS NOT NULL',(next_cleanup(),t['order_id']))
                if not c.execute("SELECT 1 FROM support_tickets WHERE conversation_id=%s AND status<>'closed'",(t['conversation_id'],)).fetchone():
                    c.execute("UPDATE conversations SET status='resolved' WHERE id=%s",(t['conversation_id'],))
            else:c.execute("UPDATE conversations SET status='in_progress' WHERE id=%s",(t['conversation_id'],))
            return {'id':tid,'status':body.status}
        return mutate(c,user,request,'ticket.update:'+tid,body.model_dump(),perform)

class PhotoBody(BaseModel):
    stage:Literal['picked_up','delivered']
    data_base64:str=Field(max_length=6990510)
    content_type:Literal['image/jpeg','image/png','image/webp']

@router.post('/tasks/{tid}/photos')
def upload_photo(tid:str,body:PhotoBody,request:Request,user=Depends(auth.current_user)):
    roles(user,'driver')
    try:
        raw=base64.b64decode(body.data_base64,validate=True)
        if not 0<len(raw)<=5*1024*1024:fail(413,'الصورة تتجاوز الحجم المسموح')
        from .evidence_images import evidence_jpeg
        data=evidence_jpeg(raw,{'image/jpeg':'JPEG','image/png':'PNG','image/webp':'WEBP'}[body.content_type])
    except (ValueError,OSError,UnidentifiedImageError,Image.DecompressionBombError):fail(422,'صورة غير صالحة')
    digest=hashlib.sha256(data).hexdigest()
    with db.tx() as c:
        def perform():
            t=c.execute('SELECT * FROM driver_tasks WHERE id=%s',(tid,)).fetchone()
            if not t or t['driver_id']!=user['id']:fail(404,'المهمة غير متاحة')
            order_access(c,user,t['order_id'],True)
            t=c.execute('SELECT * FROM driver_tasks WHERE id=%s FOR UPDATE',(tid,)).fetchone()
            if t['driver_id']!=user['id']:fail(404,'المهمة غير متاحة')
            if not db.rate_limit(c,'delivery-photo:'+user['id'],12,60):fail(429,'أرسل الصور بوتيرة أبطأ')
            if t['status'] not in ('accepted','picked_up','problem'):fail(409,'المهمة لا تستقبل صورًا في حالتها الحالية')
            aid=uid();c.execute('INSERT INTO delivery_photos(id,task_id,order_id,uploader_id,stage,data,sha256,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(aid,tid,t['order_id'],user['id'],body.stage,data,digest,next_cleanup()))
            return {'id':aid,'task_id':tid}
        return mutate(c,user,request,'delivery.photo:'+tid,{'stage':body.stage,'sha256':digest},perform)

def require_photo(c,t,user,stage,photo_id):
    if user['role']!='driver' or user['id']!=t['driver_id']:fail(403,'الاستلام والتسليم يؤكدهما السائق المكلف')
    order=c.execute('SELECT branch_id FROM orders WHERE id=%s FOR UPDATE',(t['order_id'],)).fetchone()
    p=c.execute('SELECT id,sha256 FROM delivery_photos WHERE id=%s AND task_id=%s AND uploader_id=%s AND stage=%s AND data IS NOT NULL AND used_at IS NULL FOR UPDATE',(photo_id,t['id'],t['driver_id'],stage)).fetchone()
    if not p:fail(422,'صورة الاستلام أو التسليم مطلوبة لهذه الخطوة')
    c.execute('UPDATE delivery_photos SET used_at=now() WHERE id=%s',(photo_id,))
    # The order is locked by the transition caller. Only evidence actually used
    # for opposite stages counts; abandoned uploads and retries do not count.
    prior=c.execute("SELECT id,stage FROM delivery_photos WHERE order_id=%s AND sha256=%s AND stage<>%s AND used_at IS NOT NULL ORDER BY used_at,id LIMIT 1",(t['order_id'],p['sha256'],stage)).fetchone()
    if prior and not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='driver_evidence.observation' AND details->>'fingerprint'=%s",(t['order_id'],p['sha256'])).fetchone():
        db.event(c,t['order_id'],user['id'],'driver_evidence.observation',{
            'kind':'same_image_across_stages','status':'open','fingerprint':p['sha256'],
            'task_id':t['id'],'driver_id':t['driver_id'],'photo_ids':[prior['id'],p['id']],
            'branch_id':order['branch_id'],
            'note':'تطابق صورة الاستلام والتسليم؛ ملاحظة إدارية للمراجعة، ولا تثبت مخالفة.'})

def evidence_observations(c,oid):
    return c.execute("""SELECT e.id,e.details,e.created_at,u.display_name AS driver_name,
        r.created_at AS read_at,r.actor_id AS read_by,ru.display_name AS read_by_name
        FROM order_events e LEFT JOIN users u ON u.id=e.actor_id
        LEFT JOIN LATERAL (SELECT a.actor_id,a.created_at FROM order_events a
            WHERE a.order_id=e.order_id AND a.event_type='driver_evidence.observation_read'
            AND a.details->>'observation_id'=e.id ORDER BY a.created_at,a.id LIMIT 1) r ON true
        LEFT JOIN users ru ON ru.id=r.actor_id
        WHERE e.order_id=%s AND e.event_type='driver_evidence.observation'
        ORDER BY e.created_at,e.id""",(oid,)).fetchall()

@router.post('/orders/{oid}/evidence-observations/{observation_id}/read')
def read_evidence_observation(oid:str,observation_id:str,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager')
    if not auth.has_permission(user,'orders_view'):fail(403,'غير مصرح بعرض الطلبات')
    with db.tx() as c:
        # Re-authorize before an idempotency replay; removing a branch must
        # immediately revoke access to previously acknowledged private notes.
        order_access(c,user,oid)
        def perform():
            order_access(c,user,oid,True)
            note=c.execute("SELECT id FROM order_events WHERE id=%s AND order_id=%s AND event_type='driver_evidence.observation'",(observation_id,oid)).fetchone()
            if not note:fail(404,'الملاحظة غير متاحة')
            if not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='driver_evidence.observation_read' AND details->>'observation_id'=%s",(oid,observation_id)).fetchone():
                db.event(c,oid,user['id'],'driver_evidence.observation_read',{'observation_id':observation_id,'status':'open','acknowledgement_only':True})
                db.audit(c,user['id'],'driver_evidence.observation_read','order',oid,{'observation_id':observation_id})
            return safe({'observation':next(x for x in evidence_observations(c,oid) if x['id']==observation_id)})
        return mutate(c,user,request,'driver_evidence.read:'+oid+':'+observation_id,{},perform)

@router.get('/orders/{oid}/delivery-photos')
def photos(oid:str,user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager','driver')
    with db.tx() as c:
        order_access(c,user,oid)
        result={'photos':c.execute('SELECT id,stage,created_at,deleted_at,used_at FROM delivery_photos WHERE order_id=%s AND used_at IS NOT NULL ORDER BY created_at',(oid,)).fetchall()}
        if user['role'] in ('owner','delivery_manager'):result['observations']=evidence_observations(c,oid)
        return safe(result)

@router.get('/delivery-photos/{aid}')
def photo(aid:str,user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager','driver')
    with db.tx() as c:
        p=c.execute('SELECT * FROM delivery_photos WHERE id=%s',(aid,)).fetchone()
        if not p:fail(404,'الصورة غير موجودة')
        order_access(c,user,p['order_id'])
        if p['data'] is None:fail(410,'حذفت الصورة حسب مدة الاحتفاظ')
        return Response(bytes(p['data']),media_type='image/jpeg',headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

def cleanup():
    # Holds are excluded before LIMIT so unresolved cases cannot starve other orders.
    with db.tx() as c:
        orders=c.execute("""SELECT o.id FROM orders o WHERE EXISTS
            (SELECT 1 FROM delivery_photos p WHERE p.order_id=o.id AND p.data IS NOT NULL AND p.expires_at<=now())
            AND NOT EXISTS(SELECT 1 FROM support_tickets t WHERE t.order_id=o.id AND t.status<>'closed')
            AND NOT EXISTS(SELECT 1 FROM driver_tasks t WHERE t.order_id=o.id AND t.status='problem')
            AND NOT EXISTS(SELECT 1 FROM conversations v WHERE v.order_id=o.id AND v.status<>'resolved')
            ORDER BY o.id LIMIT 200 FOR UPDATE SKIP LOCKED""").fetchall()
        for o in orders:
            oid=o['id']
            hold=c.execute("SELECT 1 FROM support_tickets WHERE order_id=%s AND status<>'closed' UNION ALL SELECT 1 FROM driver_tasks WHERE order_id=%s AND status='problem' UNION ALL SELECT 1 FROM conversations WHERE order_id=%s AND status<>'resolved' LIMIT 1",(oid,oid,oid)).fetchone()
            if not hold:
                c.execute('UPDATE delivery_photos SET data=NULL,deleted_at=now() WHERE order_id=%s AND expires_at<=now() AND data IS NOT NULL',(oid,))

async def poll_loop():
    while True:
        try:await asyncio.to_thread(cleanup)
        except Exception:logging.getLogger('kps_order').exception('delivery_photo_cleanup_failed')
        await asyncio.sleep(30)

@router.get('/support-daily')
def daily(day:str|None=None,user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager')
    from datetime import date
    try:
        d=date.fromisoformat(day) if day else service_hours.delivery_operational_date()
    except ValueError:fail(422,'تاريخ غير صالح')
    lo=service_hours.parse_reporting_bound(d.isoformat(),delivery=True);hi=lo+timedelta(days=1)
    with db.tx() as c:
        rows=report_tickets(c,user['brand_id'],None if user['role']=='owner' else user.get('branch_ids',[]),lo,hi)
        if user.get('_morning_manager_context'):
            from .morning_manager_context import order_allowed
            rows=[r for r in rows if order_allowed(user,c.execute('SELECT * FROM orders WHERE id=%s',(r['order_id'],)).fetchone(),c)]
        return safe({'day':d,'tickets':rows,'categories':CATEGORIES,'states':STATES,'summary':{
            'created':sum(lo<=r['created_at']<hi for r in rows),
            'closed':sum(bool(r['closed_at'] and lo<=r['closed_at']<hi) for r in rows),
            'open':sum(r['status']!='closed' for r in rows)}})

def report_tickets(c,brand_id,branch_ids,lo,hi):
    # Reconstruct the end-of-period state from immutable events for historical days.
    rows=c.execute("""SELECT t.*, 'TKT-'||lpad(t.ticket_no::text,greatest(6,length(t.ticket_no::text)),'0') reference,
        coalesce(o.snapshot->>'order_reference',o.order_no::text) order_reference,o.branch_id,
        last.status period_status,last.note last_update,last.created_at period_updated_at
        FROM support_tickets t JOIN orders o ON o.id=t.order_id
        JOIN LATERAL (SELECT e.status,e.note,e.created_at FROM support_ticket_events e
            WHERE e.ticket_id=t.id AND e.created_at<%s ORDER BY e.created_at DESC,e.id DESC LIMIT 1) last ON true
        WHERE o.brand_id=%s AND (%s OR o.branch_id=ANY(%s)) AND t.created_at<%s
        AND (last.status<>'closed' OR last.created_at>=%s OR EXISTS
          (SELECT 1 FROM support_ticket_events e WHERE e.ticket_id=t.id AND e.created_at>=%s AND e.created_at<%s))
        ORDER BY t.ticket_no DESC""",(hi,brand_id,branch_ids is None,branch_ids or [],hi,lo,lo,hi)).fetchall()
    for r in rows:
        r['status']=r.pop('period_status');r['updated_at']=r.pop('period_updated_at')
        r['closed_at']=r['updated_at'] if r['status']=='closed' else None
        r['resolution']=r['last_update'] if r['status'] in ('resolved','closed') else ''
    return rows
