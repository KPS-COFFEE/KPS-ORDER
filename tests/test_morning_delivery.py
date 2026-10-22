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


@pytest.mark.parametrize('day,clock,manager,driver', [
    ('2026-10-22','03:44:59',False,False),
    ('2026-10-22','03:45:00',True,False),
    ('2026-10-22','04:00:00',True,True),
    ('2026-10-23','03:45:00',False,False),
    ('2026-10-23','12:14:59',False,False),
    ('2026-10-23','12:15:00',True,False),
    ('2026-10-23','12:30:00',True,True),
    ('2026-10-23','16:00:00',False,False),
    ('2026-10-24','03:45:00',True,False),
])
def test_morning_staffing_preparation(day, clock, manager, driver):
    now = datetime.fromisoformat(day+'T'+clock).replace(tzinfo=service_hours.RIYADH)
    state = morning.shift_status(now, preparation=True)
    assert state['open'] is manager
    assert morning.shift_status(now)['open'] is driver
    # Preparation never extends customer admission before 04:00.
    if clock == '03:45:00':
        assert not morning.active(branch(), settings(), now)
    if manager and not driver:
        assert state['preparation']
        assert datetime.fromisoformat(state['current_window_start']).date() == now.date()


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
