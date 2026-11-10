"""KPS host binding must be enforced before endpoints or identity writes."""
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from app.main import app
from app import auth, storefront_scope


@pytest.mark.parametrize('lang', ['ar', 'en'])
@pytest.mark.parametrize('brand', ['new-brand', 'INVALID/brand', ''])
def test_contact_html_never_contains_kps_fallback(lang, brand):
    response = TestClient(app).get('/', params={'brand_id': brand, 'lang': lang})
    assert response.status_code == 200
    for contact in ['KPS COFFEE', 'كبز قهوة مختصة', '0550472662', '966550472662', 'kps.coffe', 'Kps.cofee']:
        assert contact not in response.text
    assert 'public-contact-support' in response.text
    assert response.headers['cache-control'] == 'no-store'


def test_kps_html_preserves_approved_presentation():
    response = TestClient(app).get('/')
    assert response.text == (Path(__file__).resolve().parents[1] / 'public/index.html').read_text()


@pytest.mark.parametrize('host', sorted(storefront_scope.KPS_HOSTS))
@pytest.mark.parametrize('path', ['/', '/api/catalog', '/api/branches', '/privacy', '/terms', '/refunds', '/api/banners', '/api/identity/channels'])
def test_bound_deployment_rejects_other_brand_even_with_forged_headers(monkeypatch, host, path):
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://' + host)
    response = TestClient(app).get(path, params={'brand_id': 'other-brand'},
                                   headers={'X-Forwarded-Host': 'other.invalid', 'Forwarded': 'host=other.invalid'})
    assert response.status_code == 403
    assert response.json()['detail']['code'] == 'STOREFRONT_BRAND_MISMATCH'


@pytest.mark.parametrize('path', ['/api/orders', '/api/identity/guest', '/api/auth/otp/request'])
def test_bound_deployment_rejects_body_scope_before_writes(monkeypatch, path):
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://order.kps.sa')
    response = TestClient(app).post(path, json={'brand_id': 'other-brand'}, headers={'Origin': 'https://order.kps.sa'})
    assert response.status_code == 403
    assert response.json()['detail']['code'] == 'STOREFRONT_BRAND_MISMATCH'


def test_duplicate_scope_rejected(monkeypatch):
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://order.kps.sa')
    assert TestClient(app).get('/?brand_id=kps&brand_id=other').status_code == 400


def test_host_binding_does_not_trust_forwarded_host(monkeypatch):
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://shared.invalid')
    request = Request({'type':'http', 'scheme':'https', 'path':'/', 'query_string':b'',
                       'headers':[(b'host',b'order.kps.sa'), (b'x-forwarded-host',b'other.invalid')]})
    assert storefront_scope.bound_brand(request) == 'kps'


def test_foreign_session_cannot_be_created_on_kps():
    token = storefront_scope.current_brand.set('kps')
    try:
        with pytest.raises(HTTPException) as error:
            auth.establish_session(None, {'brand_id':'other'}, None)
        assert error.value.status_code == 403
    finally:
        storefront_scope.current_brand.reset(token)


@pytest.mark.parametrize('media_type', ['', 'application/problem+json', 'application/json; charset=utf-8'])
def test_json_content_types_cannot_bypass_scope(monkeypatch, media_type):
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://order.kps.sa')
    headers = {'Origin': 'https://order.kps.sa'}
    if media_type:
        headers['Content-Type'] = media_type
    response = TestClient(app).post('/api/orders', content='{"brand_id":"other"}', headers=headers)
    assert response.status_code == 403


@pytest.mark.postgres
def test_real_foreign_session_and_catalog_are_rejected(client, monkeypatch):
    import secrets
    from app import db
    session_token = secrets.token_urlsafe(32)
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('scope-other','TEST ONLY other brand')")
        conn.execute("INSERT INTO users(id,display_name,role,brand_id) VALUES('scope-user','TEST ONLY','customer','scope-other')")
        conn.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES('scope-session',%s,%s,'scope-user',now()+interval '1 hour')", (auth.token_hash(session_token),auth.token_hash('test-only-csrf')))
    client.cookies.set(auth.COOKIE, session_token)
    assert client.get('/api/session').json()['user']['brand_id'] == 'scope-other'
    assert client.get('/api/catalog?brand_id=scope-other').status_code == 200
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://order.kps.sa')
    for path in ['/api/session', '/api/orders', '/api/catalog?brand_id=scope-other', '/api/branches?brand_id=scope-other']:
        response = client.get(path)
        assert response.status_code == 403, (path,response.text)
    client.cookies.clear()
    assert client.get('/api/catalog').json()['brand']['id'] == 'kps'
    assert all(row['brand_id'] == 'kps' for row in client.get('/api/branches').json()['branches'])
    with db.tx() as conn:
        assert conn.execute("SELECT revoked_at FROM sessions WHERE id='scope-session'").fetchone()['revoked_at'] is None
