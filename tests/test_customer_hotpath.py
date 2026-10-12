"""Fresh catalog visibility and atomic rate controls after round-trip reduction."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import hashlib

import pytest

from app import db

pytestmark = pytest.mark.postgres


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
