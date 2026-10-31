"""Launch branch selection: actual destination identity and server eligibility."""
from copy import deepcopy
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import catalog, db


def branch(number=1, lat=28.0, **changes):
    hours = [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}]
    settings = {'active': True, 'pickup_enabled': True, 'car_enabled': True,
                'delivery_enabled': True, 'ordering_verified': True,
                'location_verified': True, 'location': {'lat': lat, 'lng': 45.0},
                'opening_hours': hours, 'delivery_hours': hours,
                'payment_methods': ['cash', 'card'], 'delivery_fee_halalas': 200,
                'coverage': {'type': 'radius', 'center': {'lat': lat, 'lng': 45.0},
                             'radius_meters': 50_000}}
    return {'id': f'launch-branch-{number}', 'brand_id': 'launch-branches',
            'branch_no': number, 'name': f'TEST ONLY branch {number}', 'city': 'حفر الباطن',
            'settings': settings | changes}


def product(product_id='launch-product', **changes):
    return {'id': product_id, 'brand_id': 'launch-branches', 'name': 'TEST ONLY coffee',
            'category': 'TEST ONLY', 'price_halalas': 1000,
            'settings': {'enabled': True, 'source_verified': True} | changes}


@pytest.mark.parametrize('url', [
    'https://www.google.com/maps/@28.4,45.9,13z',
    'https://www.google.com/maps',
    'https://www.google.com/maps/search/?api=1&query=KPS+COFFEE',
    'https://www.google.com/maps/place/KPS+COFFEE/',
    'https://www.google.com/maps/?cid=12345&review=1',
    'https://www.google.com/maps/reviews/?cid=12345',
    'https://www.google.com/maps/?cid=12345&lrd=123:456,3',
    'https://www.google.com/maps/dir/?api=1&destination=KPS+COFFEE',
    'https://www.google.com/maps/dir/?api=1&destination=999,45',
    'https://@www.google.com/maps/dir/?api=1&destination=28,45',
])
def test_review_search_and_viewport_links_are_not_verified_branch_destinations(url):
    assert catalog.safe_map_url(url) is None


@pytest.mark.parametrize('url', [
    'https://www.google.com/maps/dir/?api=1&destination=28.4,45.9',
    'https://www.google.com/maps/search/?api=1&query=28.4,45.9',
    'https://www.google.com/maps/place/KPS%20COFFEE/data=!3d28.4!4d45.9',
    'https://www.google.com/maps/place/KPS/data=!1s0x123456:0xabcde!8m2',
    'https://maps.google.com/?cid=123456789',
    'https://www.google.com/maps/dir/?api=1&destination=coffee&destination_place_id=ChIJTESTONLYPLACE1234',
    'https://maps.app.goo.gl/TESTONLY1234',
    'https://goo.gl/maps/TESTONLY1234',
])
def test_maps_accept_pins_place_identity_and_owner_verifiable_short_links(url):
    assert catalog.safe_map_url(url) == url


def test_verified_place_id_precedes_coordinate_fallback_and_branch_numbers_do_not_mix():
    one = branch(1, 28.45, place_id='ChIJTESTONLYPLACE0001', map_url_verified=True)
    nine = branch(9, 28.46, place_id='ChIJTESTONLYPLACE0009', map_url_verified=True)
    q1 = parse_qs(urlsplit(catalog.directions_url(one)).query)
    q9 = parse_qs(urlsplit(catalog.directions_url(nine)).query)
    assert q1['destination_place_id'] == ['ChIJTESTONLYPLACE0001']
    assert q9['destination_place_id'] == ['ChIJTESTONLYPLACE0009']
    assert q1['destination'] != q9['destination']
    assert 'origin' not in q1 and q1['api'] == ['1']
    one['settings']['map_url_verified'] = False
    assert 'destination_place_id' not in parse_qs(urlsplit(catalog.directions_url(one)).query)
    one['settings']['location_verified'] = False
    assert catalog.directions_url(one) is None
    one['settings'].update(map_url='https://maps.app.goo.gl/TESTONLY1234', map_url_verified=True, place_id=None)
    assert catalog.directions_url(one) == 'https://maps.app.goo.gl/TESTONLY1234'


@pytest.mark.parametrize('url', [
    'http://example.com/launch', 'javascript:alert(1)', '//example.com/launch',
    'https://user:password@example.com/launch', 'https://@example.com/launch',
    'https://example.com:65536/launch', 'https://example.com:bad/launch',
    'https://bad_host.example/launch', 'https://-bad.example/launch',
    'https://example..com/launch', 'https://example.com/%0aevil',
    'https://example.com/\\evil', 'https://example.com/ bad',
])
def test_campaign_links_reject_credentials_controls_and_invalid_hosts(url):
    with pytest.raises(HTTPException) as exc:
        catalog.safe_campaign_url(url)
    assert exc.value.status_code == 422


@pytest.mark.parametrize('url', ['https://example.com/opening', 'https://example.com/a%20b',
                               'https://example.com:8443/opening?branch=1#about'])
def test_campaign_navigation_supports_https_text_links_without_fetching(url):
    assert catalog.safe_campaign_url(url) == url


def test_pickup_and_car_rank_only_open_verified_branches_without_requiring_delivery_coverage():
    today = datetime.now(catalog.RIYADH).weekday()
    closed_hours = [{'days': [(today + 1) % 7], 'start': '00:00', 'end': '00:00'}]
    rows = [branch(9, 28.1, coverage=None), branch(1, 28.0, coverage=None),
            branch(2, 28.01, opening_hours=closed_hours), branch(3, 28.02, active=False),
            branch(4, 28.03, location_verified=False), branch(5, 28.04, car_enabled=False)]
    result = catalog.rank_nearest_branches(rows, {'lat': 28, 'lng': 45}, 'car', {})
    assert [row['branch_no'] for row in result['branches']] == [1, 9]
    codes = {row['branch_no']: row['code'] for row in result['excluded']}
    assert codes == {2: 'BRANCH_CLOSED', 3: 'BRANCH_DISABLED', 4: 'BRANCH_LOCATION_UNVERIFIED', 5: 'SERVICE_DISABLED'}
    assert all(row['direct_distance_meters'] is None for row in result['excluded'])
    assert result['branches'][0]['direct_distance_meters'] == 0
    assert result['routing_provider'] is None
    assert 'ليست مسافة الطريق' in result['distance_label']
    assert all('eta' not in row and 'route_distance_meters' not in row for row in result['branches'])
    assert [row['branch_no'] for row in catalog.rank_nearest_branches(rows, {'lat': 28, 'lng': 45}, 'pickup', {})['branches']] == [1, 5, 9]


def test_delivery_does_not_suggest_the_nearest_branch_if_address_is_outside_its_coverage():
    nearest = branch(1, 28, coverage={'type': 'radius', 'center': {'lat': 29, 'lng': 45}, 'radius_meters': 50})
    farther = branch(9, 28.1)
    result = catalog.rank_nearest_branches([nearest, farther], {'lat': 28, 'lng': 45}, 'delivery', {})
    assert [row['branch_no'] for row in result['branches']] == [9]
    assert result['excluded'][0]['code'] == 'OUTSIDE_COVERAGE'
    assert not catalog.rank_nearest_branches([farther], {'lat': 28, 'lng': 45}, 'pickup', {'services': {'pickup': False}})['branches']


def test_nearest_reuses_commercial_approval_digest_instead_of_only_source_verified_flag():
    p = product(menu_approval={'effective_commercial_digest': 'stale-approval'})
    result = catalog.rank_nearest_branches([branch()], {'lat': 28, 'lng': 45}, 'pickup', {}, [p], {p['id']: p})
    assert result['branches'] == []
    missing = result['excluded'][0]['unavailable_products'][0]
    assert missing['code'] == 'CATALOG_SOURCE_UNVERIFIED'


def test_selected_classic_stock_is_respected_without_disabling_the_available_pecan_choice():
    parent = product('kps-33', unified_khaliya=True, option_groups=[
        {'code': 'TYPE', 'name': 'اختر النوع', 'required': True, 'min_select': 1, 'max_select': 1,
         'values': [{'code': 'classic', 'name': 'كلاسيك', 'source_product_id': 'kps-34'},
                    {'code': 'pecan', 'name': 'بيكان', 'source_product_id': 'kps-33'}]}])
    classic = product('kps-34', purchasable=False, unavailable_branch_ids=['launch-branch-1'])
    by_id = {row['id']: row for row in [parent, classic]}
    rows = [branch(1, 28), branch(9, 28.1)]
    original = deepcopy([parent, classic, rows])
    selected = {'kps-33': [{'options': {'TYPE': 'classic'}}]}
    result = catalog.rank_nearest_branches(rows, {'lat': 28, 'lng': 45}, 'pickup', {}, [parent], by_id, selected)
    assert [row['branch_no'] for row in result['branches']] == [9]
    assert result['excluded'][0]['unavailable_products'][0]['code'] == 'PRODUCT_VARIANT_UNAVAILABLE'
    pecan = catalog.rank_nearest_branches(rows, {'lat': 28, 'lng': 45}, 'pickup', {}, [parent], by_id,
                                          {'kps-33': [{'options': {'TYPE': 'pecan'}}]})
    assert [row['branch_no'] for row in pecan['branches']] == [1, 9]
    generic = catalog.rank_nearest_branches(rows, {'lat': 28, 'lng': 45}, 'pickup', {}, [parent], by_id)
    assert [row['branch_no'] for row in generic['branches']] == [1, 9]
    assert [parent, classic, rows] == original


@pytest.mark.parametrize('body', [
    {'brand_id': 'kps', 'service': 'other', 'location': {'lat': 28, 'lng': 45}},
    {'brand_id': 'kps', 'service': 'pickup', 'location': {'lat': 91, 'lng': 45}},
    {'brand_id': 'kps', 'service': 'pickup', 'location': {'lat': float('nan'), 'lng': 45}},
    {'brand_id': 'kps', 'service': 'pickup', 'location': {'lat': 28, 'lng': 45}, 'product_ids': ['x' * 81]},
])
def test_public_nearest_body_bounds_service_location_and_identifiers(body):
    with pytest.raises(ValidationError):
        catalog.PublicNearestBody.model_validate(body)


@pytest.fixture
def launch_branches(client):
    rows = [branch(1, 28), branch(9, 28.1)]
    p = product()
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('launch-branches','TEST ONLY launch branches')")
        for row in rows:
            conn.execute('INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                         (row['id'], row['branch_no'], 'TEST ONLY:' + row['id'], row['brand_id'], row['name'], row['city'], db.json(row['settings'])))
        conn.execute('INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,%s,%s,%s,%s,%s)',
                     (p['id'], p['brand_id'], p['name'], p['category'], p['price_halalas'], db.json(p['settings'])))
    return client, {'brand_id': 'launch-branches', 'service': 'pickup', 'location': {'lat': 28, 'lng': 45},
                    'product_ids': [p['id']]}


@pytest.mark.postgres
def test_public_nearest_is_guest_read_only_and_preserves_origin_protection(launch_branches):
    client, payload = launch_branches
    result = client.post('/api/branches/nearest', json=payload)
    assert result.status_code == 200, result.text
    assert [row['branch_no'] for row in result.json()['branches']] == [1, 9]
    assert result.headers.get('set-cookie') is None
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) AS count FROM orders').fetchone()['count'] == 0
        assert conn.execute('SELECT count(*) AS count FROM sessions').fetchone()['count'] == 0
    client.headers.pop('origin')
    assert client.post('/api/branches/nearest', json=payload).status_code == 403
    assert client.post('/api/branches/nearest', json=payload, headers={'Origin': 'https://evil.invalid'}).status_code == 403


@pytest.mark.postgres
def test_public_nearest_unknown_brand_and_cross_brand_products_fail_closed(launch_branches):
    client, payload = launch_branches
    assert client.post('/api/branches/nearest', json=payload | {'brand_id': 'missing-brand'}).status_code == 404
    invalid = client.post('/api/branches/nearest', json=payload | {'product_ids': ['kps-33']})
    assert invalid.status_code == 422 and invalid.json()['detail']['code'] == 'PRODUCT_NOT_FOUND'
    with db.tx() as conn:
        conn.execute("UPDATE brands SET active=false WHERE id='launch-branches'")
    assert client.post('/api/branches/nearest', json=payload).status_code == 404


@pytest.mark.postgres
def test_public_nearest_runtime_stock_service_and_coverage_are_rechecked(launch_branches):
    client, payload = launch_branches
    with db.tx() as conn:
        conn.execute("UPDATE products SET settings=settings || %s WHERE id='launch-product'",
                     (db.json({'unavailable_branch_ids': ['launch-branch-1']}),))
        conn.execute("UPDATE branches SET settings=settings || %s WHERE id='launch-branch-9'",
                     (db.json({'car_enabled': False, 'coverage': {'type': 'radius', 'center': {'lat': 29, 'lng': 45}, 'radius_meters': 10}}),))
    pickup = client.post('/api/branches/nearest', json=payload).json()
    assert [row['branch_no'] for row in pickup['branches']] == [9]
    assert pickup['excluded'][0]['unavailable_products'][0]['product_id'] == 'launch-product'
    assert client.post('/api/branches/nearest', json=payload | {'service': 'car'}).json()['branches'] == []
    delivery = client.post('/api/branches/nearest', json=payload | {'service': 'delivery'}).json()
    assert delivery['branches'] == []
    assert next(row for row in delivery['excluded'] if row['branch_no'] == 9)['code'] == 'OUTSIDE_COVERAGE'
    db.migrate()
    assert client.post('/api/branches/nearest', json=payload).json() == pickup
