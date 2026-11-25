"""Isolated PostgreSQL witness; never connects to the fixed live target."""
import pytest
from app import db
from tools import production_release_preflight as preflight


@pytest.mark.postgres
def test_private_preflight_reads_real_schema_without_mutation(database, monkeypatch):
    with db.tx() as conn:
        actual = conn.execute('SELECT current_database() AS name,current_schema() AS schema').fetchone()
        before = conn.execute('SELECT version,sha256,applied_at FROM schema_migrations ORDER BY version').fetchall()
    # Only the test process substitutes an isolated fixture identity. CLI has no
    # argument/environment setting to alter its fixed production DB/schema.
    monkeypatch.setattr(preflight, 'DATABASE_NAME', actual['name'])
    monkeypatch.setattr(preflight, 'DATABASE_SCHEMA', actual['schema'])
    monkeypatch.setenv('RUNTIME_REVISION', 'a' * 40)
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout='2s'")
        conn.execute("SET LOCAL lock_timeout='250ms'")
        conn.execute("SET LOCAL idle_in_transaction_session_timeout='5s'")
        conn.execute("SET LOCAL transaction_timeout='15s'")
        report = preflight.collect(conn, preflight.migration_catalog(), '0500001234')
        assert conn.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] == 'on'
        assert report['registry']['compatible_applied_checksums'] is True
        assert report['staff_readback']['phone_last4'] == '1234'
        assert report['staff_readback']['accounts'] == []
        assert report['table_sizes']
        assert report['lock_counts']
        assert not report['release_approved']
        assert not report['backup_verified']
        assert not report['restore_verified']
        assert not report['production_migration_permitted']
        assert conn.execute('SELECT version,sha256,applied_at FROM schema_migrations ORDER BY version').fetchall() == before
