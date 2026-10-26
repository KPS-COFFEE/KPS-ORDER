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
