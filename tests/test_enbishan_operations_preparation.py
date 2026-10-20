from copy import deepcopy

import pytest

from app import admin, catalog
from app.enbishan_operations_preparation import prepare_branch_patches, apply_isolated


def source(identifier='kps-1', fee=500):
    return {'id': identifier, 'brand_id': 'kps', 'settings': {
        'delivery_fee_halalas': fee, 'minimum_order_halalas': 2000,
        'opening_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
        'delivery_hours': [{'days': list(range(7)), 'start': '16:00', 'end': '04:00'}],
        'coverage': {'private': 'never copy'}, 'online_payment_seller': 'kps-private'}}


def target(number=1):
    return {'id': f'enbishan-{number}', 'brand_id': 'enbishan', 'branch_no': number, 'settings': {}}


def test_independent_snapshot_preserves_owner_edits_and_never_copies_sensitive_settings():
    origin, destination = source(), target()
    first = prepare_branch_patches([origin], [destination])[0]
    settings = first['body']['settings']
    assert settings['delivery_fee_halalas'] == 500
    assert not settings['delivery_enabled'] and not settings['pickup_enabled'] and not settings['car_enabled']
    assert 'coverage' not in settings and 'online_payment_seller' not in settings
    origin['settings']['delivery_hours'][0]['start'] = '12:00'
    assert settings['delivery_hours'][0]['start'] == '16:00'
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
