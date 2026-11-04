"""Bounded road geometry and sustained GPS evidence for staff route alerts."""
import math
from datetime import timedelta

import httpx

EARTH_METERS = 6371008.8
MAX_VERTICES = 10000


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _coordinate(lng, lat):
    return _number(lng) and _number(lat) and -180 <= lng <= 180 and -90 <= lat <= 90


def validate_geometry(value):
    if not isinstance(value, dict) or value.get('type') != 'LineString':
        raise ValueError('road_geometry_invalid')
    coordinates = value.get('coordinates')
    if not isinstance(coordinates, list) or not 2 <= len(coordinates) <= MAX_VERTICES:
        raise ValueError('road_geometry_invalid')
    normalized = []
    for position in coordinates:
        if not isinstance(position, (list, tuple)) or len(position) != 2 or not _coordinate(*position):
            raise ValueError('road_geometry_invalid')
        normalized.append([float(position[0]), float(position[1])])
    return {'type': 'LineString', 'coordinates': normalized}


def fetch_geometry(origin, target, token, transport=None):
    """One Directions request; caller must reserve the shared budget before I/O."""
    for point in (origin, target):
        if not isinstance(point, dict) or not _coordinate(point.get('lng'), point.get('lat')):
            raise ValueError('road_geometry_invalid')
    coordinates = ';'.join(f"{p['lng']},{p['lat']}" for p in (origin, target))
    with httpx.Client(timeout=httpx.Timeout(4.0, connect=2.0), follow_redirects=False,
                      trust_env=False, transport=transport) as client:
        response = client.get('https://api.mapbox.com/directions/v5/mapbox/driving/' + coordinates,
                              params={'access_token': token, 'geometries': 'geojson',
                                      'overview': 'full', 'alternatives': 'false', 'steps': 'false'})
        if response.status_code != 200 or len(response.content) > 262144:
            raise ValueError('road_geometry_unavailable')
        value = response.json()
        if not isinstance(value, dict) or value.get('code') != 'Ok':
            raise ValueError('road_geometry_unavailable')
        routes = value.get('routes')
        if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
            raise ValueError('road_geometry_unavailable')
        return validate_geometry(routes[0].get('geometry'))


def _wrapped_degrees(value):
    return (value + 180) % 360 - 180


def distance_to_geometry(point, geometry):
    """Nearest segment distance in a local tangent projection, in meters."""
    if not isinstance(point, dict) or not _coordinate(point.get('lng'), point.get('lat')):
        raise ValueError('road_geometry_invalid')
    vertices = validate_geometry(geometry)['coordinates']
    scale = math.cos(math.radians(point['lat']))
    unit = math.pi / 180 * EARTH_METERS
    best = math.inf
    for start, end in zip(vertices, vertices[1:]):
        x1 = _wrapped_degrees(start[0] - point['lng']) * scale * unit
        y1 = (start[1] - point['lat']) * unit
        x2 = x1 + _wrapped_degrees(end[0] - start[0]) * scale * unit
        y2 = (end[1] - point['lat']) * unit
        dx, dy = x2 - x1, y2 - y1
        length2 = dx * dx + dy * dy
        fraction = min(1., max(0., -(x1 * dx + y1 * dy) / length2)) if length2 else 0.
        best = min(best, math.hypot(x1 + fraction * dx, y1 + fraction * dy))
    return best


def corridor_alert(points, geometry, calculated_at, now):
    """Reject stale/history-only GPS, gaps, and observations predating this road."""
    if not calculated_at or calculated_at > now:
        return None
    try:
        validate_geometry(geometry)
    except ValueError:
        return None
    recent = sorted((p for p in points if
                     _number(p.get('accuracy_m')) and 0 <= p['accuracy_m'] <= 100 and
                     _coordinate(p.get('lng'), p.get('lat')) and
                     max(calculated_at, now - timedelta(minutes=5)) <= p['captured_at'] <= now),
                    key=lambda p: p['captured_at'])
    if len(recent) < 3 or (now - recent[-1]['captured_at']).total_seconds() > 120:
        return None
    # Distinct captures and uninterrupted observations are essential evidence.
    gaps = [(b['captured_at'] - a['captured_at']).total_seconds() for a, b in zip(recent, recent[1:])]
    span = (recent[-1]['captured_at'] - recent[0]['captured_at']).total_seconds()
    if span < 60 or any(gap <= 0 or gap > 120 for gap in gaps):
        return None
    distances = [distance_to_geometry(p, geometry) for p in recent]
    if any(distance <= max(150, p['accuracy_m'] * 2) for p, distance in zip(recent, distances)):
        return None
    return {'kind': 'route_corridor_deviation', 'basis': 'road_geometry',
            'distance_m': round(distances[-1]), 'observed_seconds': round(span),
            'suggested_action': 'review_route_with_driver'}
