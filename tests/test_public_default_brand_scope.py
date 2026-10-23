"""Real host-bound public routes must never resolve omitted input to another brand."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import auth, catalog, db, storefront_scope
from app.main import app
from test_enbishan_dual_brand import dual
from test_multibrand_final_boundary import mapped_runtime


@pytest.mark.parametrize('brand', ['kps', 'enbishan', None])
def test_body_defaults_use_request_context_without_changing_unbound_kps(brand):
    token = storefront_scope.current_brand.set(brand)
    try:
        expected = brand or 'kps'
        assert auth.OTPRequestBody(phone='0500000000').brand_id == expected
        assert auth.GuestBody(phone='0500000000', display_name='TEST').brand_id == expected
        assert auth.EmailRequestBody(email='test@example.invalid').brand_id == expected
        assert auth.StaffCodeRequestBody(phone='0500000000').brand_id == expected
        assert catalog.PublicNearestBody(location={'lat': 28.43, 'lng': 45.97}, service='delivery').brand_id == expected
    finally:
        storefront_scope.current_brand.reset(token)


def test_unrelated_route_query_is_not_modified(monkeypatch):
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from app.http_security import SecurityMiddleware
    mapped_runtime(monkeypatch)
    child = Starlette(routes=[Route('/unrelated', lambda request: PlainTextResponse(request.scope['query_string'].decode()))])
    child.add_middleware(SecurityMiddleware)
    assert TestClient(child, base_url='https://order.enbishan.com').get('/unrelated?opaque=unchanged').text == 'opaque=unchanged'


@pytest.mark.parametrize('path', ['/privacy', '/terms', '/refunds'])
def test_actual_legal_routes_default_to_bound_brand(monkeypatch, path):
    mapped_runtime(monkeypatch)
    monkeypatch.setenv('APP_ALLOWED_HOSTS', 'testserver,order.kps.sa,order.enbishan.com')
    monkeypatch.setenv('KPS_STOREFRONT_HOST_BRANDS', '{"testserver":"enbishan","order.kps.sa":"kps","order.enbishan.com":"enbishan"}')
    response = TestClient(app).get(path)
    assert response.status_code == 200
    assert 'brand_id=enbishan' in response.text
    assert 'KPS COFFEE' not in response.text
    foreign = TestClient(app).get(path+'?brand_id=kps')
    assert foreign.status_code == 403


def test_verified_work_public_route_still_rejects_unknown_virtual_host(monkeypatch):
    mapped_runtime(monkeypatch)
    request = Request({'type':'http', 'scheme':'https', 'method':'GET', 'path':'/api/catalog',
                       'query_string':b'', 'headers':[(b'host',b'foreign.invalid'),(b'x-kps-context',b'work')]})
    request.state.verified_work_storefront = 'kps'
    with pytest.raises(HTTPException) as error:
        storefront_scope.bound_brand(request)
    assert error.value.status_code == 404


@pytest.mark.postgres
def test_actual_public_defaults_and_post_bodies_never_read_or_create_kps(dual, monkeypatch):
    client, _, _, _, _ = dual
    client.headers.pop('X-KPS-Context', None)
    client.headers.pop('X-Operations-Brand', None)
    client.cookies.clear()
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://enbishan.test')
    with db.tx() as c:
        c.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled) VALUES('enbishan','guest','active',true) ON CONFLICT(brand_id,channel) DO UPDATE SET state='active',enabled=true")
        c.execute("UPDATE app_settings SET settings=settings||'{\"customer_phone_required\":true}'::jsonb WHERE brand_id='enbishan'")
        c.execute("UPDATE app_settings SET settings=jsonb_set(settings,'{storefront_advertising}','{\"advertising_enabled\":true}'::jsonb) WHERE brand_id IN ('kps','enbishan')")
        c.execute("INSERT INTO banners(id,brand_id,title,active) VALUES('scope-kps-banner','kps','KPS SYNTHETIC',true),('scope-enbishan-banner','enbishan','ENBISHAN SYNTHETIC',true)")
    data = client.get('/api/catalog').json()
    assert data['brand']['id'] == 'enbishan'
    assert data['products'] and all(p['brand_id'] == 'enbishan' for p in data['products'])
    branches = client.get('/api/branches').json()['branches']
    assert branches and all(b['brand_id'] == 'enbishan' for b in branches)
    # Readiness differs by brand; the omitted value must use ENBISHAN settings.
    assert client.get('/api/identity/channels').json()['customer_phone_required'] is True
    assert [banner['id'] for banner in client.get('/api/banners').json()['banners']] == ['scope-enbishan-banner']
    for path in ['/api/catalog', '/api/branches', '/api/banners', '/api/identity/channels',
                 '/api/customer-map/config', '/api/customer/legal', '/api/experience/public',
                 '/privacy', '/terms', '/refunds']:
        correct = client.get(path)
        assert correct.status_code == 200, (path, correct.text)
        rejected = client.get(path, params={'brand_id': 'kps'})
        assert rejected.status_code == 403, (path, rejected.text)
    seen = []
    def reject_unapproved_provider(provider, brand):
        seen.append(brand)
        raise HTTPException(503, {'code': 'SYNTHETIC_NO_SEND'})
    monkeypatch.setattr(auth, '_customer_provider_ready', reject_unapproved_provider)
    otp = client.post('/api/auth/otp/request', json={'phone': '0500000000'})
    assert otp.status_code == 503 and seen == ['enbishan']
    # Explicitly foreign requests fail before even provider-readiness evaluation.
    assert client.post('/api/auth/otp/request', json={'phone':'0500000000','brand_id':'kps'}).status_code == 403
    assert seen == ['enbishan']
    with db.tx() as c:
        c.execute("UPDATE app_settings SET settings=settings||'{\"customer_phone_required\":false}'::jsonb WHERE brand_id='enbishan'")
    guest = client.post('/api/identity/guest', json={'phone': '0500000098', 'display_name': 'SYNTHETIC DEFAULT SCOPE'})
    assert guest.status_code == 200, guest.text
    assert guest.json()['user']['brand_id'] == 'enbishan'
    with db.tx() as c:
        assert c.execute("SELECT brand_id FROM users WHERE display_name='SYNTHETIC DEFAULT SCOPE'").fetchone()['brand_id'] == 'enbishan'
        assert c.execute('SELECT count(*) n FROM otp_challenges WHERE id LIKE %s', ('otp_%',)).fetchone()['n'] == 0


@pytest.mark.postgres
def test_verified_work_public_reads_require_current_projected_brand(dual, monkeypatch):
    client, actor, _, _, _ = dual
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://kps.test')
    # Remove the fixture membership; the work-context header is no authority.
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-manager'")
    current = actor('manager')
    assert current.get('/api/catalog').json()['brand']['id'] == 'kps'
    denied = current.get('/api/catalog?brand_id=enbishan')
    assert denied.status_code == 403, denied.text
    denied = actor('manager', 'enbishan').get('/api/catalog?brand_id=enbishan')
    assert denied.status_code == 403
    # Restoring an explicit grant allows projection; omitted scope then resolves
    # to that fresh principal, not the KPS host or original home brand.
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=true WHERE user_id='dual-manager'")
    projected = actor('manager', 'enbishan')
    assert projected.get('/api/catalog').json()['brand']['id'] == 'enbishan'
    assert projected.get('/api/catalog?brand_id=kps').status_code == 403
    # A customer's forged work header cannot create a verified staff override.
    forged = actor('enbishan-customer')
    forged.headers['X-KPS-Context'] = 'work'
    response = forged.get('/api/catalog?brand_id=enbishan')
    assert response.status_code == 403
    assert forged.get('/api/session').status_code in {401, 403}
