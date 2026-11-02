"""Geographic policy trusts only the configured ingress, never client hints."""
import asyncio
import json

import pytest
from starlette.requests import Request

from app import staff_geo
from app.http_security import SecurityMiddleware


def request(path='/ops', *, peer='10.195.1.2', country='SA', ip='5.41.1.2', headers=(), method='GET'):
    items = [(b'host', b'testserver'), (b'origin', b'http://testserver'),
             (b'cf-ray', b'a4ff521a6e78c090-FRA')]
    if country is not None:
        items.append((b'cf-ipcountry', country.encode()))
    if ip is not None:
        items.append((b'cf-connecting-ip', ip.encode()))
    items.extend(headers)
    return Request({'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                    'path': path, 'raw_path': path.encode(), 'root_path': '', 'query_string': b'',
                    'method': method, 'scheme': 'http', 'server': ('testserver', 80),
                    'client': (peer, 1234), 'headers': items})


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('STAFF_GEO_MODE', 'enforce')
    monkeypatch.setenv('STAFF_GEO_SOURCE', 'render-cloudflare')
    monkeypatch.setenv('STAFF_GEO_TRUSTED_PROXY_CIDRS', '10.0.0.0/8')


def test_explicitly_configured_render_ingress_accepts_saudi_country(configured):
    info = staff_geo.location(request())
    assert info == {'country_code': 'SA', 'client_ip': '5.41.1.2', 'source': 'render-cloudflare'}
    staff_geo.require_domestic(request())


def test_headers_do_not_enable_an_unconfigured_source(monkeypatch):
    monkeypatch.delenv('STAFF_GEO_SOURCE', raising=False)
    assert staff_geo.location(request())['country_code'] is None


@pytest.mark.parametrize('peer', ['8.8.8.8', '127.0.0.1', '172.16.1.1', '192.168.1.1', '2001:4860:4860::8888'])
def test_direct_or_other_network_cannot_claim_saudi_using_forwarded_headers(configured, peer):
    assert staff_geo.location(request(peer=peer))['client_ip'] is None


@pytest.mark.parametrize('extra', [
    (b'cf-ipcountry', b'SA'), (b'cf-connecting-ip', b'5.41.1.2'),
    (b'cf-ray', b'a4ff521a6e78c091-FRA'), (b'cf-worker', b'untrusted.example'),
])
def test_duplicate_or_worker_headers_cannot_authorize(configured, extra):
    assert staff_geo.location(request(headers=[extra]))['country_code'] is None


@pytest.mark.parametrize('ip', [None, '', '10.1.1.1', '127.0.0.1', '169.254.1.1', '100.64.0.1',
                                  '5.41.1.2, 8.8.8.8', 'not-an-ip', 'fe80::1%eth0',
                                  '2a06:98c0:3600::103'])
def test_unknown_private_ambiguous_or_worker_ip_fails_closed(configured, ip):
    assert staff_geo.location(request(ip=ip))['client_ip'] is None


@pytest.mark.parametrize('country', [None, '', 'sa', 'SA,US', 'XX', 'T1', 'ZZ', 'SAUDI'])
def test_unknown_or_malformed_country_never_means_saudi(configured, country):
    assert staff_geo.location(request(country=country))['country_code'] is None


def test_other_country_and_ipv6_have_no_domestic_bypass(configured):
    req = request(country='US', ip='2001:4860:4860::8888', headers=[
        (b'x-forwarded-for', b'5.41.1.2'), (b'x-real-ip', b'5.41.1.2'),
        (b'forwarded', b'for=5.41.1.2'), (b'x-kps-context', b'customer')])
    assert staff_geo.location(req)['country_code'] == 'US'
    with pytest.raises(Exception) as error:
        staff_geo.require_domestic(req)
    assert error.value.status_code == 403


def test_mapped_ipv4_is_canonicalized(configured):
    assert staff_geo.location(request(ip='::ffff:5.41.1.2'))['client_ip'] == '5.41.1.2'


@pytest.mark.parametrize('value', ['', '0.0.0.0/0', '::/0', '8.0.0.0/8', 'not-a-network'])
def test_unsafe_proxy_configuration_cannot_start(configured, monkeypatch, value):
    monkeypatch.setenv('STAFF_GEO_TRUSTED_PROXY_CIDRS', value)
    with pytest.raises(RuntimeError):
        staff_geo.validate_configuration()


@pytest.mark.parametrize('path', ['/ops', '/ops.html', '/campaign-report', '/customer-crm', '/customer-report',
                                  '/assets/customer-report.js', '/assets/customer-report.css',
                                  '/driver-hours', '/owner-phone', '/api/auth/login',
                                  '/api/identity/staff/code/request', '/api/identity/staff/sms/verify'])
def test_work_entry_inventory(path):
    assert staff_geo.is_work_entry(request(path, headers=[(b'x-kps-context', b'customer')]))


async def _run(req):
    messages, effects = [], []
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}
    async def send(message):
        messages.append(message)
    async def app(scope, receive, send):
        effects.append('application')
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'allowed'})
    await SecurityMiddleware(app)(req.scope, receive, send)
    return messages, effects


@pytest.mark.parametrize('path', ['/', '/api/catalog', '/api/auth/otp/request', '/work-access',
                                  '/work-access.js', '/ops.css', '/health', '/ready', '/paymenthooks/moyasar'])
def test_public_customer_entry_health_and_webhooks_not_geoblocked(configured, path):
    messages, effects = asyncio.run(_run(request(path, country='US')))
    assert messages[0]['status'] == 200 and effects == ['application']


@pytest.mark.parametrize('path', ['/ops', '/ops.html', '/campaign-report'])
def test_foreign_admin_shell_is_denied_before_application_or_database(configured, path):
    messages, effects = asyncio.run(_run(request(path, country='US')))
    assert messages[0]['status'] == 403 and not effects
    assert b'ops.js' not in messages[1]['body']
    assert dict(messages[0]['headers'])[b'content-type'].startswith(b'text/html')


def test_saudi_admin_shell_allowed_and_foreign_printer_denied(configured):
    messages, effects = asyncio.run(_run(request()))
    assert messages[0]['status'] == 200 and effects
    messages, effects = asyncio.run(_run(request('/api/printing/bridge/next', country='US')))
    assert messages[0]['status'] == 403 and not effects
    assert json.loads(messages[1]['body'])['detail']['code'] == 'STAFF_GEO_RESTRICTED'


def test_observation_does_not_change_access_and_exposes_only_sanitized_evidence(configured, monkeypatch):
    monkeypatch.setenv('STAFF_GEO_MODE', 'observe')
    messages, effects = asyncio.run(_run(request(country='US')))
    assert messages[0]['status'] == 200 and effects
    headers = dict(messages[0]['headers'])
    assert headers[b'x-staff-geo-country'] == b'US'
    assert len(headers[b'x-staff-geo-client-digest']) == 16
    assert b'5.41.1.2' not in repr(headers).encode()
