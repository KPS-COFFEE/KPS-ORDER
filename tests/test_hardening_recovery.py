"""Isolated PG transaction interruption, migrations, access gates and alert recovery.

The database fixture restricts every test to a UUID schema in a local test DB.
Backend interruption targets only the connection opened by this test. Provider
delivery is simulated, and never establishes real inbox or provider backup proof.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime, timezone

import httpx
import psycopg
import pytest

from app import db, email_delivery, recovery
from test_operations import ops
from test_recovery import _login
from tools import portable_backup


def _verified_owner(client, monkeypatch):
    owner = _login(client)
    monkeypatch.setenv('RENDER_POSTGRES_ID', recovery.INDEPENDENT_DATABASE_ID)
    monkeypatch.setenv('RENDER_BACKUP_BRAND_ID', 'kps')
    with db.tx() as conn:
        conn.execute("UPDATE users SET email='owner@fixture.invalid',email_verified_at=now() WHERE id=%s", (owner,))
    return owner


def _mail_channel(monkeypatch):
    monkeypatch.setenv('EMAIL_PROVIDER', 'resend')
    monkeypatch.setenv('EMAIL_FROM', 'synthetic-sender@fixture.invalid')
    monkeypatch.setenv('RESEND_API_KEY', 'synthetic-never-live')
    monkeypatch.setenv('MAIL_OUTBOX_KEY', base64.urlsafe_b64encode(b'H' * 32).decode())
    with db.tx() as conn:
        conn.execute("UPDATE identity_channels SET enabled=true,state='active' WHERE brand_id='kps' AND channel='email'")


@pytest.mark.postgres
@pytest.mark.parametrize('action,event_code', [
    ('backup', 'PORTABLE_BACKUP_FAILED'),
    ('verify', 'PORTABLE_ARCHIVE_VERIFICATION_FAILED'),
    ('restore-drill', 'ISOLATED_RESTORE_FAILED'),
])
def test_portable_tool_failure_is_durable_deduplicated_and_preserves_backup_truth(client, monkeypatch, action, event_code):
    owner = _verified_owner(client, monkeypatch)
    stamp = datetime.now(timezone.utc)
    with db.tx() as conn:
        recovery._record(conn, recovery.configuration(), 'evt-success-before-tool-failure',
                         'postgres_backup_completed', 'succeeded', 'signed_webhook', stamp, 'synthetic-digest')
        previous = conn.execute('SELECT * FROM recovery_state').fetchone()
    for _ in range(2):
        assert portable_backup.record_failure(action)
    with db.tx() as conn:
        assert conn.execute('SELECT * FROM recovery_state').fetchone() == previous
        alerts = conn.execute('SELECT * FROM recovery_alerts').fetchall()
        assert len(alerts) == 1 and alerts[0]['code'] == event_code
        notices = conn.execute('SELECT * FROM recovery_alert_notifications').fetchall()
        assert len(notices) == 1 and notices[0]['recipient_user_id'] == owner
        assert notices[0]['email_outbox_id'] is None
        assert conn.execute("SELECT count(*) n FROM recovery_events WHERE source='portable_backup'").fetchone()['n'] == 2
    db.migrate()
    status = client.get('/api/admin/recovery').json()
    assert status['last_success_at'] == stamp.isoformat()
    assert status['open_alerts'][0]['code'] == event_code
    assert status['external_alert_delivery']['statuses'] == {'waiting_channel': 1}


@pytest.mark.postgres
def test_cli_snapshot_write_failure_records_owner_alert_without_sensitive_path(client, monkeypatch, tmp_path):
    _verified_owner(client, monkeypatch)
    def failed_write(*args):
        raise OSError('synthetic-password-secret-in-path')
    monkeypatch.setattr(portable_backup, 'private_write', failed_write)
    monkeypatch.setattr(sys, 'argv', ['portable_backup', 'backup', '--output', str(tmp_path / 'private.zip')])
    with pytest.raises(OSError):
        portable_backup.main()
    with db.tx() as conn:
        events = conn.execute("SELECT metadata FROM recovery_events WHERE source='portable_backup'").fetchall()
        assert events == [{'metadata': {'action': 'backup'}}]
        assert 'synthetic-password-secret' not in json.dumps(events)
        assert conn.execute('SELECT code FROM recovery_alerts').fetchone()['code'] == 'PORTABLE_BACKUP_FAILED'
    assert not list(tmp_path.iterdir())


def test_failure_observation_fails_closed_when_unbound_or_source_unavailable(monkeypatch, capsys):
    def unavailable():
        raise psycopg.OperationalError('postgresql://private-password-must-not-escape')
    monkeypatch.setattr(db, 'tx', unavailable)
    assert portable_backup.record_failure('backup') is False
    assert not capsys.readouterr().err
    monkeypatch.setenv('RENDER_POSTGRES_ID', recovery.INDEPENDENT_DATABASE_ID)
    assert portable_backup.record_failure('backup') is False
    message = capsys.readouterr().err
    assert 'independent scheduler monitoring is required' in message
    assert 'private-password' not in message and 'postgresql://' not in message


@pytest.mark.postgres
def test_failed_owner_notice_retries_same_durable_mail_without_duplicate_alert(client, monkeypatch):
    _verified_owner(client, monkeypatch)
    _mail_channel(monkeypatch)
    assert portable_backup.record_failure('backup')
    seen = []
    def unavailable(row):
        seen.append(row['id'])
        raise httpx.ReadTimeout('private-provider-secret-must-not-escape')
    monkeypatch.setattr(email_delivery, '_send', unavailable)
    assert email_delivery.dispatch_once(1)['provider_accepted'] == 0
    with db.tx() as conn:
        row = conn.execute('SELECT * FROM email_outbox').fetchone()
        assert row['status'] == 'failed' and row['last_error'] == 'DELIVERY_READTIMEOUT'
        assert row['attempts'] == 1 and row['lease_until'] is None
        conn.execute("UPDATE email_outbox SET next_attempt_at=now()-interval '1 second' WHERE id=%s", (row['id'],))
    assert portable_backup.record_failure('backup')
    def accepted(row):
        seen.append(row['id'])
        return 'synthetic-provider-accepted-reference'
    monkeypatch.setattr(email_delivery, '_send', accepted)
    assert email_delivery.dispatch_once(1)['provider_accepted'] == 1
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM email_outbox').fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) n FROM recovery_alerts').fetchone()['n'] == 1
        assert conn.execute('SELECT status,attempts FROM email_outbox').fetchone() == {'status': 'provider_accepted', 'attempts': 2}
    assert seen == [row['id'], row['id']]
    status = client.get('/api/admin/recovery').json()['external_alert_delivery']
    assert status['statuses'] == {'provider_accepted': 1}
    assert status['inbox_receipt_verified'] is False


@pytest.mark.postgres
def test_terminated_pg_connection_rolls_back_uncommitted_order_event_and_replay_key(ops):
    _, _, order, _ = ops
    committed = order('pickup')
    interrupted = db.new_id('interrupted-order-')
    with pytest.raises(psycopg.OperationalError):
        with db.tx() as conn:
            pid = conn.execute('SELECT pg_backend_pid() n').fetchone()['n']
            schema = conn.execute('SELECT current_schema() n').fetchone()['n']
            assert schema.startswith('kps_test_')
            def perform():
                conn.execute('INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) '
                             'SELECT %s,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer FROM orders WHERE id=%s',
                             (interrupted, committed))
                db.event(conn, interrupted, 'ops-customer', 'created')
                return {'id': interrupted}
            db.idempotent(conn, 'ops-customer', 'interrupted-hardening-key', 'create-order', {'order': interrupted}, perform)
            # Fixtures validate local host/test database before this control connection.
            with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True) as control:
                assert control.execute('SELECT pg_terminate_backend(%s)', (pid,)).fetchone()[0]
            conn.execute('SELECT 1')
    with db.tx() as conn:
        assert conn.execute('SELECT id FROM orders WHERE id=%s', (committed,)).fetchone()
        assert conn.execute('SELECT id FROM orders WHERE id=%s', (interrupted,)).fetchone() is None
        assert conn.execute('SELECT id FROM order_events WHERE order_id=%s', (interrupted,)).fetchone() is None
        assert conn.execute("SELECT key FROM idempotency_keys WHERE key='interrupted-hardening-key'").fetchone() is None
    db.migrate()
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM orders').fetchone()['n'] == 1


@pytest.mark.postgres
def test_failed_forward_migration_rolls_back_schema_and_preserves_existing_rows(database, monkeypatch, tmp_path):
    migration_dir = tmp_path / 'migrations'
    migration_dir.mkdir()
    (migration_dir / '999_hardening_failure.sql').write_text(
        'CREATE TABLE hardening_partial_migration(id integer PRIMARY KEY); '
        'INSERT INTO hardening_partial_migration VALUES(1); SELECT 1/0;')
    with db.tx() as conn:
        before = db.preservation_snapshot(conn)
        applied = conn.execute('SELECT version,sha256 FROM schema_migrations ORDER BY version').fetchall()
    monkeypatch.setattr(db, 'ROOT', tmp_path)
    with pytest.raises(psycopg.errors.DivisionByZero):
        with db.tx() as conn:
            db.apply_migrations(conn, seed=False, owner_decisions=False)
    with db.tx() as conn:
        assert db.preservation_snapshot(conn, before) == before
        assert conn.execute("SELECT to_regclass('hardening_partial_migration') n").fetchone()['n'] is None
        assert conn.execute('SELECT version,sha256 FROM schema_migrations ORDER BY version').fetchall() == applied


@pytest.mark.postgres
@pytest.mark.parametrize('kind', ['applied_checksum', 'destructive_sql'])
def test_applied_migration_tampering_and_destructive_forward_sql_fail_closed(database, monkeypatch, tmp_path, kind):
    migrations = tmp_path / 'migrations'
    migrations.mkdir()
    with db.tx() as conn:
        before = db.preservation_snapshot(conn)
        version = conn.execute('SELECT version FROM schema_migrations ORDER BY version LIMIT 1').fetchone()['version']
    if kind == 'applied_checksum':
        (migrations / version).write_text('SELECT 1; -- altered applied migration')
        match = 'changed'
    else:
        (migrations / '999_destructive.sql').write_text('TRUNCATE orders;')
        match = 'Destructive'
    monkeypatch.setattr(db, 'ROOT', tmp_path)
    with pytest.raises(RuntimeError, match=match):
        with db.tx() as conn:
            db.apply_migrations(conn, seed=False, owner_decisions=False)
    with db.tx() as conn:
        assert db.preservation_snapshot(conn, before) == before


@pytest.mark.postgres
@pytest.mark.parametrize('target', ['order_event', 'collection', 'closure_report'])
def test_database_foreign_keys_reject_orphan_operational_records(ops, target):
    statements = {
        'order_event': ("INSERT INTO order_events(id,order_id,actor_id,event_type) VALUES('orphan-event','missing-order','ops-manager','created')", ()),
        'collection': ("INSERT INTO collections(id,order_id,collector_id,method,amount_halalas) VALUES('orphan-collection','missing-order','ops-manager','cash',1)", ()),
        'closure_report': ("INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES('orphan-report','missing-shift','driver','ops-brand','[]','ops-driver','{}')", ()),
    }
    statement, params = statements[target]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.tx() as conn:
            conn.execute(statement, params)
    with db.tx() as conn:
        assert conn.execute("SELECT count(*) n FROM order_events WHERE id='orphan-event'").fetchone()['n'] == 0
        assert conn.execute("SELECT count(*) n FROM collections WHERE id='orphan-collection'").fetchone()['n'] == 0
        assert conn.execute("SELECT count(*) n FROM closure_reports WHERE id='orphan-report'").fetchone()['n'] == 0


@pytest.mark.postgres
@pytest.mark.parametrize('condition', ['expired', 'revoked', 'inactive'])
def test_staff_session_expiry_revocation_and_disable_are_immediate_server_gates(client, condition):
    owner = _login(client)
    assert client.get('/api/orders').status_code == 200
    with db.tx() as conn:
        if condition == 'expired':
            conn.execute("UPDATE sessions SET expires_at=now()-interval '1 second' WHERE user_id=%s", (owner,))
        elif condition == 'revoked':
            conn.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s', (owner,))
        else:
            conn.execute('UPDATE users SET active=false WHERE id=%s', (owner,))
    assert client.get('/api/orders').status_code == 401
    assert client.post('/api/admin/recovery/refresh').status_code == 401
    assert client.get('/api/session').json()['user'] is None


@pytest.mark.postgres
def test_customer_and_staff_identifier_tampering_never_reveals_other_order(ops):
    actor, _, order, _ = ops
    oid = order('pickup')
    with db.tx() as conn:
        conn.execute("INSERT INTO users(id,display_name,role,brand_id) VALUES('ops-customer2','Second synthetic customer','customer','ops-brand')")
    for user in ('customer2', 'other', 'driver', 'manager'):
        hidden = actor(user).get('/api/orders/' + oid)
        absent = actor(user).get('/api/orders/does-not-exist')
        assert hidden.status_code == absent.status_code == 404
        assert hidden.json() == absent.json()
    assert actor('customer').get('/api/orders/' + oid).status_code == 200
    assert actor('branch').get('/api/orders/' + oid).status_code == 200
    with db.tx() as conn:
        conn.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-branch'")
    assert actor('branch').get('/api/orders/' + oid).status_code == 404


@pytest.mark.postgres
@pytest.mark.parametrize('payload', [
    {'display_name': 'Valid name', 'phone': '0500000001', 'role': 'owner'},
    {'display_name': '\x00', 'phone': '0500000001'},
    {'display_name': 'Valid name', 'phone': "'; DROP TABLE users; --"},
])
def test_malformed_guest_payload_cannot_change_authority_or_leak_input(client, payload):
    with db.tx() as conn:
        before = conn.execute('SELECT count(*) n FROM users').fetchone()['n']
    response = client.post('/api/identity/guest', json=payload)
    assert response.status_code == 422
    assert "DROP TABLE" not in response.text and '\\u0000' not in response.text
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM users').fetchone()['n'] == before


@pytest.mark.postgres
def test_database_connection_failure_is_sanitized_and_next_request_recovers(client, monkeypatch):
    _login(client)
    assert client.get('/api/orders').status_code == 200
    original = db.tx
    def unavailable():
        raise psycopg.OperationalError('postgresql://private-password@source.invalid/customer-data')
    monkeypatch.setattr(db, 'tx', unavailable)
    unavailable_response = client.get('/api/orders')
    assert unavailable_response.status_code == 503
    assert 'private-password' not in unavailable_response.text
    assert 'source.invalid' not in unavailable_response.text
    assert unavailable_response.headers['X-Request-ID']
    assert client.get('/ready').status_code == 503
    assert client.get('/health').status_code == 200
    monkeypatch.setattr(db, 'tx', original)
    assert client.get('/ready').status_code == 200
    assert client.get('/api/orders').status_code == 200
