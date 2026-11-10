from copy import deepcopy
from datetime import datetime

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import catalog, morning_delivery as morning, service_hours


def at(clock):
    return datetime.fromisoformat('2026-10-20T' + clock).replace(tzinfo=service_hours.RIYADH)


def branch(number=1):
    return {'id': f'kps-{number}', 'brand_id': 'kps', 'branch_no': number,
            'city': 'حفر الباطن', 'settings': {}}


def settings():
    return {'morning_delivery': morning.defaults() | {'enabled': True,
            'execution_branch_ids': ['kps-1', 'kps-2'],
            'preparer_user_ids': ['preparer'], 'manager_user_ids': ['manager']}}


@pytest.mark.parametrize('clock,expected', [('03:59:59', False), ('04:00:00', True),
    ('15:59:59', True), ('16:00:00', False)])
def test_morning_boundaries(clock, expected):
    assert morning.active(branch(), settings(), at(clock)) is expected


def test_disabled_other_brand_and_other_city():
    assert not morning.active(branch(), {}, at('08:00:00'))
    assert not morning.active(branch() | {'brand_id': 'other'}, settings(), at('08:00:00'))
    assert not morning.active(branch() | {'city': 'الرياض'}, settings(), at('08:00:00'))


def test_routing_is_approved_order_and_whole_khaliya_branch_two(monkeypatch):
    monkeypatch.setattr(catalog, 'branch_service', lambda *a, **kw: (True, None, None))
    rows = [branch(1), branch(2), branch(3)]
    assert morning.target(rows, rows[2], settings(), now=at('08:00:00'))['id'] == 'kps-1'
    assert morning.target(rows, rows[2], settings(), khaliya=True, now=at('08:00:00'))['id'] == 'kps-2'
    config = settings()
    config['morning_delivery']['execution_branch_ids'] = ['kps-1']
    with pytest.raises(HTTPException) as error:
        morning.target(rows, rows[2], config, khaliya=True, now=at('08:00:00'))
    assert error.value.status_code == 409


def test_no_open_executor_fails_closed_and_evening_preserved(monkeypatch):
    monkeypatch.setattr(catalog, 'branch_service', lambda *a, **kw: (False, 'CLOSED', 'closed'))
    selected = branch(3)
    with pytest.raises(HTTPException):
        morning.target([branch(1)], selected, settings(), now=at('08:00:00'))
    assert morning.target([], selected, settings(), now=at('16:00:00')) == selected


def test_policy_requires_staff_and_unique_branches():
    with pytest.raises(ValidationError):
        morning.MorningPolicy(enabled=True)
    with pytest.raises(ValidationError):
        morning.MorningPolicy(execution_branch_ids=['a', 'a'])
    original = settings()
    returned = morning.policy(original)
    returned['execution_branch_ids'].clear()
    assert original['morning_delivery']['execution_branch_ids']


def test_customer_quote_hides_execution_and_policy_revision():
    quote = {'branch_id': 'execution', 'morning_policy_version': 3,
             'delivery_period': 'morning', 'internal_routing': {
                 'selected_branch': {'id': 'selected', 'name': 'Selected', 'city': 'حفر الباطن', 'branch_no': 3}}}
    result = catalog.customer_quote_view(deepcopy(quote))
    assert 'internal_routing' not in result
    assert 'morning_policy_version' not in result


@pytest.mark.parametrize('day,clock,expected', [
    ('2026-11-08', '08:59:59', False), ('2026-11-08', '09:00:00', True),
    ('2026-11-08', '11:59:59', True), ('2026-11-08', '12:00:00', False),
    ('2026-11-06', '10:00:00', False),
])
def test_enbishan_morning_only_branch_one_and_approved_window(day, clock, expected):
    date = datetime.fromisoformat(day + 'T' + clock).replace(tzinfo=service_hours.RIYADH)
    branch_one = branch() | {'id': 'enbishan-1', 'brand_id': 'enbishan'}
    config = {'morning_delivery': {'enabled': True, 'execution_branch_ids': ['enbishan-1']}}
    assert morning.active(branch_one, config, date) is expected
    assert not morning.active(branch_one, {}, date)
    assert not morning.active(branch_one | {'branch_no': 2}, config, date)
    assert not morning.active(branch_one | {'city': 'الدمام'}, config, date)


def test_enbishan_morning_manager_can_prepare_without_admitting_customers():
    date = datetime.fromisoformat('2026-11-08T08:45:00').replace(tzinfo=service_hours.RIYADH)
    status = morning.manager_shift_status(date, brand_id='enbishan')
    assert status['preparation'] and status['current_window_start'].endswith('09:00:00+03:00')
    assert not service_hours.hours_open(morning.hours_for('enbishan'), date)
