"""Verified backup observations and a durable owner failure queue.

No startup assumption creates a green backup state. Provider signing/API secrets
stay in private environment configuration. Download URLs and raw provider bodies
are never persisted or returned. No database restore or export is triggered here.
"""
from __future__ import annotations

import base64
import asyncio
import binascii
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from . import auth, db

router = APIRouter()
owner = auth.require_roles('owner')
# This temporary independent installation must never monitor or mutate APEX.
INDEPENDENT_DATABASE_ID = 'dpg-db4rjgo473hc738soueg-a'
EVENTS = {
    'postgres_backup_started': 'started',
    'postgres_backup_completed': 'succeeded',
    'postgres_backup_failed': 'failed',
    'postgres_pitr_checkpoint_started': 'started',
    'postgres_pitr_checkpoint_completed': 'succeeded',
    'postgres_pitr_checkpoint_failed': 'failed',
    'postgres_wal_archive_failed': 'failed',
    'postgres_restore_failed': 'failed',
    'postgres_restore_succeeded': 'observed',
}
MAX_BODY = 32 * 1024
logger = logging.getLogger(__name__)


def configuration():
    resource = os.getenv('RENDER_POSTGRES_ID', '')
    brand = os.getenv('RENDER_BACKUP_BRAND_ID', 'kps')
    bound = resource == INDEPENDENT_DATABASE_ID and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,80}', brand))
    api_configured = bool(bound and os.getenv('RENDER_API_KEY'))
    try:
        interval = max(900, min(86400, int(os.getenv('RECOVERY_POLL_INTERVAL_SECONDS', '900'))))
    except ValueError:
        interval = 900
    return {'database_id': resource if bound else None, 'brand_id': brand,
            'provider_configured': bound,
            'api_configured': api_configured,
            'poll_configured': api_configured and os.getenv('RECOVERY_POLL_ENABLED', 'true').lower() == 'true',
            'poll_interval_seconds': interval,
            'webhook_configured': bool(bound and os.getenv('RENDER_WEBHOOK_SIGNING_SECRET'))}


def owner_configuration(user):
    config = configuration()
    if user['brand_id'] != config['brand_id']:
        raise db.error(404, 'RECOVERY_SCOPE_NOT_FOUND', 'لا توجد بيانات استعادة ضمن نطاق حسابك.')
    return config


def _signature_key(secret: str) -> bytes:
    try:
        raw = secret.removeprefix('whsec_')
        key = base64.b64decode(raw, validate=True)
        if not 24 <= len(key) <= 64:
            raise ValueError
        return key
    except (ValueError, binascii.Error):
        raise db.error(503, 'RECOVERY_SIGNING_UNCONFIGURED', 'لم يُهيأ تحقق إشعارات النسخ الاحتياطي.') from None


def verify_signature(body: bytes, headers, secret: str, now: float | None = None) -> str:
    """Standard Webhooks v1: HMAC over the exact raw ID.timestamp.body bytes."""
    event_id = headers.get('webhook-id', '')
    stamp = headers.get('webhook-timestamp', '')
    signatures = headers.get('webhook-signature', '')
    if (len(body) > MAX_BODY or not re.fullmatch(r'evt-[A-Za-z0-9_-]{1,120}', event_id)
            or not re.fullmatch(r'[0-9]{1,12}', stamp) or len(signatures) > 2048):
        raise db.error(401, 'RECOVERY_SIGNATURE_INVALID', 'تعذر التحقق من مصدر الإشعار.')
    if abs((time.time() if now is None else now) - int(stamp)) > 300:
        raise db.error(401, 'RECOVERY_SIGNATURE_EXPIRED', 'انتهت صلاحية توقيع الإشعار.')
    key = _signature_key(secret)
    expected = hmac.new(key, event_id.encode() + b'.' + stamp.encode() + b'.' + body, hashlib.sha256).digest()
    for entry in signatures.split(' '):
        version, separator, encoded = entry.partition(',')
        if version != 'v1' or not separator:
            continue
        try:
            candidate = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            continue
        if hmac.compare_digest(expected, candidate):
            return event_id
    raise db.error(401, 'RECOVERY_SIGNATURE_INVALID', 'تعذر التحقق من مصدر الإشعار.')


def _timestamp(value):
    try:
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None or result.timestamp() > time.time() + 300:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise db.error(422, 'RECOVERY_EVENT_INVALID', 'بيانات إشعار الاستعادة غير صحيحة.') from None


def _record(conn, config, provider_id, event_type, outcome, source, occurred, digest, metadata=None):
    record_id = db.new_id('recovery_')
    inserted = conn.execute('INSERT INTO recovery_events(id,brand_id,database_id,provider_event_id,event_type,outcome,source,request_digest,occurred_at,metadata) '
                            'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(database_id,provider_event_id) DO NOTHING RETURNING id',
                            (record_id, config['brand_id'], config['database_id'], provider_id, event_type, outcome, source, digest,
                             occurred, db.json(metadata or {}))).fetchone()
    if not inserted:
        previous = conn.execute('SELECT request_digest FROM recovery_events WHERE database_id=%s AND provider_event_id=%s',
                                (config['database_id'], provider_id)).fetchone()
        if previous['request_digest'] != digest:
            raise db.error(409, 'RECOVERY_EVENT_CONFLICT', 'معرف الإشعار مستخدم لبيانات مختلفة.')
        return False
    conn.execute('INSERT INTO recovery_state(brand_id,database_id,last_success_at,last_failure_at,last_verified_at,verification_source) '
                 'VALUES(%s,%s,%s,%s,now(),%s) ON CONFLICT(brand_id,database_id) DO UPDATE SET '
                 'last_success_at=GREATEST(recovery_state.last_success_at,EXCLUDED.last_success_at),'
                 'last_failure_at=GREATEST(recovery_state.last_failure_at,EXCLUDED.last_failure_at),'
                 'last_verified_at=now(),verification_source=EXCLUDED.verification_source',
                 (config['brand_id'], config['database_id'], occurred if outcome == 'succeeded' else None,
                  occurred if outcome == 'failed' else None, source))
    if outcome == 'failed':
        code = {'provider_verification_failed': 'PROVIDER_VERIFICATION_FAILED',
                'provider_recovery_not_available': 'PROVIDER_RECOVERY_UNAVAILABLE'}.get(event_type, 'BACKUP_PROVIDER_FAILURE')
        # Concurrent polls/webhook retries retain all observations, but only one
        # unacknowledged alert per failure type is created within a 24-hour span.
        lock = 'recovery-alert:' + config['brand_id'] + ':' + config['database_id'] + ':' + code + ':' + event_type
        conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (lock,))
        conn.execute('INSERT INTO recovery_alerts(id,brand_id,database_id,event_id,code,event_type) '
                     'SELECT %s,%s,%s,%s,%s,%s WHERE NOT EXISTS(SELECT 1 FROM recovery_alerts '
                     "WHERE brand_id=%s AND database_id=%s AND code=%s AND event_type=%s AND acknowledged_at IS NULL AND created_at>now()-interval '24 hours')",
                     (db.new_id('recovery_alert_'), config['brand_id'], config['database_id'], record_id, code, event_type,
                      config['brand_id'], config['database_id'], code, event_type))
    return True


@router.post('/webhooks/render/recovery')
async def receive_provider_event(request: Request):
    config = configuration()
    if not config['webhook_configured']:
        raise db.error(503, 'RECOVERY_PROVIDER_UNCONFIGURED', 'إشعارات النسخ الاحتياطي غير مهيأة.')
    length = request.headers.get('content-length', '')
    if length and (not length.isdecimal() or int(length) > MAX_BODY):
        raise db.error(413, 'RECOVERY_EVENT_TOO_LARGE', 'الإشعار أكبر من الحد المسموح.')
    body = await request.body()
    if len(body) > MAX_BODY:
        raise db.error(413, 'RECOVERY_EVENT_TOO_LARGE', 'الإشعار أكبر من الحد المسموح.')
    provider_id = verify_signature(body, request.headers, os.environ['RENDER_WEBHOOK_SIGNING_SECRET'])
    try:
        payload = json.loads(body)
        data = payload['data']
        event_type = payload['type']
        if not isinstance(data, dict) or not isinstance(event_type, str) or data.get('id') != provider_id:
            raise ValueError
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise db.error(422, 'RECOVERY_EVENT_INVALID', 'بيانات إشعار الاستعادة غير صحيحة.') from None
    if data.get('serviceId') != config['database_id']:
        raise db.error(403, 'RECOVERY_RESOURCE_REJECTED', 'الإشعار لا يخص قاعدة هذا الموقع المستقلة.')
    if event_type not in EVENTS:
        raise db.error(422, 'RECOVERY_EVENT_UNSUPPORTED', 'نوع إشعار الاستعادة غير مدعوم.')
    occurred = _timestamp(payload.get('timestamp'))
    with db.tx() as conn:
        inserted = _record(conn, config, provider_id, event_type, EVENTS[event_type], 'signed_webhook', occurred,
                           hashlib.sha256(body).hexdigest())
    return {'accepted': True, 'duplicate': not inserted}


def _iso(value):
    return value.isoformat() if value else None


@router.get('/api/admin/recovery')
def recovery_status(user=Depends(owner)):
    config = owner_configuration(user)
    state, alerts, provider_status = None, [], None
    if config['provider_configured']:
        with db.tx() as conn:
            state = conn.execute('SELECT * FROM recovery_state WHERE brand_id=%s AND database_id=%s',
                                 (user['brand_id'], config['database_id'])).fetchone()
            alerts = conn.execute('SELECT id,code,event_type,created_at,acknowledged_at FROM recovery_alerts '
                                  'WHERE brand_id=%s AND database_id=%s AND acknowledged_at IS NULL ORDER BY created_at DESC LIMIT 100',
                                  (user['brand_id'], config['database_id'])).fetchall()
            latest_api = conn.execute('SELECT metadata FROM recovery_events WHERE brand_id=%s AND database_id=%s '
                                      "AND source='provider_api' ORDER BY verified_at DESC,id DESC LIMIT 1",
                                      (user['brand_id'], config['database_id'])).fetchone()
            if latest_api:
                provider_status = latest_api['metadata'].get('provider_recovery_status')
    state = state or {}
    blockers = []
    if not config['provider_configured']:
        blockers.append('PROVIDER_DATABASE_NOT_BOUND')
    if not config['api_configured'] and not config['webhook_configured']:
        blockers.append('PROVIDER_VERIFICATION_NOT_CONFIGURED')
    if not state.get('last_verified_at'):
        blockers.append('PROVIDER_NOT_YET_VERIFIED')
    if not state.get('retention_days'):
        blockers.append('RETENTION_WINDOW_UNVERIFIED')
    if not state.get('last_success_at'):
        blockers.append('SUCCESSFUL_BACKUP_NOT_OBSERVED')
    else:
        age = (datetime.now(timezone.utc) - state['last_success_at']).total_seconds()
        if age > 36 * 3600:
            blockers.append('SUCCESSFUL_BACKUP_OBSERVATION_STALE')
    if provider_status in ('BACKUP_NOT_READY', 'NOT_AVAILABLE'):
        blockers.append('PROVIDER_RECOVERY_' + provider_status)
    if alerts:
        blockers.append('UNACKNOWLEDGED_RECOVERY_ALERTS')
    status = 'unconfigured' if not config['provider_configured'] else 'unverified'
    if state:
        status = 'attention_required' if blockers else 'observed'
    return {'status': status, 'provider': 'render', **{key: config[key] for key in ('provider_configured','api_configured','webhook_configured')},
            'poll_configured': config['poll_configured'], 'poll_interval_seconds': config['poll_interval_seconds'],
            'provider_recovery_status': provider_status,
            'last_success_at': _iso(state.get('last_success_at')), 'last_failure_at': _iso(state.get('last_failure_at')),
            'last_verified_at': _iso(state.get('last_verified_at')), 'verification_source': state.get('verification_source', 'none'),
            'retention_days': state.get('retention_days'), 'recoverable_from': _iso(state.get('recoverable_from')),
            'recoverable_until': _iso(state.get('recoverable_until')),
            'potential_loss_window_seconds': state.get('potential_loss_window_seconds'), 'measured_restore_seconds': None,
            'open_alerts': [{**item, 'created_at': _iso(item['created_at'])} for item in alerts], 'blockers': blockers,
            'external_alert_delivery_configured': False}


@router.post('/api/admin/recovery/alerts/{alert_id}/ack')
def acknowledge_alert(alert_id: str, user=Depends(owner)):
    config = owner_configuration(user)
    with db.tx() as conn:
        row = conn.execute('UPDATE recovery_alerts SET acknowledged_at=COALESCE(acknowledged_at,now()),'
                           'acknowledged_by=COALESCE(acknowledged_by,%s) WHERE id=%s AND brand_id=%s AND database_id=%s RETURNING id,acknowledged_at',
                           (user['id'], alert_id, user['brand_id'], config['database_id'])).fetchone()
        if not row:
            raise db.error(404, 'RECOVERY_ALERT_NOT_FOUND', 'التنبيه غير موجود ضمن نطاق حسابك.')
        db.audit(conn, user['id'], 'recovery.alert.acknowledged', 'recovery_alert', alert_id)
    return {'id': row['id'], 'acknowledged_at': _iso(row['acknowledged_at'])}


def parse_provider_observation(exports, recovery):
    """Parse the official Render OpenAPI response; omit all download URLs.

    /recovery supplies recoveryStatus and optional startsAt, not a latest point,
    retention duration, loss-window bound, or measured restore time.
    """
    if not isinstance(exports, list) or not isinstance(recovery, dict):
        raise ValueError('Invalid provider response')
    status = recovery.get('recoveryStatus')
    if status not in ('AVAILABLE', 'BACKUP_NOT_READY', 'NOT_AVAILABLE'):
        raise ValueError('Invalid provider recovery status')
    dates = []
    try:
        for item in exports:
            if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not item['id'] or len(item['id']) > 200:
                raise ValueError('Invalid provider export')
            dates.append(_timestamp(item['createdAt']))
        starts = _timestamp(recovery['startsAt']) if recovery.get('startsAt') is not None else None
    except (KeyError, HTTPException) as exc:
        raise ValueError('Invalid provider timestamp') from exc
    return {'latest_export_at': max(dates) if dates else None,
            'recoverable_from': starts if status == 'AVAILABLE' else None,
            'metadata': {'logical_export_count': len(exports), 'provider_recovery_status': status}}


def poll_provider(config=None, actor_id=None):
    """Read only the bound independent database, then persist a verified result.

    Provider failures create sanitized durable owner alerts before returning 503.
    Configuration has no secret values; the API key is read only at request time.
    """
    config = configuration() if config is None else config
    if (not config.get('api_configured') or config.get('database_id') != INDEPENDENT_DATABASE_ID
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', config.get('brand_id', '')) or not os.getenv('RENDER_API_KEY')):
        raise db.error(503, 'RECOVERY_API_UNCONFIGURED', 'لم يهيأ التحقق الخاص من النسخ لدى مزود الاستضافة.')
    now = datetime.now(timezone.utc)
    provider_id = db.new_id('provider_poll_')
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            headers = {'Authorization': 'Bearer ' + os.environ['RENDER_API_KEY'], 'Accept': 'application/json'}
            export = client.get(f"https://api.render.com/v1/postgres/{config['database_id']}/export", headers=headers)
            recovery = client.get(f"https://api.render.com/v1/postgres/{config['database_id']}/recovery", headers=headers)
            export.raise_for_status(); recovery.raise_for_status()
            if len(export.content) > 256 * 1024 or len(recovery.content) > 64 * 1024:
                raise ValueError
            parsed = parse_provider_observation(export.json(), recovery.json())
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        with db.tx() as conn:
            _record(conn, config, provider_id, 'provider_verification_failed', 'failed', 'provider_api', now,
                    db.digest({'code': 'PROVIDER_VERIFICATION_FAILED'}))
            conn.execute('UPDATE recovery_state SET recoverable_from=NULL,recoverable_until=NULL,retention_days=NULL,'
                         'potential_loss_window_seconds=NULL WHERE brand_id=%s AND database_id=%s',
                         (config['brand_id'], config['database_id']))
        raise db.error(503, 'RECOVERY_PROVIDER_UNAVAILABLE', 'تعذر التحقق من حالة النسخ لدى مزود الاستضافة؛ حُفظ تنبيه للمالك.') from None
    latest = parsed['latest_export_at']
    metadata = parsed['metadata']
    with db.tx() as conn:
        _record(conn, config, provider_id, 'provider_api_verified', 'succeeded' if latest else 'observed',
                'provider_api', latest or now, db.digest(metadata), metadata)
        conn.execute('UPDATE recovery_state SET recoverable_from=%s,recoverable_until=NULL,retention_days=NULL,'
                     'potential_loss_window_seconds=NULL WHERE brand_id=%s AND database_id=%s',
                     (parsed['recoverable_from'], config['brand_id'], config['database_id']))
        if metadata['provider_recovery_status'] == 'NOT_AVAILABLE':
            _record(conn, config, provider_id + '_availability', 'provider_recovery_not_available', 'failed',
                    'provider_api', now, db.digest(metadata), metadata)
        if actor_id:
            db.audit(conn, actor_id, 'recovery.provider.refreshed', 'recovery', config['database_id'], metadata)
    return metadata


async def poll_loop():
    """Optional polling in the existing web process; no separate paid cron job."""
    if not configuration()['poll_configured'] or not os.getenv('DATABASE_URL'):
        return
    await asyncio.sleep(1)
    while True:
        config = configuration()
        if not config['poll_configured'] or not os.getenv('DATABASE_URL'):
            return
        try:
            await asyncio.to_thread(poll_provider, config)
        except HTTPException:
            # The provider error has already been recorded without credentials.
            logger.warning('Recovery provider verification failed; owner alert queued.')
        except Exception:
            # Never interpolate exception text: database/provider errors can
            # contain sensitive request or connection information.
            logger.warning('Recovery monitoring could not persist an observation.')
        await asyncio.sleep(config['poll_interval_seconds'])


@router.post('/api/admin/recovery/refresh')
def refresh_provider_observation(user=Depends(owner)):
    config = owner_configuration(user)
    poll_provider(config, actor_id=user['id'])
    return recovery_status(user)
