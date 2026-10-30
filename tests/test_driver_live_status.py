"""Live staffing totals use readiness and retain off-duty roster members."""
from contextlib import contextmanager

import pytest
from app import driver_readiness, operations


@pytest.mark.parametrize('availability', [
    'busy', 'on_break', 'manually_paused', 'waiting_for_fuel',
    'fuel_exception_pending', 'manager_or_scope_not_ready',
    'closing_or_closed', 'support_requires_handover', 'inactive',
])
def test_unavailable_physical_duty_stays_in_busy_bucket(availability):
    row = {'shift_id':'shift', 'shift_status':'open', 'availability':availability}
    assert driver_readiness.live_status(row) == 'busy'


def test_no_shift_and_closed_are_never_available_from_stale_readiness():
    for row in ({'shift_id':None}, {'shift_id':'old', 'shift_status':'closed'}):
        assert driver_readiness.live_status(dict(row, availability='available')) == 'no_shift'
    assert driver_readiness.live_status({'shift_id':'live', 'shift_status':'open', 'availability':'available'}) == 'available'


@pytest.mark.parametrize('multibrand', [False, True])
def test_roster_counts_authorized_drivers_including_no_shift(monkeypatch, multibrand):
    rows = [
        dict(id='ready', branch_ids=['a','b'], shift_id='ready-shift', shift_status='open', availability='available'),
        dict(id='break', branch_ids=['a'], shift_id='break-shift', shift_status='open', availability='on_break'),
        dict(id='off', branch_ids=['a'], shift_id=None, shift_status=None, availability='no_active_shift'),
        dict(id='hidden', branch_ids=['b'], shift_id=None, shift_status=None, availability='no_active_shift'),
    ]
    class Connection:
        def execute(self, query, params=()):
            if query == 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY':
                assert params == ()
                return self
            # Canonical latest physical duty, one row per user. The optional
            # migration column stays gated for legacy non-multibrand installs.
            assert 'LEFT JOIN LATERAL' in query and 'LIMIT 1' in query
            assert "s.status<>'closed'" in query
            assert ('s.staffing_ended_at IS NULL' in query) == multibrand
            assert params == ('kps',)
            return self
        def fetchall(self): return rows
    @contextmanager
    def transaction(): yield Connection()
    monkeypatch.setattr(operations.db, 'tx', transaction)
    monkeypatch.setattr('app.multibrand.enabled', lambda:multibrand)
    monkeypatch.setattr(operations, 'roles', lambda *args:None)
    monkeypatch.setattr(driver_readiness, 'describe', lambda c,r:(r['availability'],'none',[]))
    result=operations.driver_roster({'id':'manager','role':'delivery_manager','brand_id':'kps','branch_ids':['a']})
    assert result['driver_counts'] == {'total':3,'available':1,'busy':1,'no_shift':1}
    assert [r['live_status'] for r in result['drivers']] == ['available','busy','no_shift']
    assert all(r['branch_ids']==['a'] for r in result['drivers'])
    assert result['drivers'][0]['assignment_eligible'] is True
    assert all(r['assignment_eligible'] is False for r in result['drivers'][1:])
