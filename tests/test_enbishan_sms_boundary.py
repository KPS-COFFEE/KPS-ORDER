"""Synthetic transport proves Enbishan can never borrow KPS's sender credential."""
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from fastapi import HTTPException
from app import auth, sms_verification


def approved(monkeypatch):
    sha = 'a' * 40
    for key, value in {
        'RUNTIME_REVISION': sha, 'KPS_CUSTOMER_SMS_ENBISHAN_ENABLED': 'true',
        'KPS_CUSTOMER_SMS_ENBISHAN_ACCEPTANCE_SHA': sha,
        'KPS_CUSTOMER_SMS_ENBISHAN_OWNER_APPROVAL_SHA': sha,
        'ENBISHAN_TAWKED_SENDER': 'ENBISHAN',
        'ENBISHAN_TAWKED_SENDER_APPROVAL_REFERENCE': 'SYNTHETIC-APPROVAL-ONLY',
        'ENBISHAN_TAWKED_API_KEY': 'tk_test_enbishan_synthetic',
        'TAWKED_API_KEY': 'tk_test_kps_synthetic',
    }.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize('missing', [
    'KPS_CUSTOMER_SMS_ENBISHAN_ENABLED', 'KPS_CUSTOMER_SMS_ENBISHAN_ACCEPTANCE_SHA',
    'KPS_CUSTOMER_SMS_ENBISHAN_OWNER_APPROVAL_SHA', 'ENBISHAN_TAWKED_SENDER',
    'ENBISHAN_TAWKED_SENDER_APPROVAL_REFERENCE', 'ENBISHAN_TAWKED_API_KEY',
])
@pytest.mark.parametrize('action', ['start', 'check'])
def test_unapproved_enbishan_never_contacts_provider(monkeypatch, missing, action):
    approved(monkeypatch)
    monkeypatch.delenv(missing)
    monkeypatch.setattr(httpx, 'Client', lambda **kw: pytest.fail('Unexpected provider call'))
    with pytest.raises(HTTPException):
        if action == 'start':
            sms_verification.start('+966500000000', 'synthetic', brand='enbishan')
        else:
            sms_verification.check('synthetic', '0123', brand='enbishan')


def test_sender_must_not_reuse_kps_credentials(monkeypatch):
    approved(monkeypatch)
    monkeypatch.setenv('ENBISHAN_TAWKED_API_KEY', 'tk_test_kps_synthetic')
    with pytest.raises(HTTPException) as error:
        auth._customer_provider_ready('tawked', 'enbishan')
    assert error.value.detail['code'] == 'OTP_BRAND_SENDER_UNAPPROVED'


def test_enbishan_start_and_check_use_same_dedicated_credential(monkeypatch):
    approved(monkeypatch)
    seen = []
    def handle(request):
        seen.append(request.headers['Authorization'])
        if request.url.path.endswith('/start'):
            return httpx.Response(201, json={'id': 'synthetic-ref', 'status': 'pending',
                'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()})
        return httpx.Response(200, json={'verified': True, 'status': 'verified'})
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    assert sms_verification.start('+966500000000', 'synthetic', brand='enbishan')[0] == 'synthetic-ref'
    assert sms_verification.check('synthetic-ref', '0123', brand='enbishan')
    sms_verification.start('+966500000000', 'synthetic-kps')
    assert seen == ['Bearer tk_test_enbishan_synthetic'] * 2 + ['Bearer tk_test_kps_synthetic']


def test_kps_provider_readiness_is_unchanged(monkeypatch):
    seen = []
    monkeypatch.setattr(auth, '_provider_ready', lambda provider: seen.append(provider))
    auth._customer_provider_ready('tawked', 'kps')
    assert seen == ['tawked']


def test_enbishan_rejects_an_unapproved_alternate_provider(monkeypatch):
    approved(monkeypatch)
    with pytest.raises(HTTPException):
        auth._customer_provider_ready('twilio_verify', 'enbishan')


def test_active_channel_cannot_advertise_kps_credential_as_enbishan_ready(monkeypatch):
    approved(monkeypatch)
    monkeypatch.delenv('ENBISHAN_TAWKED_API_KEY')
    class Rows:
        def fetchall(self):
            return [{'channel': 'sms', 'state': 'active', 'enabled': True,
                     'delivery_proven_at': datetime.now(timezone.utc), 'sms_provider': 'tawked'}]
    class Conn:
        def execute(self, *args):
            return Rows()
    channel = auth.channel_readiness(Conn(), 'enbishan')['sms']
    assert channel['configured'] is False
    assert channel['available'] is False
