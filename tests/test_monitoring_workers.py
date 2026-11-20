"""Real PostgreSQL interval aggregation, racing publishers and isolated restore."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from threading import Barrier, current_thread
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest

from app import db, monitoring, recovery
from app.exports import build_archive, restore_archive
from test_recovery import _login

pytestmark = pytest.mark.postgres


@pytest.fixture
def pulses(client, monkeypatch):
    _login(client)
    monkeypatch.setenv('RENDER_POSTGRES_ID', recovery.INDEPENDENT_DATABASE_ID)
    monkeypatch.setattr(monitoring, '_requests', {'requests': 0, 'server_errors': 0})
    monkeypatch.setattr(monitoring, '_runtime_resources', lambda: {
        'process_cpu_seconds': 1.5, 'process_memory_peak_bytes': 1000,
        'container_memory_bytes': 10, 'container_memory_limit_bytes': 100,
        'ephemeral_disk_used_bytes': 10, 'ephemeral_disk_total_bytes': 100})
    with db.tx() as conn:
        recovery._record(conn, recovery.configuration(), 'evt-worker-monitor-backup', 'postgres_backup_completed',
                         'succeeded', 'signed_webhook', datetime.now(timezone.utc), 'synthetic-backup-digest')
        # Freeze a clock reading obtained from PostgreSQL only for deterministic
        # publication races/expiry tests; production always reads the DB clock.
        tick = monitoring._publication_tick(conn, monitoring.configuration()['runtime_interval_seconds'])
    monkeypatch.setattr(monitoring, '_publication_tick', lambda conn, seconds: dict(tick))
    return client, tick


def state():
    with db.tx() as conn:
        return conn.execute('SELECT metrics,failing_checks FROM recovery_monitor_state WHERE brand_id=%s AND database_id=%s',
                            ('kps', recovery.INDEPENDENT_DATABASE_ID)).fetchone()


def worker(monkeypatch, name):
    monkeypatch.setattr(monitoring, '_worker_id', lambda: name)


def test_two_workers_accumulate_subthreshold_errors_and_refresh_cannot_erase_them(pulses, monkeypatch):
    client, tick = pulses
    worker(monkeypatch, 'worker-a')
    for _ in range(2):
        monitoring.record_response(503)
    assert monitoring.check_once()['failing_checks'] == []
    assert state()['metrics']['server_errors'] == 2
    assert monitoring.check_once()['failing_checks'] == []
    assert state()['metrics']['server_errors'] == 2
    worker(monkeypatch, 'worker-b')
    for _ in range(2):
        monitoring.record_response(500)
    assert monitoring.check_once()['failing_checks'] == ['runtime_server_errors']
    metrics = state()['metrics']
    assert metrics['requests'] == metrics['server_errors'] == 4
    assert metrics['observed_workers'] == 2
    assert metrics['http_counter_scope'] == 'all_workers_in_current_interval'
    assert metrics['process_resource_scope'] == 'publishing_worker'
    assert metrics['process_cpu_seconds'] == 1.5
    assert metrics['http_counter_window']['start'] == tick['window_start'].isoformat()
    assert metrics['http_counter_window']['clock'] == 'postgresql'
    assert monitoring.check_once()['failing_checks'] == ['runtime_server_errors']
    with db.tx() as conn:
        assert conn.execute("SELECT count(*) n FROM recovery_alerts WHERE event_type='runtime_server_errors'").fetchone()['n'] == 1
        assert conn.execute('SELECT count(*) n FROM recovery_worker_observations').fetchone()['n'] == 2
        assert conn.execute('SELECT last_success_at FROM recovery_state').fetchone()['last_success_at'] is not None
    response = client.get('/api/admin/monitoring')
    assert response.status_code == 200 and response.json()['metrics']['server_errors'] == 4


def test_racing_worker_publications_share_one_threshold_and_one_alert(pulses, monkeypatch):
    barrier = Barrier(2)
    monkeypatch.setattr(monitoring, '_take_requests', lambda: {'requests': 2, 'server_errors': 2})
    def identity():
        barrier.wait(timeout=10)
        return current_thread().name.replace('_', '-')
    monkeypatch.setattr(monitoring, '_worker_id', identity)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='worker') as executor:
        results = list(executor.map(lambda _: monitoring.check_once(), range(2)))
    assert all(result['checked'] for result in results)
    assert sorted(bool(result['failing_checks']) for result in results) == [False, True]
    assert state()['metrics']['requests'] == state()['metrics']['server_errors'] == 4
    assert state()['metrics']['observed_workers'] == 2
    with db.tx() as conn:
        assert conn.execute("SELECT count(*) n FROM recovery_alerts WHERE event_type='runtime_server_errors'").fetchone()['n'] == 1


def test_expired_interval_retires_stale_workers_and_clears_counters_without_dropping_alerts(pulses, monkeypatch):
    _, tick = pulses
    worker(monkeypatch, 'retired-worker')
    for _ in range(3):
        monitoring.record_response(503)
    assert monitoring.check_once()['failing_checks'] == ['runtime_server_errors']
    seconds = monitoring.configuration()['runtime_interval_seconds']
    tick['window_start'] += timedelta(seconds=seconds)
    tick['observed_at'] += timedelta(seconds=seconds)
    worker(monkeypatch, 'new-worker')
    monitoring.record_response(200)
    assert monitoring.check_once()['failing_checks'] == []
    metrics = state()['metrics']
    assert metrics['requests'] == 1 and metrics['server_errors'] == 0
    assert metrics['observed_workers'] == 1
    with db.tx() as conn:
        assert [row['worker_id'] for row in conn.execute('SELECT worker_id FROM recovery_worker_observations').fetchall()] == ['new-worker']
        assert conn.execute("SELECT count(*) n FROM recovery_alerts WHERE event_type='runtime_server_errors' AND acknowledged_at IS NULL").fetchone()['n'] == 1
    # A configuration interval change also retires incompatible old buckets.
    monkeypatch.setenv('MONITOR_INTERVAL_SECONDS', str(seconds * 2))
    assert monitoring.check_once()['checked']
    with db.tx() as conn:
        assert conn.execute('SELECT DISTINCT window_seconds FROM recovery_worker_observations').fetchall() == [{'window_seconds': seconds * 2}]


def test_other_brand_and_database_pulses_never_enter_or_leave_current_scope(pulses, monkeypatch):
    worker(monkeypatch, 'current-worker')
    assert monitoring.check_once()['checked']
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('other-monitor-brand','TEST other brand')")
        for brand, database in [('other-monitor-brand', recovery.INDEPENDENT_DATABASE_ID), ('kps', 'other-database')]:
            conn.execute('INSERT INTO recovery_worker_observations(brand_id,database_id,worker_id,window_start,window_seconds,checked_at,requests,server_errors) '
                         "SELECT %s,%s,'other-worker',window_start-interval '1 day',window_seconds,checked_at-interval '1 day',99,99 "
                         "FROM recovery_worker_observations WHERE brand_id='kps' AND database_id=%s LIMIT 1",
                         (brand, database, recovery.INDEPENDENT_DATABASE_ID))
    assert monitoring.check_once()['failing_checks'] == []
    assert state()['metrics']['server_errors'] == 0 and state()['metrics']['observed_workers'] == 1
    with db.tx() as conn:
        assert conn.execute("SELECT count(*) n FROM recovery_worker_observations WHERE worker_id='other-worker'").fetchone()['n'] == 2


def test_publication_failure_rolls_back_pulses_and_restores_local_counters(pulses, monkeypatch):
    worker(monkeypatch, 'rollback-worker')
    assert monitoring.check_once()['checked']
    with db.tx() as conn:
        before = conn.execute('SELECT * FROM recovery_worker_observations').fetchall()
        conn.execute("ALTER TABLE recovery_monitor_state ADD CONSTRAINT reject_nonzero_worker_errors CHECK((metrics->>'server_errors')::bigint=0)")
    monitoring.record_response(503)
    monitoring.record_response(503)
    failed = monitoring.check_once()
    assert failed['checked'] is False and failed['database'] == 'unavailable'
    assert monitoring._requests == {'requests': 2, 'server_errors': 3}
    with db.tx() as conn:
        assert conn.execute('SELECT * FROM recovery_worker_observations').fetchall() == before
        assert conn.execute('SELECT count(*) n FROM recovery_alerts').fetchone()['n'] == 0
        conn.execute('ALTER TABLE recovery_monitor_state DROP CONSTRAINT reject_nonzero_worker_errors')
    assert monitoring.check_once()['failing_checks'] == ['runtime_server_errors']
    assert monitoring._requests == {'requests': 0, 'server_errors': 0}
    assert state()['metrics']['server_errors'] == 3


def test_isolated_restore_discards_verified_ephemeral_pulses_but_preserves_history(pulses, monkeypatch):
    worker(monkeypatch, 'source-worker')
    for _ in range(3):
        monitoring.record_response(500)
    assert monitoring.check_once()['failing_checks'] == ['runtime_server_errors']
    source_state = state()
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        archive, manifest = build_archive(conn, full=True)
    target = 'kps_restore_' + uuid4().hex
    base = os.environ['TEST_DATABASE_URL']
    try:
        result = restore_archive(archive, target_url=base, target_schema=target)
        assert result['verified'] and result['counts'] == manifest['counts']
        assert result['ephemeral_worker_observations_discarded'] == 1
        assert result['ephemeral_monitor_state_discarded'] == 1
        with psycopg.connect(base, row_factory=dict_row) as restored:
            restored.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
            assert restored.execute('SELECT count(*) n FROM recovery_worker_observations').fetchone()['n'] == 0
            assert restored.execute('SELECT count(*) n FROM recovery_monitor_state').fetchone()['n'] == 0
            assert restored.execute("SELECT count(*) n FROM recovery_alerts WHERE event_type='runtime_server_errors'").fetchone()['n'] == 1
            assert restored.execute('SELECT last_success_at FROM recovery_state').fetchone()['last_success_at'] is not None
            audit = restored.execute("SELECT details FROM audit_events WHERE action='restore.ephemeral_worker_observations_discarded'").fetchone()
            assert audit['details'] == {'tables': {'recovery_worker_observations': 1, 'recovery_monitor_state': 1}}
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) n FROM recovery_worker_observations').fetchone()['n'] == 1
        assert state() == source_state
    finally:
        with psycopg.connect(base, autocommit=True) as cleanup:
            cleanup.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(target)))
