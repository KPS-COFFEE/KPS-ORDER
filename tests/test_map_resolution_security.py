"""SEC-01 prevention tests. All HTTP transports are synthetic; PG is fixture-owned."""
import asyncio
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import multiprocessing
import os
import time
from unittest.mock import patch

import httpx
import pytest

from app import catalog, db, map_resolution as maps

SHORT = 'https://maps.app.goo.gl/SyntheticSecurityOnly'
PIN = 'https://www.google.com/maps?q=28.4,45.9'


@contextmanager
def _fixed_rate_bucket(bucket):
    # Pin only the admission minute. Counters, transactions, locking and leases
    # stay real; a wall-clock minute boundary is not a reset within this test.
    real_tx = db.tx

    @contextmanager
    def fixed_bucket_tx(*args, **kwargs):
        with real_tx(*args, **kwargs) as conn:
            class FixedBucketConnection:
                def __getattr__(self, name):
                    return getattr(conn, name)

                def execute(self, query, *args, **kwargs):
                    if query == "SELECT date_trunc('minute',clock_timestamp()) AS bucket":
                        return conn.execute('SELECT %s::timestamptz AS bucket', (bucket,))
                    return conn.execute(query, *args, **kwargs)
            yield FixedBucketConnection()

    with patch.object(db, 'tx', fixed_bucket_tx):
        yield


def _admit_in_bucket(url, cookie=None, bucket=None):
    if bucket is None:
        return maps.admit(url, cookie)
    with _fixed_rate_bucket(bucket):
        return maps.admit(url, cookie)


def _worker_admit(url, cookie, database, bucket=None):
    # Spawned process owns its DB pool, as real Uvicorn workers do.
    os.environ['DATABASE_URL'] = database
    from app import db
    try:
        result = _admit_in_bucket(url, cookie, bucket)
        return {k: (v.status_code if k == 'error' else v) for k, v in result.items()}
    finally:
        db.close_pools()


def test_normal_anonymous_use_cache_rate_limit_and_no_outbound_after_limit(client, monkeypatch):
    calls = []
    async def resolve(url):
        calls.append(url)
        return PIN
    monkeypatch.setattr(maps, 'resolve_short_map_url', resolve)
    for _ in range(maps.CLIENT_LIMIT):
        response = client.post('/api/location/parse-map-url', json={'url': SHORT})
        assert response.status_code == 200, response.text
        assert response.json()['location'] == {'lat': 28.4, 'lng': 45.9}
    rejected = client.post('/api/location/parse-map-url', json={'url': SHORT + 'Other'})
    assert rejected.status_code == 429
    assert rejected.headers['retry-after'] == '60'
    assert len(calls) == 1
    assert maps.COOKIE in client.cookies


def test_clients_behind_same_proxy_do_not_share_browser_budget(client, monkeypatch):
    async def resolve(url): return PIN
    monkeypatch.setattr(maps, 'resolve_short_map_url', resolve)
    for _ in range(maps.CLIENT_LIMIT):
        assert client.post('/api/location/parse-map-url', json={'url': SHORT}).status_code == 200
    first_cookie = client.cookies[maps.COOKIE]
    assert client.post('/api/location/parse-map-url', json={'url': SHORT}).status_code == 429
    client.cookies.clear()
    # Same ASGI intermediary, including spoofed headers, gets its OWN budget.
    response = client.post('/api/location/parse-map-url', json={'url': SHORT},
                           headers={'X-Forwarded-For': '203.0.113.77', 'Forwarded': 'for=203.0.113.77'})
    assert response.status_code == 200
    assert client.cookies[maps.COOKIE] != first_cookie
    client.cookies.clear()
    client.cookies.set(maps.COOKIE, first_cookie, path='/api/location')
    assert client.post('/api/location/parse-map-url', json={'url': SHORT},
                       headers={'X-Forwarded-For': '198.51.100.99'}).status_code == 429


def test_signed_cookie_cannot_choose_an_existing_browser_identity(database):
    first = maps.admit(SHORT)
    maps.finish(first)
    forged = first['cookie'][:-1] + ('0' if first['cookie'][-1] != '0' else '1')
    second = maps.admit(SHORT, forged)
    assert second['cookie'] != forged and second['cookie'] != first['cookie']
    maps.finish(second)


def test_global_budget_covers_reset_cookies_before_any_http(client, monkeypatch):
    monkeypatch.setattr(maps, 'GLOBAL_LIMIT', 2)
    calls = []
    async def resolve(url):
        calls.append(url)
        return PIN
    monkeypatch.setattr(maps, 'resolve_short_map_url', resolve)
    for index in range(2):
        client.cookies.clear()
        assert client.post('/api/location/parse-map-url', json={'url': SHORT + str(index)}).status_code == 200
    client.cookies.clear()
    assert client.post('/api/location/parse-map-url', json={'url': SHORT + '3'}).status_code == 429
    assert len(calls) == 2


def test_global_concurrency_is_shared_by_distinct_os_workers(database):
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context('spawn')) as executor:
        futures = [executor.submit(_worker_admit, SHORT + str(i), None, database)
                   for i in range(maps.CONCURRENCY_LIMIT + 1)]
        values = [future.result(timeout=30) for future in futures]
    leases = [value for value in values if 'lease' in value]
    assert len(leases) == maps.CONCURRENCY_LIMIT
    assert [value['error'] for value in values if 'error' in value] == [429]
    for value in leases: maps.finish(value)
    assert 'lease' in (last := maps.admit(SHORT))
    maps.finish(last)


def test_client_budget_and_cache_are_shared_by_distinct_os_workers(database):
    bucket = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    admission = _admit_in_bucket(SHORT, bucket=bucket)
    maps.finish(admission, catalog._parse_map_pin(PIN))
    with ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context('spawn')) as executor:
        values = list(executor.map(_worker_admit, [SHORT] * maps.CLIENT_LIMIT,
                                   [admission['cookie']] * maps.CLIENT_LIMIT,
                                   [database] * maps.CLIENT_LIMIT,
                                   [bucket] * maps.CLIENT_LIMIT))
    assert sum('result' in value for value in values) == maps.CLIENT_LIMIT - 1
    assert sum(value.get('error') == 429 for value in values) == 1


def test_expired_lease_recovers_and_cache_has_hard_bound(database, monkeypatch):
    monkeypatch.setattr(maps, 'CACHE_LIMIT', 2)
    for index in range(4):
        admission = maps.admit(SHORT + str(index))
        maps.finish(admission, catalog._parse_map_pin(PIN))
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) AS n FROM map_resolution_cache').fetchone()['n'] == 2
        conn.execute("INSERT INTO map_resolution_leases(id,expires_at) VALUES('expired',clock_timestamp()-interval '1 second')")
    admission = maps.admit(SHORT + '5')
    with db.tx() as conn:
        assert conn.execute("SELECT count(*) AS n FROM map_resolution_leases WHERE id='expired'").fetchone()['n'] == 0
    maps.finish(admission)


def test_failed_resolution_not_cached_and_lease_released(client, monkeypatch):
    async def resolve(url): return 'https://www.google.com/maps?query=place_id:abcdefghijklm'
    monkeypatch.setattr(maps, 'resolve_short_map_url', resolve)
    assert client.post('/api/location/parse-map-url', json={'url': SHORT}).status_code == 422
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) AS n FROM map_resolution_cache').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM map_resolution_leases').fetchone()['n'] == 0


@pytest.mark.parametrize('location', ['https://evil.example/maps?q=1,2', 'http://127.0.0.1/maps?q=1,2',
                                     'https://www.google.com.evil.test/maps?q=1,2',
                                     'https://www.google.com/maps/@28,45,12z'])
def test_redirect_guards_preserved_no_untrusted_fetch(monkeypatch, location):
    seen = []
    real_client = httpx.AsyncClient
    async def handle(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={'location': location})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(Exception) as error:
        asyncio.run(maps.resolve_short_map_url(SHORT))
    assert error.value.status_code == 422
    assert seen == [SHORT]


def test_total_deadline_includes_all_redirects_and_never_reads_body(monkeypatch):
    real_client = httpx.AsyncClient
    seen = []
    class NoBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail('Resolver must not consume remote bodies')
            yield b''
    clock = {'now': 0., 'start': 0.}
    async def handle(request):
        seen.append(str(request.url))
        # Advance the resolver's real event-loop deadline cumulatively without
        # making shared-runner CPU scheduling determine the number of hops.
        clock['now'] += .06
        # The first turn admits the due timeout callback; the second gives it
        # the chance to cancel this transport before a response is returned.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return httpx.Response(302, headers={'location': SHORT + str(len(seen))}, stream=NoBody())
    async def resolve_with_controlled_clock():
        loop = asyncio.get_running_loop()
        clock['now'] = clock['start'] = loop.time()
        with monkeypatch.context() as controlled:
            controlled.setattr(loop, 'time', lambda: clock['now'])
            return await maps.resolve_short_map_url(SHORT)
    monkeypatch.setattr(maps, 'DEADLINE_SECONDS', .1)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    start = time.monotonic()
    with pytest.raises(Exception) as error: asyncio.run(resolve_with_controlled_clock())
    assert error.value.status_code == 422
    assert time.monotonic() - start < .3
    assert seen == [SHORT, SHORT + '1']
    assert clock['now'] - clock['start'] == pytest.approx(.12)


def test_route_cancellation_releases_distributed_lease(database, monkeypatch):
    from starlette.requests import Request
    from starlette.responses import Response
    async def resolve(url):
        raise asyncio.CancelledError()
    monkeypatch.setattr(maps, 'resolve_short_map_url', resolve)
    request = Request({'type': 'http', 'headers': [], 'method': 'POST', 'path': '/api/location/parse-map-url'})
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(catalog.parse_map_url_http(catalog.MapURLBody(url=SHORT), request, Response()))
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) AS n FROM map_resolution_leases').fetchone()['n'] == 0


def test_direct_coordinates_need_neither_authentication_nor_network(client, monkeypatch):
    async def no_network(url): pytest.fail('Direct coordinates must not invoke HTTP')
    monkeypatch.setattr(maps, 'resolve_short_map_url', no_network)
    response = client.post('/api/location/parse-map-url', json={'url': PIN})
    assert response.status_code == 200 and response.json()['location']['lat'] == 28.4


def test_concurrency_rejects_endpoint_before_http_and_expired_lease_cannot_cache(client, monkeypatch):
    admissions = [maps.admit(SHORT + str(i)) for i in range(maps.CONCURRENCY_LIMIT)]
    async def no_http(url): pytest.fail('Capacity rejection must precede HTTP')
    monkeypatch.setattr(maps, 'resolve_short_map_url', no_http)
    assert client.post('/api/location/parse-map-url', json={'url': SHORT + 'busy'}).status_code == 429
    with db.tx() as conn:
        conn.execute("UPDATE map_resolution_leases SET expires_at=clock_timestamp()-interval '1 second'")
    maps.finish(admissions[0], catalog._parse_map_pin(PIN))
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) AS n FROM map_resolution_cache').fetchone()['n'] == 0
    for admission in admissions[1:]: maps.finish(admission)


def test_cache_expiry_reexecutes_http_and_rejects_non_coordinate_cache_payload(client, monkeypatch):
    calls = []
    async def resolve(url):
        calls.append(url)
        return PIN
    monkeypatch.setattr(maps, 'resolve_short_map_url', resolve)
    assert client.post('/api/location/parse-map-url', json={'url': SHORT}).status_code == 200
    with db.tx() as conn:
        conn.execute("UPDATE map_resolution_cache SET expires_at=clock_timestamp()-interval '1 second'")
    assert client.post('/api/location/parse-map-url', json={'url': SHORT}).status_code == 200
    assert calls == [SHORT, SHORT]
    admission = maps.admit(SHORT + 'invalid')
    with pytest.raises(ValueError): maps.finish(admission, {'html': '<untrusted>'})
    maps.finish(admission)


def test_failed_links_keep_browser_cookie_and_eventually_block_before_http(client, monkeypatch):
    calls = []
    async def resolve(url):
        calls.append(url)
        raise db.error(422, 'MAP_SHORT_LINK_UNAVAILABLE', 'Synthetic unavailable')
    monkeypatch.setattr(maps, 'resolve_short_map_url', resolve)
    bucket = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    with _fixed_rate_bucket(bucket):
        cookie = None
        for _ in range(maps.CLIENT_LIMIT):
            assert client.post('/api/location/parse-map-url', json={'url': SHORT}).status_code == 422
            if cookie is None: cookie = client.cookies[maps.COOKIE]
            assert client.cookies[maps.COOKIE] == cookie
        rejected = client.post('/api/location/parse-map-url', json={'url': SHORT})
        assert rejected.status_code == 429
        assert client.cookies[maps.COOKIE] == cookie
        assert len(calls) == maps.CLIENT_LIMIT


def test_map_runtime_private_export_and_isolated_restore_retires_capabilities(database):
    from uuid import uuid4
    import psycopg
    from psycopg import sql
    from app.exports import build_archive, verify_archive, restore_archive, MAP_RUNTIME_TABLES
    first = maps.admit(SHORT)
    maps.finish(first, catalog._parse_map_pin(PIN))
    pending = maps.admit(SHORT + 'pending', first['cookie'])
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        signing_key = conn.execute('SELECT signing_key FROM map_resolution_state WHERE id=1').fetchone()['signing_key']
        business, business_manifest = build_archive(conn, brand_id='kps')
        full, full_manifest = build_archive(conn, full=True)
    public_archive, _ = verify_archive(business)
    assert not MAP_RUNTIME_TABLES & set(business_manifest['table_order'])
    assert MAP_RUNTIME_TABLES <= set(business_manifest['excluded_tables'])
    assert not any(signing_key.encode() in public_archive.read(name) for name in public_archive.namelist())
    assert not any(name.startswith('data/map_resolution_') for name in public_archive.namelist())
    assert MAP_RUNTIME_TABLES <= set(full_manifest['table_order'])
    assert full_manifest['counts']['map_resolution_state'] == 1
    assert full_manifest['counts']['map_resolution_cache'] == 1
    assert full_manifest['counts']['map_resolution_leases'] == 1
    schema = 'kps_restore_' + uuid4().hex
    base = os.environ['TEST_DATABASE_URL']
    try:
        result = restore_archive(full, target_url=base, target_schema=schema)
        assert result['verified']
        assert result['counts'] == full_manifest['counts']
        assert result['ephemeral_map_runtime_discarded'] == {
            table: full_manifest['counts'][table] for table in MAP_RUNTIME_TABLES}
        with psycopg.connect(base) as conn:
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            for table in MAP_RUNTIME_TABLES:
                assert conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM audit_events WHERE action='restore.map_runtime_discarded'").fetchone()[0] == 1
    finally:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
        maps.finish(pending)
