"""The 2026-11-03 01:39 Riyadh destination/GPS lock cycle, isolated PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import itertools
import threading
import time

import psycopg
from psycopg.rows import dict_row
import pytest

from app import central_driver_overview, central_workdays, db, operations
from test_central_workdays import central, inspection, open_days
from test_enbishan_dual_brand import dual

pytestmark = pytest.mark.postgres


def observe_reader(monkeypatch):
    original = db.tx
    entered = threading.Event()
    pids = []

    @contextmanager
    def transaction(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            if threading.current_thread().name.startswith('destination-reader'):
                pids.append(conn.info.backend_pid)
                entered.set()
            yield conn

    monkeypatch.setattr(db, 'tx', transaction)
    return pids, entered


def wait_for_block(writer, pid):
    until = time.monotonic() + 4
    while time.monotonic() < until:
        if writer.execute('SELECT pg_blocking_pids(%s) p', (pid,)).fetchone()['p']:
            return
        time.sleep(.01)
    raise AssertionError('The writer authorization did not reach its real row-lock barrier')


def test_destination_poll_finishes_while_tracking_holds_both_shift_rows(central, database):
    _, _, _, _, _ = central
    _, day = open_days(central)
    ids = sorted(s['id'] for s in day['shifts'])
    with db.tx() as c:
        driver = c.execute("SELECT * FROM users WHERE id='dual-driver'").fetchone()
        originals = c.execute('SELECT * FROM shifts WHERE id=ANY(%s) ORDER BY id', (ids,)).fetchall()
    with psycopg.connect(database, autocommit=True, row_factory=dict_row) as writer:
        writer.execute("SET statement_timeout='6000ms'")
        with ThreadPoolExecutor(max_workers=1) as workers:
            with writer.transaction():
                writer.execute('SELECT id FROM shifts WHERE id=ANY(%s) ORDER BY id FOR UPDATE', (ids,)).fetchall()
                future = workers.submit(central_driver_overview.own_destination, driver)
                # Must complete before the tracking transaction releases rows;
                # a retry after COMMIT cannot satisfy this assertion.
                result = future.result(timeout=4)
                assert result['visible_active_task_count'] == 0
                assert {b['id'] for b in result['branches']} == {
                    'kps-branch-1', 'enbishan-test-1', 'enbishan-test-2'}
    with db.tx() as c:
        assert c.execute('SELECT * FROM shifts WHERE id=ANY(%s) ORDER BY id', (ids,)).fetchall() == originals


def test_workday_write_authorization_uses_same_shift_order_as_tracking(central, database, monkeypatch):
    _, _, post, _, _ = central
    manager = post('manager', '/api/central-operations/workdays', {'kind': 'manager'})
    counter = itertools.count()
    monkeypatch.setattr(operations, 'uid', lambda: f'lock-order-{next(counter):04d}')
    day = post('driver', '/api/central-operations/workdays', {
        'kind': 'driver', 'manager_shift_id': manager['shift']['id'],
        'vehicle_inspection': inspection()})
    ids = sorted(s['id'] for s in day['shifts'])
    by_id = {s['id']: s['brand_id'] for s in day['shifts']}
    # Home KPS opens first, but brand sorting would lock Enbishan first.
    assert [by_id[sid] for sid in ids] == ['kps', 'enbishan']
    with db.tx() as c:
        driver = c.execute("SELECT * FROM users WHERE id='dual-driver'").fetchone()
    pids, entered = observe_reader(monkeypatch)

    def authorize():
        with db.tx() as c:
            return central_workdays.authorized(c, driver, day['shift']['id'])

    with psycopg.connect(database, autocommit=True, row_factory=dict_row) as writer:
        writer.execute("SET statement_timeout='6000ms'")
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='destination-reader') as workers:
            with writer.transaction():
                writer.execute('SELECT id FROM shifts WHERE id=%s FOR UPDATE', (ids[0],))
                future = workers.submit(authorize)
                assert entered.wait(4)
                wait_for_block(writer, pids[0])
                # A blocked shared-workday writer must not have locked the
                # second leg first. NOWAIT proves this without a timing guess.
                writer.execute('SELECT id FROM shifts WHERE id=%s FOR UPDATE NOWAIT', (ids[1],))
            _, legs = future.result(timeout=4)
            assert {s['id'] for _, s in legs} == set(ids)
