from datetime import datetime, timedelta, timezone
import math

import httpx
import pytest

from app.route_corridor import corridor_alert, distance_to_geometry, fetch_geometry, validate_geometry

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
ROAD = {'type': 'LineString', 'coordinates': [[45., 28.], [45.1, 28.]]}


def point(age, lat=28.004, accuracy=10):
    return {'lat': lat, 'lng': 45.05, 'accuracy_m': accuracy,
            'captured_at': NOW - timedelta(seconds=age)}


def test_nearest_segment_not_vertex_distance():
    assert distance_to_geometry(point(0, lat=28), ROAD) < 0.01
    assert 440 < distance_to_geometry(point(0), ROAD) < 450
    assert 1100 < distance_to_geometry({'lat': 28., 'lng': 45.112}, ROAD) < 1200


def test_wrap_longitude_and_duplicate_vertices():
    road = {'type': 'LineString', 'coordinates': [[179.9, 0], [-179.9, 0]]}
    assert distance_to_geometry({'lat': 0, 'lng': 180}, road) < 0.01
    duplicate = {'type': 'LineString', 'coordinates': [[45, 28], [45, 28]]}
    assert distance_to_geometry({'lat': 28, 'lng': 45}, duplicate) == 0


@pytest.mark.parametrize('geometry', [None, {}, {'type': 'Polygon', 'coordinates': []},
    {'type': 'LineString', 'coordinates': [[45, 28]]},
    {'type': 'LineString', 'coordinates': [[45, 28], [math.nan, 28]]},
    {'type': 'LineString', 'coordinates': [[45, 28], [45, math.inf]]},
    {'type': 'LineString', 'coordinates': [[45, 28], [181, 28]]},
    {'type': 'LineString', 'coordinates': [[45, 28], [True, 28]]},
    {'type': 'LineString', 'coordinates': [[45, 28]] * 10001}])
def test_geometry_rejects_unbounded_or_invalid_coordinates(geometry):
    with pytest.raises(ValueError):
        validate_geometry(geometry)


def test_sustained_offroad_points_raise_road_corridor_alert():
    alert = corridor_alert([point(90), point(60), point(1)], ROAD, NOW - timedelta(minutes=5), NOW)
    assert alert['kind'] == 'route_corridor_deviation'
    assert alert['basis'] == 'road_geometry'
    assert 440 < alert['distance_m'] < 450


@pytest.mark.parametrize('points', [[point(90), point(1)], [point(90), point(60), point(1, lat=28)],
    [point(90), point(60), point(1, accuracy=150)], [point(300), point(150), point(1)],
    [point(300), point(270), point(240)], [point(90), point(90), point(1)],
    [point(50), point(30), point(1)], [point(90, accuracy=-1), point(60), point(1)]])
def test_insufficient_stale_inaccurate_gapped_or_onroad_evidence_is_silent(points):
    assert corridor_alert(points, ROAD, NOW - timedelta(minutes=10), NOW) is None


def test_new_route_does_not_reuse_old_points():
    assert corridor_alert([point(90), point(60), point(1)], ROAD, NOW - timedelta(seconds=30), NOW) is None
    assert corridor_alert([point(90), point(60), point(1)], None, NOW - timedelta(minutes=5), NOW) is None


def test_accuracy_buffer_suppresses_ambiguous_road_edges():
    assert corridor_alert([point(age, lat=28.0017, accuracy=100) for age in (90, 60, 1)],
                          ROAD, NOW - timedelta(minutes=5), NOW) is None


def test_provider_uses_fixed_driving_host_and_geojson():
    def handler(request):
        assert request.url.host == 'api.mapbox.com'
        assert request.url.path.startswith('/directions/v5/mapbox/driving/')
        assert request.url.params['geometries'] == 'geojson'
        assert request.url.params['alternatives'] == 'false'
        return httpx.Response(200, json={'code': 'Ok', 'routes': [{'geometry': ROAD}]})
    assert fetch_geometry({'lat': 28, 'lng': 45}, {'lat': 28, 'lng': 45.1}, 'test',
                          transport=httpx.MockTransport(handler)) == ROAD


@pytest.mark.parametrize('response', [httpx.Response(302, headers={'Location': 'https://other.invalid'}),
    httpx.Response(200, content=b'x' * 262145), httpx.Response(200, json={'code': 'NoRoute'}),
    httpx.Response(200, json={'code': 'Ok', 'routes': []})])
def test_provider_failure_never_invents_geometry(response):
    with pytest.raises(ValueError):
        fetch_geometry({'lat': 28, 'lng': 45}, {'lat': 28, 'lng': 45.1}, 'test',
                       transport=httpx.MockTransport(lambda request: response))
