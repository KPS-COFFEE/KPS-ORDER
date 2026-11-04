"""Real isolated PostgreSQL runtime/owner queues; synthetic crypto/host guards."""
from __future__ import annotations

import base64
import io
import json
import zipfile

import pytest
from cryptography.exceptions import InvalidTag

from app import db, monitoring, recovery
from app.exports import FORMAT, INDEPENDENT_RESTORE_DATABASE_ID, _restore_target
from test_recovery import _login, _payload, _signed, SECRET
from tools.portable_backup import encrypt_archive, read_archive, private_write


def _empty_archive():
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w') as archive:
        archive.writestr('manifest.json', json.dumps({'format': FORMAT, 'mode': 'database', 'files': {},
                                                    'table_order': [], 'counts': {}}))
    return result.getvalue()


def test_private_backup_encryption_authentication_and_file_permissions(monkeypatch, tmp_path):
    original = _empty_archive()
    monkeypatch.setenv('KPS_BACKUP_KEY', base64.urlsafe_b64encode(b'K' * 32).decode())
    encrypted = encrypt_archive(original)
    assert original not in encrypted and b'manifest.json' not in encrypted
    path = tmp_path / 'protected-backup.bin'
    private_write(path, encrypted)
    assert path.stat().st_mode & 0o777 == 0o600
    assert read_archive(path) == original
    with pytest.raises(FileExistsError):
        private_write(path, b'must not overwrite')
    path.write_bytes(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))
    with pytest.raises(InvalidTag):
        read_archive(path)
    monkeypatch.setenv('KPS_BACKUP_KEY', 'invalid-secret')
    with pytest.raises(ValueError):
        encrypt_archive(original)


def test_restore_remote_host_is_limited_to_existing_private_recovery_resource():
    private = 'postgresql://test-only:synthetic@' + INDEPENDENT_RESTORE_DATABASE_ID + '/recovery_test'
    assert _restore_target(private, INDEPENDENT_RESTORE_DATABASE_ID)['host'] == INDEPENDENT_RESTORE_DATABASE_ID
    for identifier, url in [
        (None, private),
        (recovery.INDEPENDENT_DATABASE_ID, private),
        (INDEPENDENT_RESTORE_DATABASE_ID, 'postgresql://synthetic@' + recovery.INDEPENDENT_DATABASE_ID + '/source'),
        (INDEPENDENT_RESTORE_DATABASE_ID, private.replace(INDEPENDENT_RESTORE_DATABASE_ID,
             INDEPENDENT_RESTORE_DATABASE_ID + '.frankfurt-postgres.render.com')),
        (INDEPENDENT_RESTORE_DATABASE_ID, private + '?options=-csearch_path=public'),
        (INDEPENDENT_RESTORE_DATABASE_ID, private.replace(INDEPENDENT_RESTORE_DATABASE_ID, 'unapproved.invalid')),
    ]:
        with pytest.raises(ValueError):
            _restore_target(url, identifier)


def test_runtime_monitor_disabled_without_exact_resource_and_bounded_intervals(monkeypatch):
    assert monitoring.check_once() == {'configured': False, 'checked': False}
    monkeypatch.setenv('DATABASE_URL', 'postgresql://synthetic/test_never_contact')
    monkeypatch.setenv('RENDER_POSTGRES_ID', 'unrelated-resource')
    assert not monitoring.configuration()['runtime_configured']
    monkeypatch.setenv('RENDER_POSTGRES_ID', recovery.INDEPENDENT_DATABASE_ID)
    monkeypatch.setenv('MONITOR_INTERVAL_SECONDS', '1')
    assert monitoring.configuration()['runtime_interval_seconds'] == 60
    monkeypatch.setenv('MONITOR_INTERVAL_SECONDS', 'malformed')
    assert monitoring.configuration()['runtime_interval_seconds'] == 120
    monkeypatch.setenv('MONITOR_ENABLED', 'false')
    assert not monitoring.configuration()['runtime_configured']


@pytest.mark.postgres
def test_failure_alert_waits_for_registered_verified_owner_and_queues_once(client, monkeypatch):
    owner_id = _login(client)
    monkeypatch.setenv('RENDER_POSTGRES_ID', recovery.INDEPENDENT_DATABASE_ID)
    monkeypatch.setenv('RENDER_WEBHOOK_SIGNING_SECRET', SECRET)
    body = _payload('evt-owner-mail', 'postgres_backup_failed')
    assert client.post('/webhooks/render/recovery', content=body, headers=_signed(body, 'evt-owner-mail')).status_code == 200
    with db.tx() as conn:
        assert not conn.execute('SELECT id FROM recovery_alert_notifications').fetchone()
        conn.execute('UPDATE users SET email=%s,email_verified_at=now() WHERE id=%s', ('registered-owner@fixture.invalid', owner_id))
        conn.execute("UPDATE identity_channels SET enabled=true,state='active' WHERE brand_id='kps' AND channel='email'")
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_FROM', 'authorized-sender@fixture.invalid')
    monkeypatch.setenv('RESEND_API_KEY', 'synthetic-not-for-live')
    monkeypatch.setenv('MAIL_OUTBOX_KEY', base64.urlsafe_b64encode(b'N' * 32).decode())
    for expected in (1, 0):
        with db.tx() as conn:
            assert recovery.queue_alert_notifications(conn, recovery.configuration()) == expected
    status = client.get('/api/admin/recovery').json()
    assert status['external_alert_delivery_configured']
    assert status['external_alert_delivery']['statuses'] == {'pending': 1}
    assert not status['external_alert_delivery']['inbox_receipt_verified']
    with db.tx() as conn:
        row = conn.execute("SELECT recipient,kind,encrypted_payload FROM email_outbox").fetchone()
        assert row['recipient'] == 'registered-owner@fixture.invalid' and row['kind'] == 'operations_alert'
        assert b'Operational alert requires review' not in bytes(row['encrypted_payload'])
        assert conn.execute('SELECT count(*) n FROM recovery_alert_notifications').fetchone()['n'] == 1


@pytest.mark.postgres
def test_runtime_observation_preserves_backup_truth_and_detects_stalled_queue(client, monkeypatch):
    _login(client)
    monkeypatch.setenv('RENDER_POSTGRES_ID', recovery.INDEPENDENT_DATABASE_ID)
    monkeypatch.setattr(monitoring, '_runtime_resources', lambda: {'container_memory_bytes': 90, 'container_memory_limit_bytes': 100,
                    'ephemeral_disk_used_bytes': 10, 'ephemeral_disk_total_bytes': 100})
    with db.tx() as conn:
        conn.execute("INSERT INTO email_outbox(id,brand_id,recipient,kind,dedupe_key,encrypted_payload,created_at) "
                     "VALUES('monitor-stalled','kps','synthetic@fixture.invalid','identity','synthetic-stalled',%s,now()-interval '30 minutes')", (b'not-real-message',))
    result = monitoring.check_once()
    assert result['checked']
    assert set(result['failing_checks']) >= {'runtime_memory_pressure','runtime_email_queue_stalled','runtime_backup_observation_stale'}
    with db.tx() as conn:
        assert not conn.execute('SELECT * FROM recovery_state').fetchone()  # A runtime check cannot certify a backup.
        events = conn.execute('SELECT outcome,source FROM recovery_events').fetchall()
        assert events and all(row['source'] == 'runtime_monitor' and row['outcome'] == 'failed' for row in events)
        count = conn.execute('SELECT count(*) n FROM recovery_alerts').fetchone()['n']
    assert monitoring.check_once()['checked']
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM recovery_alerts').fetchone()['n'] == count
    status = client.get('/api/admin/monitoring')
    assert status.status_code == 200
    assert not status.json()['out_of_process_down_alert_verified']
    assert status.json()['metrics']['mail_queued'] == 1
    assert client.post('/api/admin/monitoring/refresh').status_code == 200
    _login(client, role='driver')
    assert client.get('/api/admin/monitoring').status_code == 403
    assert client.post('/api/admin/monitoring/refresh').status_code == 403


def test_outage_observation_never_claims_it_queued_an_external_alert(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://synthetic/test_never_connected')
    monkeypatch.setenv('RENDER_POSTGRES_ID', recovery.INDEPENDENT_DATABASE_ID)
    def unavailable():
        raise RuntimeError('secret connection must not escape')
    monkeypatch.setattr(db, 'tx', unavailable)
    result = monitoring.check_once()
    assert result['database'] == 'unavailable' and not result['checked']
    assert not result['out_of_process_alert_delivery_verified'] and 'secret' not in json.dumps(result)


@pytest.mark.postgres
def test_dashboard_backup_evidence_survives_restart_without_claiming_automated_or_inbox_proof(client, monkeypatch):
    from datetime import datetime, timezone
    _login(client)
    monkeypatch.setenv('RENDER_POSTGRES_ID', recovery.INDEPENDENT_DATABASE_ID)
    stamp = datetime.now(timezone.utc)
    metadata = {'provider_recovery_status': 'AVAILABLE', 'observation_method': 'provider_dashboard',
                'logical_export_retention_days': 7}
    with db.tx() as conn:
        recovery._record(conn, recovery.configuration(), 'synthetic-dashboard-observation',
                         'postgres_backup_completed', 'succeeded', 'provider_dashboard', stamp,
                         db.digest(metadata), metadata)
        conn.execute('UPDATE recovery_state SET retention_days=3 WHERE database_id=%s',
                     (recovery.INDEPENDENT_DATABASE_ID,))
    db.migrate()
    observed = client.get('/api/admin/recovery').json()
    assert observed['last_success_at'] == stamp.isoformat()
    assert observed['verification_source'] == 'provider_dashboard'
    assert observed['provider_recovery_status'] == 'AVAILABLE' and observed['retention_days'] == 3
    assert not observed['api_configured'] and not observed['poll_configured']
    assert 'PROVIDER_VERIFICATION_NOT_CONFIGURED' in observed['blockers']
    assert not observed['external_alert_delivery']['inbox_receipt_verified']
