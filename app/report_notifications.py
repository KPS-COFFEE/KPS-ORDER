"""Durable product inbox and shared mail outbox for final manager reports.

Enqueue is part of the same approval transaction; delivery and rendering occur
only after commit and can never create a collection, cash event or second close.
"""
from __future__ import annotations
import asyncio
import os
from urllib.parse import urlsplit
from fastapi import APIRouter,Depends,Request
from . import auth,db,email_delivery
from .operations import safe,roles,fail

router=APIRouter(prefix='/api/report-notifications',tags=['report-notifications'])


def _queue_mail(c,notification):
    if notification.get('email_outbox_id'):return notification['email_outbox_id']
    channel=c.execute("SELECT enabled,state FROM identity_channels WHERE brand_id=%s AND channel='email'",(notification['brand_id'],)).fetchone()
    if not channel or not channel['enabled'] or channel['state']!='active':return None
    owner=c.execute("SELECT id,email,email_verified_at FROM users WHERE id=%s AND brand_id=%s AND role='owner' AND active AND email_verified_at IS NOT NULL",(notification['recipient_user_id'],notification['brand_id'])).fetchone()
    if not owner or not owner['email']:return None
    base=os.getenv('PUBLIC_BASE_URL','').rstrip('/')
    parsed=urlsplit(base)
    if parsed.scheme not in ('https','http') or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:return None
    if parsed.scheme!='https' and os.getenv('APP_ENV')!='testing':return None
    source=c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',(notification['report_id'],)).fetchone()
    if not source:return None
    snap=source['snapshot'];link=base+'/api/reports/'+notification['report_id']+'/renderings/v5/print'
    title='تقرير تقفيلة مدير التوصيل المعتمد'
    # Only a small non-personal summary, then authenticated link. Never bearer URLs.
    payload={'subject':title,'text':title+'\nالتاريخ التشغيلي: '+str(snap.get('operational_date',''))+'\nعدد الطلبات المكتملة: '+str(snap.get('summary',{}).get('completed_orders',0))+'\nافتح التقرير بتسجيل الدخول المصرح:\n'+link+'\nمرجع التقرير: '+notification['report_id']+'\nإصدار القالب: '+str(notification['template_version'])}
    mid=email_delivery.enqueue_mail(c,brand_id=notification['brand_id'],recipient=owner['email'],kind='owner_report',payload=payload,dedupe_key='owner-report:'+notification['report_id']+':'+owner['id']+':v'+str(notification['template_version']))
    if mid:c.execute('UPDATE report_notifications SET email_outbox_id=%s WHERE id=%s AND email_outbox_id IS NULL',(mid,notification['id']))
    return mid


def enqueue_manager_closure(c,rid):
    """Idempotent durable notice; intentionally no provider/network call."""
    from .reporting import TEMPLATE_VERSION,queue_v5_renderings
    r=c.execute("SELECT id,kind,brand_id,snapshot FROM closure_reports WHERE id=%s",(rid,)).fetchone()
    if not r or r['kind']!='manager' or r['snapshot'].get('shift',{}).get('status')!='closed':return
    queue_v5_renderings(c,rid,r['snapshot'])
    owners=c.execute("SELECT id FROM users WHERE brand_id=%s AND role='owner' AND active ORDER BY id",(r['brand_id'],)).fetchall()
    for owner in owners:
        c.execute('INSERT INTO report_notifications(id,report_id,recipient_user_id,brand_id,template_version) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(report_id,recipient_user_id,template_version) DO NOTHING',(db.new_id('notice_'),rid,owner['id'],r['brand_id'],TEMPLATE_VERSION))
        notice=c.execute('SELECT * FROM report_notifications WHERE report_id=%s AND recipient_user_id=%s AND template_version=%s',(rid,owner['id'],TEMPLATE_VERSION)).fetchone()
        # The durable inbox is atomic; mail preparation is after commit in process_once.


def _notification(c,user,nid):
    roles(user,'owner')
    row=c.execute('SELECT * FROM report_notifications WHERE id=%s AND recipient_user_id=%s AND brand_id=%s',(nid,user['id'],user['brand_id'])).fetchone()
    if not row:fail(404,'الإشعار غير موجود')
    return row


@router.get('')
def inbox(offset:int=0,user=Depends(auth.current_user)):
    roles(user,'owner')
    if offset<0 or offset>100000:fail(422,'صفحة غير صالحة')
    with db.tx() as c:
        rows=c.execute("SELECT n.*,coalesce(e.status,'waiting_channel') delivery_status,e.last_error delivery_error,e.attempts delivery_attempts,e.delivery_at,g.status rendering_status,g.error rendering_error,r.shift_id,r.snapshot->>'operational_date' operational_date FROM report_notifications n JOIN closure_reports r ON r.id=n.report_id LEFT JOIN email_outbox e ON e.id=n.email_outbox_id LEFT JOIN report_renderings g ON g.report_id=n.report_id AND g.template_version=n.template_version AND g.view_kind='owner' WHERE n.recipient_user_id=%s AND n.brand_id=%s ORDER BY n.created_at DESC,n.id LIMIT 50 OFFSET %s",(user['id'],user['brand_id'],offset)).fetchall()
        unread=c.execute('SELECT count(*) n FROM report_notifications WHERE recipient_user_id=%s AND brand_id=%s AND read_at IS NULL',(user['id'],user['brand_id'])).fetchone()['n']
    for row in rows:
        row['title']='تقرير تقفيلة مدير التوصيل - معتمد نهائيا'
        prefix='/api/reports/'+row['report_id']+'/renderings/v5/'
        row.update(print_url=prefix+'print',pdf_url=prefix+'pdf',csv_url=prefix+'csv')
    return safe({'notifications':rows,'unread_count':unread,'offset':offset,'limit':50})


@router.post('/{nid}/read')
def mark_read(nid:str,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        _notification(c,user,nid)
        c.execute('UPDATE report_notifications SET read_at=coalesce(read_at,now()) WHERE id=%s',(nid,))
        return safe({'notification':c.execute('SELECT * FROM report_notifications WHERE id=%s',(nid,)).fetchone()})


@router.post('/{nid}/retry')
def retry_notification(nid:str,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        notice=_notification(c,user,nid)
        if not db.rate_limit(c,'report-notice-retry:'+user['id'],10,60):fail(429,'انتظر قليلا قبل إعادة المحاولة')
        mid=_queue_mail(c,notice)
        if mid:
            mail=c.execute('SELECT status,last_error FROM email_outbox WHERE id=%s',(mid,)).fetchone()
            if mail and mail.get('last_error')=='EMAIL_DELIVERY_UNCERTAIN':fail(409,'نتيجة الإرسال غير مؤكدة؛ يلزم مراجعة المزود قبل إعادة الإرسال')
            # Provider acceptance/delivery is never resent through a retry button.
            c.execute("UPDATE email_outbox SET status='pending',attempts=0,next_attempt_at=now(),last_error=NULL,updated_at=now() WHERE id=%s AND status='failed'",(mid,))
        c.execute("UPDATE report_renderings SET next_attempt_at=now() WHERE report_id=%s AND template_version=%s AND status='failed'",(notice['report_id'],notice['template_version']))
        status=c.execute('SELECT status FROM email_outbox WHERE id=%s',(mid,)).fetchone() if mid else None
        db.audit(c,user['id'],'report.notification.retry','report_notification',nid,{'report_id':notice['report_id'],'template_version':notice['template_version']})
    return {'notification_id':nid,'delivery_status':status['status'] if status else 'waiting_channel','report_id':notice['report_id']}


def process_once(limit=5):
    """Recover interrupted pending generations and late-configured mail channels."""
    from .reporting import generate_files,generate_v5_files
    with db.tx() as c:
        originals=c.execute("SELECT id FROM closure_reports WHERE pdf_status='pending' ORDER BY created_at LIMIT %s",(limit,)).fetchall()
        renderings=c.execute("SELECT DISTINCT report_id FROM report_renderings WHERE status IN ('pending','failed') AND attempts<8 AND next_attempt_at<=now() LIMIT %s",(limit,)).fetchall()
        notices=c.execute('SELECT * FROM report_notifications WHERE email_outbox_id IS NULL ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED',(max(1,min(limit,20)),)).fetchall()
        for notice in notices:_queue_mail(c,notice)
    for row in originals:generate_files(row['id'])
    for row in renderings:generate_v5_files(row['report_id'])
    return {'original_reports':len(originals),'rendering_reports':len(renderings),'waiting_notices':len(notices)}


async def poll_loop():
    while True:
        try:await asyncio.to_thread(process_once)
        except Exception:
            # A transient DB/runtime failure doesn't terminate the retry worker.
            import logging
            logging.getLogger('kps_order').warning('report_worker_retry_pending')
        await asyncio.sleep(20)
