"""Only geographic exclusion outranks an operational service failure."""
from copy import deepcopy

import pytest

from app import catalog
from tests.test_launch_branches import branch


@pytest.mark.parametrize('settings', [
    {}, {'active': False}, {'delivery_enabled': False},
    {'delivery_hours': []}, {'opening_hours': []}, {'location_verified': False},
])
def test_rafha_address_outside_known_other_branch_has_consistent_reason(settings):
    row = branch(**settings)
    original = deepcopy(row)
    result = catalog.rank_nearest_branches([row], {'lat': 29.63, 'lng': 43.50}, 'delivery', {})
    assert result['branches'] == []
    assert result['excluded'][0]['code'] == 'OUTSIDE_COVERAGE'
    assert result['excluded'][0]['reason'] == 'العنوان خارج نطاق توصيل هذا الفرع.'
    assert row == original


@pytest.mark.parametrize('settings,code', [
    ({'active': False}, 'BRANCH_DISABLED'),
    ({'delivery_enabled': False}, 'SERVICE_DISABLED'),
    ({'delivery_hours': []}, 'DELIVERY_HOURS_UNCONFIGURED'),
])
def test_inside_boundary_preserves_actual_service_failure(settings, code):
    result = catalog.rank_nearest_branches([branch(**settings)], {'lat': 28, 'lng': 45}, 'delivery', {})
    assert result['excluded'][0]['code'] == code


@pytest.mark.parametrize('coverage', [None, {}, {'type': 'polygon', 'coordinates': []},
    {'type': 'radius', 'center': {'lat': 28, 'lng': 45}, 'radius_meters': -1}])
def test_unknown_boundary_is_not_reported_as_known_outside(coverage):
    row = branch(coverage=coverage, delivery_enabled=False)
    result = catalog.rank_nearest_branches([row], {'lat': 29.63, 'lng': 43.5}, 'delivery', {})
    assert result['excluded'][0]['code'] == 'SERVICE_DISABLED'


def test_non_delivery_city_without_boundary_is_explicit_not_pickup_suggestion():
    row = branch(coverage=None)
    row['city'] = 'الدمام'
    result = catalog.rank_nearest_branches([row], {'lat': 29.63, 'lng': 43.5}, 'delivery', {})
    assert result['excluded'][0]['code'] == 'DELIVERY_CITY_NOT_ELIGIBLE'
    assert result['excluded'][0]['reason'] == 'هذا الفرع غير مشمول بخدمة التوصيل حاليًا.'


def test_inside_rafha_polygon_remains_eligible_and_pickup_ignores_coverage():
    row = branch(coverage={'type': 'polygon', 'coordinates': [[43.4,29.5],[43.7,29.5],[43.7,29.8],[43.4,29.8]]})
    row['city'] = 'رفحا'
    assert catalog.rank_nearest_branches([row], {'lat':29.63,'lng':43.5}, 'delivery', {})['branches']
    assert catalog.rank_nearest_branches([row], {'lat':28,'lng':45}, 'pickup', {})['branches']
