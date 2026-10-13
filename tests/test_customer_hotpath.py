"""Fresh catalog visibility and atomic rate controls after round-trip reduction."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import hashlib
import psycopg

import pytest

from app import db
from app import auth
from fastapi.testclient import TestClient
from test_hardening_orders import checkout

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize('locale', ['ar', 'en'])
def test_single_pass_http_quote_and_order_preserve_json_and_privacy(checkout, monkeypatch, locale):
    from fastapi.encoders import jsonable_encoder
    from starlette.responses import JSONResponse
    from app import catalog, orders
    from test_hardening_orders import create, payload

    client, user = checkout
    original = catalog.quote_order
    quoted = []

    def observed(*args, **kwargs):
        result = original(*args, **kwargs)
        quoted.append(catalog.customer_quote_view(result))
        return result

    monkeypatch.setattr(catalog, 'quote_order', observed)
    quote = client.post('/api/quote', json=payload(), headers={'Accept-Language': locale})
    assert quote.status_code == 200 and len(quoted) == 1
    assert quote.content == JSONResponse(jsonable_encoder({'quote': quoted[0]})).body
    placed = create(client)
    assert placed.status_code == 200, placed.text
    identifier = placed.json()['order']['id']
    direct = orders.get_order(identifier, user=user)
    viewed = client.get('/api/orders/' + identifier, headers={'Accept-Language': locale})
    assert viewed.status_code == 200
    assert viewed.content == JSONResponse(jsonable_encoder(direct)).body
    for response in (quote, viewed):
        assert response.headers['content-type'] == 'application/json'
        assert response.headers['cache-control'] == 'no-store'
        assert 'performance_snapshot' not in response.text and 'risk_reasons' not in response.text


@pytest.mark.parametrize('failure', ['sql', 'python'])
def test_pipelined_transaction_never_commits_after_a_queued_failure(database, failure):
    brand = 'TEST-hotpath-rollback-' + failure
    with pytest.raises(psycopg.errors.DivisionByZero if failure == 'sql' else RuntimeError):
        with db.tx(pipeline=True) as conn:
            conn.execute('INSERT INTO brands(id,name) VALUES(%s,%s)', (brand, 'TEST queued write'))
            if failure == 'sql':
                conn.execute('SELECT 1/0')
            else:
                raise RuntimeError('TEST cancelled operation')
    with db.tx(pipeline=True) as conn:
        assert conn.execute('SELECT id FROM brands WHERE id=%s', (brand,)).fetchone() is None
        assert conn.execute('SHOW statement_timeout').fetchone()['statement_timeout'] == '15s'


def test_rate_counter_keeps_exact_limit_under_concurrency_and_rolls_back(database):
    barrier = Barrier(8)
    bucket = 'TEST customer hotpath atomic limit'
    def hit(_):
        barrier.wait(timeout=20)
        with db.tx() as conn:
            return db.rate_limit(conn, bucket, 3, 864000000)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(hit, range(8)))
    assert sum(results) == 3
    with pytest.raises(RuntimeError):
        with db.tx() as conn:
            assert db.rate_limit(conn, 'TEST rollback bucket', 1, 864000000)
            raise RuntimeError('cancel synthetic transaction')
    with db.tx() as conn:
        row = conn.execute('SELECT hits FROM rate_limits WHERE bucket=%s',
                           (hashlib.sha256(bucket.encode()).hexdigest(),)).fetchone()
        assert row['hits'] == 8
        assert db.rate_limit(conn, 'TEST rollback bucket', 1, 864000000)
        assert not db.rate_limit(conn, 'TEST rollback bucket', 1, 864000000)


def test_catalog_reads_stops_immediately_and_preserves_scope(client):
    path = '/api/catalog?brand_id=kps&branch_id=kps-branch-1&service=pickup'
    before = client.get(path)
    assert before.status_code == 200
    assert before.headers['cache-control'] == 'no-store'
    product_id = 'kps-01'
    assert next(p for p in before.json()['products'] if p['id'] == product_id)['available']
    with db.tx() as conn:
        conn.execute("UPDATE branches SET settings=jsonb_set(settings,'{product_availability}',%s) WHERE id='kps-branch-1'",
                     (db.json({product_id: False}),))
    stopped = client.get(path)
    assert stopped.status_code == 200
    assert not next(p for p in stopped.json()['products'] if p['id'] == product_id)['available']
    assert client.get('/api/catalog?brand_id=missing-brand&branch_id=kps-branch-1').status_code == 404
    assert client.get('/api/catalog?brand_id=kps&branch_id=missing-branch').status_code == 404
    with db.tx() as conn:
        conn.execute("UPDATE brands SET active=false WHERE id='kps'")
    assert client.get(path).status_code == 404


def test_queued_guest_audit_failure_rolls_back_identity_and_cookie_but_preserves_abuse_guard(checkout):
    client, user = checkout
    phone = '0509876543'
    normalized = auth.normalize_phone(phone)
    body = {'brand_id': user['brand_id'], 'display_name': 'TEST queued guest rollback', 'phone': phone}
    with db.tx() as conn:
        before_sessions = conn.execute('SELECT count(*) n FROM sessions').fetchone()['n']
        conn.execute("CREATE FUNCTION test_reject_guest_audit() RETURNS trigger LANGUAGE plpgsql AS $$ "
                     "BEGIN IF NEW.action='session.created' THEN RAISE EXCEPTION 'TEST audit rejection' USING ERRCODE='23514'; "
                     "END IF; RETURN NEW; END $$")
        conn.execute('CREATE TRIGGER reject_guest_audit BEFORE INSERT ON audit_events FOR EACH ROW EXECUTE FUNCTION test_reject_guest_audit()')
    with TestClient(client.app, base_url='http://testserver', headers={'Origin': 'http://testserver'}) as fresh:
        failed = fresh.post('/api/identity/guest', json=body)
        assert failed.status_code == 503, failed.text
        assert failed.json()['detail']['code'] == 'SERVICE_UNAVAILABLE'
        assert fresh.cookies.get(auth.COOKIE) is None and fresh.cookies.get(auth.CSRF_COOKIE) is None
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) n FROM sessions').fetchone()['n'] == before_sessions
            assert conn.execute('SELECT id FROM users WHERE brand_id=%s AND phone=%s', (user['brand_id'], normalized)).fetchone() is None
            bucket = hashlib.sha256(('guest-create-contact:' + user['brand_id'] + ':' + auth.token_hash(normalized)).encode()).hexdigest()
            assert conn.execute('SELECT hits FROM rate_limits WHERE bucket=%s ORDER BY window_start DESC LIMIT 1', (bucket,)).fetchone()['hits'] == 1
            conn.execute('DROP TRIGGER reject_guest_audit ON audit_events')
        retried = fresh.post('/api/identity/guest', json=body)
        assert retried.status_code == 200, retried.text
        assert fresh.cookies.get(auth.COOKIE) and fresh.cookies.get(auth.CSRF_COOKIE)
        with db.tx() as conn:
            rows = conn.execute('SELECT id FROM users WHERE brand_id=%s AND phone=%s', (user['brand_id'], normalized)).fetchall()
            assert len(rows) == 1
            assert conn.execute('SELECT count(*) n FROM sessions WHERE user_id=%s', (rows[0]['id'],)).fetchone()['n'] == 1
            assert conn.execute("SELECT count(*) n FROM audit_events WHERE actor_id=%s AND action='session.created'", (rows[0]['id'],)).fetchone()['n'] == 1
