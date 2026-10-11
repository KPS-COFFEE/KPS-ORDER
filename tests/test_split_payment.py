"""Real PostgreSQL split tender, replay, refund and custody acceptance."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from app import db
from test_v5_operations import v5

pytestmark = pytest.mark.postgres


def new_order(v5):
    _, call, body, _ = v5
    response = call('customer', 'POST', '/api/orders', body() | {'payment_method': 'split'})
    assert response.status_code == 200, response.text
    return response.json()['order']['id']


def split_body():
    return {'method': 'split', 'amount_halalas': 1000, 'cash_halalas': 400,
            'card_halalas': 600, 'tendered_halalas': 500, 'reference': 'TEST-CARD-REF'}


def test_atomic_split_replay_refund_limits_and_no_double_count(v5):
    _, call, _, _ = v5
    oid = new_order(v5)
    data = split_body()
    key = uuid4().hex
    first = call('branch', 'POST', f'/api/orders/{oid}/collect', data, key)
    assert first.status_code == 200, first.text
    assert first.json()['change_halalas'] == 100
    assert len(first.json()['collection_ids']) == 2
    assert call('branch', 'POST', f'/api/orders/{oid}/collect', data, key).json() == first.json()
    assert call('branch', 'POST', f'/api/orders/{oid}/collect', data).status_code == 409
    with db.tx() as c:
        rows = c.execute('SELECT * FROM collections WHERE order_id=%s ORDER BY method', (oid,)).fetchall()
        assert [(r['method'], r['amount_halalas']) for r in rows] == [('card', 600), ('cash', 400)]
        assert rows[0]['tendered_halalas'] is None
        assert rows[1]['change_halalas'] == 100
    r = {'method': 'cash', 'amount_halalas': 401, 'reason': 'TEST refund'}
    assert call('owner', 'POST', f'/api/orders/{oid}/refund', r).status_code == 422
    assert call('owner', 'POST', f'/api/orders/{oid}/refund', r | {'amount_halalas': 400}).json()['payment_status'] == 'partially_refunded'
    assert call('owner', 'POST', f'/api/orders/{oid}/refund', r | {'amount_halalas': 1}).status_code == 422
    assert call('owner', 'POST', f'/api/orders/{oid}/refund', r | {'method': 'card', 'amount_halalas': 600}).json()['payment_status'] == 'refunded'
    assert call('owner', 'POST', f'/api/orders/{oid}/refund', r | {'method': 'card', 'amount_halalas': 1}).status_code == 422


@pytest.mark.parametrize('patch', [
    {'cash_halalas': 0}, {'card_halalas': -1}, {'cash_halalas': 401},
    {'cash_halalas': None}, {'card_halalas': 600.5}, {'reference': ' '},
    {'tendered_halalas': 399}, {'amount_halalas': 999},
    {'method': 'cash'}, {'method': 'unknown'},
])
def test_invalid_split_writes_nothing(v5, patch):
    _, call, _, _ = v5
    oid = new_order(v5)
    response = call('branch', 'POST', f'/api/orders/{oid}/collect', split_body() | patch)
    assert response.status_code == 422, response.text
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s', (oid,)).fetchone()['n'] == 0
        assert c.execute('SELECT payment_status FROM orders WHERE id=%s', (oid,)).fetchone()['payment_status'] == 'unpaid'


def test_split_requires_both_branch_methods(v5):
    _, call, body, _ = v5
    with db.tx() as c:
        c.execute("UPDATE branches SET settings=jsonb_set(settings,'{payment_methods}','[\"cash\"]') WHERE id='v5-a'")
    r = call('customer', 'POST', '/api/quote', body() | {'payment_method': 'split'})
    assert r.status_code == 409, r.text


def test_simultaneous_collection_only_one_wins(v5):
    actor, _, _, _ = v5
    oid = new_order(v5)
    client = actor('branch')
    def run(_):
        return client.post(f'/api/orders/{oid}/collect', json=split_body(), headers={'Idempotency-Key': uuid4().hex}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, range(2))) == [200, 409]
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s', (oid,)).fetchone()['n'] == 2


def test_delivery_split_only_cash_enters_custody(v5):
    _, call, body, _ = v5
    manager = call('manager', 'POST', '/api/shifts', {'kind': 'manager', 'branch_ids': ['v5-a']}).json()['shift']['id']
    driver = call('driver', 'POST', '/api/shifts', {'kind': 'driver', 'branch_ids': ['v5-a'], 'manager_shift_id': manager}).json()['shift']['id']
    oid = call('customer', 'POST', '/api/orders', body('delivery') | {'payment_method': 'split'}).json()['order']['id']
    tid = call('manager', 'POST', f'/api/orders/{oid}/assign', {'driver_shift_id': driver}).json()['task']['id']
    data = split_body() | {'amount_halalas': 1200, 'card_halalas': 800}
    assert call('driver', 'POST', f'/api/orders/{oid}/collect', data).status_code == 409
    assert call('owner', 'POST', f'/api/orders/{oid}/collect', data).status_code == 403
    with db.tx() as c:
        c.execute("UPDATE driver_tasks SET status='picked_up' WHERE id=%s", (tid,))
        c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s", (oid,))
    assert call('driver', 'POST', f'/api/orders/{oid}/collect', data).status_code == 200
    shift = call('driver', 'GET', f'/api/shifts/{driver}').json()
    assert shift['expected_halalas'] == 400
    assert len(shift['collections']) == 2
    dashboard = call('owner', 'GET', '/api/dashboard').json()
    assert {r['method']: r['amount_halalas'] for r in dashboard['collections']} == {'cash': 400, 'card': 800}
