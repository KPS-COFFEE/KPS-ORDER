"""Real PostgreSQL startup races against live writes and another boot worker."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import psycopg
import pytest
from psycopg.rows import dict_row

from app import db

pytestmark = pytest.mark.postgres


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError('Concurrent PostgreSQL transaction did not reach its barrier')


def test_startup_snapshot_allows_opposite_order_operational_writes(database, monkeypatch):
    """Old SHARE users -> wait orders cycles with writer orders -> insert users."""
    original = db.preservation_snapshot
    started, writer_committed = threading.Event(), threading.Event()
    migration_pid = []

    def snapshot(conn, baseline=None, **kwargs):
        if baseline is None:
            migration_pid.append(conn.info.backend_pid)
            started.set()
        result = original(conn, baseline, **kwargs)
        if baseline is None:
            assert writer_committed.wait(5), 'Guard blocked the operational writer'
        return result

    monkeypatch.setattr(db, 'preservation_snapshot', snapshot)
    with db.tx() as c:
        before_count = c.execute('SELECT count(*) AS n FROM users').fetchone()['n']
    with psycopg.connect(database, autocommit=True, row_factory=dict_row) as writer:
        writer.execute("SET statement_timeout='4000ms'")
        with ThreadPoolExecutor(max_workers=1) as workers:
            try:
                with writer.transaction():
                    writer.execute('LOCK TABLE orders IN ROW EXCLUSIVE MODE')
                    boot = workers.submit(db.migrate)
                    assert started.wait(5)
                    wait_for(lambda: writer.execute(
                        "SELECT 1 FROM pg_locks WHERE pid=%s AND relation='users'::regclass "
                        "AND granted AND mode IN ('ShareLock','AccessShareLock')",
                        (migration_pid[0],)).fetchone())
                    writer.execute("INSERT INTO users(id,role,brand_id,display_name) "
                                   "VALUES('concurrent-customer','customer','kps','LIVE WRITE')")
                writer_committed.set()
                boot.result(timeout=10)
            finally:
                writer_committed.set()
    with db.tx() as c:
        assert c.execute("SELECT display_name FROM users WHERE id='concurrent-customer'").fetchone()['display_name'] == 'LIVE WRITE'
        proof = c.execute("SELECT details FROM audit_events WHERE action='release.operational_data_preserved' "
                          "ORDER BY created_at DESC LIMIT 1").fetchone()['details']
        assert proof['verified'] and proof['checks']['users']['count'] == before_count
    # The next boot must take a new snapshot and include the committed customer.
    db.migrate()
    with db.tx() as c:
        proof = c.execute("SELECT details FROM audit_events WHERE action='release.operational_data_preserved' "
                          "ORDER BY created_at DESC LIMIT 1").fetchone()['details']
        assert proof['checks']['users']['count'] == before_count + 1


def test_overlapping_boots_take_snapshot_after_migration_lock(database, monkeypatch):
    first_entered, release_first = threading.Event(), threading.Event()
    second_waiting = threading.Event()
    calls, first_pid = [], []
    counter_lock = threading.Lock()
    original_acquire = db.acquire_migration_lock

    def acquire(conn):
        if first_pid and conn.info.backend_pid != first_pid[0]:
            second_waiting.set()
        original_acquire(conn)

    def migrations(conn, **kwargs):
        with counter_lock:
            calls.append(conn.info.backend_pid)
            number = len(calls)
        if number == 1:
            first_pid.append(conn.info.backend_pid)
            first_entered.set()
            assert release_first.wait(5)
            conn.execute('CREATE TABLE startup_boot_marker(value integer NOT NULL)')
            conn.execute('INSERT INTO startup_boot_marker VALUES(1)')
        else:
            assert conn.execute('SELECT value FROM startup_boot_marker').fetchone()['value'] == 1

    monkeypatch.setattr(db, 'apply_migrations', migrations)
    monkeypatch.setattr(db, 'acquire_migration_lock', acquire)
    with psycopg.connect(database, autocommit=True, row_factory=dict_row) as observer:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(db.migrate)
            try:
                assert first_entered.wait(5)
                second = workers.submit(db.migrate)
                assert second_waiting.wait(5)
                assert observer.execute(
                    "SELECT 1 FROM pg_locks WHERE locktype='advisory' AND objid=764832610 "
                    'AND pid=%s AND granted', (first_pid[0],)).fetchone()
                assert len(calls) == 1
                release_first.set()
                first.result(timeout=10)
                second.result(timeout=10)
            finally:
                release_first.set()
    assert len(calls) == 2


def test_migration_wait_can_outlast_statement_deadline(database):
    """Reproduce the production failure without spending 15 seconds per test.

    Hold the same session lock longer than the waiter's SQL timeout; individual
    probes must succeed, preserve the timeout, and acquire only after release.
    """
    with psycopg.connect(database, autocommit=True, row_factory=dict_row) as holder:
        holder.execute('SELECT pg_advisory_lock(%s)', (db.MIGRATION_LOCK_KEY,))
        started = threading.Event()

        def waiter():
            with psycopg.connect(database, autocommit=True, row_factory=dict_row) as conn:
                conn.execute("SET statement_timeout='100ms'")
                started.set()
                db.acquire_migration_lock(conn)
                try:
                    assert conn.execute('SHOW statement_timeout').fetchone()['statement_timeout'] == '100ms'
                finally:
                    conn.execute('SELECT pg_advisory_unlock(%s)', (db.MIGRATION_LOCK_KEY,))

        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(waiter)
            try:
                assert started.wait(5)
                time.sleep(0.3)
                assert not future.done()
            finally:
                holder.execute('SELECT pg_advisory_unlock(%s)', (db.MIGRATION_LOCK_KEY,))
            future.result(timeout=5)


def test_migration_wait_timeout_never_unlocks_another_owner(database, monkeypatch):
    monkeypatch.setattr(db, 'MIGRATION_LOCK_WAIT_SECONDS', 0.1)
    with psycopg.connect(database, autocommit=True, row_factory=dict_row) as holder:
        holder.execute('SELECT pg_advisory_lock(%s)', (db.MIGRATION_LOCK_KEY,))
        with psycopg.connect(database, autocommit=True, row_factory=dict_row) as waiter:
            with pytest.raises(RuntimeError, match='bounded'):
                db.acquire_migration_lock(waiter)
            assert not waiter.execute('SELECT pg_try_advisory_lock(%s) AS acquired',
                                      (db.MIGRATION_LOCK_KEY,)).fetchone()['acquired']
        assert holder.execute('SELECT pg_advisory_unlock(%s) AS released',
                              (db.MIGRATION_LOCK_KEY,)).fetchone()['released']


@pytest.mark.parametrize('failure', [psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected])
def test_transient_migration_conflict_retries_whole_rolled_back_transaction(database, monkeypatch, failure):
    original = db.apply_migrations
    with db.tx() as c:
        name = c.execute("SELECT name FROM branches WHERE id='kps-branch-1'").fetchone()['name']
    attempts = []

    def migrations(conn, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            conn.execute("UPDATE branches SET name='ABORTED ATTEMPT' WHERE id='kps-branch-1'")
            raise failure('Synthetic test conflict')
        assert conn.execute("SELECT name FROM branches WHERE id='kps-branch-1'").fetchone()['name'] == name
        original(conn, **kwargs)

    monkeypatch.setattr(db, 'apply_migrations', migrations)
    db.migrate()
    assert len(attempts) == 2
    with db.tx() as c:
        assert c.execute("SELECT name FROM branches WHERE id='kps-branch-1'").fetchone()['name'] == name


def test_persistent_conflict_is_bounded_and_releases_migration_lock(database, monkeypatch):
    attempts = []

    def conflict(conn, **kwargs):
        attempts.append(1)
        raise psycopg.errors.DeadlockDetected('Synthetic persistent test conflict')

    monkeypatch.setattr(db, 'apply_migrations', conflict)
    with pytest.raises(psycopg.errors.DeadlockDetected):
        db.migrate()
    assert len(attempts) == 3
    with psycopg.connect(database, autocommit=True) as observer:
        assert observer.execute('SELECT pg_try_advisory_lock(764832610)').fetchone()[0]
        observer.execute('SELECT pg_advisory_unlock(764832610)')


def test_unlocked_guard_rejects_statement_only_snapshot(database):
    with db.tx() as conn:
        with pytest.raises(RuntimeError, match='transaction-wide stable snapshot'):
            db.preservation_snapshot(conn, lock_tables=False)


@pytest.mark.parametrize('change', ['append', 'update', 'delete', 'replace'])
def test_startup_preserves_original_audit_bytes_and_allows_only_additions(database, monkeypatch, change):
    """New evidence cannot mask an update, deletion or same-ID replacement."""
    with db.tx() as conn:
        db.audit(conn, None, 'test.original', 'runtime', 'audit-preservation', {'value': 1})
        original = conn.execute("SELECT id FROM audit_events WHERE action='test.original'").fetchone()['id']
        before = db.preservation_snapshot(conn)

    def migration(conn, **kwargs):
        db.audit(conn, None, 'test.appended', 'runtime', 'new-evidence', {'value': 2})
        if change == 'update':
            conn.execute("UPDATE audit_events SET details='{}'::jsonb WHERE id=%s", (original,))
        elif change in {'delete', 'replace'}:
            conn.execute('DELETE FROM audit_events WHERE id=%s', (original,))
            if change == 'replace':
                conn.execute("INSERT INTO audit_events(id,action,entity_type,entity_id) VALUES(%s,'test.replaced','runtime','replacement')", (original,))

    monkeypatch.setattr(db, 'apply_migrations', migration)
    if change == 'append':
        db.migrate()
        with db.tx() as conn:
            assert db.preservation_snapshot(conn, before) == before
            proof = conn.execute("SELECT details FROM audit_events WHERE action='release.operational_data_preserved' ORDER BY created_at DESC LIMIT 1").fetchone()['details']
            assert proof['audit_events_appended'] == 1
            assert 'row_ids' not in str(proof)
            assert original not in str(proof)
    else:
        with pytest.raises(RuntimeError, match='operational data changed'):
            db.migrate()
        with db.tx() as conn:
            assert db.preservation_snapshot(conn, before) == before
            assert not conn.execute("SELECT 1 FROM audit_events WHERE action='test.appended'").fetchone()
