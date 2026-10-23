"""Owner-authorized PITR-copy backup/restore/migration drill; never production.

Only the named disposable Render copy is a remote input. PostgreSQL restore
and candidate migrations run on a private Unix-socket server in this worker.
No application server, mail, SMS, payment or polling worker is started.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit, unquote, urlencode

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

CLONE_HOST = 'dpg-dbdpcvvavr4c738u12ag-a'
CLONE_NAME = 'kps_order_emergency_v4_db_5tkz'
HISTORICAL = {'030_menu_preview.sql': '34a7e4f5c08094a32faf2f2d09b10a79530b352ecc1ff78a88c9ab60fc04ce2e'}
ROOT = Path('/check')


def snapshot(c, baseline=None):
    names = list(baseline) if baseline is not None else [r['table_name'] for r in c.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name").fetchall()]
    result = {}
    for name in names:
        columns = baseline[name]['columns'] if baseline is not None else [r['column_name'] for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (name,)).fetchall()]
        query = sql.SQL("SELECT count(*)::bigint AS count,md5(coalesce(string_agg(h,'' ORDER BY h),'')) AS digest FROM (SELECT md5(row_to_json(r)::text) AS h FROM (SELECT {} FROM {}) r) fingerprints").format(
            sql.SQL(',').join(map(sql.Identifier, columns)), sql.Identifier(name))
        result[name] = dict(c.execute(query).fetchone(), columns=columns)
    return result


def run(args, env=None):
    completed = subprocess.run(args, env=env, capture_output=True, timeout=300)
    if completed.returncode:
        raise RuntimeError('operator_tool_failed:' + Path(args[0]).name)


def main():
    parsed = urlsplit(os.environ['KPS_RECOVERY_DATABASE_URL'])
    assert parsed.hostname == CLONE_HOST and parsed.path == '/' + CLONE_NAME
    assert not parsed.query and not parsed.fragment and parsed.port in (None, 5432)
    assert parsed.username and parsed.password
    env = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
    env.update(PGHOST=CLONE_HOST, PGPORT='5432', PGDATABASE=CLONE_NAME,
               PGUSER=unquote(parsed.username), PGPASSWORD=unquote(parsed.password),
               PGOPTIONS='-c default_transaction_read_only=on', PGCONNECT_TIMEOUT='5')
    with psycopg.connect(os.environ['KPS_RECOVERY_DATABASE_URL'], row_factory=dict_row,
                         options='-c default_transaction_read_only=on', connect_timeout=5) as source:
        source.execute("SET LOCAL statement_timeout='30s'")
        assert source.execute('SELECT current_database() AS name').fetchone()['name'] == CLONE_NAME
        assert source.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] == 'on'
        registry = source.execute('SELECT version,sha256 FROM schema_migrations ORDER BY version').fetchall()
        catalog = {p.name: hashlib.sha256(p.read_text().encode()).hexdigest()
                   for p in (ROOT / 'migrations').glob('*.sql')}
        catalog.update(HISTORICAL)
        assert all(catalog.get(r['version']) == r['sha256'] for r in registry), 'registry compatibility failed'
        historical_tables = {'menu_sessions', 'menu_events', 'menu_handoffs', 'menu_order_attribution', 'menu_link_imports'}
        before = snapshot(source)
        assert historical_tables.issubset(before), 'historical schema missing'
    print('REGISTRY_VERIFIED ' + json.dumps({'applied': len(registry), 'historical_source_sha': 'e109108', 'historical_sha256': HISTORICAL['030_menu_preview.sql'], 'tables': len(before)}), flush=True)
    folder = Path(tempfile.mkdtemp(prefix='kps-private-restore-'))
    archive = folder / 'pitr-copy.dump'
    started = time.monotonic()
    run(['pg_dump', '--format=custom', '--file', str(archive)], env)
    os.chmod(archive, 0o600)
    digest = hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest()
    run(['pg_restore', '--list', str(archive)])
    print('ARCHIVE_VERIFIED ' + json.dumps({'bytes': archive.stat().st_size, 'sha256': digest, 'seconds': round(time.monotonic()-started, 3), 'durable_off_worker_copy_verified': False}), flush=True)
    pg_bin = Path('/usr/lib/postgresql/18/bin')
    socket_dir = folder / 'socket'
    socket_dir.mkdir(mode=0o700)
    pgdata = folder / 'pgdata'
    run([str(pg_bin/'initdb'), '-D', str(pgdata), '--username=kpscheck', '--no-locale', '--encoding=UTF8', '--auth-local=trust', '--auth-host=reject'])
    run([str(pg_bin/'pg_ctl'), '-D', str(pgdata), '-l', str(folder/'postgres.log'), '-o', f"-k {socket_dir} -h '' -p 55432 -c shared_buffers=16MB -c max_connections=10 -c work_mem=2MB -c maintenance_work_mem=32MB", '-w', 'start'])
    try:
        local_env = {key: value for key, value in os.environ.items() if not key.startswith('PG') and key != 'KPS_RECOVERY_DATABASE_URL'}
        local_env.update(PGHOST=str(socket_dir), PGPORT='55432', PGUSER='kpscheck', PGDATABASE='postgres', PGCONNECT_TIMEOUT='5')
        with psycopg.connect(host=str(socket_dir), port=55432, user='kpscheck', dbname='postgres', autocommit=True) as control:
            control.execute('CREATE DATABASE test_enbishan_recovery')
        local_env['PGDATABASE'] = 'test_enbishan_recovery'
        run(['pg_restore', '--no-owner', '--no-privileges', '--exit-on-error', '--dbname', 'test_enbishan_recovery', str(archive)], local_env)
        with psycopg.connect(host=str(socket_dir), port=55432, user='kpscheck', dbname='test_enbishan_recovery', row_factory=dict_row) as restored:
            restored.execute("SET LOCAL statement_timeout='60s'")
            assert snapshot(restored, before) == before, 'restored data mismatch'
        print('RESTORE_RECONCILIATION_PASS all existing tables and columns matched', flush=True)
        local_url = 'postgresql:///test_enbishan_recovery?' + urlencode({'host': str(socket_dir), 'port': '55432', 'user': 'kpscheck'})
        revision = 'b48a1710f0085e044e23c235b99bb9163f9e7de8'
        os.environ.update(DATABASE_URL=local_url, APP_ENV='staging', INITIAL_CATALOG_IMPORT='false',
            KPS_MULTIBRAND_ENABLED='true', KPS_MULTIBRAND_ACCEPTANCE_SHA=revision,
            KPS_MULTIBRAND_OWNER_APPROVAL_SHA=revision, RUNTIME_REVISION=revision,
            KPS_MULTIBRAND_DATABASE_HOST=str(socket_dir), KPS_MULTIBRAND_DATABASE_NAME='test_enbishan_recovery',
            EMAIL_PROVIDER='disabled', OTP_PROVIDER='disabled', EMAIL_WORKER_ENABLED='false',
            REPORT_WORKER_ENABLED='false', RECOVERY_POLL_ENABLED='false')
        os.environ.pop('ENBISHAN_ISOLATED_ENABLED', None)
        from app import db
        started = time.monotonic()
        with psycopg.connect(local_url, row_factory=dict_row) as migrated:
            migrated.execute("SET LOCAL statement_timeout='60s'")
            migrated.execute("SET LOCAL lock_timeout='2s'")
            db.apply_migrations(migrated, seed=False, owner_decisions=False)
            unchanged = {k: v for k, v in before.items() if k != 'schema_migrations'}
            assert snapshot(migrated, unchanged) == unchanged, 'migration changed existing data'
        seconds = round(time.monotonic()-started, 3)
        with psycopg.connect(local_url, row_factory=dict_row) as repeated:
            repeated.execute("SET LOCAL statement_timeout='60s'")
            db.apply_migrations(repeated, seed=False, owner_decisions=False)
            assert snapshot(repeated, unchanged) == unchanged, 'repeat migration changed existing data'
            applied = repeated.execute('SELECT count(*) AS n FROM schema_migrations').fetchone()['n']
        print('SCHEMA_MIGRATION_DRILL_PASS ' + json.dumps({'seconds': seconds, 'applied_after': applied, 'preserved_tables': len(unchanged), 'repeat_pass': True, 'production_migration_approved': False, 'owner_decisions_and_seed_tested': False, 'production_contention_tested': False}), flush=True)
    finally:
        run([str(pg_bin/'pg_ctl'), '-D', str(pgdata), '-m', 'fast', '-w', 'stop'])
    print('PRIVATE_DRILL_COMPLETE archive retained in private ephemeral worker directory', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('PRIVATE_DRILL_FAILED ' + type(error).__name__, flush=True)
        raise SystemExit(1)
