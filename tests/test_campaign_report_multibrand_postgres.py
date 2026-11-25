"""Real sessions and enabled brand projection protect final report replays."""
import pytest

from app import auth, db, storefront_scope
from starlette.requests import Request
from test_enbishan_dual_brand import dual


PATH = '/api/reports/promotions/closures'
BODY = {'start': '2026-10-22', 'end': '2026-10-22',
        'reason': 'SYNTHETIC complete scoped period'}
KEY = 'SYNTHETIC-same-owner-same-period-key'


@pytest.mark.parametrize('path', [PATH, PATH + '/synthetic-id', PATH + '/synthetic-id/download'])
def test_final_report_routes_own_the_work_boundary_without_a_caller_header(path):
    request = Request({'type': 'http', 'scheme': 'http', 'method': 'GET',
                       'path': path, 'query_string': b'', 'headers': []})
    assert auth.session_context(request) == 'work'
    assert storefront_scope.forced_work_context(request)
    assert not storefront_scope.is_public_brand_route(path)


@pytest.mark.postgres
def test_real_projected_owner_same_key_isolated_and_revoked_membership_cannot_replay(dual, monkeypatch):
    _, actor, _, _, order = dual
    # Bind the deployment's public host while private report routes retain
    # their endpoint-owned staff boundary and fresh explicit membership.
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://order.kps.sa')
    orders = {brand: order(brand) for brand in ('kps', 'enbishan')}
    with db.tx() as conn:
        for brand, identifier in orders.items():
            conn.execute('UPDATE orders SET created_at=%s WHERE id=%s',
                         ('2026-10-22T09:00:00+00:00', identifier))
            conn.execute('INSERT INTO first_order_gift_claims'
                         '(brand_id,phone,order_id,customer_id,policy_version) VALUES(%s,%s,%s,%s,1)',
                         (brand, '+966500009999', identifier, 'dual-' + brand + '-customer'))

    results = {}
    for brand in ('kps', 'enbishan'):
        client = actor('owner', brand)
        created = client.post(PATH, json=BODY, headers={'Idempotency-Key': KEY})
        assert created.status_code == 200, created.text
        results[brand] = created.json()
        assert results[brand]['brand_id'] == brand
        assert results[brand]['beneficiary_count'] == 1
        identifier = results[brand]['closure']['id']
        frozen = client.get(PATH + '/' + identifier)
        assert frozen.status_code == 200, frozen.text
        saved = frozen.json()
        assert saved['closure']['brand_id'] == saved['report']['brand_id'] == brand
        assert saved['report']['summary']['online_orders'] == 1
        assert {row['order_id'] for row in saved['beneficiaries']} == {orders[brand]}
        assert saved['beneficiaries'][0]['phone_suffix'] == '••••9999'

    assert results['kps']['closure']['id'] != results['enbishan']['closure']['id']
    for brand, foreign in (('kps', 'enbishan'), ('enbishan', 'kps')):
        client = actor('owner', brand)
        replay = client.post(PATH, json=BODY, headers={'Idempotency-Key': KEY})
        assert replay.status_code == 200 and replay.json() == results[brand]
        assert client.get(PATH + '/' + results[foreign]['closure']['id']).status_code == 404
        assert client.get(PATH + '/' + results[foreign]['closure']['id'] + '/download').status_code == 404
        listing = client.get(PATH).json()
        assert listing['brand_id'] == brand
        assert [row['id'] for row in listing['closures']] == [results[brand]['closure']['id']]

    with db.tx() as conn:
        replay_rows = conn.execute('SELECT operation,response FROM idempotency_keys '
                                   'WHERE user_id=%s AND key=%s ORDER BY operation',
                                   ('dual-owner', KEY)).fetchall()
        assert {row['operation'] for row in replay_rows} == {
            'campaign.report.finalize', 'brand:enbishan:campaign.report.finalize'}
        assert {row['response']['brand_id'] for row in replay_rows} == {'kps', 'enbishan'}
        assert conn.execute('SELECT count(*) n FROM promotion_report_closures').fetchone()['n'] == 2
        conn.execute("UPDATE staff_brand_memberships SET active=false "
                     "WHERE user_id='dual-owner' AND brand_id='enbishan'")

    client = actor('owner', 'enbishan')
    for result in (client.post(PATH, json=BODY, headers={'Idempotency-Key': KEY}),
                   client.get(PATH),
                   client.get(PATH + '/' + results['enbishan']['closure']['id']),
                   client.get(PATH + '/' + results['enbishan']['closure']['id'] + '/download')):
        assert result.status_code == 403
        assert result.json()['detail']['code'] == 'OPERATIONS_BRAND_FORBIDDEN'
    client = actor('owner', 'kps')
    assert client.post(PATH, json=BODY, headers={'Idempotency-Key': KEY}).json() == results['kps']
    assert client.get(PATH + '/' + results['kps']['closure']['id']).status_code == 200
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM promotion_report_closures').fetchone()['n'] == 2
        assert conn.execute("SELECT count(*) n FROM audit_events WHERE action='campaign.report.finalized'").fetchone()['n'] == 2
