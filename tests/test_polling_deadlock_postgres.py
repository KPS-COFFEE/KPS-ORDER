"""Production polling lock cycles, using synthetic rows in a local UUID schema.

The dedicated acceptance workflow also runs these two cases on the immutable
pre-fix application. That mode must observe PostgreSQL 40P01, rather than
silently declaring a timeout to be a successful reproduction.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
import threading
import time

import psycopg
from psycopg.rows import dict_row
import pytest

from app import chat, db, multibrand, operations
from test_operations import ops

pytestmark = pytest.mark.postgres
BASELINE = os.getenv('EXPECT_POLLING_DEADLOCK_BASELINE') == '1'


def _wait_for(predicate):
    end = time.monotonic() + 4
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('The real PostgreSQL transaction did not reach its lock barrier')


def _observe_reader(monkeypatch):
    """Observe the actual endpoint's transaction, without changing its SQL."""
    original = db.tx
    pid = []
    entered = threading.Event()

    @contextmanager
    def transaction(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            if threading.current_thread().name.startswith('polling-reader'):
                pid.append(conn.info.backend_pid)
                entered.set()
            yield conn

    monkeypatch.setattr(db, 'tx', transaction)
    return pid, entered


def _result(future):
    try:
        return future.result(timeout=7), None
    except psycopg.errors.DeadlockDetected as exc:
        return None, exc.sqlstate


def test_chat_poll_does_not_cycle_with_gps_event_writer(ops, database, monkeypatch):
    _, _, order, shifts = ops
    _, sid = shifts()
    oid = order()
    with db.tx() as conn:
        conn.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by) "
                     "VALUES('poll-chat-task',%s,'ops-driver',%s,'picked_up','ops-manager')", (oid, sid))
        driver = conn.execute("SELECT * FROM users WHERE id='ops-driver'").fetchone()
        saved_order = conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
    pid, entered = _observe_reader(monkeypatch)
    states = []
    with psycopg.connect(database, autocommit=True, row_factory=dict_row) as writer:
        writer.execute("SET statement_timeout='6000ms'")
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='polling-reader') as workers:
            future = None
            try:
                with writer.transaction():
                    writer.execute('SELECT id FROM shifts WHERE id=%s FOR UPDATE', (sid,))
                    future = workers.submit(chat.driver_customer_messages, 'poll-chat-task', 0, driver)
                    assert entered.wait(4)
                    if BASELINE:
                        _wait_for(lambda: writer.execute('SELECT pg_blocking_pids(%s) p', (pid[0],)).fetchone()['p'])
                    else:
                        # This must finish BEFORE the GPS writer releases its
                        # shift lock; a retry after release cannot satisfy it.
                        assert future.result(timeout=4)['messages'] == []
                    writer.execute("INSERT INTO order_events(id,order_id,actor_id,event_type,details) "
                                   "VALUES('poll-gps-event',%s,'ops-driver','order.gps_near_customer','{}')", (oid,))
            except psycopg.errors.DeadlockDetected as exc:
                states.append(exc.sqlstate)
            finally:
                if future is not None:
                    result, state = _result(future)
                    if state:
                        states.append(state)
            if BASELINE:
                assert states == ['40P01'], states
            else:
                assert states == []
        # The database aborts exactly one deadlocked transaction atomically.
        # Replaying only this synthetic event after rollback proves no partial
        # operational order/task edit or duplicate event was committed.
        with writer.transaction():
            writer.execute("INSERT INTO order_events(id,order_id,actor_id,event_type,details) "
                           "VALUES('poll-gps-event',%s,'ops-driver','order.gps_near_customer','{}') "
                           "ON CONFLICT(id) DO NOTHING", (oid,))
    with db.tx() as conn:
        assert conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone() == saved_order
        assert conn.execute("SELECT count(*) n FROM order_events WHERE id='poll-gps-event'").fetchone()['n'] == 1
        assert conn.execute("SELECT status,driver_id FROM driver_tasks WHERE id='poll-chat-task'").fetchone() == {
            'status': 'picked_up', 'driver_id': 'ops-driver'}


def test_roster_poll_does_not_acquire_inverse_driver_advisory_locks(ops, database, monkeypatch):
    _, post, _, shifts = ops
    manager_sid, _ = shifts()
    post('driver2', '/api/shifts', {'kind': 'driver', 'branch_ids': ['ops-branch'],
                                  'manager_shift_id': manager_sid})
    # Only the release switch/binding is substituted; all roster, eligibility,
    # dedication SQL, row locks and advisory locks are real PostgreSQL work.
    monkeypatch.setattr(multibrand, 'enabled', lambda: True)
    monkeypatch.setattr(multibrand, 'isolation_guard', lambda: None)
    with db.tx() as conn:
        multibrand.migrate(conn)
        manager = conn.execute("SELECT * FROM users WHERE id='ops-manager'").fetchone()
    pid, entered = _observe_reader(monkeypatch)
    states = []
    with psycopg.connect(database, autocommit=True, row_factory=dict_row) as writer:
        writer.execute("SET statement_timeout='6000ms'")
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='polling-reader') as workers:
            future = None
            try:
                with writer.transaction():
                    multibrand.lock_driver(writer, 'ops-driver2')
                    future = workers.submit(operations.driver_roster, manager)
                    assert entered.wait(4)
                    if BASELINE:
                        _wait_for(lambda: writer.execute('SELECT pg_blocking_pids(%s) p', (pid[0],)).fetchone()['p'])
                    else:
                        result = future.result(timeout=4)
                        assert {r['id'] for r in result['drivers']} >= {'ops-driver', 'ops-driver2'}
                        assert all(r['assignment_eligible'] for r in result['drivers']
                                   if r['id'] in {'ops-driver', 'ops-driver2'})
                    multibrand.lock_driver(writer, 'ops-driver')
            except psycopg.errors.DeadlockDetected as exc:
                states.append(exc.sqlstate)
            finally:
                if future is not None:
                    _, state = _result(future)
                    if state:
                        states.append(state)
            assert states == (['40P01'] if BASELINE else [])
    with db.tx() as conn:
        assert conn.execute("SELECT count(*) n FROM shifts WHERE user_id IN ('ops-driver','ops-driver2') "
                            "AND status='open'").fetchone()['n'] == 2


def test_chat_send_does_not_cycle_with_gps_event_writer(ops, database, monkeypatch):
    """Keep mutation exclusion, but allow the GPS event's FK key-share lock."""
    from starlette.requests import Request
    _, _, order, shifts = ops
    _, sid = shifts()
    oid = order()
    with db.tx() as conn:
        conn.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by) "
                     "VALUES('poll-send-task',%s,'ops-driver',%s,'picked_up','ops-manager')", (oid, sid))
        driver = conn.execute("SELECT * FROM users WHERE id='ops-driver'").fetchone()
        saved_order = conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
    request = Request({'type': 'http', 'headers': [(b'idempotency-key', b'poll-send-one')],
                       'method': 'POST', 'path': '/api/tasks/poll-send-task/customer-chat'})
    body = chat.DriverCustomerMessage(text='Synthetic concurrent GPS and chat message')
    pid, entered = _observe_reader(monkeypatch)
    states = []
    with psycopg.connect(database, autocommit=True, row_factory=dict_row) as writer:
        writer.execute("SET statement_timeout='6000ms'")
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='polling-reader') as workers:
            future = None
            try:
                with writer.transaction():
                    writer.execute('SELECT id FROM shifts WHERE id=%s FOR UPDATE', (sid,))
                    future = workers.submit(chat.driver_customer_send, 'poll-send-task', body, request, driver)
                    assert entered.wait(4)
                    # The actual send holds its order/task locks, and waits
                    # for this GPS transaction's shift lock in both versions.
                    _wait_for(lambda: writer.execute('SELECT pg_blocking_pids(%s) p', (pid[0],)).fetchone()['p'])
                    writer.execute("INSERT INTO order_events(id,order_id,actor_id,event_type,details) "
                                   "VALUES('poll-send-gps',%s,'ops-driver','order.gps_near_customer','{}')", (oid,))
            except psycopg.errors.DeadlockDetected as exc:
                states.append(exc.sqlstate)
            finally:
                if future is not None:
                    _, state = _result(future)
                    if state:
                        states.append(state)
            assert states == (['40P01'] if BASELINE else [])
        with writer.transaction():
            writer.execute("INSERT INTO order_events(id,order_id,actor_id,event_type,details) "
                           "VALUES('poll-send-gps',%s,'ops-driver','order.gps_near_customer','{}') "
                           "ON CONFLICT(id) DO NOTHING", (oid,))
    # A deadlocked synthetic send may have rolled back. Replay the same key
    # only after the failed transaction fully exits; verify exact-once storage.
    sent = chat.driver_customer_send('poll-send-task', body, request, driver)
    assert chat.driver_customer_send('poll-send-task', body, request, driver) == sent
    with db.tx() as conn:
        assert conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone() == saved_order
        assert conn.execute("SELECT count(*) n FROM order_events WHERE id='poll-send-gps'").fetchone()['n'] == 1
        assert conn.execute("SELECT count(*) n FROM chat_messages WHERE text=%s", (body.text,)).fetchone()['n'] == 1
        assert conn.execute("SELECT status,driver_id FROM driver_tasks WHERE id='poll-send-task'").fetchone() == {
            'status': 'picked_up', 'driver_id': 'ops-driver'}


@pytest.mark.skipif(BASELINE, reason='Authorization acceptance executes on the fixed candidate')
@pytest.mark.parametrize('change', ['reassigned', 'closed_shift', 'branch_revoked'])
def test_chat_next_read_rechecks_committed_live_scope(ops, monkeypatch, change):
    _, _, order, shifts = ops
    _, sid = shifts()
    oid = order()
    with db.tx() as conn:
        conn.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by) "
                     "VALUES('poll-scope-task',%s,'ops-driver',%s,'accepted','ops-manager')", (oid, sid))
        driver = conn.execute("SELECT * FROM users WHERE id='ops-driver'").fetchone()
    assert chat.driver_customer_messages('poll-scope-task', 0, driver)['messages'] == []
    with db.tx() as conn:
        if change == 'reassigned':
            conn.execute("UPDATE driver_tasks SET driver_id='ops-driver2' WHERE id='poll-scope-task'")
        elif change == 'closed_shift':
            conn.execute("UPDATE shifts SET status='closing' WHERE id=%s", (sid,))
        else:
            conn.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-driver'")
        # An actual request obtains a fresh principal through current_user.
        driver = conn.execute("SELECT * FROM users WHERE id='ops-driver'").fetchone()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failure:
        chat.driver_customer_messages('poll-scope-task', 0, driver)
    assert failure.value.status_code in {403, 404, 409}
