"""Accepted-task to confirmed-return work, under the owner's approved definition.

Duty, payroll, GPS presence and manager approval are deliberately not this clock.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import driver_work_hours as hours


START = datetime(2026, 10, 29, 16, tzinfo=ZoneInfo('Asia/Riyadh'))


def at(minutes):
    return START + timedelta(minutes=minutes)


def trip(identifier, departed=20, returned=90, **evidence):
    return {'id': identifier, 'departed_at': at(departed),
            'returned_at': at(returned) if returned is not None else None,
            'return_branch_id': 'selected-return-branch' if returned is not None else None, **evidence}


def activity(trips, accepted, *, lo=-60, hi=240, now=120):
    return hours.trip_activity(trips, accepted, at(lo), at(hi), at(now))


def test_completed_work_includes_accepted_task_waiting_before_pickup_and_departure():
    result = activity([trip('a', departed=30, returned=90)], {'a': [at(0)]})
    assert result['actual_seconds'] == 90 * 60
    assert result['active_work_seconds'] == 0
    assert not result['work_review_reasons']


def test_multiple_orders_in_one_trip_start_at_first_acceptance_not_sum_of_orders():
    result = activity([trip('a')], {'a': [at(30), at(0), at(10)]})
    assert result['actual_seconds'] == 90 * 60
    assert result['active_work_seconds'] == 0
    assert not result['work_review_reasons']


def test_overlapping_completed_trips_count_each_second_once():
    result = activity([trip('a', returned=90), trip('b', departed=70, returned=120)],
                      {'a': [at(0)], 'b': [at(60)]})
    assert result['actual_seconds'] == 120 * 60
    assert result['active_work_seconds'] == 0
    assert not result['work_review_reasons']


def test_duplicate_trip_and_acceptance_entries_do_not_duplicate_work():
    row = trip('a', returned=60)
    result = activity([row, dict(row)], {'a': [at(0), at(0)]})
    assert result['actual_seconds'] == 60 * 60
    assert result['active_work_seconds'] == 0


def test_disjoint_trips_do_not_turn_the_gap_into_work():
    result = activity([trip('a', returned=30), trip('b', departed=100, returned=120)],
                      {'a': [at(0)], 'b': [at(90)]})
    assert result['actual_seconds'] == 60 * 60
    assert result['active_work_seconds'] == 0
    assert not result['work_review_reasons']


def test_pending_return_has_separate_active_work_and_never_completed_actual():
    result = activity([trip('a', returned=None)], {'a': [at(0)]})
    assert result['actual_seconds'] == 0
    assert result['active_work_seconds'] == 120 * 60


def test_active_work_excludes_any_overlap_already_counted_as_completed():
    result = activity([trip('a', returned=60), trip('b', departed=40, returned=None)],
                      {'a': [at(0)], 'b': [at(30)]})
    assert result['actual_seconds'] == 60 * 60
    assert result['active_work_seconds'] == 60 * 60
    assert result['actual_seconds'] + result['active_work_seconds'] == 120 * 60


def test_one_missing_order_acceptance_prevents_certifying_a_partial_trip_start():
    result = activity([trip('a')], {'a': [at(10), None]})
    assert result['actual_seconds'] == 0
    assert result['active_work_seconds'] == 0
    assert result['work_review_reasons']


def test_absent_acceptance_evidence_is_review_not_an_invented_departure_start():
    for accepted in ({}, {'a': []}, {'a': [None]}):
        result = activity([trip('a')], accepted)
        assert result['actual_seconds'] == 0
        assert result['active_work_seconds'] == 0
        assert result['work_review_reasons']


def test_invalid_or_future_return_is_review_without_certifying_completed_work():
    for returned in (-5, 180):
        result = activity([trip('a', returned=returned)], {'a': [at(0)]})
        assert result['actual_seconds'] == 0
        assert result['active_work_seconds'] == 0
        assert result['work_review_reasons']


def test_reporting_window_clips_completed_and_active_intervals_without_overlap():
    completed = activity([trip('a', departed=0, returned=60)], {'a': [at(-30)]}, lo=0, hi=30)
    pending = activity([trip('b', returned=None)], {'b': [at(-30)]}, lo=0, hi=30)
    assert completed['actual_seconds'] == 30 * 60
    assert completed['active_work_seconds'] == 0
    assert pending['actual_seconds'] == 0
    assert pending['active_work_seconds'] == 30 * 60


def test_trip_work_is_not_clipped_by_submission_breaks_gps_or_late_manager_approval():
    plain = trip('a', returned=90)
    metadata = trip('a', returned=90, submitted_at=at(60), closed_at=at(100),
                    staffing_ended_at=at(110), scheduled_end=at(45), gps_points=[],
                    driver_breaks=[{'started_at': at(0), 'ended_at': at(90)}])
    first = activity([plain], {'a': [at(0)]})
    second = activity([metadata], {'a': [at(0)]})
    for key in ('actual_seconds', 'active_work_seconds', 'work_review_reasons'):
        assert first[key] == second[key]
    assert second['actual_seconds'] == 90 * 60
    assert second['active_work_seconds'] == 0


def test_return_timestamp_without_an_actual_return_branch_requires_review():
    result = activity([trip('a', return_branch_id=None)], {'a': [at(0)]})
    assert result['actual_seconds'] == 0
    assert result['active_work_seconds'] == 0
    assert result['work_review_reasons']


class ScopeRows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class ScopeConnection:
    def __init__(self, legs):
        self.legs = legs
        self.workday_reads = 0

    def execute(self, statement, args=()):
        if 'FROM central_workday_legs' in statement:
            self.workday_reads += 1
            return ScopeRows(self.legs)
        if 'FROM branches WHERE brand_id=' in statement:
            return ScopeRows([{'id': args[0] + '-branch'}, {'id': args[0] + '-secret'}])
        raise AssertionError('Unexpected scope query: ' + statement)


def scope_case(monkeypatch):
    from app import auth, central_workdays, multibrand
    legs = [
        {'id': 'kps-leg', 'brand_id': 'kps', 'branch_ids': ['kps-branch'],
         'user_id': 'driver', 'kind': 'driver', 'opened_at': at(0)},
        {'id': 'enbishan-leg', 'brand_id': 'enbishan', 'branch_ids': ['enbishan-branch'],
         'user_id': 'driver', 'kind': 'driver', 'opened_at': at(30)},
    ]
    day = {'id': 'physical-day', 'anchor_shift_id': 'kps-leg', 'user_id': 'driver', 'kind': 'driver'}
    user = {'id': 'reader', 'brand_id': 'kps', 'role': 'delivery_manager', 'branch_ids': ['kps-branch']}
    grants = {brand: {'id': 'reader', 'brand_id': brand, 'role': 'delivery_manager',
                      'branch_ids': [brand + '-branch'], 'effective_permissions': ['driver_hours_reports']}
              for brand in ('kps', 'enbishan')}
    monkeypatch.setattr(central_workdays, 'association', lambda c, sid: day)
    monkeypatch.setattr(multibrand, 'project', lambda c, actor, brand: grants.get(brand))
    monkeypatch.setattr(auth, 'has_permission', lambda principal, capability: capability in principal.get('effective_permissions', []))
    return ScopeConnection(legs), user, legs, day, grants


def test_fully_authorized_shared_day_expands_each_leg_before_union(monkeypatch):
    connection, user, legs, _, _ = scope_case(monkeypatch)
    selected, mission, scopes, incomplete = hours.report_shift_scope(connection, user, [legs[0]], 'driver')
    assert not incomplete
    assert {s['id'] for s in selected} == set(mission) == {'kps-leg', 'enbishan-leg'}
    assert scopes['enbishan-leg'] == {'brand_id': 'enbishan', 'branches': {'enbishan-branch'}}
    assert 'enbishan-secret' not in scopes['enbishan-leg']['branches']
    assert connection.workday_reads == 1


def test_missing_other_brand_grant_keeps_only_visible_duty_without_any_mission_clock(monkeypatch):
    connection, user, legs, _, grants = scope_case(monkeypatch)
    grants.pop('enbishan')
    selected, mission, scopes, incomplete = hours.report_shift_scope(connection, user, [legs[0]], 'driver')
    assert incomplete
    assert selected == [legs[0]]
    assert mission == scopes == {}
    assert all(s['brand_id'] == 'kps' for s in selected)


def test_other_brand_needs_report_permission_as_well_as_branch_membership(monkeypatch):
    connection, user, legs, _, grants = scope_case(monkeypatch)
    grants['enbishan']['effective_permissions'] = []
    selected, mission, scopes, incomplete = hours.report_shift_scope(connection, user, [legs[0]], 'driver')
    assert incomplete and selected == [legs[0]] and mission == scopes == {}


def test_one_forbidden_branch_blocks_the_entire_shared_mission_not_only_that_stop(monkeypatch):
    connection, user, legs, _, _ = scope_case(monkeypatch)
    legs[1]['branch_ids'] = ['enbishan-branch', 'enbishan-secret']
    selected, mission, scopes, incomplete = hours.report_shift_scope(connection, user, [legs[0]], 'driver')
    assert incomplete and selected == [legs[0]] and mission == scopes == {}


def test_projected_owner_does_not_bypass_explicit_other_brand_branch_scope(monkeypatch):
    connection, user, legs, _, grants = scope_case(monkeypatch)
    grants['enbishan'].update(role='owner', home_brand_id='kps', branch_ids=[])
    selected, mission, scopes, incomplete = hours.report_shift_scope(connection, user, [legs[0]], 'driver')
    assert incomplete and selected == [legs[0]] and mission == scopes == {}


def test_shared_scope_rejects_mismatched_day_owner_kind_or_leg_driver(monkeypatch):
    changes = [('day_owner', 'foreign'), ('day_kind', 'manager'), ('leg_driver', 'foreign'), ('missing_anchor', 'absent')]
    for change, value in changes:
        connection, user, legs, day, _ = scope_case(monkeypatch)
        if change == 'day_owner':
            day['user_id'] = value
        elif change == 'day_kind':
            day['kind'] = value
        elif change == 'leg_driver':
            legs[1]['user_id'] = value
        else:
            day['anchor_shift_id'] = value
        selected, mission, scopes, incomplete = hours.report_shift_scope(connection, user, [legs[0]], 'driver')
        assert incomplete, change
        assert selected == [legs[0]] and mission == scopes == {}, change


def test_morning_context_cannot_expand_into_a_foreign_brand_leg(monkeypatch):
    from app import morning_manager_context
    connection, user, legs, _, _ = scope_case(monkeypatch)
    user['_morning_manager_context'] = {'shift_id': 'morning-manager'}
    monkeypatch.setattr(morning_manager_context, 'shift_allowed', lambda actor, leg: True)
    selected, mission, scopes, incomplete = hours.report_shift_scope(connection, user, [legs[0]], 'driver')
    assert incomplete and selected == [legs[0]] and mission == scopes == {}


def test_shared_day_is_resolved_once_when_both_authorized_legs_are_selected(monkeypatch):
    connection, user, legs, _, _ = scope_case(monkeypatch)
    selected, mission, _, incomplete = hours.report_shift_scope(connection, user, legs, 'driver')
    assert not incomplete and len(selected) == len(mission) == 2
    assert connection.workday_reads == 1


def test_outside_shift_is_detected_by_interval_containment_not_duration_comparison():
    result = hours.trip_activity([trip('a', departed=110, returned=180)], {'a': [at(100)]},
                                 at(0), at(240), at(240), recorded_spans=[(at(0), at(120))])
    assert result['actual_seconds'] == 80 * 60
    assert result['actual_seconds'] < 120 * 60
    assert 'work_outside_shift_evidence' in result['work_review_reasons']
    assert result['work_review_required']


def test_outside_configured_duty_is_review_without_clipping_trip_work():
    result = hours.trip_activity([trip('a', returned=90)], {'a': [at(0)]}, at(0), at(120), at(120),
                                 recorded_spans=[(at(0), at(120))], duty_spans=[(at(30), at(120))])
    assert result['actual_seconds'] == 90 * 60
    assert 'work_outside_configured_duty' in result['work_review_reasons']
    assert 'work_outside_shift_evidence' not in result['work_review_reasons']


def test_historical_missing_return_cannot_become_work_running_until_today():
    result = activity([trip('a', returned=None, historical=True)], {'a': [at(0)]})
    assert result['actual_seconds'] == result['active_work_seconds'] == 0
    assert 'missing_return_evidence' in result['work_review_reasons']
    assert result['work_clock_status'] == 'needs_review'
