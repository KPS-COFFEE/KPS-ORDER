"""Synthetic Tawked responses only; live delivery is a separate owner smoke test."""
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from fastapi import HTTPException
from app import auth, db, sms_verification
from tests.test_core import _authenticate, _make_user


def test_official_contract_and_idempotency(monkeypatch):
    monkeypatch.setenv('TAWKED_API_KEY', 'tk_test_synthetic_never_sent')
    seen = []
    expiry = (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()
    def handle(request):
        import json
        seen.append((str(request.url), dict(request.headers), json.loads(request.content)))
        if request.url.path.endswith('/start'):
            return httpx.Response(201, json={'id':'reference-1','status':'pending','expires_at':expiry})
        return httpx.Response(200, json={'verified':True,'status':'verified'})
    original = httpx.Client
    monkeypatch.setattr(sms_verification.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    assert sms_verification.start('+966500000000','otp_fixture','en')[0] == 'reference-1'
    assert sms_verification.check('reference-1','0123') is True
    assert seen[0][0] == 'https://tawked.com/v1/verify/start'
    assert seen[0][1]['idempotency-key'] == 'otp_fixture'
    assert seen[0][2] == {'to':'+966500000000','channel':'sms','lang':'en','reference':'otp_fixture'}
    assert seen[1][2] == {'id':'reference-1','code':'0123'}


@pytest.mark.parametrize('result', [{'verified':False,'status':'invalid_code'}, {'verified':True,'status':'expired'}, {'verified':'true','status':'verified'}, {}, {'verified':True,'status':'failed'}])
def test_fail_closed_check(monkeypatch,result):
    monkeypatch.setattr(sms_verification, '_call', lambda *a,**k:result)
    assert not sms_verification.check('r','0123')


@pytest.mark.parametrize('status', [301,401,403,429,500])
def test_provider_errors_are_sanitized(monkeypatch,status):
    monkeypatch.setenv('TAWKED_API_KEY','tk_live_synthetic_secret')
    original=httpx.Client
    monkeypatch.setattr(sms_verification.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(lambda r:httpx.Response(status,json={'secret':'DO_NOT_EXPOSE'})),**kw))
    with pytest.raises(HTTPException) as err:
        sms_verification.check('r','1234')
    assert err.value.status_code==503
    assert 'DO_NOT_EXPOSE' not in str(err.value.detail)
    assert 'synthetic_secret' not in str(err.value.detail)


@pytest.mark.parametrize('status,provider_error,expected_status,expected_code', [
    (429, 'rate_limited', 429, 'OTP_PROVIDER_DESTINATION_LIMIT'),
    (429, 'too_many_requests', 429, 'OTP_PROVIDER_RATE_LIMIT'),
    (429, 'ip_rate_limited', 429, 'OTP_PROVIDER_IP_LIMIT'),
    (429, 'spend_cap_reached', 429, 'OTP_PROVIDER_SPEND_CAP'),
    (429, 'sandbox_quota_exceeded', 429, 'OTP_PROVIDER_SANDBOX_LIMIT'),
    (402, 'insufficient_credits', 503, 'OTP_PROVIDER_BALANCE_REQUIRED'),
    (403, 'application_paused', 503, 'OTP_PROVIDER_APPLICATION_PAUSED'),
    (403, 'account_not_active', 503, 'OTP_PROVIDER_ACCOUNT_INACTIVE'),
    (401, 'unauthorized', 503, 'OTP_PROVIDER_CONFIGURATION'),
    (401, 'key_expired', 503, 'OTP_PROVIDER_CONFIGURATION'),
    (403, 'ip_not_allowed', 503, 'OTP_PROVIDER_CONFIGURATION'),
    (403, 'insufficient_scope', 503, 'OTP_PROVIDER_CONFIGURATION'),
])
@pytest.mark.parametrize('path', ['start', 'check'])
def test_documented_rejections_are_distinct_sanitized_and_never_retried(
        monkeypatch, status, provider_error, expected_status, expected_code, path):
    monkeypatch.setenv('TAWKED_API_KEY', 'tk_test_synthetic_secret')
    calls = []
    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(status, headers={'Retry-After': 'SENSITIVE_HEADER'}, json={
            'error': provider_error, 'message': 'SENSITIVE_BODY',
            'phone': '+966500000000', 'code': '1234', 'token': 'SENSITIVE_TOKEN'})
    original = httpx.Client
    monkeypatch.setattr(sms_verification.httpx, 'Client',
        lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(HTTPException) as result:
        sms_verification._call(path, {})
    error = result.value
    assert error.status_code == expected_status
    assert error.detail['code'] == expected_code
    assert error.sms_failure_reason == 'provider_rejected'
    assert error.headers is None
    assert calls == ['/v1/verify/' + path]
    assert not any(value in str(error.detail) for value in (
        'SENSITIVE', '+966500000000', '1234', 'tk_test_', provider_error))


@pytest.mark.parametrize('status,body', [
    (429, {'error': 'unknown_secret_error', 'message': 'SENSITIVE_BODY'}),
    (500, {'error': 'rate_limited'}),
    (403, {'error': 'spend_cap_reached'}),
    (429, {'error': {'code': 'rate_limited'}}),
    (429, {'error': ['rate_limited']}),
    (429, {'code': 'rate_limited'}),
    (429, ['rate_limited']),
    (429, None),
])
def test_unknown_malformed_or_mismatched_rejections_remain_generic(monkeypatch, status, body):
    monkeypatch.setenv('TAWKED_API_KEY', 'tk_test_synthetic_secret')
    original = httpx.Client
    monkeypatch.setattr(sms_verification.httpx, 'Client', lambda **kw:
        original(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)), **kw))
    with pytest.raises(HTTPException) as result:
        sms_verification._call('start', {})
    assert result.value.status_code == 503
    assert result.value.detail['code'] == 'OTP_PROVIDER_FAILURE'
    assert result.value.sms_failure_reason == 'provider_rejected'
    assert not any(value in str(result.value.detail) for value in ('SENSITIVE', 'rate_limited', 'spend_cap_reached'))


def test_non_json_rejection_remains_generic_without_body_disclosure(monkeypatch):
    monkeypatch.setenv('TAWKED_API_KEY', 'tk_test_synthetic_secret')
    original = httpx.Client
    monkeypatch.setattr(sms_verification.httpx, 'Client', lambda **kw:
        original(transport=httpx.MockTransport(lambda request: httpx.Response(429, text='SENSITIVE_BODY')), **kw))
    with pytest.raises(HTTPException) as result:
        sms_verification._call('start', {})
    assert result.value.status_code == 503
    assert result.value.detail['code'] == 'OTP_PROVIDER_FAILURE'
    assert 'SENSITIVE' not in str(result.value.detail)


@pytest.mark.parametrize('code',['123','12345','123456','١٢٣٤','12a4',' 1234'])
def test_four_ascii_digits_only(code):
    with pytest.raises(ValueError):
        auth.OTPVerifyBody(challenge_id='otp_fixture',code=code)


def setup_provider(monkeypatch,state='active'):
    monkeypatch.setenv('TAWKED_API_KEY','tk_test_synthetic_never_sent')
    with db.tx() as conn:
        conn.execute("UPDATE identity_channels SET sms_provider='tawked',state=%s,enabled=%s,delivery_proven_at=CASE WHEN %s THEN now() ELSE NULL END WHERE brand_id='kps' AND channel='sms'",(state,state=='active',state=='active'))
    monkeypatch.setattr(sms_verification,'start',lambda *a:('reference-1',datetime.now(timezone.utc)+timedelta(minutes=5)))
    monkeypatch.setattr(sms_verification,'check',lambda *a:True)


def send(client, path='/api/auth/otp/request'):
    r=client.post(path,json={'phone':'0500000000'})
    assert r.status_code==200,r.text
    assert 295 <= r.json()['expires_in'] <= 300
    return r.json()['challenge_id']


@pytest.mark.postgres
def test_success_is_single_use_and_guest_independent(client,monkeypatch):
    setup_provider(monkeypatch)
    cid=send(client)
    r=client.post('/api/auth/otp/verify',json={'challenge_id':cid,'code':'0123'})
    assert r.status_code==200,r.text
    assert r.json()['user']['phone_sms_verified_at']
    assert r.json()['user']['role']=='customer'
    assert client.post('/api/auth/otp/verify',json={'challenge_id':cid,'code':'0123'}).status_code==400
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM sessions').fetchone()['n']==1
        assert auth.channel_readiness(conn,'kps')['guest']['available'] is True


@pytest.mark.postgres
def test_three_attempts_and_expiry(client,monkeypatch):
    setup_provider(monkeypatch)
    calls=[]
    monkeypatch.setattr(sms_verification,'check',lambda *a:calls.append(a) or False)
    cid=send(client)
    for _ in range(4):
        assert client.post('/api/auth/otp/verify',json={'challenge_id':cid,'code':'0000'}).status_code==400
    assert len(calls)==3
    cid=send(client)
    with db.tx() as conn:
        conn.execute("UPDATE otp_challenges SET expires_at=now()-interval '1 second' WHERE id=%s",(cid,))
    assert client.post('/api/auth/otp/verify',json={'challenge_id':cid,'code':'0000'}).status_code==400
    assert len(calls)==3


@pytest.mark.postgres
def test_owner_testing_keeps_session_and_requires_explicit_activation(client,monkeypatch):
    setup_provider(monkeypatch,'testing')
    assert client.post('/api/auth/otp/request',json={'phone':'0500000000'}).status_code==403
    _authenticate(client,_make_user(role='driver'))
    assert client.post('/api/auth/otp/request',json={'phone':'0500000000'}).status_code==403
    owner=_make_user(role='owner')
    _authenticate(client,owner)
    assert client.post('/api/auth/otp/request',json={'phone':'0500000000'}).status_code==403
    cid=send(client, '/api/identity/owner/sms-channel-test/request')
    cookie=client.cookies.get(auth.COOKIE)
    r=client.post('/api/identity/owner/sms-channel-test/verify',json={'challenge_id':cid,'code':'0123'})
    assert r.status_code==200 and r.json()=={'verified':True,'test_only':True},r.text
    assert client.cookies.get(auth.COOKIE)==cookie
    with db.tx() as conn:
        row=auth._channel(conn,'kps','sms')
        assert row['delivery_proven_at'] and not row['enabled'] and row['state']=='testing'
        assert not conn.execute("SELECT 1 FROM users WHERE role='customer' AND phone_verified_at IS NOT NULL").fetchone()
    assert client.patch('/api/identity/settings',json={'channel':'sms','state':'active','enabled':True}).status_code==200


@pytest.mark.postgres
def test_provider_change_revokes_pending_and_proof(client,monkeypatch):
    setup_provider(monkeypatch)
    cid=send(client)
    _authenticate(client,_make_user(role='owner'))
    assert client.patch('/api/identity/settings',json={'channel':'sms','state':'active','enabled':True,'sms_provider':'twilio_verify'}).status_code==409
    r=client.patch('/api/identity/settings',json={'channel':'sms','state':'testing','enabled':False,'sms_provider':'twilio_verify'})
    assert r.status_code==200,r.text
    with db.tx() as conn:
        assert auth._channel(conn,'kps','sms')['delivery_proven_at'] is None
    assert client.post('/api/auth/otp/verify',json={'challenge_id':cid,'code':'0123'}).status_code==400


@pytest.mark.postgres
def test_paused_channel_never_sends_and_guest_works(client,monkeypatch):
    setup_provider(monkeypatch,'paused')
    monkeypatch.setattr(sms_verification,'start',lambda *a:pytest.fail('must not send'))
    assert client.post('/api/auth/otp/request',json={'phone':'0500000000'}).status_code==503
    assert client.post('/api/identity/guest',json={'display_name':'Guest','phone':'0500000000'}).status_code==200
