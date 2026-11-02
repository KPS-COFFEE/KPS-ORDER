"""Road-reference binding tests; no provider traffic or database required."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.route_eta import corridor_current, fingerprint

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
STOPS = [{'id': 'delivery-a', 'kind': 'delivery', 'location': {'lat': 28., 'lng': 45.1},
          'shift_id': 'shift-a', 'brand_id': 'kps', 'task_id': 'task-a'}]
GEOMETRY = {'type': 'LineString', 'coordinates': [[45, 28], [45.1, 28]]}


def reference(age=60):
    return {'geometry': deepcopy(GEOMETRY), 'calculated_at': (NOW - timedelta(seconds=age)).isoformat(),
            'fingerprint': fingerprint(STOPS), 'stop_id': 'delivery-a'}


def test_current_road_reference_remains_bound_to_exact_next_stop():
    assert corridor_current(reference(), fingerprint(STOPS), STOPS[0], NOW)
    assert not corridor_current(reference(), fingerprint(STOPS), {'id': 'delivery-b'}, NOW)
    assert not corridor_current(reference(), fingerprint(STOPS), None, NOW)


def test_changed_location_shift_or_brand_invalidates_road_reference():
    for field, value in [('location', {'lat': 28.1, 'lng': 45.1}),
                         ('shift_id', 'shift-b'), ('brand_id', 'enbishan'), ('task_id', 'task-b')]:
        changed = deepcopy(STOPS)
        changed[0][field] = value
        assert not corridor_current(reference(), fingerprint(changed), changed[0], NOW)


def test_added_or_removed_trip_stop_invalidates_road_reference():
    changed = deepcopy(STOPS) + [{'id': 'return-a', 'kind': 'return', 'location': {'lat': 28, 'lng': 45}}]
    assert not corridor_current(reference(), fingerprint(changed), changed[0], NOW)
    assert not corridor_current(reference(), fingerprint([]), None, NOW)


def test_expired_or_future_reference_is_not_current():
    assert not corridor_current(reference(age=86401), fingerprint(STOPS), STOPS[0], NOW)
    assert not corridor_current(reference(age=-1), fingerprint(STOPS), STOPS[0], NOW)


@pytest.mark.parametrize('value', [None, {}, {'calculated_at': 'bad'},
    {'calculated_at': NOW.replace(tzinfo=None).isoformat()},
    {**reference(), 'geometry': None}, {**reference(), 'geometry': {'type': 'LineString', 'coordinates': []}}])
def test_invalid_or_unbound_reference_fails_closed(value):
    assert not corridor_current(value, fingerprint(STOPS), STOPS[0], NOW)


@pytest.mark.parametrize('publisher_changed', [False, True])
def test_mixed_brand_corridor_uses_revalidated_publisher_itinerary(monkeypatch, publisher_changed):
    from app import route_eta
    now = datetime.now(timezone.utc)
    viewer_stops = deepcopy(STOPS)
    publisher_stops = viewer_stops + [{'id': 'delivery-enbishan', 'kind': 'delivery',
        'location': {'lat': 28.01, 'lng': 45.12}, 'shift_id': 'shift-enbishan',
        'brand_id': 'enbishan', 'task_id': 'task-enbishan'}]
    corridor = {'geometry': deepcopy(GEOMETRY), 'calculated_at': (now - timedelta(seconds=60)).isoformat(),
                'fingerprint': fingerprint(publisher_stops), 'stop_id': viewer_stops[0]['id']}
    current_publisher_stops = deepcopy(publisher_stops)
    if publisher_changed:
        current_publisher_stops[1]['location']['lng'] += .01
    saved = {'return_branch_id': None, 'road_corridor': corridor,
             'published_calculated_at': now, 'published_at': now}
    checks = []
    monkeypatch.setattr(route_eta.auth, 'has_permission', lambda *args: True)
    monkeypatch.setattr(route_eta, 'context_for', lambda *args: {'scope': 'viewer-kps'})
    monkeypatch.setattr(route_eta, 'complete_itinerary', lambda *args: True)
    monkeypatch.setattr(route_eta, 'prepare_stops', lambda *args: viewer_stops)
    monkeypatch.setattr(route_eta, 'published_rows', lambda *args: [saved])
    def remaining(c, state, stops, driver):
        checks.append(('viewer_scope', stops))
        return (publisher_stops, viewer_stops)
    def validated(c, state, driver):
        checks.append(('publisher_scope', driver))
        return ({'scope': 'publisher-central'}, current_publisher_stops,
                current_publisher_stops, {'next_stop': viewer_stops[0]})
    monkeypatch.setattr(route_eta, 'remaining_publication', remaining)
    monkeypatch.setattr(route_eta, 'validated_publication', validated)
    view = route_eta.published_plan_for(object(), {'role': 'delivery_manager', 'brand_id': 'kps'}, 'driver-a')
    assert checks == [('viewer_scope', viewer_stops), ('publisher_scope', 'driver-a')]
    assert ('road_corridor' in view) is not publisher_changed
    if not publisher_changed:
        assert view['road_corridor'] == corridor
        assert view['next_stop']['brand_id'] == 'kps'
