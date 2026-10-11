"""Versioned, scope-safe business export and separate-schema PostgreSQL restore."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import time
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
from fastapi import APIRouter, Depends, Response
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from . import auth, db

router = APIRouter(prefix='/api/admin')
FORMAT = 'kps-portable-v2'
TABLES = ('brands', 'app_settings', 'branches', 'delivery_hours_decisions', 'branch_opening_hours_decisions', 'owner_branch_location_decisions', 'products', 'catalog_owner_approvals', 'owner_display_decisions', 'users', 'assets', 'banners',
          'shifts', 'orders', 'driver_tasks', 'collections', 'refunds', 'custody_transfers',
          'custody_events', 'retained_carryovers', 'manager_handovers', 'closure_reports',
          'report_corrections', 'conversations', 'chat_attachments',
          'chat_messages', 'conversation_reads', 'conversation_transfers', 'order_events',
          'audit_events', 'recovery_events', 'recovery_state', 'recovery_alerts',
          'idempotency_keys', 'sessions', 'otp_challenges', 'rate_limits',
          'identity_channels', 'identity_tokens', 'email_outbox', 'email_webhook_events',
          'performance_provinces', 'performance_province_branches', 'province_performance_policies',
          'closure_drafts', 'report_renderings', 'report_notifications',
          'recovery_monitor_state', 'recovery_alert_notifications', 'vehicle_reports', 'vehicle_report_events')
AUTH_TABLES = {'sessions', 'otp_challenges', 'rate_limits', 'idempotency_keys',
               'identity_tokens', 'email_outbox', 'email_webhook_events'}
INFRASTRUCTURE_TABLES = {'recovery_events', 'recovery_state', 'recovery_alerts',
                         'recovery_monitor_state', 'recovery_alert_notifications'}
PUBLIC = db.ROOT / 'public' / 'assets'
MAX_WEB_EXPORT = 64 * 1024 * 1024
INDEPENDENT_RESTORE_DATABASE_ID = 'dpg-db50dcdckfvc738gsrhg-a'


def _plain(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return {'$decimal': str(value)}
    raise TypeError(f'Unsupported export type: {type(value).__name__}')


def _encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=_plain).encode()


def _scoped_sql(table, brand):
    if brand is None:
        return 'TRUE', ()
    direct = {'branches', 'products', 'catalog_owner_approvals', 'owner_display_decisions', 'owner_branch_location_decisions', 'users', 'assets', 'banners', 'shifts', 'orders', 'closure_reports',
              'conversations', 'otp_challenges', 'identity_channels', 'identity_tokens', 'email_outbox', 'email_webhook_events',
              'performance_provinces', 'performance_province_branches', 'province_performance_policies',
              'report_notifications', 'vehicle_reports'} | INFRASTRUCTURE_TABLES
    if table == 'brands':
        return 'id=%s', (brand,)
    if table == 'app_settings' or table in direct:
        return 'brand_id=%s', (brand,)
    if table in {'delivery_hours_decisions','branch_opening_hours_decisions'}:
        return 'branch_id IN (SELECT id FROM branches WHERE brand_id=%s)', (brand,)
    if table in {'driver_tasks', 'collections', 'refunds', 'order_events'}:
        return 'order_id IN (SELECT id FROM orders WHERE brand_id=%s)', (brand,)
    if table in {'custody_transfers', 'retained_carryovers', 'manager_handovers'}:
        return 'from_shift_id IN (SELECT id FROM shifts WHERE brand_id=%s)', (brand,)
    if table == 'custody_events':
        return 'shift_id IN (SELECT id FROM shifts WHERE brand_id=%s)', (brand,)
    if table == 'vehicle_report_events':
        return 'report_id IN (SELECT id FROM vehicle_reports WHERE brand_id=%s)', (brand,)
    if table in {'report_corrections', 'report_renderings'}:
        return 'report_id IN (SELECT id FROM closure_reports WHERE brand_id=%s)', (brand,)
    if table == 'closure_drafts':
        return 'shift_id IN (SELECT id FROM shifts WHERE brand_id=%s)', (brand,)
    if table in {'chat_attachments', 'chat_messages', 'conversation_reads', 'conversation_transfers'}:
        return 'conversation_id IN (SELECT id FROM conversations WHERE brand_id=%s)', (brand,)
    if table == 'audit_events':
        return 'actor_id IN (SELECT id FROM users WHERE brand_id=%s)', (brand,)
    if table in {'sessions', 'idempotency_keys'}:
        return 'user_id IN (SELECT id FROM users WHERE brand_id=%s)', (brand,)
    raise ValueError('An unscoped table cannot enter a brand business export.')


def _totals(conn, brand_id):
    clause, values = _scoped_sql('orders', brand_id)
    order = conn.execute('SELECT count(*) orders,coalesce(sum(total_halalas),0)::bigint total_halalas FROM orders WHERE ' + clause, values).fetchone()
    col_clause, vals = _scoped_sql('collections', brand_id)
    ref_clause, ref_vals = _scoped_sql('refunds', brand_id)
    order['collected_halalas'] = conn.execute('SELECT coalesce(sum(amount_halalas),0)::bigint AS n FROM collections WHERE ' + col_clause, vals).fetchone()['n']
    order['refunded_halalas'] = conn.execute('SELECT coalesce(sum(amount_halalas),0)::bigint AS n FROM refunds WHERE ' + ref_clause, ref_vals).fetchone()['n']
    return order


def build_archive(conn, *, brand_id=None, full=False, max_bytes=None) -> tuple[bytes, dict]:
    """Caller supplies a read-only repeatable-read transaction for a consistent snapshot."""
    if not full and not brand_id:
        raise ValueError('Business export requires an explicit brand.')
    output = io.BytesIO()
    files, counts, size = {}, {}, 0
    available = {row['table_name'] for row in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE'").fetchall()}
    selected = [table for table in TABLES if table in available and (full or table not in AUTH_TABLES)]
    if available - set(TABLES) - {'schema_migrations'}:
        raise db.error(409, 'EXPORT_SCHEMA_UNSUPPORTED', 'أضيفت سجلات تشغيلية جديدة. يلزم تحديث صيغة التصدير قبل إخراج نسخة كاملة.')
    migrations = conn.execute('SELECT version,sha256 FROM schema_migrations ORDER BY version').fetchall()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        def add(name, content):
            nonlocal size
            size += len(content)
            if max_bytes and size > max_bytes:
                raise db.error(413, 'EXPORT_SIZE_LIMIT', 'حجم التصدير يتجاوز حد التنزيل المباشر. استخدم النسخة الاحتياطية الخاصة.')
            archive.writestr(name, content)
            files[name] = {'sha256': hashlib.sha256(content).hexdigest(), 'size_bytes': len(content)}

        for table in selected:
            clause, params = _scoped_sql(table, None if full else brand_id)
            ordering = ' ORDER BY kind DESC,opened_at,id' if table == 'shifts' else ''
            rows = conn.execute(sql.SQL('SELECT * FROM {} WHERE ' + clause + ordering).format(sql.Identifier(table)), params).fetchall()
            counts[table] = len(rows)
            for index, row in enumerate(rows):
                if table == 'users' and not full:
                    row.pop('password_hash', None)
                    row.pop('username', None)
                if table in {'report_notifications', 'recovery_alert_notifications'} and not full:
                    # A business export never conveys an authentication-mail capability.
                    row['email_outbox_id'] = None
                for column, value in list(row.items()):
                    if isinstance(value, (bytes, bytearray, memoryview)):
                        identity = str(row.get('id', index))
                        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identity):
                            identity = hashlib.sha256(identity.encode()).hexdigest()
                        name = f'blobs/{table}/{identity}/{column}.bin'
                        contents = bytes(value)
                        add(name, contents)
                        row[column] = {'$binary': name, 'sha256': files[name]['sha256'], 'size_bytes': len(contents)}
            add(f'data/{table}.json', _encode(rows))

        for path in sorted(PUBLIC.glob('*')) if PUBLIC.exists() else []:
            if path.is_file() and not path.is_symlink() and re.fullmatch(r'[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|webp|svg)', path.name):
                if full or brand_id == 'kps':
                    add('static/assets/' + path.name, path.read_bytes())
        manifest = {'format': FORMAT, 'mode': 'database' if full else 'business', 'created_at': datetime.now(timezone.utc).isoformat(),
                    'source_revision': os.getenv('RUNTIME_REVISION') or os.getenv('RENDER_GIT_COMMIT') or 'local-unversioned',
                    'scope': {'brand_id': None if full else brand_id}, 'migrations': migrations,
                    'table_order': selected, 'counts': counts, 'totals': _totals(conn, None if full else brand_id),
                    'files': files, 'timezone': 'Asia/Riyadh', 'currency': 'SAR',
                    'source_identifier_fields': {'branches': ['id', 'branch_no', 'source_id'],
                                                 'products': ['id', 'settings.source_id'], 'orders': ['id', 'snapshot']},
                    'deduplication': 'Preserve primary/source IDs; verify counts/totals before import. Never replay sales or collections.',
                    'credentials_included': bool(full),
                    'excluded_tables': [] if full else sorted(AUTH_TABLES),
                    'infrastructure_tables': [table for table in selected if table in INFRASTRUCTURE_TABLES],
                    'infrastructure_import_policy': 'Preserve observations and failure queues as historical source records; do not activate source hosting configuration or replay alerts.'}
        archive.writestr('manifest.json', _encode(manifest))
    return output.getvalue(), manifest


def verify_archive(contents: bytes, *, require_full=False):
    try:
        archive = zipfile.ZipFile(io.BytesIO(contents))
        names = archive.namelist()
        if len(set(names)) != len(names) or 'manifest.json' not in names:
            raise ValueError('Duplicate or missing archive records.')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('format') != FORMAT or manifest.get('mode') not in {'business', 'database'}:
            raise ValueError('Unsupported portable format.')
        if require_full and manifest['mode'] != 'database':
            raise ValueError('Business exports cannot restore authentication or the complete database.')
        if set(names) != set(manifest.get('files', {})) | {'manifest.json'}:
            raise ValueError('Unexpected or missing archive file.')
        for name, descriptor in manifest['files'].items():
            if name.startswith('/') or '..' in name.split('/') or '\\' in name:
                raise ValueError('Unsafe archive path.')
            member = archive.getinfo(name)
            if member.file_size != descriptor['size_bytes'] or member.file_size > 100 * 1024 * 1024:
                raise ValueError('Archive size mismatch.')
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != descriptor['sha256']:
                raise ValueError('Archive checksum mismatch.')
        if manifest['table_order'] != [table for table in TABLES if table in manifest['table_order']]:
            raise ValueError('Unknown or incorrectly ordered export tables.')
        for table in manifest['table_order']:
            if len(json.loads(archive.read(f'data/{table}.json'))) != manifest['counts'][table]:
                raise ValueError('Export row count mismatch.')
        return archive, manifest
    except (zipfile.BadZipFile, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError('Invalid portable archive.') from None


def _restore_target(target_url, target_database_id=None):
    try:
        connection = conninfo_to_dict(target_url)
    except psycopg.Error:
        raise ValueError('Invalid private recovery connection configuration.') from None
    name, host = connection.get('dbname', ''), connection.get('host', '')
    local = host in {'localhost', '127.0.0.1', '::1', 'postgres'} and (name.startswith('test_') or name.endswith('_test'))
    # Explicit opt-in to the existing recovery resource's INTERNAL hostname.
    # An arbitrary remote host, public Render URL or runtime DB is rejected.
    internal = target_database_id == INDEPENDENT_RESTORE_DATABASE_ID and host == INDEPENDENT_RESTORE_DATABASE_ID
    if not local and not internal:
        raise ValueError('Restore requires a local test database or the explicitly bound INTERNAL recovery database.')
    if connection.get('options'):
        raise ValueError('Restore URL options are prohibited; tool owns the new schema.')
    return connection


def _restored_evidence(conn, archive, manifest):
    """Read the inserted target records, then compare IDs and every stored blob."""
    identity_digests, binary_count = {}, 0
    for table in manifest['table_order']:
        source = json.loads(archive.read(f'data/{table}.json'))
        target = conn.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table))).fetchall()
        keys = [row['column_name'] for row in conn.execute(
            "SELECT a.attname column_name FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
            "WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY a.attname", (table,)).fetchall()]
        if not keys:
            raise ValueError('Target table lacks a primary key.')
        identity = lambda row: _encode({key: row[key] for key in keys})
        source_ids, target_ids = sorted(identity(row) for row in source), sorted(identity(row) for row in target)
        if source_ids != target_ids:
            raise ValueError('Restored primary identifiers differ from snapshot.')
        identity_digests[table] = hashlib.sha256(b'\n'.join(target_ids)).hexdigest()
        indexed = {identity(row): row for row in target}
        for row in source:
            actual = indexed[identity(row)]
            for column, value in row.items():
                if isinstance(value, dict) and '$binary' in value:
                    restored = bytes(actual[column])
                    if len(restored) != value['size_bytes'] or hashlib.sha256(restored).hexdigest() != value['sha256']:
                        raise ValueError('Restored binary checksum mismatch.')
                    binary_count += 1
    return identity_digests, binary_count


def restore_archive(contents: bytes, *, target_url: str, target_schema: str,
                    target_database_id: str | None = None, asset_directory: str | Path | None = None) -> dict:
    """Restore to a NEW isolated test schema; no existing schema is overwritten.

    Remote operation is limited to the pre-existing recovery resource over its
    private internal hostname. Static files require an explicitly new directory.
    """
    started = time.monotonic()
    archive, manifest = verify_archive(contents, require_full=True)
    if not re.fullmatch(r'kps_restore_[a-f0-9]{32}', target_schema):
        raise ValueError('Restore schema must be kps_restore_ plus a new UUID hex value.')
    _restore_target(target_url, target_database_id)
    expected = [{'version': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in sorted((db.ROOT / 'migrations').glob('*.sql'))]
    if manifest['migrations'] != expected:
        raise ValueError('Archive migrations do not match this code. Restore with matching code first.')
    destination, files_restored = Path(asset_directory) if asset_directory is not None else None, 0
    if destination and (destination.exists() or destination.is_symlink()):
        raise ValueError('Static asset restoration refuses existing destinations.')
    with psycopg.connect(target_url, row_factory=dict_row, connect_timeout=10) as conn:
        with conn.transaction():
            if conn.execute('SELECT 1 FROM information_schema.schemata WHERE schema_name=%s', (target_schema,)).fetchone():
                raise ValueError('Restore refuses existing schemas. Choose a new UUID.')
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(target_schema)))
            conn.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(target_schema)))
            db.apply_migrations(conn, seed=False, owner_decisions=False)
            actual = {row['table_name'] for row in conn.execute(
                'SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type=%s',
                (target_schema, 'BASE TABLE')).fetchall()} - {'schema_migrations'}
            if set(manifest['table_order']) != actual:
                raise ValueError('Backup is missing application tables; restoration aborted.')
            for table in manifest['table_order']:
                columns = {row['column_name']: row['udt_name'] for row in conn.execute(
                    'SELECT column_name,udt_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s',
                    (target_schema, table)).fetchall()}
                for row in json.loads(archive.read(f'data/{table}.json')):
                    if set(row) != set(columns):
                        raise ValueError('Backup columns do not match target.')
                    values = []
                    for column, value in row.items():
                        if columns[column] == 'bytea' and value is not None:
                            if not isinstance(value, dict) or set(value) != {'$binary', 'sha256', 'size_bytes'}:
                                raise ValueError('Invalid binary reference.')
                            payload = archive.read(value['$binary'])
                            if len(payload) != value['size_bytes'] or hashlib.sha256(payload).hexdigest() != value['sha256']:
                                raise ValueError('Attachment checksum mismatch.')
                            values.append(payload)
                        elif columns[column] in {'json', 'jsonb'}:
                            values.append(db.json(value))
                        elif isinstance(value, dict) and '$decimal' in value:
                            values.append(Decimal(value['$decimal']))
                        else:
                            values.append(value)
                    statement = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
                        sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, row)),
                        sql.SQL(',').join(sql.Placeholder() for _ in row))
                    conn.execute(statement, values)
            if 'chat_messages' in actual:
                sequence = conn.execute("SELECT pg_get_serial_sequence('chat_messages','seq') AS name").fetchone()['name']
                if sequence:
                    conn.execute("SELECT setval(%s::regclass,greatest(coalesce((SELECT max(seq) FROM chat_messages),0),1),EXISTS(SELECT 1 FROM chat_messages))", (sequence,))
            if 'orders' in actual:
                conn.execute("SELECT setval('order_human_number_seq',greatest(coalesce((SELECT max(order_no) FROM orders),0),1),EXISTS(SELECT 1 FROM orders))")
            if 'shifts' in actual:
                sequence = conn.execute("SELECT pg_get_serial_sequence('shifts','shift_no') AS name").fetchone()['name']
                if sequence:
                    conn.execute("SELECT setval(%s::regclass,greatest(coalesce((SELECT max(shift_no) FROM shifts),0),1),EXISTS(SELECT 1 FROM shifts))", (sequence,))
            counts = {table: conn.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table))).fetchone()['n']
                      for table in manifest['table_order']}
            if counts != manifest['counts'] or _totals(conn, None) != manifest['totals']:
                raise ValueError('Restored counts or monetary totals differ from snapshot.')
            identities, binary_count = _restored_evidence(conn, archive, manifest)
            if destination:
                # Creation is exclusive. Failure removes only the directory
                # created by this invocation and rolls back the target schema.
                destination.mkdir(mode=0o700)
                try:
                    for name, descriptor in manifest['files'].items():
                        if not name.startswith('static/assets/'):
                            continue
                        basename = name.removeprefix('static/assets/')
                        if not re.fullmatch(r'[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|webp|svg)', basename):
                            raise ValueError('Unexpected static asset path.')
                        path = destination / basename
                        descriptor_fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                        with os.fdopen(descriptor_fd, 'wb') as output:
                            output.write(archive.read(name))
                        if hashlib.sha256(path.read_bytes()).hexdigest() != descriptor['sha256']:
                            raise ValueError('Restored static asset checksum mismatch.')
                        files_restored += 1
                except Exception:
                    shutil.rmtree(destination)
                    raise
    return {'target_schema': target_schema, 'counts': counts, 'totals': manifest['totals'], 'verified': True,
            'snapshot_created_at': manifest['created_at'], 'source_revision': manifest['source_revision'],
            'identifier_sha256': identities, 'binary_files_verified': binary_count,
            'static_files_archived': sum(name.startswith('static/assets/') for name in manifest['files']),
            'static_files_restored': files_restored, 'static_restore_verified': destination is not None,
            'archive_sha256': hashlib.sha256(contents).hexdigest(), 'elapsed_seconds': round(time.monotonic() - started, 3)}


@router.get('/export')
def business_export(user=Depends(auth.require_roles('owner'))):
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        contents, manifest = build_archive(conn, brand_id=user['brand_id'], max_bytes=MAX_WEB_EXPORT)
    with db.tx() as conn:
        db.audit(conn, user['id'], 'business.export.generated', 'brand', user['brand_id'],
                 {'format': FORMAT, 'counts': manifest['counts'], 'sha256': hashlib.sha256(contents).hexdigest()})
    filename = 'kps-business-export-v2-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.zip'
    return Response(contents, media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'no-store'})
