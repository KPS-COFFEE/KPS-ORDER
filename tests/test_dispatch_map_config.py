from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import auth, dispatch_map
from app.http_security import SecurityMiddleware


def test_staff_map_scoped_to_authenticated_brand_and_explicit_enable(monkeypatch):
    app = FastAPI()
    app.include_router(dispatch_map.router)
    principal = {'role':'delivery_manager','brand_id':'kps'}
    app.dependency_overrides[auth.current_user] = lambda: principal
    client = TestClient(app)
    monkeypatch.setenv('MAPBOX_PUBLIC_TOKEN_KPS', 'pk.'+'a'*30)
    monkeypatch.setenv('MAPBOX_PUBLIC_TOKEN_ENBISHAN', 'pk.'+'b'*30)
    monkeypatch.setenv('MAPBOX_ENABLED', 'false')
    assert client.get('/api/operations/map-config').json() == {'enabled':False}
    monkeypatch.setenv('MAPBOX_ENABLED', 'true')
    assert client.get('/api/operations/map-config?brand_id=enbishan').json()['public_token'] == 'pk.'+'a'*30
    principal['role'] = 'customer'
    assert client.get('/api/operations/map-config').status_code == 403
    principal['role'] = 'delivery_staff'
    monkeypatch.setenv('MAPBOX_PUBLIC_TOKEN_KPS', 'sk.'+'a'*30)
    assert client.get('/api/operations/map-config').json() == {'enabled':False}


def test_map_csp_does_not_change_any_customer_document_headers(monkeypatch):
    app = FastAPI()
    app.add_middleware(SecurityMiddleware)
    @app.get('/')
    @app.get('/index.html')
    @app.get('/ops')
    @app.get('/ops.html')
    @app.get('/unrelated')
    def page(): return {'ok':True}
    client = TestClient(app)
    keys = ['content-security-policy','referrer-policy','permissions-policy']
    monkeypatch.setenv('MAPBOX_ENABLED','false')
    baseline = {path:{key:client.get(path).headers[key] for key in keys}
                for path in ['/', '/index.html', '/unrelated']}
    assert 'mapbox.com' not in client.get('/ops').headers['content-security-policy']
    monkeypatch.setenv('MAPBOX_ENABLED','true')
    for path, headers in baseline.items():
        actual = client.get(path).headers
        assert {key:actual[key] for key in keys} == headers
    for path in ['/ops','/ops.html']:
        headers = client.get(path).headers
        assert 'https://api.mapbox.com/mapbox-gl-js/v3.33.0/' in headers['content-security-policy']
        assert "https://api.mapbox.com/directions/v5/mapbox/driving/" in headers["content-security-policy"]
        assert "'unsafe-eval'" not in headers['content-security-policy']
        assert headers['referrer-policy'] == 'strict-origin-when-cross-origin'
