"""Owner-selected notification addresses, independent of sign-in identities.

Use the existing encrypted outbox and canonical operational read permissions.
No provider call occurs in a request or business transaction.
"""
import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import auth, db, email_delivery, multibrand, service_hours

router = APIRouter(prefix='/api/manager-mail', tags=['manager-mail'])
KEY = 'manager_mail'


class Preferences(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: str = Field(max_length=254)
    enabled: StrictBool = False
    chats: StrictBool = True
    interventions: StrictBool = True
    reports: StrictBool = True
    reason: str = Field(min_length=3, max_length=500)


def principal(c, uid, brand):
    user = c.execute('SELECT * FROM users WHERE id=%s AND active', (uid,)).fetchone()
    if not user or user['role'] not in {'owner', 'delivery_manager'}:
        return None
    if not c.execute('SELECT 1 FROM brands WHERE id=%s AND active', (brand,)).fetchone():
        return None
    return multibrand.project(c, user, brand)


def preferences(c, uid, brand):
    row = c.execute('SELECT settings FROM app_settings WHERE brand_id=%s', (brand,)).fetchone()
    return ((row or {}).get('settings', {}).get(KEY) or {}).get(uid)


def fingerprint(user):
    return hashlib.sha256(json.dumps([user['role'], sorted(user.get('branch_ids') or []),
        sorted(auth.employee_permissions.effective(user))], sort_keys=True).encode()).hexdigest()


def owner_target(c, user, uid):
    target = principal(c, uid, user['brand_id'])
    if (not target or user.get('home_brand_id') and
            not set(target.get('branch_ids') or []) <= set(user.get('branch_ids') or [])):
        raise db.error(404, 'MANAGER_MAIL_TARGET', 'حساب المدير غير متاح ضمن نطاقك.')
    return target


@router.get('/staff/{uid}')
def read(uid: str, user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        owner_target(c, user, uid)
        pref = preferences(c, uid, user['brand_id']) or {}
        return {'preferences': {key: pref.get(key, False if key == 'enabled' else '' if key == 'email' else True)
            for key in ('email', 'enabled', 'chats', 'interventions', 'reports')},
            'channel': email_delivery.configuration()}


@router.put('/staff/{uid}')
def update(uid: str, body: Preferences, user=Depends(auth.require_roles('owner'))):
    email = email_delivery.normalize_email(body.email)
    with db.tx() as c:
        auth.require_owner_sms_step_up(c, user)
        owner_target(c, user, uid)
        row = c.execute('SELECT settings FROM app_settings WHERE brand_id=%s FOR UPDATE',
                        (user['brand_id'],)).fetchone()
        if not row:
            raise db.error(409, 'MANAGER_MAIL_SETTINGS', 'إعدادات البراند غير جاهزة.')
        settings = dict(row['settings'])
        saved = dict(settings.get(KEY) or {})
        pref = {**body.model_dump(exclude={'reason'}), 'email': email,
                'version': secrets.token_hex(12), 'configured_at': datetime.now(ZoneInfo('UTC')).isoformat()}
        saved[uid] = pref
        settings[KEY] = saved
        c.execute('UPDATE app_settings SET settings=%s WHERE brand_id=%s', (db.json(settings), user['brand_id']))
        db.audit(c, user['id'], 'manager_mail.updated', 'user', uid,
                 {'brand_id': user['brand_id'], 'enabled': body.enabled, 'reason': body.reason})
    return {'saved': True, 'enabled': body.enabled, 'provider_configured': email_delivery.configuration()['configured']}


def base_url():
    base = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
    url = urlsplit(base)
    if (url.scheme != 'https' or not url.netloc or url.username or url.password or url.query or url.fragment):
        return None
    return base


def authorized(c, row, payload):
    meta = payload.get('_manager_mail') or {}
    pref = preferences(c, meta.get('uid'), row['brand_id'])
    user = principal(c, meta.get('uid'), row['brand_id'])
    kind = meta.get('category')
    if not (pref and user and pref.get('enabled') and pref.get(kind) and
            pref.get('email') == row['recipient'] and pref.get('version') == meta.get('version') and
            fingerprint(user) == meta.get('scope')):
        return False
    if kind == 'chats':
        from . import chat
        conversation = c.execute('SELECT * FROM conversations WHERE id=%s', (meta.get('resource'),)).fetchone()
        return bool(conversation and conversation['status'] != 'resolved' and chat.allowed(user, conversation, c) and
                    auth.has_permission(user, 'chat_view') and auth.has_permission(user, 'chat_reply'))
    if kind == 'interventions':
        from . import order_interventions
        order = c.execute('SELECT * FROM orders WHERE id=%s', (meta.get('resource'),)).fetchone()
        return bool(order and auth.can_access_order(user, order, c) and order_interventions.can_review(user))
    return kind == 'reports' and auth.has_permission(user, 'orders_view') and auth.has_permission(user, 'reports_view')


def enqueue(c, user, pref, category, event, resource, title, text):
    base = base_url()
    if not base:
        return None
    payload = {'subject': title, 'text': title + '\n' + text + '\n' + base + '/ops?' + urlencode({'operations_brand': user['brand_id']}),
        '_manager_mail': {'uid': user['id'], 'version': pref['version'], 'scope': fingerprint(user),
                          'category': category, 'resource': resource}}
    # Config edits do not re-send every old event; identity is user/brand/event.
    return email_delivery.enqueue_mail(c, brand_id=user['brand_id'], recipient=pref['email'],
        kind='manager_notice', payload=payload, dedupe_key='manager-notice:' + user['id'] + ':' + category + ':' + event)


def process_once():
    """Bounded polling, shared durable dedupe, no changes to orders or accounting."""
    if not email_delivery.configuration()['configured'] or not base_url():
        return {'configured': False}
    with db.tx() as c:
        configs = c.execute("SELECT brand_id,settings->%s preferences FROM app_settings WHERE settings ? %s",
                            (KEY, KEY)).fetchall()
    for config in configs:
        for uid, pref in (config['preferences'] or {}).items():
            if not pref.get('enabled'):
                continue
            with db.tx() as c:
                user = principal(c, uid, config['brand_id'])
            if not user:
                continue
            events = []
            if pref.get('chats') and auth.has_permission(user, 'chat_view') and auth.has_permission(user, 'chat_reply'):
                from . import chat
                for row in chat.reply_alerts(user=user)['awaiting_reply']:
                    with db.tx() as c:
                        current = c.execute('SELECT status FROM conversations WHERE id=%s', (row['id'],)).fetchone()
                    if not current or current['status'] == 'resolved':
                        continue
                    events.append(('chats', row['id'] + ':' + str(row['seq']), row['id'],
                        'محادثة تحتاج ردك', 'توجد رسالة تحتاج متابعتك. افتح المحادثات داخل البرنامج.'))
            if pref.get('interventions'):
                from . import order_interventions
                if order_interventions.can_review(user):
                    for row in order_interventions.pending_cancellations(user=user)['requests']:
                        events.append(('interventions', row['id'] + ':' + str(row['workflow'].get('revision', 0)), row['order_id'],
                            'طلب يحتاج تدخلك', 'توجد معاملة بانتظار مراجعتك. افتح طلبات المعالجة داخل البرنامج.'))
            if pref.get('reports') and auth.has_permission(user, 'reports_view') and auth.has_permission(user, 'orders_view'):
                day = service_hours.delivery_operational_date() - timedelta(days=1)
                with db.tx() as c:
                    done = c.execute('SELECT 1 FROM email_outbox WHERE brand_id=%s AND dedupe_key=%s',
                        (user['brand_id'], 'manager-notice:' + uid + ':reports:' + day.isoformat())).fetchone()
                if not done:
                    from . import branch_proximity
                    result = branch_proximity.report(Response(), day=day, user=user)
                    lines = ['اليوم التشغيلي: ' + day.isoformat(), 'عدد طلبات التوصيل ضمن نطاقك: ' + str(len(result['rows'])),
                        'الطلب | الحي | فرع التحضير | الفرع الأقرب جغرافيًا']
                    lines += [' | '.join(str(row.get(key) or '—') for key in
                        ('reference', 'neighborhood', 'preparation_branch_name', 'nearest_branch_name')) for row in result['rows']]
                    lines += [result['note'], 'التقفيلات واعتمادها المحاسبي داخل البرنامج ضمن صلاحياتك.']
                    events.append(('reports', day.isoformat(), day.isoformat(), 'التقرير اليومي للتشغيل', '\n'.join(lines)))
            with db.tx() as c:
                live_pref = preferences(c, uid, config['brand_id'])
                live_user = principal(c, uid, config['brand_id'])
                if not live_user or live_pref != pref or fingerprint(live_user) != fingerprint(user):
                    continue
                for args in events:
                    enqueue(c, live_user, pref, *args)
    return {'configured': True}


async def poll_loop():
    import asyncio
    import logging
    while True:
        try:
            await asyncio.to_thread(process_once)
        except Exception as exc:
            logging.getLogger('kps_order').warning('manager_mail_retry type=%s', type(exc).__name__)
        await asyncio.sleep(60)
