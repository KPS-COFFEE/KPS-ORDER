import httpx
import pytest
from fastapi import HTTPException
from app import catalog


ROUTE = ('https://www.google.com/maps/dir/28.4273846,45.9577043/'
         '28.4274006,45.9575348/@28.4276237,45.9599341,17z/'
         'data=!3m1!4b1!4m5!4m4!1m1!4e1!1m0!3e0!5m2!1e4!1e1?entry=tts')


def test_shared_route_extracts_arrival_not_origin_or_camera(monkeypatch):
    seen = []
    client = httpx.Client
    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={'location': ROUTE})
    monkeypatch.setattr(httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(handle), **kw))
    result = catalog.parse_map_url(catalog.MapURLBody(url='https://maps.app.goo.gl/SYNTHETIC'))
    assert result == {'location': {'lat': 28.4274006, 'lng': 45.9575348},
                      'source': 'customer_selected', 'location_is_device_verified': False}
    assert len(seen) == 1


@pytest.mark.parametrize('path', [
    '/maps/dir/28,45/@29,46,17z',
    '/maps/dir/28,45/Somewhere/@29,46,17z',
    '/maps/dir/28,45/91,45/@29,46,17z',
    '/maps/@28,45,17z',
])
def test_route_never_guesses_missing_or_invalid_destination(path):
    assert catalog.map_route_destination(path) is None
    with pytest.raises(HTTPException):
        catalog.parse_map_url(catalog.MapURLBody(url='https://www.google.com' + path))


@pytest.mark.parametrize('path', [
    '/maps/dir/28,45/29,46/30,47/@31,48,17z',
    '/maps/dir//30%2C47/',
])
def test_route_supports_last_stop_and_current_location_origin(path):
    assert catalog.map_route_destination(path) == '30,47'


def test_route_does_not_expand_trusted_hosts():
    assert catalog.safe_map_url(ROUTE.replace('www.google.com', 'evil.example')) is None
    assert catalog.safe_map_url(ROUTE.replace('www.google.com', 'www.google.com.evil.example')) is None
