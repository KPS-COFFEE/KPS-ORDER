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
    calls, first_pid = [], []
    counter_lock = threading.Lock()

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
    with psycopg.connect(database, autocommit=True, row_factory=dict_row) as observer:
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(db.migrate)
            try:
                assert first_entered.wait(5)
                second = workers.submit(db.migrate)
                wait_for(lambda: observer.execute(
                    "SELECT 1 FROM pg_locks WHERE locktype='advisory' AND objid=764832610 "
                    'AND pid<>%s AND NOT granted', (first_pid[0],)).fetchone())
                release_first.set()
                first.result(timeout=10)
                second.result(timeout=10)
            finally:
                release_first.set()
    assert len(calls) == 2


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
