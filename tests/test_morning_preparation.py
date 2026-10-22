"""Staffing entry points keep time, approval and operational-day boundaries."""
from copy import deepcopy
from datetime import datetime

import pytest
from fastapi import HTTPException

from app import morning_delivery, operations, service_hours


class ConfigConnection:
    def __init__(self, config):
        self.config = config

    def execute(self, *args):
        return self

    def fetchone(self):
        return {'settings': self.config}


def setup_window(monkeypatch, stamp):
    now = datetime.fromisoformat(stamp).replace(tzinfo=service_hours.RIYADH)
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: now)
    config = {'morning_delivery': morning_delivery.defaults() | {
        'enabled': True, 'execution_branch_ids': ['branch2'], 'manager_user_ids': ['ahmed']}}
    branch = {'id': 'branch2', 'brand_id': 'kps', 'city': 'حفر الباطن',
              'settings': {'delivery_enabled': True}}
    principal = {'id': 'ahmed', 'brand_id': 'kps', 'role': 'delivery_manager', 'active': True}
    return ConfigConnection(config), branch, principal


@pytest.mark.parametrize('stamp,start,opdate', [
    ('2026-10-22T03:45:00','2026-10-22T04:00:00+03:00','2026-10-21'),
    ('2026-10-23T12:15:00','2026-10-23T12:30:00+03:00','2026-10-22'),
])
def test_preparation_reaches_shift_gate_and_keeps_period_snapshot(monkeypatch, stamp, start, opdate):
    conn, branch, principal = setup_window(monkeypatch, stamp)
    day, snapshot = operations.delivery_shift_window(conn, 'kps', [branch],
        manager_preparation=True, delivery_period='morning', principal=principal)
    assert day.isoformat() == opdate
    assert snapshot['period'] == 'morning'
    assert snapshot['branches']['branch2']['window_start'] == start
    assert snapshot['branches']['branch2']['preparation_start'] == stamp+'+03:00'
    # Driver opening does not inherit the manager-only early opening.
    with pytest.raises(HTTPException):
        operations.delivery_shift_window(conn, 'kps', [branch],
            delivery_period='morning', principal=principal)


@pytest.mark.parametrize('stamp', ['2026-10-22T03:44:59', '2026-10-23T04:00:00',
                                  '2026-10-23T12:14:59', '2026-10-23T16:00:00'])
def test_manager_cannot_open_outside_staffing_window(monkeypatch, stamp):
    conn, branch, principal = setup_window(monkeypatch, stamp)
    with pytest.raises(HTTPException):
        operations.delivery_shift_window(conn, 'kps', [branch],
            manager_preparation=True, delivery_period='morning', principal=principal)


@pytest.mark.parametrize('change', ['revoked', 'inactive', 'wrong-brand', 'wrong-branch', 'disabled'])
def test_preparation_does_not_bypass_approval_or_branch_scope(monkeypatch, change):
    conn, branch, principal = setup_window(monkeypatch, '2026-10-22T03:45:00')
    if change == 'revoked':
        conn.config['morning_delivery']['manager_user_ids'] = []
    elif change == 'inactive':
        principal['active'] = False
    elif change == 'wrong-brand':
        principal['brand_id'] = 'other'
    elif change == 'wrong-branch':
        branch['id'] = 'branch3'
    else:
        conn.config['morning_delivery']['enabled'] = False
    before = deepcopy((conn.config, principal, branch))
    with pytest.raises(HTTPException):
        operations.delivery_shift_window(conn, 'kps', [branch],
            manager_preparation=True, delivery_period='morning', principal=principal)
    assert (conn.config, principal, branch) == before
