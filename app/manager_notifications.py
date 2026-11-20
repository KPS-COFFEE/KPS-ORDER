"""Owner-configured action/report mail, never a new source of staff authority.

Only generic notices leave the application. Resource contents remain behind
normal authenticated screens. Current routing/grants and unresolved state are
rechecked before a queued mail can be sent.
"""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import date
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from . import auth, db, email_delivery, multibrand, service_hours
from .operations import safe

router=APIRouter(prefix='/api/manager-notifications',tags=['manager-notifications'])
ROLES={'owner','delivery_manager','admin'}
TITLES={'action':'طلب يحتاج تدخلك','conversation':'محادثة تحتاج ردك',
        'shift_report':'تقرير شفت معتمد جاهز','daily_report':'التقرير اليومي المكتمل جاهز'}


class Subscription(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    email:str=Field(min_length=3,max_length=254)
    enabled:StrictBool=True
    actions:StrictBool=True
    conversations:StrictBool=True
    shift_reports:StrictBool=True
    daily_reports:StrictBool=True


def principal(c,user_id,brand_id):
    user=c.execute('SELECT u.* FROM users u JOIN brands home ON home.id=u.brand_id AND home.active '
                   'JOIN brands target ON target.id=%s AND target.active WHERE u.id=%s AND u.active',
                   (brand_id,user_id)).fetchone()
    if not user or user['role'] not in ROLES:return None
    user=multibrand.project(c,user,brand_id)
    if not user:return None
    from .customer_message_routing import capabilities
    return dict(user,_message_capabilities=sorted(capabilities(c,user)))


def owner_scope(c,user):
    if user['role']!='owner':raise db.error(403,'MANAGER_NOTICE_OWNER','إعداد الإشعارات للمالك فقط.')
    from .customer_message_routing import owner_brands
    if user['brand_id'] not in owner_brands(c,user):
        raise db.error(403,'MANAGER_NOTICE_SCOPE','إعداد الإشعارات خارج نطاقك.')


@router.get('/subscriptions')
def subscriptions(user=Depends(auth.current_user)):
    with db.tx() as c:
        owner_scope(c,user)
        rows=c.execute('SELECT p.*,u.display_name,u.role FROM manager_notification_subscriptions p '
                       'JOIN users u ON u.id=p.user_id WHERE p.brand_id=%s ORDER BY u.display_name,p.id',
                       (user['brand_id'],)).fetchall()
    return safe({'subscriptions':rows,'mail_configuration':email_delivery.configuration(),
                 'note':'عنوان إشعارات مصرح من المالك؛ لا يغير بريد الدخول أو يوثّق ملكية البريد.'})


@router.put('/subscriptions/{user_id}')
def configure(user_id:str,body:Subscription,user=Depends(auth.current_user)):
    email=email_delivery.normalize_email(body.email)
    with db.tx() as c:
        owner_scope(c,user)
        auth.require_owner_sms_step_up(c,user)
        target=principal(c,user_id,user['brand_id'])
        if target and user.get('home_brand_id') and not set(target.get('branch_ids') or []) <= set(user.get('branch_ids') or []):
            target=None
        if not target:raise db.error(404,'MANAGER_NOTICE_RECIPIENT','الموظف غير متاح ضمن البراند وصلاحياتك.')
        row=c.execute('INSERT INTO manager_notification_subscriptions '
            '(id,user_id,brand_id,recipient_email,enabled,actions,conversations,shift_reports,daily_reports,configured_by) '
            'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id,brand_id) DO UPDATE SET '
            'recipient_email=EXCLUDED.recipient_email,enabled=EXCLUDED.enabled,actions=EXCLUDED.actions,'
            'conversations=EXCLUDED.conversations,shift_reports=EXCLUDED.shift_reports,daily_reports=EXCLUDED.daily_reports,'
            'configured_by=EXCLUDED.configured_by,configured_source=\'owner_ui\',revision=manager_notification_subscriptions.revision+CASE WHEN '
            '(manager_notification_subscriptions.recipient_email,manager_notification_subscriptions.enabled,'
            'manager_notification_subscriptions.actions,manager_notification_subscriptions.conversations,'
            'manager_notification_subscriptions.shift_reports,manager_notification_subscriptions.daily_reports) IS DISTINCT FROM '
            '(EXCLUDED.recipient_email,EXCLUDED.enabled,EXCLUDED.actions,EXCLUDED.conversations,EXCLUDED.shift_reports,EXCLUDED.daily_reports) '
            'THEN 1 ELSE 0 END,updated_at=now(),scanned_at=NULL '
            'RETURNING *', (db.new_id('mnsub_'),user_id,user['brand_id'],email,body.enabled,body.actions,
                           body.conversations,body.shift_reports,body.daily_reports,user['id'])).fetchone()
        db.audit(c,user['id'],'manager_notifications.configured','user',user_id,
                 {'brand_id':user['brand_id'],'enabled':body.enabled,'subscription_id':row['id'],'revision':row['revision']})
    return safe({'subscription':row,'mail_configuration':email_delivery.configuration(),
                 'reports_available':auth.has_permission(target,'reports_view'),
                 'note':'حُفظ إعداد الإشعارات؛ التوصيل يتطلب مزود بريد مهيأ واشتراك إشعارات مفعّل. لم يتغير بريد الدخول.'})


def _permitted(call,user):
    try:return call(user=user)
    except HTTPException as exc:
        if exc.status_code in {403,404}:return {}
        raise


def event(category,resource_id,version=''):
    return dict(category=category,resource_id=str(resource_id),resource_version=str(version),
                event_key=':'.join([category,str(resource_id),str(version)]))


def current_events(user,pref):
    """Canonical inboxes determine who owes an action, never a name or email match."""
    from . import chat, operations, order_amendments, order_interventions, order_support, reporting, operating_reports
    events=[]
    if pref['actions']:
        if auth.has_permission(user,'operational_alerts') and auth.has_permission(user,'orders_view') and user['role'] in {'owner','delivery_manager'}:
            alerts=_permitted(operations.operational_alerts,user)
            for row in alerts.get('unassigned',[]):events.append(event('action',row['id'],'dispatch'))
            for row in alerts.get('problems',[]):events.append(event('action',row['id'],'problem:'+row['task_id']))
            for row in alerts.get('printing',[]):
                if row.get('order_id'):events.append(event('action',row['order_id'],'print:'+row['id']))
        if auth.has_permission(user,'orders_view') and auth.has_permission(user,'orders_amend') and user['role'] in {'owner','delivery_manager'}:
            changes=_permitted(order_amendments.inbox,user)
            for row in [*changes.get('orders',[]),*changes.get('requests',[])]:
                events.append(event('action',row.get('order_id') or row['id'],'amend:'+str(row.get('amendment_id') or row.get('request_id') or row['id'])+':'+str(row.get('amendment_revision',''))))
        if auth.has_permission(user,'support_manage') and user['role'] in {'owner','delivery_manager'}:
            tickets=_permitted(order_support.tickets,user)
            for row in tickets.get('tickets',[]):
                if row['status'] in {'received','in_progress'}:events.append(event('action',row['order_id'],'ticket:'+row['id']))
        # Admin receives only intervention decisions explicitly granted to them.
        interventions=_permitted(order_interventions.pending_cancellations,user)
        for row in interventions.get('requests',[]):events.append(event('action',row['order_id'],'intervention:'+row['id']+':'+str((row.get('workflow') or {}).get('revision',0))))
    if pref['conversations'] and auth.has_permission(user,'chat_view') and auth.has_permission(user,'chat_reply'):
        for row in _permitted(chat.reply_alerts,user).get('awaiting_reply',[]):
            events.append(event('conversation',row['id'],row['seq']))
    if auth.has_permission(user,'reports_view') and (pref['shift_reports'] or pref['daily_reports']):
        with db.tx() as c:
            reports=c.execute("SELECT * FROM closure_reports WHERE brand_id=%s AND kind='manager' "
                              "AND created_at>=%s AND snapshot->'shift'->>'status'='closed' "
                              'ORDER BY created_at DESC,id DESC LIMIT 100',(user['brand_id'],pref['created_at'])).fetchall()
            reports=[r for r in reports if reporting.can_read(user,r)]
            if pref['shift_reports']:
                events.extend(event('shift_report',r['id'],r['version']) for r in reports)
            # The canonical daily presentation aggregates authorized brands.
            # Queue it through the home-brand subscription once per manager/day.
            if pref['daily_reports'] and not user.get('home_brand_id'):
                days=sorted({str(r['snapshot'].get('operational_date')) for r in reports if r['snapshot'].get('operational_date')},reverse=True)[:7]
                for day in days:
                    # A closed morning duty cannot certify the rest of a live
                    # operational day before its later duties have even opened.
                    if date.fromisoformat(day)>=service_hours.delivery_operational_date():continue
                    if day in pref.get('_queued_daily_days',[]):continue
                    if pref.get('_daily_resource') and day!=pref['_daily_resource']:continue
                    try:
                        canonical=operating_reports.build(c,user,date.fromisoformat(day),'day')
                    except HTTPException as exc:
                        if exc.status_code in {403,404,409}:continue
                        raise
                    if canonical['operational_complete'] and not canonical['evidence_missing']:
                        events.append(event('daily_report',day))
    return {e['event_key']:e for e in events}


def public_base():
    base=os.getenv('PUBLIC_BASE_URL','').rstrip('/')
    parsed=urlsplit(base)
    if (not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.scheme not in ({'http','https'} if os.getenv('APP_ENV')=='testing' else {'https'})):
        return None
    return base


def payload(notice):
    base=public_base()
    if not base:return None
    link=base+'/ops?'+urlencode({'manager_notification':notice['id'],'operations_brand':notice['brand_id']})
    title=TITLES[notice['category']]
    return {'subject':title,'text':title+'\nافتح السجل من حسابك المصرح:\n'+link+
            '\nهذا إشعار فقط؛ فتح البريد لا ينفذ إجراءً أو اعتمادًا.'}


def notice_preferences(row):
    flags={'action':'actions','conversation':'conversations','shift_report':'shift_reports','daily_report':'daily_reports'}
    selected=flags[row['category']]
    return dict(row,created_at=row['subscription_created_at'],
                **{flag:bool(row.get(flag)) and flag==selected for flag in flags.values()},
                _daily_resource=row['resource_id'] if row['category']=='daily_report' else None)


def resource_authorized(c,user,row):
    from . import reporting, operating_reports
    if row['category']=='shift_report':
        if not row.get('shift_reports') or not auth.has_permission(user,'reports_view'):return False
        report=c.execute('SELECT * FROM closure_reports WHERE id=%s AND brand_id=%s',
                         (row['resource_id'],row['brand_id'])).fetchone()
        return bool(report and str(report['version'])==row['resource_version']
                    and report['snapshot'].get('shift',{}).get('status')=='closed'
                    and reporting.can_read(user,report))
    if row['category']=='daily_report':
        if (not row.get('daily_reports') or user.get('home_brand_id')
                or not auth.has_permission(user,'reports_view')):return False
        try:
            day=date.fromisoformat(row['resource_id'])
            if day>=service_hours.delivery_operational_date():return False
            report=operating_reports.build(c,user,day,'day')
            return bool(report['operational_complete'] and not report['evidence_missing'])
        except (ValueError,HTTPException):return False
    return row['event_key'] in current_events(user,notice_preferences(row))


def authorized(c,mail):
    row=c.execute('SELECT n.*,p.recipient_email,p.enabled,p.revision,p.actions,p.conversations,p.shift_reports,'
                  'p.daily_reports,p.created_at subscription_created_at FROM manager_email_notifications n '
                  'JOIN manager_notification_subscriptions p ON p.id=n.subscription_id '
                  'WHERE n.email_outbox_id=%s',(mail['id'],)).fetchone()
    if not row or not row['enabled'] or row['subscription_revision']!=row['revision'] or row['recipient_email']!=mail['recipient']:
        return False
    user=principal(c,row['recipient_user_id'],row['brand_id'])
    if not user:return False
    return resource_authorized(c,user,row)


@router.get('/{notice_id}')
def detail(notice_id:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        row=c.execute('SELECT n.*,p.enabled,p.revision,p.recipient_email,p.actions,p.conversations,p.shift_reports,'
                      'p.daily_reports,p.created_at subscription_created_at FROM manager_email_notifications n '
                      'JOIN manager_notification_subscriptions p ON p.id=n.subscription_id '
                      'WHERE n.id=%s AND n.recipient_user_id=%s',(notice_id,user['id'])).fetchone()
        if not row or row['brand_id']!=user['brand_id'] or not row['enabled'] or row['revision']!=row['subscription_revision']:
            raise db.error(404,'MANAGER_NOTICE_NOT_FOUND','الإشعار غير متاح.')
        current=principal(c,user['id'],row['brand_id'])
        if not current or not resource_authorized(c,current,row):
            raise db.error(404,'MANAGER_NOTICE_COMPLETE','الإجراء أُنجز أو لم يعد متاحًا لحسابك.')
        return {'category':row['category'],'resource_id':row['resource_id'],'brand_id':row['brand_id']}


def process_once(limit=5):
    if not email_delivery.configuration()['configured'] or not public_base():
        return {'subscriptions_scanned':0,'queued':0}
    with db.tx() as c:
        prefs=c.execute('SELECT * FROM manager_notification_subscriptions WHERE enabled '
                        'ORDER BY scanned_at NULLS FIRST,id LIMIT %s FOR UPDATE SKIP LOCKED',(max(1,min(limit,20)),)).fetchall()
        for pref in prefs:c.execute('UPDATE manager_notification_subscriptions SET scanned_at=now() WHERE id=%s',(pref['id'],))
    queued=0
    for pref in prefs:
        with db.tx() as c:
            user=principal(c,pref['user_id'],pref['brand_id'])
            pref['_queued_daily_days']=[row['resource_id'] for row in c.execute(
                "SELECT resource_id FROM manager_email_notifications WHERE subscription_id=%s AND subscription_revision=%s "
                "AND category='daily_report' AND email_outbox_id IS NOT NULL",(pref['id'],pref['revision'])).fetchall()]
        if not user:continue
        events=current_events(user,pref)
        with db.tx() as c:
            live=c.execute('SELECT * FROM manager_notification_subscriptions WHERE id=%s AND enabled FOR UPDATE',(pref['id'],)).fetchone()
            if not live or live['revision']!=pref['revision']:continue
            if not events:continue
            additions=[dict(item,id=db.new_id('mnotice_')) for item in events.values()]
            c.execute('INSERT INTO manager_email_notifications '
                '(id,subscription_id,recipient_user_id,brand_id,subscription_revision,event_key,category,resource_id,resource_version) '
                'SELECT e.id,%s,%s,%s,%s,e.event_key,e.category,e.resource_id,e.resource_version '
                'FROM jsonb_to_recordset(%s::jsonb) AS e(id text,event_key text,category text,resource_id text,resource_version text) WHERE TRUE '
                'ON CONFLICT(subscription_id,subscription_revision,event_key) DO NOTHING',
                (pref['id'],pref['user_id'],pref['brand_id'],pref['revision'],db.json(additions)))
            waiting=c.execute('SELECT * FROM manager_email_notifications WHERE subscription_id=%s '
                'AND subscription_revision=%s AND event_key=ANY(%s) AND email_outbox_id IS NULL '
                'ORDER BY created_at,id LIMIT 30 FOR UPDATE SKIP LOCKED',
                (pref['id'],pref['revision'],list(events))).fetchall()
            for notice in waiting:
                body=payload(notice)
                if not body:continue
                mid=email_delivery.enqueue_mail(c,brand_id=pref['brand_id'],recipient=pref['recipient_email'],
                    kind='manager_notification',payload=body,dedupe_key='manager-notification:'+notice['id'])
                if mid:
                    c.execute('UPDATE manager_email_notifications SET email_outbox_id=%s WHERE id=%s',(mid,notice['id']))
                    queued+=1
    return {'subscriptions_scanned':len(prefs),'queued':queued}


async def poll_loop():
    while True:
        try:await asyncio.to_thread(process_once)
        except Exception:logging.getLogger('kps_order').warning('manager_notification_retry_pending')
        await asyncio.sleep(30)
