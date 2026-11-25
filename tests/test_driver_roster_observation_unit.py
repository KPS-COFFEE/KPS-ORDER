"""Roster observations share eligibility decisions without mutation locks."""
from copy import deepcopy
import pytest
from fastapi import HTTPException
from app import operations, multibrand


class Result:
    def __init__(self, row): self.row = row
    def fetchone(self): return self.row
    def fetchall(self): return self.row


class Connection:
    def __init__(self):
        self.queries = []
        self.shift = dict(id='driver-shift', user_id='driver', kind='driver', status='open',
                          brand_id='kps', branch_ids=['branch'], manager_shift_id='manager-shift',
                          available=True, vehicle_log={})
        self.manager = dict(id='manager-shift', user_id='manager', kind='manager', status='open',
                            brand_id='kps', branch_ids=['branch'])
        self.driver = dict(id='driver', role='driver', active=True, brand_id='kps', branch_ids=['branch'])
        self.principal = dict(id='manager', role='delivery_manager', active=True, brand_id='kps', branch_ids=['branch'])
        self.dedicated = None
        self.on_break = None
        self.branches = {'branch': {'id': 'branch', 'city': 'حفر الباطن'}}
        self.active_task_cities = []

    def execute(self, query, params=()):
        self.queries.append((query, params))
        if query.startswith('SELECT pg_advisory_xact_lock'): return Result(None)
        if query.startswith('SELECT 1 FROM driver_breaks'): return Result(self.on_break)
        if query.startswith('SELECT brand_id FROM driver_brand_dedications'): return Result(self.dedicated)
        if query.startswith('SELECT id,city FROM branches WHERE id=ANY'):
            return Result([self.branches[branch] for branch in params[0] if branch in self.branches])
        if query.startswith('SELECT b.city FROM effective_driver_tasks t'):
            assert params == ('driver', 'order')
            return Result(self.active_task_cities)
        if query.startswith('SELECT * FROM shifts WHERE id='):
            return Result(self.manager if params[0] == 'manager-shift' else self.shift)
        if query.startswith('SELECT * FROM users WHERE id='):
            return Result(self.driver if params[0] == 'driver' else self.principal)
        raise AssertionError('Unexpected SQL: '+query)


def enabled_multibrand(monkeypatch):
    monkeypatch.setattr(multibrand, 'enabled', lambda:True)
    monkeypatch.setattr(multibrand, 'isolation_guard', lambda:None)


def outcome(function, conn):
    try:
        return ('eligible', function(conn, conn.shift, dict(id='order', brand_id='kps', branch_id='branch'))['id'])
    except HTTPException as error:
        return ('denied', error.status_code, error.detail)


@pytest.mark.parametrize('change', [
    'ready', 'driver_inactive', 'driver_wrong_role', 'driver_branch_removed',
    'manager_closed', 'manager_branch_removed', 'principal_inactive',
    'principal_wrong_role', 'principal_branch_removed', 'shift_paused',
    'shift_branch_removed', 'shift_brand_changed', 'fuel_not_ready', 'on_break',
    'dedicated_other_brand', 'unrestricted_owner',
])
def test_observation_and_assignment_make_identical_readiness_decisions(monkeypatch, change):
    enabled_multibrand(monkeypatch)
    current=Connection()
    if change=='driver_inactive':current.driver['active']=False
    if change=='driver_wrong_role':current.driver['role']='delivery_staff'
    if change=='driver_branch_removed':current.driver['branch_ids']=[]
    if change=='manager_closed':current.manager['status']='closed'
    if change=='manager_branch_removed':current.manager['branch_ids']=[]
    if change=='principal_inactive':current.principal['active']=False
    if change=='principal_wrong_role':current.principal['role']='admin'
    if change=='principal_branch_removed':current.principal['branch_ids']=[]
    if change=='shift_paused':current.shift['available']=False
    if change=='shift_branch_removed':current.shift['branch_ids']=[]
    if change=='shift_brand_changed':current.shift['brand_id']='other'
    if change=='fuel_not_ready':current.shift['vehicle_log']={'awaiting_refuel':True}
    if change=='on_break':current.on_break={'exists':True}
    if change=='dedicated_other_brand':current.dedicated={'brand_id':'enbishan'}
    if change=='unrestricted_owner':current.principal.update(role='owner',branch_ids=[])
    canonical=deepcopy(current)
    assert outcome(operations.observe_driver_shift,current)==outcome(operations.eligible_driver_shift,canonical)
    assert all('FOR UPDATE' not in sql and 'pg_advisory_xact_lock' not in sql for sql,_ in current.queries)
    assert not any(sql.startswith(('UPDATE','INSERT','DELETE')) for sql,_ in current.queries)


def test_canonical_assignment_still_acquires_driver_advisory_and_manager_row_lock(monkeypatch):
    enabled_multibrand(monkeypatch)
    current=Connection()
    assert outcome(operations.eligible_driver_shift,current)==('eligible','manager-shift')
    assert any('pg_advisory_xact_lock' in sql and params==('multibrand-driver:driver',) for sql,params in current.queries)
    assert any('FOR UPDATE' in sql and params==('manager-shift',) for sql,params in current.queries)
    observed=Connection()
    assert outcome(operations.observe_driver_shift,observed)==('eligible','manager-shift')
    assert not any('FOR UPDATE' in sql or 'pg_advisory_xact_lock' in sql for sql,_ in observed.queries)


def test_observation_uses_fresh_dedication_values_without_result_caching(monkeypatch):
    enabled_multibrand(monkeypatch)
    current=Connection()
    assert outcome(operations.observe_driver_shift,current)[0]=='eligible'
    current.dedicated={'brand_id':'enbishan'}
    assert outcome(operations.observe_driver_shift,current)[0]=='denied'
    current.dedicated=None
    current.driver['active']=False
    assert outcome(operations.observe_driver_shift,current)[0]=='denied'
