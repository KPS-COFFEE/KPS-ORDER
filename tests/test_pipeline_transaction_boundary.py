"""Real PostgreSQL proof of batched COMMIT, rollback and pool reuse."""
import os
from uuid import uuid4

import psycopg
import pytest

from app import db

pytestmark = pytest.mark.postgres


def test_transaction_is_acknowledged_before_the_caller_can_write(database):
    with db.tx(pipeline=True) as conn:
        assert conn.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS
        assert conn.info.pipeline_status == psycopg.pq.PipelineStatus.ON
        assert conn.execute('SELECT 1 AS n').fetchone()['n'] == 1


def test_no_pipeline_sync_between_last_business_write_and_commit(database, monkeypatch, tmp_path):
    trace_path = tmp_path / 'synthetic-only-protocol.trace'
    identifier = 'TEST-batched-' + uuid4().hex
    execute = psycopg.Connection.execute
    exit_connection = psycopg.Connection.__exit__
    traced = []
    with trace_path.open('wb') as trace:
        def observed(conn, query, *args, **kwargs):
            if query == 'BEGIN' and not traced:
                conn.pgconn.trace(trace.fileno())
                traced.append(conn)
            return execute(conn, query, *args, **kwargs)

        def finished(conn, *args):
            if conn in traced:
                # Observe before returning ownership to the pool/reset worker.
                assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
                conn.pgconn.untrace()
            return exit_connection(conn, *args)

        monkeypatch.setattr(psycopg.Connection, 'execute', observed)
        monkeypatch.setattr(psycopg.Connection, '__exit__', finished)
        with db.tx(pipeline=True) as conn:
            # A prior fetch must not interfere with batching the final write.
            assert conn.execute('SELECT 1 AS n').fetchone()['n'] == 1
            conn.execute("INSERT INTO audit_events(id,action,entity_type,entity_id,details) "
                         "VALUES(%s,'TEST pipeline','TEST','TEST','{}')", (identifier,))
    lines = trace_path.read_text().splitlines()
    write = next(i for i, line in enumerate(lines) if 'Parse' in line and 'INSERT INTO audit_events' in line)
    commit = next(i for i, line in enumerate(lines[write + 1:], write + 1) if 'Parse' in line and '"COMMIT"' in line)
    assert not any('\tSync' in line or '\tReadyForQuery' in line for line in lines[write:commit])
    assert any('CommandComplete' in line and '"COMMIT"' in line for line in lines[commit:])
    # Independent session confirms durability, not just the HTTP/driver result.
    with psycopg.connect(os.environ['DATABASE_URL']) as control:
        assert control.execute('SELECT count(*) FROM audit_events WHERE id=%s', (identifier,)).fetchone()[0] == 1


@pytest.mark.parametrize('deferred', [False, True])
def test_sql_failure_rolls_back_before_connection_reuse(database, monkeypatch, deferred):
    url = os.environ['DATABASE_URL']
    db.close_pools(url)
    monkeypatch.setattr(db, 'POOL_MIN_SIZE', 1)
    monkeypatch.setattr(db, 'POOL_MAX_SIZE', 1)
    identifier = 'TEST-failed-' + uuid4().hex
    with pytest.raises(psycopg.errors.DivisionByZero):
        with db.tx(pipeline=True) as conn:
            pid = conn.execute('SELECT pg_backend_pid() pid').fetchone()['pid']
            conn.execute("INSERT INTO audit_events(id,action,entity_type,entity_id,details) "
                         "VALUES(%s,'TEST pipeline','TEST','TEST','{}')", (identifier,))
            failed = conn.execute('SELECT 1/0')
            if not deferred:
                failed.fetchone()
    with db.tx(pipeline=True) as reused:
        assert reused.execute('SELECT pg_backend_pid() pid').fetchone()['pid'] == pid
        assert reused.execute('SELECT count(*) n FROM audit_events WHERE id=%s', (identifier,)).fetchone()['n'] == 0
        assert reused.execute("SHOW statement_timeout").fetchone()['statement_timeout'] == '15s'


def test_deferred_commit_constraint_failure_never_looks_successful(database):
    with db.tx() as conn:
        conn.execute('CREATE TABLE test_pipeline_deferred (id int UNIQUE DEFERRABLE INITIALLY DEFERRED)')
    with pytest.raises(psycopg.errors.UniqueViolation):
        with db.tx(pipeline=True) as conn:
            conn.execute('INSERT INTO test_pipeline_deferred VALUES (1),(1)')
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM test_pipeline_deferred').fetchone()['n'] == 0


def test_caught_sql_error_cannot_return_a_false_success(database):
    with pytest.raises(psycopg.OperationalError, match='not committed'):
        with db.tx(pipeline=True) as conn:
            with pytest.raises(psycopg.errors.DivisionByZero):
                # Complete the failed pipeline segment but leave the explicit
                # transaction failed. PostgreSQL replies ROLLBACK to COMMIT.
                with conn.pipeline():
                    conn.execute('SELECT 1/0')


def test_nested_savepoint_failure_preserves_outer_transaction(database):
    with db.tx() as conn:
        conn.execute('CREATE TABLE test_pipeline_nested (id int PRIMARY KEY)')
    with db.tx(pipeline=True) as conn:
        conn.execute('INSERT INTO test_pipeline_nested VALUES (1)')
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.transaction():
                conn.execute('INSERT INTO test_pipeline_nested VALUES (2),(1)')
        conn.execute('INSERT INTO test_pipeline_nested VALUES (3)')
    with db.tx() as conn:
        assert conn.execute('SELECT id FROM test_pipeline_nested ORDER BY id').fetchall() == [{'id': 1}, {'id': 3}]
