from copy import deepcopy

import pytest

from app import admin, catalog
from app.enbishan_operations_preparation import prepare_branch_patches, apply_isolated
from app.enbishan_operations_preparation import prepared_hours
from app import service_hours
from datetime import datetime


def source(identifier='kps-1', fee=500):
    return {'id': identifier, 'brand_id': 'kps', 'settings': {
        'delivery_fee_halalas': fee, 'minimum_order_halalas': 2000,
        'opening_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
        'delivery_hours': [{'days': list(range(7)), 'start': '16:00', 'end': '04:00'}],
        'coverage': {'private': 'never copy'}, 'online_payment_seller': 'kps-private'}}


def target(number=1):
    return {'id': f'enbishan-{number}', 'brand_id': 'enbishan', 'branch_no': number, 'settings': {}}


def test_beverages_last_preserves_food_combo_order_prices_and_kps_order():
    products = [{'id':'pepsi','category':'المشروبات','price_halalas':250},
                {'id':'leaves','category':'محاشي','price_halalas':1200},
                {'id':'mojito-combo','category':'أصناف أخرى','price_halalas':1950},
                {'id':'water','category':'المشروبات','price_halalas':100},
                {'id':'kibbeh','category':'مقليات','price_halalas':1400}]
    before = deepcopy(products)
    ordered = catalog.menu_display_order(products, 'enbishan')
    assert [p['id'] for p in ordered] == ['leaves','mojito-combo','kibbeh','pepsi','water']
    assert products == before
    assert {p['id']:p['price_halalas'] for p in ordered} == {p['id']:p['price_halalas'] for p in products}
    assert catalog.menu_display_order(products,'kps') is products


def test_independent_snapshot_preserves_owner_edits_and_never_copies_sensitive_settings():
    origin, destination = source(), target()
    first = prepare_branch_patches([origin], [destination])[0]
    settings = first['body']['settings']
    assert settings['delivery_fee_halalas'] == 500
    assert not settings['delivery_enabled'] and not settings['pickup_enabled'] and not settings['car_enabled']
    assert 'coverage' not in settings and 'online_payment_seller' not in settings
    origin['settings']['delivery_hours'][0]['start'] = '12:00'
    assert settings['delivery_hours'][0]['start'] == '15:00'
    destination['settings'] = deepcopy(settings)
    destination['settings']['delivery_fee_halalas'] = 700
    destination['settings']['delivery_hours'][0]['start'] = '17:00'
    next_patch = prepare_branch_patches([origin], [destination])[0]['body']['settings']
    assert 'delivery_fee_halalas' not in next_patch and 'delivery_hours' not in next_patch


def test_differing_kps_fees_require_explicit_source_selection():
    with pytest.raises(ValueError, match='settings differ'):
        prepare_branch_patches([source(), source('kps-2', 900)], [target()])
    patch = prepare_branch_patches([source(), source('kps-2', 900)], [target()], source_by_branch={'enbishan-1': 'kps-2'})[0]
    assert patch['body']['settings']['delivery_fee_halalas'] == 900


@pytest.mark.parametrize('clock,expected', [('2026-10-22T14:59:59', False),
    ('2026-10-22T15:00:00', True), ('2026-10-23T02:59:59', True),
    ('2026-10-23T03:00:00', False), ('2026-10-23T09:00:00', False)])
def test_owner_evening_hours_and_dormant_morning_slot(clock, expected):
    intervals = prepared_hours()
    admin.hours(intervals)
    assert service_hours.hours_open(intervals, datetime.fromisoformat(clock)) is expected
    assert intervals[1]['enabled'] is False and intervals[1]['start'] is None
    # Enbishan preparation never changes the shared KPS defaults.
    assert service_hours.DEFAULT_DELIVERY_HOURS == [
        {'days': list(range(7)), 'start': '16:00', 'end': '04:00'}]


def test_owner_can_prepare_and_enable_morning_without_changing_evening():
    intervals = prepared_hours()
    intervals[1].update(start='08:00', end='12:00')
    admin.hours(intervals)
    now = datetime.fromisoformat('2026-10-23T09:00:00')
    assert not service_hours.hours_open(intervals, now)
    intervals[1]['enabled'] = True
    admin.hours(intervals)
    assert service_hours.hours_open(intervals, now)
    assert service_hours.hours_open(intervals, datetime.fromisoformat('2026-10-23T22:00:00'))
    assert not service_hours.hours_open(intervals, datetime.fromisoformat('2026-10-23T13:00:00'))


@pytest.mark.parametrize('clock,expected', [
    ('2026-10-22T08:59:59', False), ('2026-10-22T09:00:00', True),
    ('2026-10-22T11:59:59', True), ('2026-10-22T12:00:00', False),
    ('2026-10-23T10:00:00', False), ('2026-10-24T10:00:00', True),
    ('2026-10-23T22:00:00', True),
])
def test_branch_one_approved_morning_excludes_friday_and_keeps_evening(clock, expected):
    patches = prepare_branch_patches([source()], [target(1), target(2)])
    branch_one = patches[0]['body']['settings']
    branch_two = patches[1]['body']['settings']
    for field in ('opening_hours', 'delivery_hours'):
        admin.hours(branch_one[field])
        assert service_hours.hours_open(branch_one[field], datetime.fromisoformat(clock)) is expected
        assert branch_two[field] == prepared_hours()
    assert branch_one['delivery_enabled'] is False
    assert branch_one['opening_hours'] is not branch_one['delivery_hours']


@pytest.mark.parametrize('period', [
    {'days':[0], 'start':None, 'end':None, 'enabled':True},
    {'days':[0], 'start':'08:00', 'end':None, 'enabled':False},
    {'days':[0], 'start':'08:00', 'end':'12:00', 'enabled':'false'},
    {'days':[], 'start':None, 'end':None, 'enabled':False},
    {'days':[True], 'start':None, 'end':None, 'enabled':False},
])
def test_invalid_prepared_period_cannot_be_saved(period):
    with pytest.raises(Exception) as error:
        admin.hours([period])
    assert error.value.status_code == 422


def test_same_city_coverage_and_fees_are_independent_owner_editable_snapshots():
    hfr, qsm = source('kps-hfr', 500), source('kps-qsm', 700)
    hfr['city'], qsm['city'] = 'حفر الباطن', 'القيصومة'
    polygon = {'type':'polygon', 'coordinates':[[45.8,28.3],[46.0,28.3],[45.9,28.5]]}
    radius = {'type':'radius', 'center':{'lat':28.3,'lng':46.1}, 'radius_meters':3000}
    hfr['settings']['coverage'], qsm['settings']['coverage'] = polygon, radius
    destinations = [target(), target(4)]
    destinations[0]['city'], destinations[1]['city'] = 'حفرالباطن', 'القيصومة'
    before = deepcopy([hfr,qsm])
    patches = prepare_branch_patches([hfr,qsm], destinations, copy_city_coverage=True)
    assert patches[0]['body']['settings']['coverage'] == polygon
    assert patches[1]['body']['settings']['coverage'] == radius
    assert patches[1]['body']['settings']['delivery_fee_halalas'] == 700
    assert all(p['body']['settings']['delivery_enabled'] is False for p in patches)
    patches[0]['body']['settings']['coverage']['coordinates'][0][0] = 45.7
    assert [hfr,qsm] == before
    destinations[0]['settings'] = deepcopy(patches[0]['body']['settings'])
    rerun = prepare_branch_patches([hfr,qsm], destinations[:1], copy_city_coverage=True)[0]
    assert 'coverage' not in rerun['body']['settings'] and 'delivery_hours' not in rerun['body']['settings']


def test_coverage_never_uses_a_different_city_or_guesses_between_conflicting_polygons():
    first, second = source('kps-hfr'), source('kps-other')
    first['city'], second['city'] = 'حفر الباطن', 'القيصومة'
    first['settings']['coverage'] = {'type':'radius','center':{'lat':28.4,'lng':45.9},'radius_meters':1000}
    second['settings']['coverage'] = {'type':'radius','center':{'lat':28.3,'lng':46.1},'radius_meters':2000}
    destination = target();destination['city'] = 'حفرالباطن'
    with pytest.raises(ValueError, match='same-city'):
        prepare_branch_patches([second], [destination], copy_city_coverage=True)
    second['city'] = 'حفرالباطن'
    with pytest.raises(ValueError, match='coverage differs'):
        prepare_branch_patches([first,second], [destination], copy_city_coverage=True)
    selected = prepare_branch_patches([first,second], [destination],
        source_by_branch={destination['id']:first['id']}, copy_city_coverage=True)[0]
    assert selected['body']['settings']['coverage'] == first['settings']['coverage']


@pytest.mark.parametrize('mutation', ['wrong_source_brand', 'wrong_target_brand', 'unapproved_branch', 'live_branch', 'duplicate_target'])
def test_fail_closed_scope_and_live_branch_protection(mutation):
    s, t = source(), target()
    targets = [t]
    if mutation == 'wrong_source_brand': s['brand_id'] = 'enbishan'
    if mutation == 'wrong_target_brand': t['brand_id'] = 'kps'
    if mutation == 'unapproved_branch': t['branch_no'] = 5
    if mutation == 'live_branch': t['settings']['delivery_enabled'] = True
    if mutation == 'duplicate_target': targets.append(deepcopy(t))
    with pytest.raises(ValueError): prepare_branch_patches([s], targets)


def test_four_branch_city_scope_does_not_widen_kps():
    for number, city in [(1, 'حفر الباطن'), (2, 'حفرالباطن'), (3, 'الدمام'), (4, 'القيصومة')]:
        branch = {'brand_id': 'enbishan', 'branch_no': number, 'city': city}
        assert catalog.delivery_city_eligible(branch)
        admin.validate_branch_settings({'delivery_enabled': True}, city, brand_id='enbishan', branch_no=number)
    assert not catalog.delivery_city_eligible({'brand_id': 'kps', 'branch_no': 3, 'city': 'الدمام'})
    for number, city in [(3, 'حفر الباطن'), (5, 'القيصومة'), (True, 'حفر الباطن')]:
        assert not catalog.delivery_city_eligible({'brand_id': 'enbishan', 'branch_no': number, 'city': city})
        with pytest.raises(Exception):
            admin.validate_branch_settings({'delivery_enabled': True}, city, brand_id='enbishan', branch_no=number)


def test_application_rejects_nonisolated_execution_before_database_access(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'production')
    with pytest.raises(RuntimeError, match='local-test-only'):
        apply_isolated(None, 'owner', ['kps-1'], ['enbishan-1'])


def test_explicit_invoice_profile_is_independent_and_preserves_owner_changes():
    from app.enbishan_operations_preparation import prepare_invoice_profiles
    from app import fiscal
    before = deepcopy(fiscal.SELLER)
    prepared = prepare_invoice_profiles([target(i) for i in range(1, 5)])
    assert len(prepared['assignments']) == 4
    assert prepared['entities']['reem']['address'] == fiscal.SELLER['address']
    assert prepared['entities']['reem']['vat_enabled'] is True
    prepared['entities']['reem']['address'] = 'Owner edited address'
    rerun = prepare_invoice_profiles([target()], prepared)
    assert rerun['entities']['reem']['address'] == 'Owner edited address'
    assert fiscal.SELLER == before
    with pytest.raises(ValueError): prepare_invoice_profiles([{'id': 'kps-1', 'brand_id': 'kps', 'branch_no': 1}])
