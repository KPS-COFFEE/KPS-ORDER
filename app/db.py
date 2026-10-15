"""PostgreSQL-only persistence, forward migrations and atomic replay protection."""
from __future__ import annotations

import atexit
import hashlib
import json as jsonlib
import os
import re
import threading
import uuid
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import psycopg
from fastapi import HTTPException
from psycopg.rows import dict_row
from psycopg import sql
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolClosed, PoolTimeout, TooManyRequests

ROOT = Path(__file__).resolve().parents[1]
POOL_MIN_SIZE = 2
# Two release workers share a 0.1-CPU / 256-MiB database. Forty backends
# multiply plan memory and contention instead of adding database CPU.
# Bound execution concurrency; HTTP load and checkout deadlines are unchanged.
POOL_MAX_SIZE = 4
POOL_CHECKOUT_SECONDS = 5.0
_pools: dict[tuple[int, str], ConnectionPool] = {}
_pools_lock = threading.Lock()
_inherited_pools = []


def _after_fork_child():
    global _pools, _pools_lock
    # Retain inherited objects without touching their sockets or possibly
    # locked thread primitives. Only fresh child-owned pools are accessible.
    _inherited_pools.append(_pools)
    _pools = {}
    _pools_lock = threading.Lock()


if hasattr(os, 'register_at_fork'):
    os.register_at_fork(after_in_child=_after_fork_child)


def _reset_connection(conn):
    # Keep only bounded parameterized SQL plans, never results or identity.
    # DISCARD ALL also deallocates plans and forced every fresh authorization
    # lookup to be parsed/planned again. Reset all other session state using
    # PostgreSQL's documented DISCARD ALL components, in one protocol message.
    # Pool identity still includes the complete DSN, PID and schema options.
    conn.execute('CLOSE ALL; SET SESSION AUTHORIZATION DEFAULT; RESET ALL; '
                 'UNLISTEN *; SELECT pg_advisory_unlock_all(); '
                 'DISCARD TEMP; DISCARD SEQUENCES', prepare=False)


def _configure_connection(conn):
    # Bound per-backend plan memory on the small database. No result caching.
    conn.prepared_max = 64


def _pool_for_url(url):
    if not url.startswith(('postgres://', 'postgresql://')):
        raise RuntimeError('A PostgreSQL DATABASE_URL is required; no temporary storage fallback exists.')
    # Full URL identity includes database, SSL parameters and search_path.
    # PID identity prevents inherited worker connections from being reused.
    identity = (os.getpid(), url)
    with _pools_lock:
        pool = _pools.get(identity)
        if pool is None:
            name = 'kps_order_pool_' + str(identity[0]) + '_' + hashlib.sha256(url.encode()).hexdigest()[:12]
            pool = ConnectionPool(url, min_size=POOL_MIN_SIZE, max_size=POOL_MAX_SIZE,
                timeout=POOL_CHECKOUT_SECONDS, max_waiting=200, open=False, name=name,
                kwargs={'row_factory': dict_row, 'connect_timeout': 5, 'autocommit': True,
                        'prepare_threshold': 5, 'application_name': name},
                configure=_configure_connection,
                check=ConnectionPool.check_connection, reset=_reset_connection,
                max_lifetime=600, max_idle=60, reconnect_timeout=10, num_workers=3)
            pool.open()
            _pools[identity] = pool
        return pool


def close_pools(url=None):
    """Close this process's pools at lifespan exit/test-schema cleanup."""
    with _pools_lock:
        keys = [key for key in _pools if key[0] == os.getpid() and (url is None or key[1] == url)]
        pools = [_pools.pop(key) for key in keys]
    for pool in pools:
        pool.close(timeout=5)


def pool_statistics():
    """Opaque-name, numeric observation; never exposes DSNs or credentials."""
    with _pools_lock:
        pools = [pool for key, pool in _pools.items() if key[0] == os.getpid()]
    return {pool.name: pool.get_stats() for pool in pools}


atexit.register(close_pools)


def json(value: Any) -> Jsonb:
    return Jsonb(value)


def new_id(prefix: str = '') -> str:
    return prefix + uuid.uuid4().hex


def error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={'code': code, 'message': message})


@contextmanager
def tx(*, pipeline: bool = False):
    url = os.environ.get('DATABASE_URL', '')
    if not url.startswith(('postgres://', 'postgresql://')):
        raise RuntimeError('A PostgreSQL DATABASE_URL is required; no temporary storage fallback exists.')
    pool = _pool_for_url(url)
    try:
        with pool.connection(timeout=POOL_CHECKOUT_SECONDS) as conn:
            if pipeline:
                # This connection is exclusively checked out and reset. Queue
                # COMMIT in the SAME pipeline as the business commands.
                # Connection.transaction() inside a pipeline
                # inserts nested synchronization points, including one between
                # the final write and COMMIT. That needlessly holds the branch
                # receipt lock while the Python worker is rescheduled.
                if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                    raise psycopg.OperationalError('Database connection is not transaction-idle.')
                # Acknowledge BEGIN before handing the connection to callers.
                # libpq only updates its transaction state at a Sync boundary;
                # a queued, unfetched BEGIN still looks idle to safety guards.
                conn.execute('BEGIN')
                with conn.pipeline():
                    conn.execute("SET LOCAL statement_timeout = '15000ms'")
                    yield conn
                    committed = conn.execute('COMMIT')
                # Pipeline exit has consumed every result, including COMMIT.
                # Any queued error escapes to pool.connection(), whose normal
                # connection context rolls back before reset/reuse. PostgreSQL
                # can reply ROLLBACK to COMMIT after a caught SQL error: never
                # report a successful business operation in that case.
                if committed.statusmessage != 'COMMIT':
                    raise psycopg.OperationalError('Database transaction was not committed.')
            else:
                # Migrations, COPY and other callers keep the standard context.
                with conn.transaction():
                    conn.execute("SET LOCAL statement_timeout = '15000ms'")
                    yield conn
    except (PoolClosed, PoolTimeout, TooManyRequests) as exc:
        # Preserve the existing sanitized database-unavailable HTTP contract.
        raise psycopg.OperationalError('Database connection capacity is unavailable.') from exc


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


@dataclass(frozen=True)
class AtomicIdempotentWrite:
    """Trusted SQL CTEs ending in operation_response(response).

    Prepared by application code, never by a request. The shared replay engine
    appends its own INSERT so business writes and the durable response execute
    without an intervening Python fetch/serialization round trip.
    """
    ctes: str
    params: tuple


@dataclass(frozen=True)
class DeferredAtomicResponse:
    """Buffered result decoded only after the owning tx/pipeline has exited.

    No SQL is issued by resolve(): the successful transaction context already
    consumed the server's write/COMMIT results. Cursor rows remain local even
    after the connection is returned to its pool.
    """
    cursor: Any

    def resolve(self) -> dict | str:
        if self.cursor.pgresult is None:
            raise RuntimeError('Atomic response must be resolved after the transaction finishes.')
        try:
            saved = self.cursor.fetchone()
            if saved is None:
                raise RuntimeError('Atomic operation did not persist its replay response.')
            return saved['response']
        finally:
            self.cursor.close()


def idempotent(conn, user_id: str, key: str | None, operation: str, body: Any, perform: Callable[[], dict | AtomicIdempotentWrite],
               *, pipeline_writes: bool = False, defer_atomic_response: bool = False,
               encoded_response: bool = False) -> dict | str | DeferredAtomicResponse:
    if not key or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,128}', key):
        raise error(400, 'IDEMPOTENCY_KEY_REQUIRED', 'أرسل مفتاح منع تكرار صحيحًا للعملية.')
    lock_key = hashlib.sha256(f'{user_id}:{operation}:{key}'.encode()).digest()[:8]
    conn.execute('SELECT pg_advisory_xact_lock(%s)', (int.from_bytes(lock_key, 'big', signed=True),))
    # An opted-in HTTP caller can send the database's already-authorized JSON
    # projection without decoding and encoding a complete receipt twice.
    response_column='response::text AS response' if encoded_response else 'response'
    saved = conn.execute('SELECT request_digest,'+response_column+' FROM idempotency_keys WHERE user_id=%s AND operation=%s AND key=%s',
                         (user_id, operation, key)).fetchone()
    request_digest = digest(body)
    if saved:
        if saved['request_digest'] != request_digest:
            raise error(409, 'IDEMPOTENCY_CONFLICT', 'هذا المفتاح استُخدم لعملية ببيانات مختلفة.')
        return saved['response']
    # Opted-in callers keep all existing read/lock/transaction semantics.
    # Cursor fetches synchronize their dependent reads; independent trailing
    # event/audit/replay writes share one pipeline flush. Exiting the context
    # must succeed before returning a response, and the caller's transaction
    # still atomically commits or rolls back the entire business operation.
    # The checkout's outer tx owns its pipeline. Do not create an inner Sync
    # between its final write and COMMIT: that holds the receipt-counter lock
    # while Python waits, decodes JSON and gets scheduled again under load.
    if defer_atomic_response and (conn.info.pipeline_status != psycopg.pq.PipelineStatus.ON
                                  or conn.info.transaction_status != psycopg.pq.TransactionStatus.INTRANS):
        raise RuntimeError('Deferred atomic writes require an enclosing pipelined transaction.')
    with conn.pipeline() if pipeline_writes and not defer_atomic_response else nullcontext():
        result = perform()
        if isinstance(result, AtomicIdempotentWrite):
            statement = ('WITH ' + result.ctes +
                         ' INSERT INTO idempotency_keys(user_id,operation,key,request_digest,response) '
                         'SELECT %s,%s,%s,%s,response FROM operation_response RETURNING '+response_column)
            params = result.params + (user_id, operation, key, request_digest)
            if defer_atomic_response:
                # Enforce exactly one saved response *on the server*, before
                # COMMIT, including an empty operation_response. A missing row
                # raises DivisionByZero and rolls back every business write.
                statement = ('WITH ' + result.ctes + ', saved_response AS ('
                             'INSERT INTO idempotency_keys(user_id,operation,key,request_digest,response) '
                             'SELECT %s,%s,%s,%s,response FROM operation_response RETURNING response) '
                             'SELECT (CASE WHEN count(*)=1 THEN jsonb_agg(response)->0 '
                             'ELSE to_jsonb(1/(count(*)-count(*))) END)'+
                             ('::text' if encoded_response else '')+' AS response FROM saved_response')
                return DeferredAtomicResponse(conn.execute(statement, params))
            saved = conn.execute(statement, params).fetchone()
            if saved is None:
                raise RuntimeError('Atomic operation did not persist its replay response.')
            return saved['response']
        stable = jsonlib.loads(jsonlib.dumps(result, ensure_ascii=False, default=str))
        conn.execute('INSERT INTO idempotency_keys(user_id,operation,key,request_digest,response) VALUES(%s,%s,%s,%s,%s)',
                     (user_id, operation, key, request_digest, json(stable)))
    return jsonlib.dumps(stable,ensure_ascii=False,separators=(',',':')) if encoded_response else stable


def rate_limit(conn, bucket: str, maximum: int, window_seconds: int):
    # Atomic database counters survive restarts and work across all web processes.
    row = conn.execute('INSERT INTO rate_limits(bucket,window_start,hits) '
                       'VALUES(%s,floor(extract(epoch FROM now())/%s)::bigint,1) '
                       'ON CONFLICT(bucket,window_start) DO UPDATE SET hits=rate_limits.hits+1 RETURNING hits',
                       (hashlib.sha256(bucket.encode()).hexdigest(), window_seconds)).fetchone()
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


PRESERVED_TABLES = ('users', 'driver_evidence', 'driver_break_policies', 'driver_breaks', 'driver_break_events', 'dispatch_offers', 'dispatch_offer_events', 'driver_float_requests', 'driver_float_decisions', 'customer_addresses', 'customer_identity_links', 'sessions', 'identity_tokens', 'otp_challenges', 'email_outbox',
                    'orders', 'order_amendments', 'order_revisions', 'order_revision_receipts', 'order_daily_counters', 'order_branch_counters', 'order_events', 'shifts', 'driver_tasks', 'collections', 'refunds',
                    'custody_transfers', 'custody_events', 'retained_carryovers', 'manager_handovers',
                    'closure_reports', 'manager_financial_matches', 'report_corrections', 'report_renderings', 'report_notifications',
                    'closure_drafts', 'conversations', 'chat_messages', 'chat_attachments',
                    'conversation_reads', 'conversation_transfers',
                    'recovery_events', 'recovery_state', 'recovery_alerts',
                    'recovery_monitor_state', 'recovery_alert_notifications', 'recovery_worker_observations',
                    'vehicle_reports', 'vehicle_report_events', 'fleet_vehicles', 'vehicle_operating_logs', 'vehicle_log_corrections', 'fleet_route_readings')


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
