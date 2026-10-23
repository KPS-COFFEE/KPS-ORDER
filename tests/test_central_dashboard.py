"""Weighted central report and fresh grant witnesses."""
from copy import deepcopy

import pytest

from app import db
from app.central_dashboard import merge_dashboards
from test_enbishan_dual_brand import dual


def part(valid, average):
    return {'period': {'start': '2026-10-23T16:00:00+03:00'},
            'summary': {'orders': valid, 'net_completed_halalas': valid * 1000},
            'delivery_time': {'valid': valid, 'excluded': 0, 'average_seconds': average,
                              'delayed': 1, 'threshold_minutes': 60, 'threshold_configured': True,
                              'classification_basis': 'frozen_province_policy'},
            'delivery_performance': {'valid': valid, 'average_seconds': average, 'excluded': 0,
                                     'classifications': {'good': valid}, 'live': {'good': 0},
                                     'completed': {'good': valid}, 'total_orders': valid,
                                     'basis': 'driver_pickup_to_customer_delivery'},
            'collections': [{'method': 'cash', 'amount_halalas': valid * 500}],
            'top_items': [], 'top_branches': [], 'shifts': []}


def test_weighted_valid_observations_and_money_are_combined_without_mutating_sources():
    rows = [part(1, 600), part(3, 120)]
    original = deepcopy(rows)
    result = merge_dashboards(rows)
    assert result['delivery_time']['average_seconds'] == 240
    assert result['delivery_performance']['average_seconds'] == 240
    assert result['delivery_performance']['classifications'] == {'good': 4}
    assert result['summary']['net_completed_halalas'] == 4000
    assert result['collections'] == [{'method': 'cash', 'amount_halalas': 2000}]
    assert rows == original


def test_no_valid_observations_stays_unknown_and_mixed_threshold_is_not_fabricated():
    rows = [part(0, None), part(0, None)]
    rows[1]['delivery_time']['threshold_minutes'] = 30
    result = merge_dashboards(rows)
    assert result['delivery_time']['average_seconds'] is None
    assert result['delivery_time']['threshold_minutes'] is None


@pytest.fixture
def central(dual, monkeypatch):
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED', 'true')
    return dual


@pytest.mark.postgres
def test_central_dashboard_combines_orders_and_rechecks_explicit_foreign_owner_grants(central):
    _, actor, _, _, order = central
    order('kps')
    remote = order('enbishan')
    response = actor('owner').get('/api/central-operations/dashboard')
    assert response.status_code == 200, response.text
    assert response.json()['summary']['orders'] == 2
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids='[\"enbishan-test-2\"]' WHERE user_id='dual-owner' AND brand_id='enbishan'")
    result = actor('owner').get('/api/central-operations/dashboard')
    assert result.status_code == 200, result.text
    assert result.json()['summary']['orders'] == 1
    denied = actor('owner').get('/api/central-operations/dashboard?branch_id=enbishan-test-1')
    assert denied.status_code == 403, denied.text
    # The original source row remains untouched by reporting.
    with db.tx() as c:
        assert c.execute('SELECT id FROM orders WHERE id=%s', (remote,)).fetchone()


@pytest.mark.postgres
def test_central_dashboard_counts_one_physical_manager_workday(central):
    _, actor, post, _, _ = central
    post('manager', '/api/central-operations/workdays', {'kind': 'manager'})
    response = actor('manager').get('/api/central-operations/dashboard')
    assert response.status_code == 200, response.text
    assert sum(row['count'] for row in response.json()['shifts']) == 1


@pytest.mark.postgres
def test_disabled_and_customer_central_dashboard_remain_unavailable(dual):
    _, actor, _, _, _ = dual
    assert actor('manager').get('/api/central-operations/dashboard').status_code == 404
    assert actor('enbishan-customer').get('/api/central-operations/dashboard').status_code == 404
