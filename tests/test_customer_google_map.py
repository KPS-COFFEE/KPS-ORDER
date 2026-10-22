"""Google customer picker gates use only synthetic browser credentials; no Google calls."""
import subprocess
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.customer_map import public_config
from app.http_security import SecurityMiddleware

ROOT = Path(__file__).resolve().parents[1]
KEY = 'AIza' + 'a' * 35


def mapbox(monkeypatch):
    monkeypatch.setenv('MAPBOX_ENABLED', 'true')
    monkeypatch.setenv('MAPBOX_CUSTOMER_ENABLED', 'true')
    monkeypatch.setenv('MAPBOX_PUBLIC_TOKEN_KPS', 'pk.' + 'a' * 30)


def google(monkeypatch):
    monkeypatch.setenv('CUSTOMER_MAP_PROVIDER_KPS', 'google')
    monkeypatch.setenv('GOOGLE_MAPS_CUSTOMER_ENABLED', 'true')
    monkeypatch.setenv('GOOGLE_MAPS_BROWSER_KEY_KPS', KEY)


def test_google_preparation_does_not_switch_existing_mapbox(monkeypatch):
    mapbox(monkeypatch)
    monkeypatch.setenv('GOOGLE_MAPS_BROWSER_KEY_KPS', KEY)
    monkeypatch.setenv('GOOGLE_MAPS_CUSTOMER_ENABLED', 'true')
    assert public_config('kps')['provider'] == 'mapbox'
    assert 'browser_key' not in public_config('kps')
    monkeypatch.setenv('CUSTOMER_MAP_PROVIDER_KPS', 'google')
    monkeypatch.setenv('GOOGLE_MAPS_CUSTOMER_ENABLED', 'false')
    assert public_config('kps')['provider'] == 'mapbox'


def test_google_is_explicit_brand_scoped_and_independent_of_staff_switch(monkeypatch):
    google(monkeypatch)
    assert public_config('kps') == {'enabled': True, 'provider': 'google', 'browser_key': KEY}
    assert public_config('enbishan') == {'enabled': False}
    assert public_config('../kps') == {'enabled': False}
    monkeypatch.setenv('MAPBOX_ENABLED', 'false')
    assert public_config('kps')['provider'] == 'google'
    monkeypatch.setenv('GOOGLE_MAPS_BROWSER_KEY_ENBISHAN', 'AIza' + 'b' * 35)
    assert public_config('enbishan') == {'enabled': False}, 'A key alone never activates another brand'
    monkeypatch.setenv('CUSTOMER_MAP_PROVIDER_ENBISHAN', 'google')
    assert public_config('enbishan')['browser_key'] != public_config('kps')['browser_key']


@pytest.mark.parametrize('key', ['', 'sk.' + 'a' * 40, 'server-secret', KEY+'\n', KEY+'?inject=1'])
def test_missing_or_malformed_google_setup_keeps_mapbox(monkeypatch, key):
    mapbox(monkeypatch)
    google(monkeypatch)
    monkeypatch.setenv('GOOGLE_MAPS_BROWSER_KEY_KPS', key)
    config = public_config('kps')
    assert config['provider'] == 'mapbox'
    assert 'browser_key' not in config
    monkeypatch.setenv('MAPBOX_CUSTOMER_ENABLED', 'false')
    assert public_config('kps') == {'enabled': False}


def test_google_csp_only_on_selected_customer_pages_and_staff_policy_unchanged(monkeypatch):
    app = FastAPI()
    app.add_middleware(SecurityMiddleware)
    @app.get('/')
    @app.get('/index.html')
    @app.get('/ops')
    @app.get('/ops.html')
    @app.get('/delivery-track')
    @app.get('/unrelated')
    def page(): return {'ok': True}
    client = TestClient(app)
    mapbox(monkeypatch)
    before = {path:dict(client.get(path).headers) for path in ('/ops', '/ops.html', '/delivery-track', '/unrelated')}
    google(monkeypatch)
    for path in ('/', '/index.html'):
        response = client.get(path)
        policy = response.headers['content-security-policy']
        assert 'https://api.mapbox.com' not in policy
        script = policy.split('script-src ',1)[1].split(';',1)[0]
        assert 'unsafe-' not in script and '*' not in script and 'data:' not in script
        # The bootstrap imports vendor modules at other paths; allow both exact origins.
        assert set(script.split())=={"'self'",'https://maps.googleapis.com','https://maps.gstatic.com'}
        assert 'frame-ancestors \'none\'' in policy and "form-action 'self'" in policy
        assert response.headers['referrer-policy'] == 'strict-origin-when-cross-origin'
        assert response.headers['permissions-policy'] == 'camera=(), microphone=(), geolocation=(self)'
    for path, headers in before.items():
        response = client.get(path)
        for field in ('content-security-policy','referrer-policy','permissions-policy'):
            assert response.headers[field] == headers[field]
    # Another brand uses its own selected provider, not KPS's browser key or policy.
    assert 'maps.googleapis.com' not in client.get('/?brand_id=enbishan').headers['content-security-policy']
    monkeypatch.setenv('GOOGLE_MAPS_BROWSER_KEY_KPS', '')
    assert 'maps.googleapis.com' not in client.get('/').headers['content-security-policy']
    assert 'api.mapbox.com' in client.get('/').headers['content-security-policy']


def test_actual_google_picker_logic_in_node_without_network():
    subprocess.run(['node', 'tests/test_customer_google_map.cjs'], cwd=ROOT, check=True,
                   capture_output=True, text=True)


def test_bound_storefront_rejects_another_brand_before_reading_google_config(monkeypatch):
    from contextlib import contextmanager
    from app import customer_map
    google(monkeypatch)
    monkeypatch.setenv('CUSTOMER_MAP_PROVIDER_ENBISHAN', 'google')
    monkeypatch.setenv('GOOGLE_MAPS_BROWSER_KEY_ENBISHAN', 'AIza'+'b'*35)
    class Connection:
        def execute(self, *args): return self
        def fetchone(self): return {'exists': True}
    @contextmanager
    def isolated_brand_lookup(): yield Connection()
    monkeypatch.setattr(customer_map.db, 'tx', isolated_brand_lookup)
    app=FastAPI()
    app.add_middleware(SecurityMiddleware)
    app.include_router(customer_map.router)
    client=TestClient(app,base_url='https://order.kps.sa')
    response=client.get('/api/customer-map/config?brand_id=enbishan')
    assert response.status_code==403
    assert 'browser_key' not in response.text and 'AIza' not in response.text
    response=client.get('/api/customer-map/config?brand_id=kps')
    assert response.status_code==200
    assert response.json()=={'enabled':True,'provider':'google','browser_key':KEY}
    assert response.headers['cache-control']=='no-store'
