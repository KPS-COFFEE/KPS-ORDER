"""Host cutover boundary contract: no database or production changes."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.host_policy import OfficialHostMiddleware


def client(monkeypatch, enabled=True):
    monkeypatch.setenv('KPS_OFFICIAL_HOST_ENFORCEMENT_ENABLED', 'true' if enabled else 'false')
    monkeypatch.setenv('KPS_OFFICIAL_HOSTS', 'order.kps.sa,admin.kps.sa,staff.kps.sa')
    monkeypatch.setenv('KPS_INTERNAL_HEALTH_HOSTS', 'kps-order-emergency-v4.onrender.com')
    app = FastAPI()
    @app.api_route('/{path:path}', methods=['GET', 'POST', 'HEAD'])
    def endpoint(path):
        return {'ok': True}
    app.add_middleware(OfficialHostMiddleware)
    return TestClient(app, base_url='https://order.kps.sa', follow_redirects=False)


def test_default_off_preserves_existing_render_and_branch_devices(monkeypatch):
    with client(monkeypatch, enabled=False) as c:
        assert c.post('/api/branch-printing/heartbeat', headers={'Host': 'kps-order-emergency-v4.onrender.com'}).status_code == 200


@pytest.mark.parametrize('host', ['order.kps.sa', 'admin.kps.sa', 'staff.kps.sa', 'ORDER.KPS.SA:443'])
def test_official_hosts_keep_login_api_and_print_traffic(monkeypatch, host):
    with client(monkeypatch) as c:
        for path in ('/', '/ops', '/api/session', '/api/branch-printing/heartbeat'):
            assert c.post(path, headers={'Host': host}).status_code == 200


@pytest.mark.parametrize('host', ['kps-order-emergency-v4.onrender.com', 'evil.test', 'order.kps.sa.evil.test', 'order.kps.sa.', 'order.kps.sa:8443', 'order.kps.sa,evil.test'])
def test_rejects_actual_host_without_redirect_even_if_forwarded_host_official(monkeypatch, host):
    with client(monkeypatch) as c:
        response = c.get('/ops', headers={'Host': host, 'X-Forwarded-Host': 'order.kps.sa', 'Forwarded': 'host=order.kps.sa'})
        assert response.status_code == 421
        assert 'location' not in response.headers
        assert response.json()['detail']['code'] == 'OFFICIAL_HOST_REQUIRED'


def test_internal_host_only_exact_readonly_probe_paths(monkeypatch):
    with client(monkeypatch) as c:
        headers = {'Host': 'kps-order-emergency-v4.onrender.com'}
        for path in ('/health', '/ready'):
            assert c.get(path, headers=headers).status_code == 200
            assert c.head(path, headers=headers).status_code == 200
            assert c.post(path, headers=headers).status_code == 421
        for path in ('/health/', '/ready/', '/api/session', '/ops', '/'):
            assert c.get(path, headers=headers).status_code == 421
        assert c.get('/health', headers={'Host': 'unknown.onrender.com'}).status_code == 421


def test_rejects_duplicate_host_headers(monkeypatch):
    with client(monkeypatch) as c:
        assert c.get('/', headers=[('Host', 'order.kps.sa'), ('Host', 'evil.test')]).status_code == 421


@pytest.mark.parametrize('hosts', ['*', '*.kps.sa', 'https://order.kps.sa', 'order.kps.sa:443', '', 'kps-order-emergency-v4.onrender.com'])
def test_invalid_cutover_configuration_fails_closed(monkeypatch, hosts):
    monkeypatch.setenv('KPS_OFFICIAL_HOST_ENFORCEMENT_ENABLED', 'true')
    monkeypatch.setenv('KPS_OFFICIAL_HOSTS', hosts)
    with pytest.raises(ValueError):
        OfficialHostMiddleware(None)


def test_no_rollout_configuration_has_no_effect(monkeypatch):
    monkeypatch.delenv('KPS_OFFICIAL_HOST_ENFORCEMENT_ENABLED', raising=False)
    middleware = OfficialHostMiddleware(None)
    assert middleware.enabled is False


def test_websocket_does_not_bypass_host_boundary(monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    with client(monkeypatch) as c:
        with pytest.raises(WebSocketDisconnect) as exc:
            with c.websocket_connect('/socket', headers={'Host': 'kps-order-emergency-v4.onrender.com'}):
                pass
        assert exc.value.code == 1008
