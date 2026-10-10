"""Versioned, scope-safe business export and separate-schema PostgreSQL restore."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg
from fastapi import APIRouter, Depends, Response
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from . import auth, db

router = APIRouter(prefix='/api/admin')
FORMAT = 'kps-portable-v1'
TABLES = ('brands', 'app_settings', 'branches', 'products', 'users', 'assets', 'banners',
          'shifts', 'orders', 'driver_tasks', 'collections', 'refunds', 'custody_transfers',
          'custody_events', 'retained_carryovers', 'manager_handovers', 'closure_reports',
          'report_corrections', 'conversations', 'chat_attachments',
          'chat_messages', 'conversation_reads', 'conversation_transfers', 'order_events',
          'audit_events', 'recovery_events', 'recovery_state', 'recovery_alerts',
          'idempotency_keys', 'sessions', 'otp_challenges', 'rate_limits')
AUTH_TABLES = {'sessions', 'otp_challenges', 'rate_limits', 'idempotency_keys'}
INFRASTRUCTURE_TABLES = {'recovery_events', 'recovery_state', 'recovery_alerts'}
PUBLIC = db.ROOT / 'public' / 'assets'
MAX_WEB_EXPORT = 64 * 1024 * 1024


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
    direct = {'branches', 'products', 'users', 'assets', 'banners', 'shifts', 'orders', 'closure_reports',
              'conversations', 'otp_challenges'} | INFRASTRUCTURE_TABLES
    if table == 'brands':
        return 'id=%s', (brand,)
    if table == 'app_settings' or table in direct:
        return 'brand_id=%s', (brand,)
    if table in {'driver_tasks', 'collections', 'refunds', 'order_events'}:
        return 'order_id IN (SELECT id FROM orders WHERE brand_id=%s)', (brand,)
    if table in {'custody_transfers', 'retained_carryovers', 'manager_handovers'}:
        return 'from_shift_id IN (SELECT id FROM shifts WHERE brand_id=%s)', (brand,)
    if table == 'custody_events':
        return 'shift_id IN (SELECT id FROM shifts WHERE brand_id=%s)', (brand,)
    if table == 'report_corrections':
        return 'report_id IN (SELECT id FROM closure_reports WHERE brand_id=%s)', (brand,)
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


def restore_archive(contents: bytes, *, target_url: str, target_schema: str) -> dict:
    """Restore only to a NEW isolated local test schema, never over source or production."""
    archive, manifest = verify_archive(contents, require_full=True)
    if not re.fullmatch(r'kps_restore_[a-f0-9]{32}', target_schema):
        raise ValueError('Restore schema must be kps_restore_ plus a new UUID hex value.')
    connection = conninfo_to_dict(target_url)
    name = connection.get('dbname', '')
    if connection.get('host') not in {'localhost', '127.0.0.1', '::1', 'postgres'} or not (name.startswith('test_') or name.endswith('_test')):
        raise ValueError('Restore requires an explicitly named local PostgreSQL test database.')
    if connection.get('options'):
        raise ValueError('Restore URL options are prohibited; tool owns the new schema.')
    expected = [{'version': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                for path in sorted((db.ROOT / 'migrations').glob('*.sql'))]
    if manifest['migrations'] != expected:
        raise ValueError('Archive migrations do not match this code. Restore with matching code first.')
    with psycopg.connect(target_url, row_factory=dict_row, connect_timeout=10) as conn:
        with conn.transaction():
            if conn.execute('SELECT 1 FROM information_schema.schemata WHERE schema_name=%s', (target_schema,)).fetchone():
                raise ValueError('Restore refuses existing schemas. Choose a new UUID.')
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(target_schema)))
            conn.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(target_schema)))
            db.apply_migrations(conn, seed=False)
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
            counts = {table: conn.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table))).fetchone()['n']
                      for table in manifest['table_order']}
            if counts != manifest['counts'] or _totals(conn, None) != manifest['totals']:
                raise ValueError('Restored counts or monetary totals differ from snapshot.')
    return {'target_schema': target_schema, 'counts': counts, 'totals': manifest['totals'], 'verified': True}


@router.get('/export')
def business_export(user=Depends(auth.require_roles('owner'))):
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        contents, manifest = build_archive(conn, brand_id=user['brand_id'], max_bytes=MAX_WEB_EXPORT)
    with db.tx() as conn:
        db.audit(conn, user['id'], 'business.export.generated', 'brand', user['brand_id'],
                 {'format': FORMAT, 'counts': manifest['counts'], 'sha256': hashlib.sha256(contents).hexdigest()})
    filename = 'kps-business-export-v1-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.zip'
    return Response(contents, media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'no-store'})
