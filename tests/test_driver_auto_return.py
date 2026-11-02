"""Conservative physical return evidence: capture time is never receipt time."""
from datetime import datetime, timedelta, timezone

import pytest

from app.driver_auto_return import evaluate_point

NOW = datetime(2026, 10, 29, 15, tzinfo=timezone.utc)
BRANCH = {'id': 'return-branch', 'settings': {'location_verified': True, 'location': {'lat': 28.4, 'lng': 45.9}}}
START = NOW - timedelta(minutes=10)
DELIVERY = NOW - timedelta(minutes=2)


def sample(seconds, *, outside=False, accuracy=10, **changes):
    result = {'id': 'point-' + str(seconds), 'captured_at': NOW + timedelta(seconds=seconds),
              'lat': 28.4, 'lng': 45.92 if outside else 45.9, 'accuracy_m': accuracy}
    result.update(changes)
    return result


def feed(points, *, clocks=None, branch=BRANCH, eligible_after=DELIVERY, started_at=START):
    state = {}
    arrivals = []
    for index, point in enumerate(points):
        clock = clocks[index] if clocks else point['captured_at']
        state, arrived = evaluate_point(state, point, branch, clock, eligible_after, started_at)
        arrivals.append(arrived)
    return state, arrivals


def complete_points():
    return [sample(-90, outside=True), sample(-60), sample(-30), sample(0)]


def test_verified_exit_then_three_fresh_inside_points_certifies_actual_capture_clock():
    _, arrivals = feed(complete_points())
    assert arrivals[:3] == [None, None, None]
    assert arrivals[-1] is not None
    assert NOW - timedelta(seconds=60) <= arrivals[-1] <= NOW


def test_dwell_at_branch_without_departure_never_proves_a_delivery_return():
    assert all(value is None for value in feed([sample(-90), sample(-60), sample(-30), sample(0)])[1])


@pytest.mark.parametrize('count', [1, 2])
def test_single_or_two_fixes_do_not_certify_return(count):
    _, arrivals = feed([sample(-90, outside=True)] + [sample(-60 + n*60) for n in range(count)])
    assert all(value is None for value in arrivals)


def test_many_fast_fixes_do_not_replace_sixty_seconds_dwell():
    _, arrivals = feed([sample(-60, outside=True)] + [sample(n) for n in range(-5, 1)])
    assert all(value is None for value in arrivals)


def test_backlog_with_old_captures_and_instant_receipt_does_not_certify_live_return():
    _, arrivals = feed(complete_points(), clocks=[NOW] * 4)
    assert all(value is None for value in arrivals)


def test_stale_backlog_is_not_live_even_if_capture_dwell_is_long_enough():
    _, arrivals = feed(complete_points(), clocks=[NOW + timedelta(minutes=10)] * 4)
    assert all(value is None for value in arrivals)


@pytest.mark.parametrize('accuracy', [36, 100, 1000])
def test_inaccurate_fixes_cannot_close_a_mission(accuracy):
    points = [sample(-90, outside=True)] + [sample(t, accuracy=accuracy) for t in (-60, -30, 0)]
    assert all(value is None for value in feed(points)[1])


def test_uncertainty_disc_must_fit_inside_branch_geofence():
    # About 78 metres from centre plus 30 m uncertainty exceeds 100 m fence.
    points = [sample(-90, outside=True)] + [sample(t, lng=45.9008, accuracy=30) for t in (-60, -30, 0)]
    assert all(value is None for value in feed(points)[1])


def test_return_to_a_different_branch_does_not_close_selected_destination():
    branch = {'id': 'other-return', 'settings': {'location_verified': True, 'location': {'lat': 28.5, 'lng': 45.9}}}
    assert all(value is None for value in feed(complete_points(), branch=branch)[1])


@pytest.mark.parametrize('branch', [{'id': 'b', 'settings': {}}, {'id': 'b', 'settings': {'location_verified': True, 'location': None}}])
def test_missing_branch_pin_never_fabricates_arrival(branch):
    assert all(value is None for value in feed(complete_points(), branch=branch)[1])


def test_inside_capture_gap_restarts_dwell():
    points = [sample(-120, outside=True), sample(-90), sample(-30), sample(0)]
    assert all(value is None for value in feed(points)[1])


def test_leaving_branch_mid_dwell_restarts_the_inside_witness():
    points = [sample(-120, outside=True), sample(-90), sample(-60, outside=True), sample(-30), sample(0)]
    assert all(value is None for value in feed(points)[1])


def test_points_before_last_delivery_cannot_end_delivery_work():
    assert all(value is None for value in feed(complete_points(), eligible_after=NOW + timedelta(seconds=1))[1])


def test_outside_witness_before_acceptance_is_not_reused_for_new_mission():
    assert all(value is None for value in feed(complete_points(), started_at=NOW - timedelta(seconds=80))[1])


def test_future_capture_cannot_finish_live_work():
    points = complete_points()
    assert all(value is None for value in feed(points, clocks=[p['captured_at'] - timedelta(seconds=1) for p in points])[1])


def test_duplicate_capture_timestamp_does_not_supply_three_independent_fixes():
    points = [sample(-90, outside=True), sample(-60), sample(-60, id='duplicate'), sample(0)]
    assert all(value is None for value in feed(points)[1])


def test_out_of_order_capture_resets_return_witness():
    points = [sample(-90, outside=True), sample(-60), sample(-30), sample(-45), sample(0)]
    assert all(value is None for value in feed(points)[1])


def test_unverified_branch_location_cannot_certify_return():
    branch = {'id':'unverified', 'settings':{'location':{'lat':28.4,'lng':45.9}, 'location_verified':False}}
    assert all(value is None for value in feed(complete_points(), branch=branch)[1])


def test_changed_verified_pin_cannot_reuse_outside_or_dwell_from_old_geofence():
    state = {}
    for point in complete_points()[:3]:
        state, arrived = evaluate_point(state, point, BRANCH, point['captured_at'], DELIVERY, START)
        assert arrived is None
    moved = {'id':'return-branch', 'settings':{'location_verified':True, 'location':{'lat':28.4,'lng':45.9001}}}
    # Both pins contain this last fix: only the changed target evidence prevents
    # incorrectly completing the previous location's already-started dwell.
    state, arrived = evaluate_point(state, sample(0), moved, NOW, DELIVERY, START)
    assert arrived is None
    assert state.get('outside_seen_at') is None


def test_removed_verification_does_not_resurrect_old_outside_evidence_when_reenabled():
    state = {}
    for point in complete_points()[:2]:
        state, arrived = evaluate_point(state, point, BRANCH, point['captured_at'], DELIVERY, START)
        assert arrived is None
    removed = {'id':'return-branch', 'settings':{'location_verified':False, 'location':{'lat':28.4,'lng':45.9}}}
    state, arrived = evaluate_point(state, sample(-30), removed, NOW-timedelta(seconds=30), DELIVERY, START)
    assert arrived is None
    state, arrived = evaluate_point(state, sample(0), BRANCH, NOW, DELIVERY, START)
    assert arrived is None
    assert state.get('outside_seen_at') is None


def test_first_geofence_initialization_preserves_current_terminal_observation():
    # The task's transfer/cancellation clock is first observed by the server on
    # the outside fix. Selecting the geofence must not silently discard it and
    # restart that independent task clock on the first inside fix.
    observed = NOW-timedelta(seconds=90)
    state = {'session_id':'session', 'terminal_signature':['task:transferred'],
             'terminal_observed_at':observed.isoformat()}
    for point in complete_points():
        state, arrived = evaluate_point(state, point, BRANCH, point['captured_at'], observed, START)
        assert state['terminal_signature'] == ['task:transferred']
        assert state['terminal_observed_at'] == observed.isoformat()
    assert arrived == NOW-timedelta(seconds=60)
