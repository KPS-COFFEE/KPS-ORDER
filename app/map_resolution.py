"""Bounded anonymous map resolution, with PostgreSQL admission shared by workers.

No IP/Forwarded header is used as browser identity: a deployment proxy must not
collapse every customer into one rate bucket. See docs/maps-security.md.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
from urllib.parse import urljoin, urlsplit

import httpx

from . import db

COOKIE = 'kps_map_client'
CLIENT_LIMIT = 10
GLOBAL_LIMIT = 120  # cache misses per minute, across all anonymous identities
CONCURRENCY_LIMIT = 4
DEADLINE_SECONDS = 3.0
LEASE_SECONDS = 15  # exceeds network deadline; crash recovery, not request timeout
CACHE_SECONDS = 300
CACHE_LIMIT = 256
CLIENT_STATE_LIMIT = 4096
LOCK_KEY = 746231899
SHORT_HOSTS = {'maps.app.goo.gl', 'goo.gl'}


def _cookie_identity(raw, secret):
    if isinstance(raw, str) and re.fullmatch(r'[0-9a-f]{48}\.[0-9a-f]{64}', raw):
        token, signature = raw.split('.')
        expected = hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(signature, expected):
            return hashlib.sha256(token.encode()).hexdigest(), raw
    token = secrets.token_hex(24)
    signature = hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()
    return hashlib.sha256(token.encode()).hexdigest(), token + '.' + signature


def _limited(code='MAP_RATE_LIMITED'):
    error = db.error(429, code, 'محاولات كثيرة لفتح رابط الخرائط. انتظر قليلًا ثم حاول مجددًا.')
    error.headers = {'Retry-After': '60'}
    return error


def admit(url, raw_cookie=None):
    """Commit budgets and lease BEFORE outbound I/O; no DB checkout held for I/O.

    Decisions return instead of raising inside tx, so rejected attempts cannot
    roll back/reset previously committed counters. Signed cookie IDs are bounded
    in the shared client table. Global miss budget also covers cookie rotation.
    """
    key = hashlib.sha256(url.encode()).hexdigest()
    with db.tx() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_KEY,))
        conn.execute('INSERT INTO map_resolution_state(id, signing_key) VALUES(1,%s) ON CONFLICT DO NOTHING',
                     (secrets.token_hex(32),))
        state = conn.execute('SELECT * FROM map_resolution_state WHERE id=1').fetchone()
        identity, cookie = _cookie_identity(raw_cookie, state['signing_key'])
        conn.execute('DELETE FROM map_resolution_clients WHERE expires_at<=clock_timestamp()')
        conn.execute('DELETE FROM map_resolution_leases WHERE expires_at<=clock_timestamp()')
        conn.execute('DELETE FROM map_resolution_cache WHERE expires_at<=clock_timestamp()')
        now = conn.execute("SELECT date_trunc('minute',clock_timestamp()) AS bucket").fetchone()['bucket']
        client = conn.execute('SELECT * FROM map_resolution_clients WHERE id=%s', (identity,)).fetchone()
        attempts = client['attempts'] if client and client['bucket']==now else 0
        if attempts >= CLIENT_LIMIT:
            return {'cookie': cookie, 'error': _limited()}
        if not client and conn.execute('SELECT count(*) AS n FROM map_resolution_clients').fetchone()['n'] >= CLIENT_STATE_LIMIT:
            return {'cookie': cookie, 'error': _limited('MAP_CAPACITY_LIMITED')}
        conn.execute("INSERT INTO map_resolution_clients(id,bucket,attempts,expires_at) VALUES(%s,%s,%s,clock_timestamp()+interval '2 minutes') "
                     'ON CONFLICT(id) DO UPDATE SET bucket=excluded.bucket,attempts=excluded.attempts,expires_at=excluded.expires_at',
                     (identity,now,attempts+1))
        cached = conn.execute('SELECT result FROM map_resolution_cache WHERE url_hash=%s AND expires_at>clock_timestamp()', (key,)).fetchone()
        if cached:
            return {'cookie': cookie, 'result': cached['result']}
        global_attempts = state['attempts'] if state['bucket']==now else 0
        if global_attempts >= GLOBAL_LIMIT:
            return {'cookie': cookie, 'error': _limited('MAP_GLOBAL_RATE_LIMITED')}
        if conn.execute('SELECT count(*) AS n FROM map_resolution_leases').fetchone()['n'] >= CONCURRENCY_LIMIT:
            return {'cookie': cookie, 'error': _limited('MAP_CONCURRENCY_LIMITED')}
        lease = secrets.token_hex(24)
        conn.execute('UPDATE map_resolution_state SET bucket=%s,attempts=%s WHERE id=1', (now,global_attempts+1))
        conn.execute("INSERT INTO map_resolution_leases(id,expires_at) VALUES(%s,clock_timestamp()+%s*interval '1 second')", (lease,LEASE_SECONDS))
        return {'cookie': cookie, 'lease': lease, 'key': key}


def finish(admission, result=None):
    """Only an accepted coordinate result is cacheable; failures never populate it."""
    if not admission.get('lease'):
        return
    if result is not None:
        point = result.get('location', {})
        if (set(result) != {'location','source','location_is_device_verified'}
                or set(point) != {'lat','lng'}
                or result['source'] != 'customer_selected'
                or result['location_is_device_verified'] is not False
                or any(type(point[k]) not in (int,float) for k in ('lat','lng'))
                or not (-90 <= point['lat'] <= 90 and -180 <= point['lng'] <= 180)):
            raise ValueError('Unvalidated map result cannot be cached')
    with db.tx() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_KEY,))
        # An expired/crashed lease may not later write a stale result.
        active = conn.execute('DELETE FROM map_resolution_leases WHERE id=%s AND expires_at>clock_timestamp() RETURNING id',
                              (admission['lease'],)).fetchone()
        if active and result is not None:
            conn.execute('DELETE FROM map_resolution_cache WHERE expires_at<=clock_timestamp()')
            conn.execute("INSERT INTO map_resolution_cache(url_hash,result,expires_at) VALUES(%s,%s,clock_timestamp()+%s*interval '1 second') "
                         'ON CONFLICT(url_hash) DO UPDATE SET result=excluded.result,expires_at=excluded.expires_at',
                         (admission['key'],db.json(result),CACHE_SECONDS))
            conn.execute('DELETE FROM map_resolution_cache WHERE url_hash IN '
                         '(SELECT url_hash FROM map_resolution_cache ORDER BY expires_at DESC,url_hash OFFSET %s)', (CACHE_LIMIT,))


async def resolve_short_map_url(value):
    """One cancellable wall-clock deadline over ALL redirects; never read bodies."""
    from .catalog import safe_map_url
    try:
        async with asyncio.timeout(DEADLINE_SECONDS):
            async with httpx.AsyncClient(timeout=DEADLINE_SECONDS, follow_redirects=False, trust_env=False) as client:
                for _ in range(4):
                    if not safe_map_url(value):
                        raise db.error(422,'MAP_REDIRECT_NOT_ALLOWED','الرابط لا يشير إلى موقع Google Maps موثوق.')
                    if urlsplit(value).hostname not in SHORT_HOSTS:
                        return value
                    async with client.stream('GET',value,headers={'User-Agent':'Mozilla/5.0'}) as response:
                        if response.status_code not in (301,302,303,307,308) or not response.headers.get('location'):
                            break
                        value = urljoin(value,response.headers['location'])
                        if len(value)>2000:
                            break
                if len(value)<=2000 and safe_map_url(value) and urlsplit(value).hostname not in SHORT_HOSTS:
                    return value
    except (httpx.HTTPError,TimeoutError):
        pass
    raise db.error(422,'MAP_SHORT_LINK_UNAVAILABLE','تعذر فتح الرابط المختصر. افتحه في خرائط جوجل وانسخ رابط الدبوس الكامل، أو استخدم الإحداثيات الصحيحة.')
