from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.customer_map import public_config
from app.http_security import SecurityMiddleware


def test_map_is_opt_in_brand_specific_and_never_returns_secret_token(monkeypatch):
    monkeypatch.delenv('MAPBOX_ENABLED', raising=False)
    monkeypatch.setenv('MAPBOX_PUBLIC_TOKEN_KPS', 'pk.'+'a'*30)
    assert public_config('kps')=={'enabled':False}
    monkeypatch.setenv('MAPBOX_ENABLED','true')
    assert public_config('kps')['enabled']
    monkeypatch.delenv('MAPBOX_PUBLIC_TOKEN_ENBISHAN',raising=False)
    assert public_config('enbishan')=={'enabled':False}
    monkeypatch.setenv('MAPBOX_PUBLIC_TOKEN_KPS','sk.'+'a'*30)
    assert public_config('kps')=={'enabled':False}


def test_csp_map_allowlist_only_when_enabled_and_only_map_surfaces(monkeypatch):
    app=FastAPI()
    app.add_middleware(SecurityMiddleware)
    @app.get('/')
    @app.get('/ops')
    @app.get('/unrelated')
    def page():return {'ok':True}
    client=TestClient(app)
    monkeypatch.delenv('MAPBOX_ENABLED', raising=False)
    assert 'mapbox.com' not in client.get('/').headers['content-security-policy']
    assert 'mapbox.com' not in client.get('/ops').headers['content-security-policy']
    monkeypatch.setenv('MAPBOX_ENABLED','true')
    response=client.get('/')
    assert 'worker-src' in response.headers['content-security-policy']
    assert "'unsafe-eval'" not in response.headers['content-security-policy']
    assert 'https://api.mapbox.com/mapbox-gl-js/v3.33.0/' in response.headers['content-security-policy']
    assert response.headers['referrer-policy']=='strict-origin-when-cross-origin'
    assert 'https://api.mapbox.com/mapbox-gl-js/v3.33.0/' in client.get('/ops').headers['content-security-policy']
    assert 'mapbox.com' not in client.get('/unrelated').headers['content-security-policy']
