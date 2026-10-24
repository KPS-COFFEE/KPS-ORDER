"""One durable encrypted email outbox; provider acceptance is distinct from delivery."""
from __future__ import annotations

import asyncio
import base64
import json
import hashlib
import hmac
import logging
import time
import os
import re
import secrets
import smtplib
import ssl
from email.message import EmailMessage

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, Request

from . import db


def normalize_email(value: str) -> str:
    value = value.strip().casefold()
    if len(value) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", value):
        raise db.error(422, 'EMAIL_INVALID', 'أدخل بريدًا إلكترونيًا صحيحًا.')
    if '..' in value or any(ch in value for ch in '\r\n\x00'):
        raise db.error(422, 'EMAIL_INVALID', 'البريد غير صالح.')
    return value


def _key():
    try:
        key = base64.urlsafe_b64decode(os.getenv('MAIL_OUTBOX_KEY', ''))
        return key if len(key) == 32 else None
    except (ValueError, TypeError):
        return None


def configuration():
    provider = os.getenv('EMAIL_PROVIDER', 'disabled')
    sender = os.getenv('EMAIL_FROM', '')
    try:
        normalize_email(sender)
        valid_sender = True
    except Exception:
        valid_sender = False
    configured = bool(_key() and valid_sender and (
        provider == 'resend' and os.getenv('RESEND_API_KEY') or
        provider == 'smtp_ssl' and os.getenv('SMTP_HOST') and os.getenv('SMTP_USERNAME') and os.getenv('SMTP_PASSWORD')))
    return {'provider': provider if provider in {'resend','smtp_ssl'} else 'disabled',
            'configured': configured, 'encrypted_outbox': bool(_key()),
            'delivery_proven': False}


def enqueue_mail(conn, *, brand_id: str, recipient: str, kind: str, payload: dict, dedupe_key: str):
    """Atomic with the business operation. Never place raw login secrets in storage."""
    key = _key()
    if not key or not configuration()['configured']:
        return None
    recipient = normalize_email(recipient)
    identifier = db.new_id('mail_')
    nonce = secrets.token_bytes(12)
    encrypted = nonce + AESGCM(key).encrypt(nonce, json.dumps(payload, ensure_ascii=False).encode(),
                                           (brand_id + ':' + recipient + ':' + kind).encode())
    conn.execute('INSERT INTO email_outbox(id,brand_id,recipient,kind,dedupe_key,encrypted_payload) '
                 'VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(brand_id,dedupe_key) DO NOTHING',
                 (identifier,brand_id,recipient,kind,dedupe_key,encrypted))
    return conn.execute('SELECT id FROM email_outbox WHERE brand_id=%s AND dedupe_key=%s',
                        (brand_id,dedupe_key)).fetchone()['id']


def _payload(row):
    blob = bytes(row['encrypted_payload'])
    return json.loads(AESGCM(_key()).decrypt(blob[:12],blob[12:],
                      (row['brand_id'] + ':' + row['recipient'] + ':' + row['kind']).encode()))


def _send(row):
    payload = _payload(row)
    sender = normalize_email(os.environ['EMAIL_FROM'])
    if os.getenv('EMAIL_PROVIDER') == 'resend':
        # Fixed authoritative API URL; configuration never supplies arbitrary fetch URLs.
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            result = client.post('https://api.resend.com/emails',
                headers={'Authorization':'Bearer '+os.environ['RESEND_API_KEY'],
                         'Idempotency-Key':row['id']},
                json={'from':sender,'to':[row['recipient']], 'subject':payload['subject'],
                      'text':payload['text'], **({'html':payload['html']} if payload.get('html') else {})})
        if not 200 <= result.status_code < 300:
            raise RuntimeError('EMAIL_PROVIDER_REJECTED')
        reference = result.json().get('id')
        if not reference:
            raise RuntimeError('EMAIL_PROVIDER_RESPONSE_INVALID')
        return str(reference)[:200]
    host = os.environ['SMTP_HOST']
    if not re.fullmatch(r'[A-Za-z0-9.-]{1,253}',host):
        raise RuntimeError('SMTP_HOST_INVALID')
    port = int(os.getenv('SMTP_PORT','465'))
    if port != 465:
        raise RuntimeError('SMTP_SSL_PORT_REQUIRED')
    message = EmailMessage()
    message['From'],message['To'],message['Subject'] = sender,row['recipient'],payload['subject']
    message['Message-ID'] = '<'+row['id']+'@'+sender.split('@')[1]+'>'
    message.set_content(payload['text'])
    if payload.get('html'):
        message.add_alternative(payload['html'], subtype='html')
    with smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context()) as smtp:
        smtp.login(os.environ['SMTP_USERNAME'],os.environ['SMTP_PASSWORD'])
        rejected = smtp.send_message(message)
        if rejected:
            raise RuntimeError('SMTP_RECIPIENT_REJECTED')
    return message['Message-ID']


def dispatch_once(limit=10):
    if not configuration()['configured']:
        return {'attempted':0, 'provider_accepted':0, 'configured':False}
    accepted = 0
    attempted = 0
    for _ in range(max(1,min(20,limit))):
        with db.tx() as conn:
            row = conn.execute("SELECT * FROM email_outbox WHERE ((status IN ('pending','failed') AND attempts<8 AND next_attempt_at<=now()) OR (status='sending' AND lease_until<now())) AND (kind NOT IN ('owner_report','operations_alert') OR EXISTS(SELECT 1 FROM identity_channels ic WHERE ic.brand_id=email_outbox.brand_id AND ic.channel='email' AND ic.enabled AND ic.state='active')) AND (kind<>'identity' OR EXISTS(SELECT 1 FROM identity_channels ic WHERE ic.brand_id=email_outbox.brand_id AND ic.channel='email' AND (ic.state='testing' OR (ic.state='active' AND ic.enabled)))) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
            if not row:
                break
            if row['status']=='sending' and (os.getenv('EMAIL_PROVIDER')=='smtp_ssl' or row['attempts']>=8):
                # SMTP cannot prove whether a crashed send already reached the server. Require review.
                conn.execute("UPDATE email_outbox SET status='failed',attempts=greatest(attempts,8),last_error='EMAIL_DELIVERY_UNCERTAIN',lease_until=NULL,updated_at=now() WHERE id=%s",(row['id'],))
                continue
            authorized=True
            if row['kind']=='identity':
                # Reuse the canonical identity predicates without creating a
                # second authentication engine or logging the recipient/code.
                from .auth import identity_mail_authorized
                authorized=identity_mail_authorized(conn,row)
            elif row['kind']=='owner_report':
                authorized=bool(conn.execute("SELECT 1 FROM report_notifications n JOIN users u ON u.id=n.recipient_user_id JOIN brands b ON b.id=u.brand_id WHERE n.email_outbox_id=%s AND u.active AND b.active AND u.role='owner' AND u.brand_id=%s AND u.email_verified_at IS NOT NULL AND lower(btrim(u.email))=%s",(row['id'],row['brand_id'],row['recipient'])).fetchone())
            elif row['kind']=='operations_alert':
                authorized=bool(conn.execute("SELECT 1 FROM recovery_alert_notifications n JOIN users u ON u.id=n.recipient_user_id JOIN recovery_alerts a ON a.id=n.alert_id JOIN brands b ON b.id=u.brand_id WHERE n.email_outbox_id=%s AND u.active AND b.active AND u.role='owner' AND u.brand_id=%s AND u.email_verified_at IS NOT NULL AND lower(btrim(u.email))=%s AND a.brand_id=u.brand_id",(row['id'],row['brand_id'],row['recipient'])).fetchone())
            if not authorized:
                conn.execute("UPDATE email_outbox SET status='cancelled',updated_at=now() WHERE id=%s",(row['id'],))
                continue
            conn.execute("UPDATE email_outbox SET status='sending',attempts=attempts+1,lease_until=now()+interval '60 seconds',updated_at=now() WHERE id=%s",(row['id'],))
        attempted += 1
        try:
            reference = _send(row)
            with db.tx() as conn:
                conn.execute("UPDATE email_outbox SET status='provider_accepted',provider_reference=%s,last_error=NULL,lease_until=NULL,updated_at=now() WHERE id=%s AND status='sending'",(reference,row['id']))
                conn.execute('UPDATE email_webhook_events SET brand_id=%s WHERE provider_reference=%s AND brand_id IS NULL',(row['brand_id'],reference))
                _apply_delivery_events(conn,reference)
            accepted += 1
        except Exception as exc:
            # Provider bodies can contain addresses/secrets. Persist only controlled type codes.
            with db.tx() as conn:
                uncertain=os.getenv('EMAIL_PROVIDER')=='smtp_ssl'
                conn.execute("UPDATE email_outbox SET status='failed',last_error=%s,attempts=CASE WHEN %s THEN greatest(attempts,8) ELSE attempts END,lease_until=NULL,next_attempt_at=now()+interval '5 minutes',updated_at=now() WHERE id=%s AND status='sending'",('EMAIL_DELIVERY_UNCERTAIN' if uncertain else 'DELIVERY_'+type(exc).__name__.upper()[:50],uncertain,row['id']))
    return {'attempted':attempted, 'provider_accepted':accepted, 'configured':True}


async def poll_loop():
    while True:
        try:
            await asyncio.to_thread(dispatch_once)
        except Exception as exc:
            logging.getLogger('kps_order').error('mail_worker_failure type=%s',type(exc).__name__)
        await asyncio.sleep(20)


def _apply_delivery_events(conn,reference):
    delivered=conn.execute("SELECT received_at FROM email_webhook_events WHERE provider_reference=%s AND event_type='email.delivered' ORDER BY received_at LIMIT 1",(reference,)).fetchone()
    if delivered:
        # Delivery here means recipient mail-server acceptance. Inbox token use is stronger evidence.
        conn.execute("UPDATE email_outbox SET status='delivered',delivery_at=coalesce(delivery_at,%s),last_error=NULL,updated_at=now() WHERE provider_reference=%s AND status<>'cancelled'",(delivered['received_at'],reference))
    else:
        failed=conn.execute("SELECT event_type FROM email_webhook_events WHERE provider_reference=%s AND event_type IN ('email.bounced','email.failed','email.complained') LIMIT 1",(reference,)).fetchone()
        if failed:
            conn.execute("UPDATE email_outbox SET status='failed',attempts=greatest(attempts,8),last_error=%s,updated_at=now() WHERE provider_reference=%s AND status NOT IN ('delivered','cancelled')",(failed['event_type'].upper().replace('.','_'),reference))


webhook_router=APIRouter(prefix='/mailhooks')


@webhook_router.post('/resend')
async def resend_webhook(request:Request):
    encoded=os.getenv('RESEND_WEBHOOK_SECRET','')
    if not encoded.startswith('whsec_'):
        raise db.error(503,'EMAIL_WEBHOOK_UNCONFIGURED','قناة إثبات التسليم غير مهيأة.')
    try:
        secret=base64.b64decode(encoded[6:],validate=True)
        timestamp=request.headers.get('svix-timestamp','')
        event_id=request.headers.get('svix-id','')
        if len(secret)<16 or not timestamp.isdecimal() or abs(time.time()-int(timestamp))>300 or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',event_id):
            raise ValueError('invalid envelope')
        raw=await request.body()
        if len(raw)>100_000:
            raise ValueError('oversized')
        signed=event_id.encode()+b'.'+timestamp.encode()+b'.'+raw
        expected=base64.b64encode(hmac.new(secret,signed,hashlib.sha256).digest()).decode()
        signatures=request.headers.get('svix-signature','').split(' ')
        if not any(signature.startswith('v1,') and hmac.compare_digest(signature[3:],expected) for signature in signatures):
            raise ValueError('invalid signature')
        payload=json.loads(raw)
        kind=payload.get('type')
        reference=payload.get('data',{}).get('email_id')
        if kind not in {'email.delivered','email.bounced','email.failed','email.complained','email.sent'} or not isinstance(reference,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',reference):
            raise ValueError('unsupported event')
    except (ValueError,TypeError,KeyError,AttributeError):
        raise db.error(401,'EMAIL_WEBHOOK_REJECTED','إثبات التسليم غير صالح.') from None
    with db.tx() as conn:
        match=conn.execute('SELECT brand_id FROM email_outbox WHERE provider_reference=%s LIMIT 1',(reference,)).fetchone()
        conn.execute('INSERT INTO email_webhook_events(id,event_type,provider_reference,brand_id) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',(event_id,kind,reference,match['brand_id'] if match else None))
        _apply_delivery_events(conn,reference)
    return {'accepted':True}
