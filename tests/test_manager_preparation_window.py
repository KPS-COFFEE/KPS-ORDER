"""Manager preparation never advances customer or driver admission."""
from copy import deepcopy
from datetime import datetime

import pytest
from fastapi import HTTPException

from app import morning_delivery, operations, service_hours


class ConfigConnection:
    def __init__(self, config):
        self.config = config

    def execute(self, query, args):
        assert query == 'SELECT settings FROM brands WHERE id=%s'
        return self

    def fetchone(self):
        return {'settings': self.config}


def setup_window(monkeypatch, stamp):
    now = service_hours.local_time(datetime.fromisoformat(stamp))
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: now)
    config = {'morning_delivery': morning_delivery.defaults() | {
        'enabled': True, 'execution_branch_ids': ['branch2'], 'manager_user_ids': ['manager']}}
    branch = {'id': 'branch2', 'brand_id': 'kps', 'city': 'حفر الباطن',
              'settings': {'delivery_enabled': True}}
    principal = {'id': 'manager', 'brand_id': 'kps', 'role': 'delivery_manager', 'active': True}
    return ConfigConnection(config), branch, principal, now


@pytest.mark.parametrize('stamp,allowed', [
    ('2026-10-22T03:44:59', False), ('2026-10-22T03:45:00', True),
    ('2026-10-22T03:59:59', True), ('2026-10-22T04:00:00', True),
    ('2026-10-22T15:59:59', True), ('2026-10-22T16:00:00', False),
    ('2026-10-23T04:00:00', False), ('2026-10-23T05:30:00', False),
    ('2026-10-23T12:14:59', False), ('2026-10-23T12:15:00', True),
    ('2026-10-23T12:29:59', True), ('2026-10-23T12:30:00', True),
    ('2026-10-23T15:59:59', True), ('2026-10-23T16:00:00', False),
    ('2026-10-22T00:45:00+00:00', True), ('2026-10-23T09:15:00+00:00', True),
])
def test_fresh_morning_manager_boundaries(monkeypatch, stamp, allowed):
    conn, branch, principal, now = setup_window(monkeypatch, stamp)
    if not allowed:
        with pytest.raises(HTTPException) as error:
            operations.delivery_shift_window(conn, 'kps', [branch], manager_preparation=True,
                                              delivery_period='morning', principal=principal)
        if now.weekday() == 4 and 4 <= now.hour < 16:
            assert error.value.detail['code'] == 'MORNING_MANAGER_SHIFT_HOURS'
        return
    day, snapshot = operations.delivery_shift_window(conn, 'kps', [branch], manager_preparation=True,
                                                     delivery_period='morning', principal=principal)
    assert day == now.date()
    assert snapshot['period'] == 'morning'
    assert snapshot['operational_day_start'] == '04:00'
    lane = snapshot['branches']['branch2']
    expected = '12:30' if now.weekday() == 4 else '04:00'
    assert datetime.fromisoformat(lane['window_start']).strftime('%H:%M') == expected
    if now.strftime('%H:%M') < expected:
        assert datetime.fromisoformat(lane['preparation_start']) <= now < datetime.fromisoformat(lane['window_start'])
    else:
        assert 'preparation_start' not in lane


@pytest.mark.parametrize('stamp', ['2026-10-22T03:45:00', '2026-10-23T12:15:00'])
def test_manager_only_preparation_keeps_driver_and_customer_hours(monkeypatch, stamp):
    conn, branch, principal, now = setup_window(monkeypatch, stamp)
    before = deepcopy(morning_delivery.HOURS)
    expected = morning_delivery.in_window(now)
    assert morning_delivery.active(branch, conn.config, now) is expected
    if expected:
        day, snapshot = operations.delivery_shift_window(conn, 'kps', [branch],
            delivery_period='morning', principal=principal)
        assert day == service_hours.delivery_operational_date(now)
        assert snapshot['branches']['branch2']['window_start'].endswith('T04:00:00+03:00')
        assert 'preparation_start' not in snapshot['branches']['branch2']
    else:
        with pytest.raises(HTTPException):
            operations.delivery_shift_window(conn, 'kps', [branch],
                delivery_period='morning', principal=principal)
    assert morning_delivery.HOURS == before


@pytest.mark.parametrize('change', [
    'revoked', 'inactive', 'wrong-role', 'wrong-brand', 'wrong-branch', 'wrong-city',
    'disabled-policy', 'disabled-brand-service', 'disabled-branch', 'disabled-delivery',
])
def test_early_preparation_retains_authorization_and_enablement(monkeypatch, change):
    conn, branch, principal, _ = setup_window(monkeypatch, '2026-10-22T03:45:00')
    if change == 'revoked': conn.config['morning_delivery']['manager_user_ids'] = []
    elif change == 'inactive': principal['active'] = False
    elif change == 'wrong-role': principal['role'] = 'driver'
    elif change == 'wrong-brand': principal['brand_id'] = 'enbishan'
    elif change == 'wrong-branch': branch['id'] = 'branch3'
    elif change == 'wrong-city': branch['city'] = 'الرياض'
    elif change == 'disabled-policy': conn.config['morning_delivery']['enabled'] = False
    elif change == 'disabled-brand-service': conn.config['services'] = {'delivery': False}
    elif change == 'disabled-branch': branch['settings']['active'] = False
    elif change == 'disabled-delivery': branch['settings']['delivery_enabled'] = False
    before = deepcopy((conn.config, branch, principal))
    with pytest.raises(HTTPException):
        operations.delivery_shift_window(conn, 'kps', [branch], manager_preparation=True,
            delivery_period='morning', principal=principal)
    assert (conn.config, branch, principal) == before


@pytest.mark.parametrize('stamp,manager_open,customer_open,opdate', [
    ('2026-10-22T15:44:59', False, False, '2026-10-22'),
    ('2026-10-22T15:45:00', True, False, '2026-10-22'),
    ('2026-10-23T15:45:00', True, False, '2026-10-23'),
    ('2026-10-23T16:00:00', True, True, '2026-10-23'),
    ('2026-10-24T00:00:00', True, True, '2026-10-23'),
    ('2026-10-24T04:00:00', False, False, '2026-10-24'),
])
def test_existing_evening_preparation_and_overnight_date_are_unchanged(stamp, manager_open, customer_open, opdate):
    now = service_hours.local_time(datetime.fromisoformat(stamp))
    status = service_hours.manager_shift_status(service_hours.DEFAULT_DELIVERY_HOURS, now)
    assert status['open'] is manager_open and status['operational_date'] == opdate
    assert service_hours.hours_status(service_hours.DEFAULT_DELIVERY_HOURS, now)['open'] is customer_open
