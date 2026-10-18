"""Synthetic provider signatures and actual isolated PG monitoring/role tests."""
from __future__ import annotations

import base64
import asyncio
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import HTTPException

from app import auth, db, recovery

KEY = b'synthetic-test-signing-key-32byte!'
SECRET = 'whsec_' + base64.b64encode(KEY).decode()


def _signed(body, event_id='evt-test_event', stamp=None):
    stamp = int(time.time()) if stamp is None else stamp
    signature = hmac.new(KEY, f'{event_id}.{stamp}.'.encode() + body, hashlib.sha256).digest()
    return {'webhook-id': event_id, 'webhook-timestamp': str(stamp),
            'webhook-signature': 'v1,' + base64.b64encode(signature).decode()}


def _payload(event_id='evt-test_event', event_type='postgres_backup_completed', resource=None, extra=None):
    data = {'id': event_id, 'serviceId': resource or recovery.INDEPENDENT_DATABASE_ID}
    data.update(extra or {})
    return json.dumps({'type': event_type, 'timestamp': datetime.now(timezone.utc).isoformat(), 'data': data}).encode()


def test_signature_uses_exact_raw_bytes_and_rotation_versions():
    body = b'{"type":"synthetic"}'
    headers = _signed(body, stamp=1700000000)
    headers['webhook-signature'] = 'v1a,unsupported v1,invalid ' + headers['webhook-signature']
    assert recovery.verify_signature(body, headers, SECRET, now=1700000000) == 'evt-test_event'
    with pytest.raises(HTTPException) as rejected:
        recovery.verify_signature(body + b' ', headers, SECRET, now=1700000000)
    assert rejected.value.status_code == 401


@pytest.mark.parametrize('difference', [-301, 301])
def test_signature_rejects_old_and_future_attempts(difference):
    body = b'{}'
    with pytest.raises(HTTPException) as rejected:
        recovery.verify_signature(body, _signed(body, stamp=1700000000), SECRET, now=1700000000 + difference)
    assert rejected.value.detail['code'] == 'RECOVERY_SIGNATURE_EXPIRED'


@pytest.mark.parametrize('event_id', ['evt-good.injected', 'missing-prefix', 'evt-'])
def test_signature_rejects_ambiguous_header_identifiers(event_id):
    body = b'{}'
    with pytest.raises(HTTPException) as rejected:
        recovery.verify_signature(body, _signed(body, event_id, 1700000000), SECRET, now=1700000000)
    assert rejected.value.status_code == 401


def test_other_provider_database_cannot_enable_monitoring(monkeypatch):
    monkeypatch.setenv('RENDER_POSTGRES_ID', 'dpg-unrelated-platform-a')
    monkeypatch.setenv('RENDER_API_KEY', 'synthetic-api-key')
    monkeypatch.setenv('RENDER_WEBHOOK_SIGNING_SECRET', SECRET)
    assert recovery.configuration()['provider_configured'] is False
    assert recovery.configuration()['api_configured'] is False
    assert recovery.configuration()['webhook_configured'] is False


def _login(client, role='owner', brand='kps'):
    identifier = db.new_id('recovery_test_user_')
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with db.tx() as conn:
        conn.execute('INSERT INTO brands(id,name) VALUES(%s,%s) ON CONFLICT DO NOTHING', (brand, 'Synthetic test brand'))
        conn.execute('INSERT INTO users(id,role,brand_id,display_name) VALUES(%s,%s,%s,%s)',
                     (identifier, role, brand, 'Synthetic recovery test'))
        conn.execute('INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES(%s,%s,%s,%s,now()+interval \'1 hour\')',
                     (db.new_id('test_session_'), auth.token_hash(token), auth.token_hash(csrf), identifier))
    client.cookies.clear()
    client.cookies.set(auth.COOKIE, token)
    client.cookies.set(auth.CSRF_COOKIE, csrf)
    client.headers['X-CSRF-Token'] = csrf
    return identifier


@pytest.fixture
def monitoring(client, monkeypatch):
    monkeypatch.setenv('RENDER_POSTGRES_ID', recovery.INDEPENDENT_DATABASE_ID)
    monkeypatch.setenv('RENDER_WEBHOOK_SIGNING_SECRET', SECRET)
    _login(client)
    return client


@pytest.mark.postgres
def test_status_stays_unknown_without_configuration_or_observation(client, monkeypatch):
    _login(client)
    result = client.get('/api/admin/recovery')
    assert result.status_code == 200
    assert result.json()['status'] == 'unconfigured'
    assert result.json()['last_success_at'] is None
    monkeypatch.setenv('RENDER_POSTGRES_ID', recovery.INDEPENDENT_DATABASE_ID)
    monkeypatch.setenv('RENDER_WEBHOOK_SIGNING_SECRET', SECRET)
    result = client.get('/api/admin/recovery').json()
    assert result['status'] == 'unverified'
    assert result['retention_days'] is None
    assert result['potential_loss_window_seconds'] is None
    assert result['measured_restore_seconds'] is None
    assert 'SUCCESSFUL_BACKUP_NOT_OBSERVED' in result['blockers']
    assert client.post('/api/admin/recovery/refresh').status_code == 503


@pytest.mark.postgres
def test_signed_events_are_durable_idempotent_and_failure_alert_requires_owner_ack(monitoring):
    body = _payload(extra={'url': 'https://must-not-store.example/private-download?token=secret'})
    first = monitoring.post('/webhooks/render/recovery', content=body, headers=_signed(body))
    assert first.status_code == 200 and first.json()['duplicate'] is False
    repeated = monitoring.post('/webhooks/render/recovery', content=body, headers=_signed(body))
    assert repeated.status_code == 200 and repeated.json()['duplicate'] is True
    failure = _payload('evt-test_failure', 'postgres_wal_archive_failed')
    assert monitoring.post('/webhooks/render/recovery', content=failure,
                           headers=_signed(failure, 'evt-test_failure')).status_code == 200
    # Re-running actual startup migrations preserves observed state and alerts.
    db.migrate()
    result = monitoring.get('/api/admin/recovery').json()
    assert result['status'] == 'attention_required'
    assert result['last_success_at'] and result['last_failure_at']
    assert result['verification_source'] == 'signed_webhook'
    assert result['retention_days'] is None and result['recoverable_until'] is None
    assert len(result['open_alerts']) == 1
    alert = result['open_alerts'][0]
    assert alert['event_type'] == 'postgres_wal_archive_failed'
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) AS n FROM recovery_events').fetchone()['n'] == 2
        records = conn.execute('SELECT metadata FROM recovery_events').fetchall()
    assert 'private-download' not in json.dumps(records)
    assert 'private-download' not in json.dumps(result)
    assert monitoring.post(f"/api/admin/recovery/alerts/{alert['id']}/ack").status_code == 200
    again = monitoring.post(f"/api/admin/recovery/alerts/{alert['id']}/ack")
    assert again.status_code == 200
    assert monitoring.get('/api/admin/recovery').json()['open_alerts'] == []
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) AS n FROM recovery_alerts').fetchone()['n'] == 1


@pytest.mark.postgres
def test_signature_resource_and_replay_failures_do_not_create_state(monitoring):
    body = _payload(resource='dpg-unrelated-platform-a')
    assert monitoring.post('/webhooks/render/recovery', content=body, headers=_signed(body)).status_code == 403
    valid = _payload()
    assert monitoring.post('/webhooks/render/recovery', content=valid,
                           headers=_signed(valid, stamp=int(time.time())-301)).status_code == 401
    headers = _signed(valid)
    headers['webhook-signature'] = 'v1,not-a-signature'
    assert monitoring.post('/webhooks/render/recovery', content=valid, headers=headers).status_code == 401
    assert monitoring.post('/webhooks/render/recovery', content=valid, headers=_signed(valid)).status_code == 200
    changed = _payload(event_type='postgres_backup_failed')
    assert monitoring.post('/webhooks/render/recovery', content=changed, headers=_signed(changed)).status_code == 409
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) AS n FROM recovery_events').fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) AS n FROM recovery_alerts').fetchone()['n'] == 0


@pytest.mark.parametrize('role', ['customer','driver','branch_manager','delivery_manager','delivery_staff'])
@pytest.mark.postgres
def test_monitoring_rejects_every_non_owner_role(client, role):
    _login(client, role=role)
    assert client.get('/api/admin/recovery').status_code == 403
    assert client.post('/api/admin/recovery/refresh').status_code == 403


@pytest.mark.postgres
def test_other_brand_owner_and_missing_csrf_cannot_acknowledge_alert(monitoring):
    body = _payload('evt-scope_failure', 'postgres_backup_failed')
    assert monitoring.post('/webhooks/render/recovery', content=body, headers=_signed(body, 'evt-scope_failure')).status_code == 200
    alert = monitoring.get('/api/admin/recovery').json()['open_alerts'][0]
    monitoring.headers.pop('X-CSRF-Token')
    assert monitoring.post(f"/api/admin/recovery/alerts/{alert['id']}/ack").status_code == 403
    _login(monitoring, brand='other-recovery-test-brand')
    assert monitoring.get('/api/admin/recovery').status_code == 404
    assert monitoring.post(f"/api/admin/recovery/alerts/{alert['id']}/ack").status_code == 404


@pytest.mark.postgres
def test_api_refresh_uses_readonly_fixed_resource_and_drops_download_urls(monitoring, monkeypatch):
    monkeypatch.setenv('RENDER_API_KEY', 'synthetic-never-live-api-key')
    calls = []

    class Provider:
        def __init__(self, **kwargs):
            assert kwargs['follow_redirects'] is False
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def get(self, url, **kwargs):
            calls.append(url)
            body = ([{'id': 'synthetic_export', 'createdAt': datetime.now(timezone.utc).isoformat(),
                      'url': 'https://private.example/download?secret=never-store'}] if url.endswith('/export') else
                    {'recoveryStatus': 'AVAILABLE', 'startsAt': '2026-10-07T10:00:00Z'})
            return httpx.Response(200, json=body, request=httpx.Request('GET', url))

    monkeypatch.setattr(recovery.httpx, 'Client', Provider)
    response = monitoring.post('/api/admin/recovery/refresh')
    assert response.status_code == 200
    assert response.json()['verification_source'] == 'provider_api'
    assert response.json()['last_success_at']
    assert response.json()['retention_days'] is None  # No guessed provider fields.
    assert response.json()['provider_recovery_status'] == 'AVAILABLE'
    assert response.json()['recoverable_from'] == '2026-10-07T10:00:00+00:00'
    assert response.json()['recoverable_until'] is None
    assert response.json()['potential_loss_window_seconds'] is None
    assert calls == [f'https://api.render.com/v1/postgres/{recovery.INDEPENDENT_DATABASE_ID}/export',
                     f'https://api.render.com/v1/postgres/{recovery.INDEPENDENT_DATABASE_ID}/recovery']
    with db.tx() as conn:
        saved = conn.execute('SELECT metadata FROM recovery_events').fetchall()
    assert 'never-store' not in json.dumps(saved) and 'never-live' not in json.dumps(saved)


@pytest.mark.postgres
def test_api_poll_failure_creates_truthful_durable_verification_alert(monitoring, monkeypatch):
    monkeypatch.setenv('RENDER_API_KEY', 'synthetic-never-live-api-key')

    class Provider:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def get(self, url, **kwargs):
            return httpx.Response(500, json={'secret': 'not-for-client'}, request=httpx.Request('GET', url))

    monkeypatch.setattr(recovery.httpx, 'Client', Provider)
    failed = monitoring.post('/api/admin/recovery/refresh')
    assert failed.status_code == 503 and 'not-for-client' not in failed.text
    status = monitoring.get('/api/admin/recovery').json()
    assert status['last_success_at'] is None
    assert status['open_alerts'][0]['code'] == 'PROVIDER_VERIFICATION_FAILED'
    assert status['open_alerts'][0]['event_type'] == 'provider_verification_failed'
    assert monitoring.post('/api/admin/recovery/refresh').status_code == 503
    assert len(monitoring.get('/api/admin/recovery').json()['open_alerts']) == 1
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) AS n FROM recovery_events').fetchone()['n'] == 2
    alert = status['open_alerts'][0]
    assert monitoring.post(f"/api/admin/recovery/alerts/{alert['id']}/ack").status_code == 200
    assert monitoring.post('/api/admin/recovery/refresh').status_code == 503
    assert len(monitoring.get('/api/admin/recovery').json()['open_alerts']) == 1
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) AS n FROM recovery_alerts').fetchone()['n'] == 2
        conn.execute("UPDATE recovery_alerts SET created_at=now()-interval '25 hours' WHERE acknowledged_at IS NULL")
    assert monitoring.post('/api/admin/recovery/refresh').status_code == 503
    assert len(monitoring.get('/api/admin/recovery').json()['open_alerts']) == 2


def test_official_provider_schema_parses_only_actual_earliest_recovery_point():
    parsed = recovery.parse_provider_observation(
        [{'id': 'test_export', 'createdAt': '2026-10-08T10:00:00Z', 'url': 'https://secret.invalid'}],
        {'recoveryStatus': 'AVAILABLE', 'startsAt': '2026-10-07T10:00:00Z', 'retentionDays': 99})
    assert parsed['recoverable_from'].isoformat() == '2026-10-07T10:00:00+00:00'
    assert parsed['latest_export_at'].isoformat() == '2026-10-08T10:00:00+00:00'
    assert parsed['metadata'] == {'logical_export_count': 1, 'provider_recovery_status': 'AVAILABLE'}
    assert recovery.parse_provider_observation([], {'recoveryStatus': 'BACKUP_NOT_READY'})['recoverable_from'] is None
    assert recovery.parse_provider_observation([], {'recoveryStatus': 'NOT_AVAILABLE'})['latest_export_at'] is None


@pytest.mark.parametrize('exports,status', [
    ({}, {'recoveryStatus': 'AVAILABLE'}),
    ([], {'available': True}),
    ([{'id': 'export'}], {'recoveryStatus': 'AVAILABLE'}),
    ([{'id': 'export', 'createdAt': 'not-date'}], {'recoveryStatus': 'AVAILABLE'}),
    ([], {'recoveryStatus': 'AVAILABLE', 'startsAt': '2026-10-07T10:00:00'}),
])
def test_provider_schema_rejects_malformed_or_guessed_fields(exports, status):
    with pytest.raises(ValueError):
        recovery.parse_provider_observation(exports, status)


def test_optional_polling_requires_bound_private_configuration(monkeypatch):
    assert recovery.configuration()['poll_configured'] is False
    monkeypatch.setenv('RENDER_POSTGRES_ID', recovery.INDEPENDENT_DATABASE_ID)
    monkeypatch.setenv('RENDER_API_KEY', 'synthetic-never-live-key')
    assert recovery.configuration()['poll_configured'] is True
    assert recovery.configuration()['poll_interval_seconds'] == 900
    monkeypatch.setenv('RECOVERY_POLL_INTERVAL_SECONDS', '1')
    assert recovery.configuration()['poll_interval_seconds'] == 900
    monkeypatch.setenv('RECOVERY_POLL_INTERVAL_SECONDS', 'invalid')
    assert recovery.configuration()['poll_interval_seconds'] == 900
    monkeypatch.setenv('RECOVERY_POLL_ENABLED', 'false')
    assert recovery.configuration()['poll_configured'] is False


def test_disabled_poll_loop_never_contacts_provider(monkeypatch):
    def reject(*args, **kwargs):
        pytest.fail('Unconfigured monitoring must never contact a provider')
    monkeypatch.setattr(recovery, 'poll_provider', reject)
    asyncio.run(recovery.poll_loop())


def test_enabled_poll_loop_uses_bounded_interval_and_cancels_cleanly(monkeypatch):
    monkeypatch.setenv('RENDER_POSTGRES_ID', recovery.INDEPENDENT_DATABASE_ID)
    monkeypatch.setenv('RENDER_API_KEY', 'synthetic-never-live-key')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://synthetic-no-connection/test_synthetic')
    observations, waits = [], []
    monkeypatch.setattr(recovery, 'poll_provider', lambda config: observations.append(config['database_id']))

    async def bounded_wait(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(recovery.asyncio, 'sleep', bounded_wait)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(recovery.poll_loop())
    assert observations == [recovery.INDEPENDENT_DATABASE_ID]
    assert waits == [1, 900]


@pytest.mark.postgres
def test_confirmed_unavailable_recovery_creates_durable_distinct_alert(monitoring, monkeypatch):
    monkeypatch.setenv('RENDER_API_KEY', 'synthetic-never-live-key')

    class Provider:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def get(self, url, **kwargs):
            return httpx.Response(200, json=[] if url.endswith('/export') else {'recoveryStatus': 'NOT_AVAILABLE'},
                                  request=httpx.Request('GET', url))

    monkeypatch.setattr(recovery.httpx, 'Client', Provider)
    assert monitoring.post('/api/admin/recovery/refresh').status_code == 200
    status = monitoring.get('/api/admin/recovery').json()
    assert status['provider_recovery_status'] == 'NOT_AVAILABLE'
    assert status['last_success_at'] is None and status['recoverable_from'] is None
    assert status['open_alerts'][0]['code'] == 'PROVIDER_RECOVERY_UNAVAILABLE'
    assert 'PROVIDER_RECOVERY_NOT_AVAILABLE' in status['blockers']
    assert monitoring.post('/api/admin/recovery/refresh').status_code == 200
    assert len(monitoring.get('/api/admin/recovery').json()['open_alerts']) == 1
