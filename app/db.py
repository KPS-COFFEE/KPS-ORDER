"""PostgreSQL-only persistence, forward migrations and atomic replay protection."""
from __future__ import annotations

import hashlib
import json as jsonlib
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg import sql
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]


def json(value: Any) -> Jsonb:
    return Jsonb(value)


def new_id(prefix: str = '') -> str:
    return prefix + uuid.uuid4().hex


def error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={'code': code, 'message': message})


@contextmanager
def tx():
    url = os.environ.get('DATABASE_URL', '')
    if not url.startswith(('postgres://', 'postgresql://')):
        raise RuntimeError('A PostgreSQL DATABASE_URL is required; no temporary storage fallback exists.')
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=10) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL statement_timeout = '15000ms'")
            yield conn


get_conn = tx


def fetch_one(conn, sql: str, params=()):
    return conn.execute(sql, params).fetchone()


def fetch_all(conn, sql: str, params=()):
    return conn.execute(sql, params).fetchall()


def audit(conn, user_id: str | None, action: str, entity_type: str, entity_id: str, details=None):
    # Never insert passwords, OTP codes, session values or arbitrary request headers here.
    conn.execute('INSERT INTO audit_events(id,actor_id,action,entity_type,entity_id,details) VALUES(%s,%s,%s,%s,%s,%s)',
                 (new_id('audit_'), user_id, action, entity_type, entity_id, json(details or {})))


def event(conn, order_id, user_id, event_type, details=None):
    conn.execute('INSERT INTO order_events(id,order_id,actor_id,event_type,details) VALUES(%s,%s,%s,%s,%s)',
                 (new_id('evt_'), order_id, user_id, event_type, json(details or {})))


def digest(body: Any) -> str:
    encoded = jsonlib.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def idempotent(conn, user_id: str, key: str | None, operation: str, body: Any, perform: Callable[[], dict]) -> dict:
    if not key or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', key):
        raise error(400, 'IDEMPOTENCY_KEY_REQUIRED', 'أرسل مفتاح منع تكرار صحيحًا للعملية.')
    lock_key = hashlib.sha256(f'{user_id}:{operation}:{key}'.encode()).digest()[:8]
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (int.from_bytes(lock_key, 'big', signed=True),))
    saved = conn.execute('SELECT request_digest,response FROM idempotency_keys WHERE user_id=%s AND operation=%s AND key=%s',
                         (user_id, operation, key)).fetchone()
    request_digest = digest(body)
    if saved:
        if saved['request_digest'] != request_digest:
            raise error(409, 'IDEMPOTENCY_CONFLICT', 'هذا المفتاح استُخدم لعملية ببيانات مختلفة.')
        return saved['response']
    result = perform()
    stable = jsonlib.loads(jsonlib.dumps(result, ensure_ascii=False, default=str))
    conn.execute('INSERT INTO idempotency_keys(user_id,operation,key,request_digest,response) VALUES(%s,%s,%s,%s,%s)',
                 (user_id, operation, key, request_digest, json(stable)))
    return stable


def rate_limit(conn, bucket: str, maximum: int, window_seconds: int):
    # Atomic database counters survive restarts and work across all web processes.
    window = conn.execute('SELECT floor(extract(epoch FROM now())/%s)::bigint AS n', (window_seconds,)).fetchone()['n']
    row = conn.execute('INSERT INTO rate_limits(bucket,window_start,hits) VALUES(%s,%s,1) '
                       'ON CONFLICT(bucket,window_start) DO UPDATE SET hits=rate_limits.hits+1 RETURNING hits',
                       (hashlib.sha256(bucket.encode()).hexdigest(), window)).fetchone()
    return row['hits'] <= maximum


def migrate():
    with tx() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (764832610,))
        baseline = preservation_snapshot(conn)
        apply_migrations(conn, seed=os.environ.get('INITIAL_CATALOG_IMPORT', 'true').lower() == 'true')
        after = preservation_snapshot(conn, baseline)
        if baseline != after:
            raise RuntimeError('Release aborted: existing operational data changed during migration.')
        revision = os.getenv('RUNTIME_REVISION') or os.getenv('RENDER_GIT_COMMIT') or 'local-unversioned'
        audit(conn, None, 'release.operational_data_preserved', 'runtime', revision,
              {'verified': bool(baseline), 'tables_checked': len(baseline),
               'checks': {table: {'count': value['count'], 'digest': value['digest']} for table, value in after.items()}})


PRESERVED_TABLES = ('users', 'sessions', 'identity_tokens', 'otp_challenges', 'email_outbox',
                    'orders', 'order_events', 'shifts', 'driver_tasks', 'collections', 'refunds',
                    'custody_transfers', 'custody_events', 'retained_carryovers', 'manager_handovers',
                    'closure_reports', 'report_corrections', 'report_renderings', 'report_notifications',
                    'closure_drafts', 'conversations', 'chat_messages', 'chat_attachments',
                    'conversation_reads', 'conversation_transfers',
                    'recovery_events', 'recovery_state', 'recovery_alerts',
                    'recovery_monitor_state', 'recovery_alert_notifications',
                    'vehicle_reports', 'vehicle_report_events')


def preservation_snapshot(conn, baseline=None):
    """Compare existing columns/bytes in the same bounded migration transaction.

    New columns are additive and excluded from an old baseline. Brief shared
    locks prevent concurrent operational writes from creating a false mismatch.
    Only counts and opaque hashes enter the private audit, never row contents.
    Catalog/branch edits are separately guarded by their explicit owner decisions.
    """
    available = {row['table_name'] for row in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE'").fetchall()}
    names = [name for name in PRESERVED_TABLES if name in available] if baseline is None else list(baseline)
    if names:
        conn.execute(sql.SQL('LOCK TABLE {} IN SHARE MODE').format(sql.SQL(',').join(map(sql.Identifier, names))))
    result = {}
    for table in names:
        columns = baseline[table]['columns'] if baseline is not None else [row['column_name'] for row in conn.execute(
            'SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position', (table,)).fetchall()]
        statement = sql.SQL("SELECT count(*)::bigint AS count,md5(coalesce(string_agg(h,'' ORDER BY h),'')) AS digest "
                            "FROM (SELECT md5(row_to_json(r)::text) AS h FROM (SELECT {} FROM {}) r) fingerprints").format(
            sql.SQL(',').join(map(sql.Identifier, columns)), sql.Identifier(table))
        result[table] = dict(conn.execute(statement).fetchone(), columns=columns)
    return result


def apply_migrations(conn, seed=True, owner_decisions=True):
    """Shared migration engine for startup and a separate, empty-schema restore drill."""
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (764832610,))
    conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())')
    for file in sorted((ROOT / 'migrations').glob('*.sql')):
        contents = file.read_text()
        checksum = hashlib.sha256(contents.encode()).hexdigest()
        previous = conn.execute('SELECT sha256 FROM schema_migrations WHERE version=%s', (file.name,)).fetchone()
        if previous:
            if previous['sha256'] != checksum:
                raise RuntimeError(f'Applied migration {file.name} changed; add a forward migration instead.')
            continue
        if re.search(r'\b(?:DROP\s+TABLE|TRUNCATE|DROP\s+DATABASE)\b', contents, re.I):
            raise RuntimeError('Destructive startup migrations are prohibited.')
        conn.execute(contents)
        conn.execute('INSERT INTO schema_migrations(version,sha256) VALUES(%s,%s)', (file.name, checksum))
    if seed:
        seed_initial(conn)
    # Explicit one-time owner decisions apply to the intended imported identities.
    # Existing seeds stay immutable; subsequent boots never reset admin edits.
    if owner_decisions:
        from . import branch_locations, catalog, service_hours
        catalog.apply_final_catalog_approval(conn)
        apply_owner_display_decisions(conn)
        service_hours.apply_default_branch_hours(conn)
        service_hours.apply_default_delivery_hours(conn)
        branch_locations.apply_workbook_matches(conn)
        branch_locations.apply_owner_confirmed_matches(conn)


def apply_owner_display_decisions(conn):
    conn.execute("INSERT INTO owner_display_decisions(decision_id,brand_id,branch_id,branch_no,source_id,approved_name) "
                 "VALUES ('branch-9-owner-final-2026-10-10','kps','kps-branch-9',9,'apex-canonical-branch-no:9',%s) ON CONFLICT DO NOTHING",
                 ('حفرالباطن - المروج - درايف ثرو',))
    rows = conn.execute('SELECT * FROM owner_display_decisions WHERE evaluated_at IS NULL FOR UPDATE').fetchall()
    for decision in rows:
        branch = conn.execute('SELECT * FROM branches WHERE id=%s AND brand_id=%s FOR UPDATE',
                              (decision['branch_id'], decision['brand_id'])).fetchone()
        result = 'skipped_identity_mismatch'
        if branch and branch['branch_no'] == decision['branch_no'] and branch['source_id'] == decision['source_id']:
            conn.execute('UPDATE branches SET name=%s,updated_at=now() WHERE id=%s',
                         (decision['approved_name'], branch['id']))
            audit(conn, None, 'branch.owner_display_name', 'branch', branch['id'],
                  {'decision_id': decision['decision_id'], 'branch_no': branch['branch_no'],
                   'before_name': branch['name'], 'after_name': decision['approved_name'],
                   'immutable_history_preserved': True})
            result = 'applied'
        conn.execute('UPDATE owner_display_decisions SET evaluated_at=now(),result=%s WHERE decision_id=%s',
                     (result, decision['decision_id']))


def seed_initial(conn):
    conn.execute("INSERT INTO brands(id,name) VALUES('kps','KPS COFFEE') ON CONFLICT DO NOTHING")
    conn.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled) VALUES('kps','guest','active',true) ON CONFLICT DO NOTHING")
    conn.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled) SELECT 'kps',channel,'unconfigured',false FROM (VALUES('email'),('sms'),('whatsapp')) c(channel) ON CONFLICT DO NOTHING")
    for name, kind in [('branches.json', 'branches'), ('catalog.json', 'products')]:
        file = ROOT / 'data' / name
        if not file.exists():
            continue
        document = jsonlib.loads(file.read_text(encoding='utf-8-sig'))
        rows = document if isinstance(document, list) else document.get(kind, [])
        for item in rows:
            brand_id = item.get('brand_id', 'kps')
            if not conn.execute('SELECT 1 FROM brands WHERE id=%s', (brand_id,)).fetchone():
                raise RuntimeError('Seed refers to an unknown brand; initialize that brand explicitly.')
            if kind == 'branches':
                conn.execute('INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                             (item['id'], item['branch_no'], item['source_id'], brand_id, item['name'], item['city'], json(item.get('settings', {}))))
            else:
                settings = dict(item.get('settings', {}))
                for field in ('image', 'image_url', 'description', 'option_groups', 'available_cities', 'enabled', 'source_id'):
                    if field in item and field not in settings:
                        settings[field] = item[field]
                conn.execute('INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                             (item['id'], brand_id, item['name'], item.get('category', ''), item['price_halalas'], json(settings)))
    conn.execute("INSERT INTO app_settings(brand_id,settings) VALUES('kps','{}') ON CONFLICT DO NOTHING")
