"""Unapproved provider selection must stay closed without changing the KPS fallback.

No real provider requests, credentials, DNS or database access.
"""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app import auth


def test_authentica_cannot_be_selected_by_owner_settings():
    with pytest.raises(ValidationError):
        auth.ChannelUpdateBody(channel='sms', state='testing', enabled=False,
                               sms_provider='authentica')


def test_authentica_secret_does_not_enable_global_provider(monkeypatch):
    monkeypatch.setenv('OTP_PROVIDER', 'authentica')
    monkeypatch.setenv('AUTHENTICA_API_KEY', 'synthetic_never_sent')
    with pytest.raises(HTTPException) as result:
        auth.provider_config()
    assert result.value.status_code == 503
    assert result.value.detail['code'] == 'OTP_PROVIDER_UNAVAILABLE'
    assert 'synthetic' not in str(result.value.detail)


def test_authentica_readiness_cannot_reuse_existing_kps_credentials(monkeypatch):
    monkeypatch.setenv('TAWKED_API_KEY', 'tk_test_synthetic_never_sent')
    monkeypatch.setenv('AUTHENTICA_API_KEY', 'synthetic_never_sent')
    with pytest.raises(HTTPException) as result:
        auth._provider_ready('authentica')
    assert result.value.status_code == 503
    assert result.value.detail['code'] == 'OTP_PROVIDER_UNAVAILABLE'


def test_brand_specific_provider_selection_does_not_change_global_fallback(monkeypatch):
    monkeypatch.setenv('OTP_PROVIDER', 'tawked')
    # A future integration must extend the reviewed allow-list/adapter. Merely
    # reading a brand selection must not mutate the KPS default.
    assert auth._selected_sms_provider({'sms_provider': 'disabled'}) == 'disabled'
    assert auth._selected_sms_provider({}) == 'tawked'
    assert auth._selected_sms_provider({'sms_provider': 'twilio_verify'}) == 'twilio_verify'
    assert auth._selected_sms_provider({}) == 'tawked'


def _transport(monkeypatch, handler):
    import httpx
    from app import authentica_verification
    original = httpx.Client
    options = []
    def client(**kwargs):
        options.append(kwargs)
        return original(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(authentica_verification.httpx, 'Client', client)
    return authentica_verification, options


def test_authentica_v2_transport_contract_is_server_only_and_brand_specific(monkeypatch):
    import httpx
    import json
    monkeypatch.setenv('AUTHENTICA_ENBISHAN_API_KEY', 'synthetic_enbishan_never_sent')
    monkeypatch.setenv('AUTHENTICA_API_KEY', 'synthetic_other_brand')
    seen = []
    def handle(request):
        seen.append((str(request.url), dict(request.headers), json.loads(request.content)))
        return httpx.Response(200, json={'success': True} if request.url.path.endswith('/send-otp') else {'status': True})
    adapter, options = _transport(monkeypatch, handle)
    assert adapter.send('+966500000000', brand_id='enbishan') is None
    assert adapter.verify('+966500000000', '123456', brand_id='enbishan') is True
    assert seen[0][0] == 'https://api.authentica.sa/api/v2/send-otp'
    assert seen[1][0] == 'https://api.authentica.sa/api/v2/verify-otp'
    assert seen[0][1]['x-authorization'] == 'synthetic_enbishan_never_sent'
    assert seen[0][2] == {'method': 'sms', 'phone': '+966500000000'}
    assert seen[1][2] == {'phone': '+966500000000', 'otp': '123456'}
    assert all(option == {'timeout': 12, 'follow_redirects': False} for option in options)


@pytest.mark.parametrize('brand,key', [('kps', 'synthetic_enbishan'), ('unknown', 'synthetic_enbishan'), ('enbishan', '')])
def test_transport_rejects_other_brand_or_missing_dedicated_key_before_network(monkeypatch, brand, key):
    monkeypatch.setenv('AUTHENTICA_ENBISHAN_API_KEY', key)
    monkeypatch.setenv('AUTHENTICA_API_KEY', 'synthetic_global_must_not_be_used')
    calls = []
    adapter, _ = _transport(monkeypatch, lambda request: calls.append(request))
    with pytest.raises(HTTPException) as result:
        adapter.send('+966500000000', brand_id=brand)
    assert result.value.status_code == 503
    assert not calls


@pytest.mark.parametrize('payload', [{}, {'status': 'true'}, {'status': 1}, {'verified': True}, {'status': False}])
def test_transport_requires_documented_boolean_verification(monkeypatch, payload):
    import httpx
    monkeypatch.setenv('AUTHENTICA_ENBISHAN_API_KEY', 'synthetic_never_sent')
    adapter, _ = _transport(monkeypatch, lambda request: httpx.Response(200, json=payload))
    assert adapter.verify('+966500000000', '123456', brand_id='enbishan') is False


@pytest.mark.parametrize('status', [301, 302, 401, 403, 429, 500])
def test_transport_sanitizes_provider_errors_without_retry(monkeypatch, status):
    import httpx
    monkeypatch.setenv('AUTHENTICA_ENBISHAN_API_KEY', 'synthetic_never_sent')
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={'Location': 'https://untrusted.invalid/', 'Retry-After': 'SECRET'},
                              json={'message': 'SECRET', 'phone': '+966500000000', 'code': '123456'})
    adapter, _ = _transport(monkeypatch, handle)
    with pytest.raises(HTTPException) as result:
        adapter.send('+966500000000', brand_id='enbishan')
    assert result.value.status_code == (429 if status == 429 else 503)
    assert result.value.headers is None
    assert len(calls) == 1
    assert not any(s in str(result.value.detail) for s in ('SECRET', 'synthetic', '+966500000000', '123456'))


@pytest.mark.parametrize('payload', [{}, {'success': 'true'}, {'success': 1}, {'status': True}])
def test_transport_does_not_claim_send_without_documented_acceptance(monkeypatch, payload):
    import httpx
    monkeypatch.setenv('AUTHENTICA_ENBISHAN_API_KEY', 'synthetic_never_sent')
    adapter, _ = _transport(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(HTTPException) as result:
        adapter.send('+966500000000', brand_id='enbishan')
    assert result.value.detail['code'] == 'OTP_NOT_SENT'


def test_transport_never_returns_provider_details_on_malformed_response(monkeypatch):
    import httpx
    monkeypatch.setenv('AUTHENTICA_ENBISHAN_API_KEY', 'synthetic_never_sent')
    adapter, _ = _transport(monkeypatch, lambda request: httpx.Response(200, text='SECRET'))
    with pytest.raises(HTTPException) as result:
        adapter.verify('+966500000000', '123456', brand_id='enbishan')
    assert result.value.detail['code'] == 'OTP_PROVIDER_FAILURE'
    assert 'SECRET' not in str(result.value.detail)


@pytest.mark.parametrize('credential', ['AUTHENTICA_API_KEY', 'AUTHENTICA_ENBISHAN_API_KEY'])
def test_isolated_development_rejects_inherited_authentica_credentials(monkeypatch, credential):
    from app import multibrand
    monkeypatch.setenv('APP_ENV', 'testing')
    monkeypatch.setenv('ENBISHAN_ISOLATED_ENABLED', 'true')
    monkeypatch.delenv('KPS_MULTIBRAND_ENABLED', raising=False)
    monkeypatch.setenv('DATABASE_URL', 'postgresql://synthetic@127.0.0.1/test_enbishan_provider_guard')
    monkeypatch.setenv(credential, 'synthetic_never_sent')
    with pytest.raises(RuntimeError, match='Provider credentials are prohibited'):
        multibrand.isolation_guard()
