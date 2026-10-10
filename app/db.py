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
        if os.environ.get('INITIAL_CATALOG_IMPORT', 'true').lower() == 'true':
            seed_initial(conn)


def seed_initial(conn):
    conn.execute("INSERT INTO brands(id,name) VALUES('kps','KPS COFFEE') ON CONFLICT DO NOTHING")
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
