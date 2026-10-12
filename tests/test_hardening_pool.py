"""Real PostgreSQL reuse, state isolation, rollback and bounded capacity."""
import multiprocessing
import os
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from app import db

pytestmark = pytest.mark.postgres


def single_connection(monkeypatch):
    url = os.environ['DATABASE_URL']
    db.close_pools(url)
    monkeypatch.setattr(db, 'POOL_MIN_SIZE', 1)
    monkeypatch.setattr(db, 'POOL_MAX_SIZE', 1)
    return url


def test_connections_are_reused_and_session_state_is_removed(database, monkeypatch):
    single_connection(monkeypatch)
    with db.tx() as conn:
        before = conn.execute('SELECT pg_backend_pid() pid,current_schema() schema').fetchone()
        conn.execute("SET search_path='public'")
        conn.execute("SET application_name='TEST polluted session'")
        conn.execute("SET statement_timeout='123ms'")
        conn.execute('CREATE TEMP TABLE hardening_pool_leak(value int)')
        conn.execute('SELECT pg_advisory_lock(819264731)')
    pids = []
    for _ in range(6):
        with db.tx() as conn:
            state = conn.execute("SELECT pg_backend_pid() pid,current_schema() schema,current_setting('application_name') name,"
                "current_setting('statement_timeout') timeout,to_regclass('pg_temp.hardening_pool_leak') temp_table,"
                "(SELECT count(*) FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory') locks").fetchone()
            assert state['schema'] == before['schema']
            assert state['name'].startswith(f'kps_order_pool_{os.getpid()}_')
            assert state['timeout'] == '15s' and state['temp_table'] is None and state['locks'] == 0
            pids.append(state['pid'])
    assert set(pids) == {before['pid']}


def test_exception_rolls_back_before_reuse_and_normal_exit_commits(database, monkeypatch):
    single_connection(monkeypatch)
    identifier = 'pool-test-' + uuid4().hex
    with pytest.raises(RuntimeError, match='TEST rollback'):
        with db.tx() as conn:
            conn.execute("INSERT INTO audit_events(id,action,entity_type,entity_id,details) VALUES(%s,'TEST pool','TEST','TEST','{}')", (identifier,))
            raise RuntimeError('TEST rollback')
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM audit_events WHERE id=%s', (identifier,)).fetchone()['n'] == 0
        conn.execute("INSERT INTO audit_events(id,action,entity_type,entity_id,details) VALUES(%s,'TEST pool','TEST','TEST','{}')", (identifier,))
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM audit_events WHERE id=%s', (identifier,)).fetchone()['n'] == 1


def test_full_url_schema_options_keep_separate_pools(database, monkeypatch):
    original = os.environ['DATABASE_URL']
    schema = 'kps_test_pool_' + uuid4().hex
    parsed = urlsplit(original)
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key not in {'options', 'sslmode'}]
    query += [('options', '-csearch_path=' + schema), ('sslmode', 'prefer')]
    other = urlunsplit(parsed._replace(query=urlencode(query)))
    with db.tx() as conn:
        expected = conn.execute('SELECT current_schema() schema').fetchone()['schema']
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    first_pool = db._pool_for_url(original)
    try:
        monkeypatch.setenv('DATABASE_URL', other)
        second_pool = db._pool_for_url(other)
        assert second_pool is not first_pool and second_pool.conninfo == other
        with db.tx() as conn:
            assert conn.execute('SELECT current_schema() schema').fetchone()['schema'] == schema
            assert conn.info.get_parameters()['sslmode'] == 'prefer'
        monkeypatch.setenv('DATABASE_URL', original)
        with db.tx() as conn:
            assert conn.execute('SELECT current_schema() schema').fetchone()['schema'] == expected
        assert db._pool_for_url(original) is first_pool
    finally:
        monkeypatch.setenv('DATABASE_URL', original)
        db.close_pools(other)
        with db.tx() as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_dead_idle_backend_is_discarded_and_replaced(database, monkeypatch):
    url = single_connection(monkeypatch)
    with db.tx() as conn:
        old_pid = conn.execute('SELECT pg_backend_pid() pid').fetchone()['pid']
    with psycopg.connect(url, autocommit=True) as control:
        assert control.execute('SELECT pg_terminate_backend(%s)', (old_pid,)).fetchone()[0]
    with db.tx() as conn:
        assert conn.execute('SELECT pg_backend_pid() pid').fetchone()['pid'] != old_pid
        assert conn.execute('SELECT 17 n').fetchone()['n'] == 17


def test_real_pool_capacity_is_bounded_and_http_failure_recovers_without_partial_writes(client):
    url = os.environ['DATABASE_URL']
    pool = db._pool_for_url(url)
    with db.tx() as conn:
        before = conn.execute('SELECT count(*) n FROM users').fetchone()['n']
    holders = []
    try:
        for _ in range(db.POOL_MAX_SIZE):
            holders.append(pool.getconn(timeout=5))
        assert len({conn.info.backend_pid for conn in holders}) == db.POOL_MAX_SIZE == 20
        assert pool.get_stats()['pool_size'] == 20
        started = time.monotonic()
        ready = client.get('/ready')
        assert ready.status_code == 503 and ready.json()['status'] == 'not_ready'
        assert time.monotonic() - started < 9
        failed = client.post('/api/identity/guest', json={
            'brand_id': 'kps', 'display_name': 'TEST capacity guest', 'phone': '0509876543'})
        assert failed.status_code == 503
        assert failed.json()['detail']['code'] == 'DATABASE_UNAVAILABLE'
        assert client.get('/health').status_code == 200
    finally:
        for conn in holders:
            pool.putconn(conn)
    assert client.get('/ready').status_code == 200
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM users').fetchone()['n'] == before


def _forked_query(queue, url):
    os.environ['DATABASE_URL'] = url
    try:
        with db.tx() as conn:
            queue.put(conn.execute('SELECT pg_backend_pid() pid,current_schema() schema').fetchone())
    finally:
        db.close_pools()


@pytest.mark.skipif(not hasattr(os, 'register_at_fork'), reason='Runtime has no fork support.')
def test_fork_does_not_reuse_sockets_or_inherit_a_locked_registry(database, monkeypatch):
    url = single_connection(monkeypatch)
    with db.tx() as conn:
        parent = conn.execute('SELECT pg_backend_pid() pid,current_schema() schema').fetchone()
    context = multiprocessing.get_context('fork')
    queue = context.Queue()
    child = context.Process(target=_forked_query, args=(queue, url))
    try:
        with db._pools_lock:
            child.start()
        result = queue.get(timeout=15)
        child.join(timeout=10)
        assert child.exitcode == 0
        assert result['pid'] != parent['pid'] and result['schema'] == parent['schema']
        with db.tx() as conn:
            assert conn.execute('SELECT pg_backend_pid() pid').fetchone()['pid'] == parent['pid']
    finally:
        if child.is_alive():
            child.terminate()
            child.join(timeout=5)
        queue.close()
