"""Synthetic HTTP and real isolated PostgreSQL witnesses; never live delivery.

The settings in this file are fictional review attestations for tests, not
provider approval or evidence that ENBISHAN's application has these settings.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from threading import Event

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import auth, authentica_challenges as candidate, authentica_verification, db
from tests.test_core import _authenticate, _make_user

REQUEST = '/api/auth/otp/request'
VERIFY = '/api/auth/otp/verify'
PROBE_REQUEST = '/api/identity/owner/sms-channel-test/request'
PROBE_VERIFY = '/api/identity/owner/sms-channel-test/verify'
PHONE = '+966500000000'
CODE = '123456'
_HTTP_CLIENT = httpx.Client


def contract(**updates):
    return {'application_id': 'synthetic-enbishan-application', 'sender': 'ENBISHAN',
            'provider_approval_reference': 'SYNTHETIC-NOT-REAL-APPROVAL',
            'settings_evidence_reference': 'SYNTHETIC-NOT-REAL-SETTINGS',
            'code_length': 6, 'expiry_seconds': 300, 'resend_cooldown_seconds': 60,
            'resend_replaces_code': True, 'verification_consumes_code': True, **updates}


def configure(monkeypatch, **updates):
    sha = 'd' * 40
    for feature in ('CUSTOMER_SMS', 'AUTHENTICA_CUSTOMER_SMS'):
        for suffix, value in (('ENABLED', 'true'), ('ACCEPTANCE_SHA', sha), ('OWNER_APPROVAL_SHA', sha)):
            monkeypatch.setenv('KPS_' + feature + '_ENBISHAN_' + suffix, value)
    monkeypatch.setenv('RUNTIME_REVISION', sha)
    monkeypatch.setenv('AUTHENTICA_ENBISHAN_API_KEY', 'synthetic-never-sent-enbishan-key')
    raw = json.dumps(contract(**updates), sort_keys=True)
    monkeypatch.setenv(candidate._CONTRACT, raw)
    monkeypatch.setenv(candidate._CONTRACT_HASH, hashlib.sha256(raw.encode()).hexdigest())


def mock_transport(monkeypatch, response=None):
    original = _HTTP_CLIENT
    seen = []
    def handler(request):
        seen.append((request.url.path, json.loads(request.content), request.headers['x-authorization']))
        if response:
            return response(request)
        return httpx.Response(200, json={'success': True} if request.url.path.endswith('send-otp') else {'status': True})
    monkeypatch.setattr(authentica_verification.httpx, 'Client',
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return seen


def test_approved_contract_is_explicit_and_never_changes_global_kps_selection(monkeypatch):
    configure(monkeypatch)
    settings, digest = candidate.ready('enbishan')
    assert settings.code_length == 6 and len(digest) == 64
    monkeypatch.setenv('OTP_PROVIDER', 'tawked')
    assert auth._selected_sms_provider({}) == 'tawked'
    assert auth._available_sms_providers('kps') == ['disabled', 'tawked', 'twilio_verify']
    with pytest.raises(HTTPException):
        candidate.ready('kps')
    with pytest.raises(HTTPException):
        auth._provider_ready('authentica')


@pytest.mark.parametrize('field,value', [
    ('code_length', 3), ('code_length', 11), ('code_length', True), ('code_length', '6'),
    ('expiry_seconds', 0), ('expiry_seconds', 3601), ('resend_cooldown_seconds', 59),
    ('resend_replaces_code', False), ('verification_consumes_code', False),
    ('verification_consumes_code', 'true'), ('sender', 'KPS'),
    ('provider_approval_reference', ''), ('settings_evidence_reference', ''),
    ('application_id', '\nunsafe'), ('unexpected', 'unreviewed'),
])
def test_unconfirmed_or_invalid_application_contract_fails_closed(monkeypatch, field, value):
    configure(monkeypatch, **{field: value})
    with pytest.raises(HTTPException) as result:
        candidate.ready('enbishan')
    assert result.value.detail['code'] == 'OTP_APPLICATION_CONTRACT_UNVERIFIED'
    assert 'synthetic-never-sent' not in str(result.value)


@pytest.mark.parametrize('missing', [
    candidate._CONTRACT, candidate._CONTRACT_HASH, 'AUTHENTICA_ENBISHAN_API_KEY',
    'KPS_AUTHENTICA_CUSTOMER_SMS_ENBISHAN_ENABLED',
    'KPS_AUTHENTICA_CUSTOMER_SMS_ENBISHAN_ACCEPTANCE_SHA',
    'KPS_CUSTOMER_SMS_ENBISHAN_OWNER_APPROVAL_SHA',
])
def test_key_and_provider_vocabulary_are_insufficient_to_activate(monkeypatch, missing):
    configure(monkeypatch)
    monkeypatch.delenv(missing)
    with pytest.raises(HTTPException):
        candidate.ready('enbishan')
    assert 'authentica' not in auth._available_sms_providers('enbishan')


@pytest.fixture
def channel(database, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv('SESSION_CONTEXT_COOKIES_ENABLED', 'false')
    monkeypatch.setenv('APP_ALLOWED_ORIGINS', 'http://testserver')
    with db.tx() as c:
        c.execute("INSERT INTO brands(id,name) VALUES('enbishan','TEST ONLY ENBISHAN') ON CONFLICT DO NOTHING")
        c.execute("INSERT INTO app_settings(brand_id,settings) VALUES('enbishan','{\"customer_phone_required\":true}') ON CONFLICT DO NOTHING")
        c.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled,sms_provider,delivery_proven_at) "
                  "VALUES('enbishan','sms','active',true,'authentica',now()) ON CONFLICT(brand_id,channel) "
                  "DO UPDATE SET state='active',enabled=true,sms_provider='authentica',delivery_proven_at=now()")
    app = FastAPI()
    app.include_router(auth.router)
    with TestClient(app, headers={'Origin': 'http://testserver'}) as client:
        yield client


def send(client, phone=PHONE, path=REQUEST):
    return client.post(path, json={'phone': phone, 'brand_id': 'enbishan'})


def verify(client, identifier, code=CODE, path=VERIFY, **kwargs):
    return client.post(path, json={'challenge_id': identifier, 'code': code,
                                  'display_name': 'Synthetic customer', **kwargs})


def age_cooldown(identifier):
    with db.tx() as c:
        c.execute("UPDATE otp_challenges SET created_at=created_at-interval '61 seconds' WHERE id=%s", (identifier,))


@pytest.mark.postgres
def test_real_canonical_send_verify_single_use_and_stored_phone(channel, monkeypatch):
    seen = mock_transport(monkeypatch)
    sent = send(channel, '٠٥٠ ٠٠٠ ٠٠٠٠')
    assert sent.status_code == 200, sent.text
    assert sent.json()['code_length'] == 6 and 295 <= sent.json()['expires_in'] <= 300
    identifier = sent.json()['challenge_id']
    checked = verify(channel, identifier)
    assert checked.status_code == 200, checked.text
    assert checked.json()['customer_verified'] is True
    assert checked.json()['user']['brand_id'] == 'enbishan'
    assert checked.json()['user']['phone'] == PHONE
    assert 'set-cookie' in checked.headers
    assert verify(channel, identifier).status_code == 400
    assert seen == [('/api/v2/send-otp', {'method': 'sms', 'phone': PHONE}, 'synthetic-never-sent-enbishan-key'),
                    ('/api/v2/verify-otp', {'phone': PHONE, 'otp': CODE}, 'synthetic-never-sent-enbishan-key')]
    with db.tx() as c:
        challenge = c.execute('SELECT * FROM otp_challenges WHERE id=%s', (identifier,)).fetchone()
        assert challenge['attempts'] == 1 and challenge['verified_at'] and challenge['provider_name'] == 'authentica'
        assert challenge['authentica_contract_hash'] == candidate.ready('enbishan')[1]
        assert c.execute("SELECT count(*) n FROM sessions s JOIN users u ON u.id=s.user_id WHERE u.brand_id='enbishan'").fetchone()['n'] == 1
        # Audits contain IDs/counts, never recipient, code or credential.
        logs = str(c.execute("SELECT details FROM audit_events WHERE action LIKE 'identity.authentica.%'").fetchall())
        assert PHONE not in logs and CODE not in logs and 'synthetic-never-sent-enbishan-key' not in logs


@pytest.mark.postgres
def test_resend_retires_old_generation_before_new_phone_only_verification(channel, monkeypatch):
    seen = mock_transport(monkeypatch)
    first = send(channel).json()['challenge_id']
    assert send(channel).status_code == 429
    assert len(seen) == 1
    age_cooldown(first)
    second = send(channel).json()['challenge_id']
    assert first != second
    assert verify(channel, first).status_code == 400
    assert len(seen) == 2  # Old challenge cannot query newer provider code.
    assert verify(channel, second).status_code == 200
    assert len(seen) == 3


@pytest.mark.postgres
def test_provider_failure_persists_old_generation_retirement_and_cooldown(channel, monkeypatch):
    seen = mock_transport(monkeypatch)
    first = send(channel).json()['challenge_id']
    age_cooldown(first)
    def fail(request):
        return httpx.Response(500, json={'error': 'DO-NOT-EXPOSE', 'phone': PHONE, 'otp': CODE})
    mock_transport(monkeypatch, fail)
    failed = send(channel)
    assert failed.status_code == 503
    assert all(value not in failed.text for value in ('DO-NOT-EXPOSE', PHONE, CODE))
    assert verify(channel, first).status_code == 400
    assert send(channel).status_code == 429
    with db.tx() as c:
        rows = c.execute("SELECT expires_at<=clock_timestamp() expired,verified_at FROM otp_challenges WHERE provider_name='authentica'").fetchall()
        assert len(rows) == 2 and all(r['expired'] and r['verified_at'] is None for r in rows)


@pytest.mark.postgres
def test_three_attempts_expiry_and_contract_switch_reject_without_extra_network(channel, monkeypatch):
    seen = mock_transport(monkeypatch, lambda r: httpx.Response(200, json={'success': True} if r.url.path.endswith('send-otp') else {'status': False}))
    first = send(channel).json()['challenge_id']
    for _ in range(4):
        assert verify(channel, first).status_code == 400
    assert len(seen) == 4
    age_cooldown(first)
    second = send(channel).json()['challenge_id']
    with db.tx() as c:
        c.execute("UPDATE otp_challenges SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (second,))
    assert verify(channel, second).status_code == 400
    assert len(seen) == 5
    age_cooldown(second)
    third = send(channel).json()['challenge_id']
    configure(monkeypatch, code_length=5)
    assert verify(channel, third).status_code == 409
    assert len(seen) == 6


@pytest.mark.postgres
def test_provider_switch_and_unapproved_setting_cannot_create_proof(channel, monkeypatch):
    seen = mock_transport(monkeypatch)
    identifier = send(channel).json()['challenge_id']
    with db.tx() as c:
        c.execute("UPDATE identity_channels SET sms_provider='disabled' WHERE brand_id='enbishan' AND channel='sms'")
    assert verify(channel, identifier).status_code == 409
    assert len(seen) == 1
    owner = _make_user('owner', brand='enbishan')
    _authenticate(channel, owner)
    monkeypatch.delenv('KPS_AUTHENTICA_CUSTOMER_SMS_ENBISHAN_OWNER_APPROVAL_SHA')
    denied = channel.patch('/api/identity/settings', json={'channel':'sms','state':'testing','enabled':False,'sms_provider':'authentica'})
    assert denied.status_code != 200
    with db.tx() as c:
        assert auth._channel(c, 'enbishan', 'sms')['sms_provider'] == 'disabled'


@pytest.mark.postgres
def test_testing_requires_owner_and_does_not_create_customer_or_replace_work_session(channel, monkeypatch):
    seen = mock_transport(monkeypatch)
    with db.tx() as c:
        c.execute("UPDATE identity_channels SET state='testing',enabled=false,delivery_proven_at=NULL WHERE brand_id='enbishan' AND channel='sms'")
    assert send(channel).status_code == 403
    owner = _make_user('owner', brand='enbishan')
    _authenticate(channel, owner)
    cookies = dict(channel.cookies)
    identifier = send(channel, path=PROBE_REQUEST).json()['challenge_id']
    assert verify(channel, identifier, path=VERIFY).status_code == 400
    checked = verify(channel, identifier, path=PROBE_VERIFY)
    assert checked.status_code == 200 and checked.json() == {'verified':True,'test_only':True}, checked.text
    assert 'set-cookie' not in checked.headers and dict(channel.cookies) == cookies
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM users WHERE brand_id='enbishan' AND role='customer'").fetchone()['n'] == 0
        row = auth._channel(c, 'enbishan', 'sms')
        assert row['state'] == 'testing' and not row['enabled'] and row['delivery_proven_at']
    assert len(seen) == 2


@pytest.mark.postgres
def test_concurrent_send_and_verify_never_overlap_phone_provider_calls(channel, monkeypatch):
    entered, finish = Event(), Event()
    seen = []
    original = _HTTP_CLIENT
    def handler(request):
        seen.append(request.url.path)
        entered.set()
        assert finish.wait(15)
        return httpx.Response(200, json={'success': True})
    monkeypatch.setattr(authentica_verification.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    # HTTP sessions are separate. Only the synthetic provider hook is shared.
    def first_request():
        with TestClient(channel.app, headers={'Origin':'http://testserver'}) as another:
            return send(another)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first_request)
        assert entered.wait(10)
        try:
            second = send(channel)
            assert second.status_code == 429 and second.json()['detail']['code'] == 'OTP_REQUEST_IN_PROGRESS'
            assert len(seen) == 1
        finally:
            finish.set()
        assert pending.result(timeout=15).status_code == 200


@pytest.mark.postgres
def test_concurrent_verification_creates_exactly_one_session(channel, monkeypatch):
    mock_transport(monkeypatch)
    identifier = send(channel).json()['challenge_id']
    entered, finish = Event(), Event()
    calls = []
    original = _HTTP_CLIENT
    def handler(request):
        calls.append(request.url.path)
        entered.set()
        assert finish.wait(15)
        return httpx.Response(200, json={'status': True})
    monkeypatch.setattr(authentica_verification.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    def first_check():
        with TestClient(channel.app, headers={'Origin':'http://testserver'}) as another:
            return verify(another, identifier)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first_check)
        assert entered.wait(10)
        try:
            second = verify(channel, identifier)
            assert second.status_code == 429 and second.json()['detail']['code'] == 'OTP_REQUEST_IN_PROGRESS'
        finally:
            finish.set()
        assert pending.result(timeout=15).status_code == 200
    assert len(calls) == 1
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM sessions s JOIN users u ON u.id=s.user_id WHERE u.brand_id='enbishan'").fetchone()['n'] == 1


@pytest.mark.postgres
def test_cross_brand_db_provider_and_customer_code_constraints_remain_closed(channel, monkeypatch):
    import psycopg
    seen = mock_transport(monkeypatch)
    with db.tx() as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            with c.transaction():
                c.execute("UPDATE identity_channels SET sms_provider='authentica' WHERE brand_id='kps' AND channel='sms'")
    identifier = send(channel).json()['challenge_id']
    rejected = verify(channel, identifier, code='1234')
    assert rejected.status_code == 422
    assert len(seen) == 1
    from app import sms_verification
    from tests.test_tawked_otp import setup_provider
    setup_provider(monkeypatch)
    kps = channel.post(REQUEST, json={'phone':PHONE,'brand_id':'kps'})
    assert kps.status_code == 200, kps.text
    monkeypatch.setattr(sms_verification, 'check', lambda *a: pytest.fail('KPS must reject a six-digit code before provider verification'))
    assert verify(channel, kps.json()['challenge_id'], code=CODE).status_code == 422


@pytest.mark.postgres
def test_failed_identity_finalization_commits_no_partial_identity_or_cookie(channel, monkeypatch):
    mock_transport(monkeypatch)
    identifier = send(channel).json()['challenge_id']
    failed = verify(channel, identifier, display_name='')
    assert failed.status_code == 422 and 'set-cookie' not in failed.headers
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM users WHERE brand_id='enbishan' AND role='customer'").fetchone()['n'] == 0
        row = c.execute('SELECT attempts,verified_at,expires_at<=clock_timestamp() expired FROM otp_challenges WHERE id=%s', (identifier,)).fetchone()
        assert row['attempts'] == 1 and row['verified_at'] is None and row['expired']


@pytest.mark.postgres
def test_expiry_during_provider_latency_cannot_create_a_session(channel, monkeypatch):
    import time
    mock_transport(monkeypatch)
    identifier = send(channel).json()['challenge_id']
    with db.tx() as c:
        c.execute("UPDATE otp_challenges SET expires_at=clock_timestamp()+interval '0.1 seconds' WHERE id=%s", (identifier,))
    def delayed(phone, code, *, brand_id):
        time.sleep(0.2)  # Small synthetic delay crosses the real PostgreSQL deadline.
        return True
    monkeypatch.setattr(authentica_verification, 'verify', delayed)
    result = verify(channel, identifier)
    assert result.status_code in {400, 409} and 'set-cookie' not in result.headers
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM users WHERE brand_id='enbishan' AND role='customer'").fetchone()['n'] == 0


def test_dedicated_key_cannot_reuse_a_global_authentica_credential(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv('AUTHENTICA_API_KEY', 'synthetic-never-sent-enbishan-key')
    with pytest.raises(HTTPException) as result:
        candidate.ready('enbishan')
    assert result.value.detail['code'] == 'OTP_PROVIDER_UNAVAILABLE'


@pytest.mark.postgres
def test_non_http_failure_after_provider_send_cannot_revive_previous_generation(channel, monkeypatch):
    seen = mock_transport(monkeypatch)
    first = send(channel).json()['challenge_id']
    age_cooldown(first)
    def sent_then_crashed(phone, *, brand_id):
        # A provider call may have succeeded before the process/audit/SQL fails.
        raise RuntimeError('synthetic process interruption after accepted send')
    monkeypatch.setattr(authentica_verification, 'send', sent_then_crashed)
    with pytest.raises(RuntimeError, match='synthetic process interruption'):
        send(channel)
    assert verify(channel, first).status_code == 400
    assert send(channel).status_code == 429
    with db.tx() as c:
        rows = c.execute("SELECT expires_at<=clock_timestamp() expired FROM otp_challenges WHERE provider_name='authentica'").fetchall()
        assert len(rows) == 2 and all(r['expired'] for r in rows)


@pytest.mark.postgres
def test_post_send_audit_rollback_cannot_revive_previous_generation(channel, monkeypatch):
    seen = mock_transport(monkeypatch)
    first = send(channel).json()['challenge_id']
    age_cooldown(first)
    actual_audit = db.audit
    def fail_audit(conn, actor, action, *args, **kwargs):
        if action == 'identity.authentica.sent':
            raise RuntimeError('synthetic post-send audit failure')
        return actual_audit(conn, actor, action, *args, **kwargs)
    monkeypatch.setattr(db, 'audit', fail_audit)
    with pytest.raises(RuntimeError, match='synthetic post-send audit failure'):
        send(channel)
    assert len(seen) == 2  # The second provider call really happened.
    assert verify(channel, first).status_code == 400
    with db.tx() as c:
        rows = c.execute("SELECT expires_at<=clock_timestamp() expired FROM otp_challenges WHERE provider_name='authentica'").fetchall()
        assert len(rows) == 2 and all(r['expired'] for r in rows)


@pytest.mark.postgres
def test_unknown_verify_result_keeps_attempt_and_retires_challenge(channel, monkeypatch):
    mock_transport(monkeypatch)
    identifier = send(channel).json()['challenge_id']
    def failed_verify(*args, **kwargs):
        raise RuntimeError('synthetic verify interruption')
    monkeypatch.setattr(authentica_verification, 'verify', failed_verify)
    with pytest.raises(RuntimeError, match='synthetic verify interruption'):
        verify(channel, identifier)
    with db.tx() as c:
        row = c.execute('SELECT attempts,verified_at,expires_at<=clock_timestamp() expired FROM otp_challenges WHERE id=%s', (identifier,)).fetchone()
        assert row['attempts'] == 1 and row['verified_at'] is None and row['expired']
        assert c.execute("SELECT count(*) n FROM users WHERE brand_id='enbishan' AND role='customer'").fetchone()['n'] == 0


@pytest.mark.postgres
def test_different_phone_network_calls_can_overlap_without_brand_wide_lock(channel, monkeypatch):
    first_entered, second_entered, finish = Event(), Event(), Event()
    original = _HTTP_CLIENT
    def handler(request):
        phone = json.loads(request.content)['phone']
        (first_entered if phone == PHONE else second_entered).set()
        assert finish.wait(15)
        return httpx.Response(200, json={'success': True})
    monkeypatch.setattr(authentica_verification.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    def request_phone(phone):
        with TestClient(channel.app, headers={'Origin':'http://testserver'}) as another:
            return send(another, phone)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(request_phone, PHONE)
        assert first_entered.wait(10)
        second = pool.submit(request_phone, '+966500000001')
        try:
            assert second_entered.wait(10), 'Different phones were serialized across provider latency'
        finally:
            finish.set()
        assert first.result(timeout=15).status_code == 200
        assert second.result(timeout=15).status_code == 200


@pytest.mark.postgres
def test_provider_changed_while_send_in_flight_leaves_no_usable_challenge(channel, monkeypatch):
    original = _HTTP_CLIENT
    def changed(request):
        # Independent transaction while the provider call is in flight: there
        # must be no channel row lock held by the sending transaction.
        with db.tx() as c:
            c.execute("UPDATE identity_channels SET sms_provider='disabled' WHERE brand_id='enbishan' AND channel='sms'")
        return httpx.Response(200, json={'success': True})
    monkeypatch.setattr(authentica_verification.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(changed), **kw))
    denied = send(channel)
    assert denied.status_code == 409
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM otp_challenges WHERE provider_name='authentica' AND expires_at>clock_timestamp()").fetchone()['n'] == 0


def test_authentica_whatsapp_readiness_remains_closed(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv('OTP_CHANNEL', 'whatsapp')
    class Rows:
        def fetchall(self):
            return [{'channel':'sms','state':'active','enabled':True,'delivery_proven_at':True,'sms_provider':'authentica'},
                    {'channel':'whatsapp','state':'active','enabled':True,'delivery_proven_at':True}]
    class Conn:
        def execute(self, *args):
            return Rows()
    readiness = auth.channel_readiness(Conn(), 'enbishan')
    assert not readiness['whatsapp']['available'] and not readiness['whatsapp']['configured']


@pytest.mark.postgres
def test_switch_away_and_back_during_send_cannot_revive_retired_generation(channel, monkeypatch):
    with db.tx() as c:
        c.execute("UPDATE identity_channels SET state='testing',enabled=false,delivery_proven_at=NULL WHERE brand_id='enbishan' AND channel='sms'")
    owner = _make_user('owner', brand='enbishan')
    _authenticate(channel, owner)
    original = _HTTP_CLIENT
    def switched_back(request):
        # Exercise the actual owner settings endpoint twice during provider I/O.
        # Back-to-original provider name is insufficient to revive old proof.
        away = channel.patch('/api/identity/settings', json={'channel':'sms','state':'testing','enabled':False,'sms_provider':'disabled'})
        assert away.status_code == 200, away.text
        back = channel.patch('/api/identity/settings', json={'channel':'sms','state':'testing','enabled':False,'sms_provider':'authentica'})
        assert back.status_code == 200, back.text
        return httpx.Response(200, json={'success':True})
    monkeypatch.setattr(authentica_verification.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(switched_back), **kw))
    result = send(channel, path=PROBE_REQUEST)
    assert result.status_code == 409 and result.json()['detail']['code'] == 'OTP_PROVIDER_CHANGED', result.text
    with db.tx() as c:
        assert auth._channel(c, 'enbishan', 'sms')['sms_provider'] == 'authentica'
        assert c.execute("SELECT count(*) n FROM otp_challenges WHERE provider_name='authentica' AND expires_at>clock_timestamp()").fetchone()['n'] == 0


def test_shipped_customer_form_uses_reviewed_provider_length_and_keeps_kps_default():
    from pathlib import Path
    import subprocess
    program = r'''
const assert=require('assert'),fs=require('fs'),vm=require('vm');
const source=fs.readFileSync('public/customer.js','utf8');
const start=source.indexOf('  async function requestOTP('),end=source.indexOf('  async function verifyOTP(',start);
assert(start>=0&&end>start);
async function journey(result,expected){
 const elements=new Map(),errors=[];
 const el=id=>{if(!elements.has(id))elements.set(id,{value:id==='phone'?'0500000000':'',hidden:false,focus(){this.focused=true;}});return elements.get(id);};
 const context={$:el,state:{brand:{id:'enbishan'}},language:'ar',api:async()=>result,
  busy:async(b,f)=>f(),t:x=>x,text:(id,value)=>{el(id).textContent=value;},
  showError:(id,value)=>errors.push(value),errorMessage:error=>error.message};
 vm.createContext(context);vm.runInContext(source.slice(start,end),context);
 await context.requestOTP({preventDefault(){}});
 if(expected){assert.equal(el('otp-code').maxLength,expected);assert.equal(el('otp-code').minLength,expected);
  assert.equal(el('otp-code').pattern,'[0-9]{'+expected+'}');assert(el('otp-code').focused);
  assert.equal(context.state.challenge,result.challenge_id);assert(el('otp-request-form').hidden);}
 else {assert.equal(context.state.challenge,undefined);assert(errors.at(-1));}
}
(async()=>{await journey({challenge_id:'otp_kps_legacy',channel:'sms'},4);
 await journey({challenge_id:'otp_enbishan_approved',channel:'sms',code_length:6},6);
 await journey({challenge_id:'otp_bad',channel:'sms',code_length:'6'},null);
 await journey({challenge_id:'otp_bad',channel:'sms',code_length:11},null);})().catch(e=>{console.error(e);process.exit(1);});
'''
    subprocess.run(['node','-e',program], cwd=Path(__file__).resolve().parents[1], check=True, timeout=15)


@pytest.mark.postgres
def test_another_phone_verification_during_send_is_not_a_provider_configuration_change(channel, monkeypatch):
    mock_transport(monkeypatch)
    existing = send(channel).json()['challenge_id']
    original = _HTTP_CLIENT
    def interleaved(request):
        if request.url.path.endswith('/send-otp'):
            # Canonical success refreshes delivery_proven_at/updated_at. It must
            # not invalidate a different phone's in-flight send generation.
            checked = verify(channel, existing)
            assert checked.status_code == 200, checked.text
            return httpx.Response(200, json={'success':True})
        return httpx.Response(200, json={'status':True})
    monkeypatch.setattr(authentica_verification.httpx, 'Client', lambda **kw: original(transport=httpx.MockTransport(interleaved), **kw))
    sent = send(channel, phone='+966500000001')
    assert sent.status_code == 200, sent.text
    with db.tx() as c:
        row = auth._channel(c, 'enbishan', 'sms')
        assert row['authentica_configuration_generation'] == 0
        assert c.execute('SELECT expires_at>clock_timestamp() active FROM otp_challenges WHERE id=%s', (sent.json()['challenge_id'],)).fetchone()['active']


def test_shipped_owner_delivery_probe_accepts_provider_length_without_activation():
    from pathlib import Path
    import subprocess
    program = r'''
const assert=require('assert'),fs=require('fs'),vm=require('vm');
const source=fs.readFileSync('public/ops.js','utf8');
const start=source.indexOf('  function ownerSmsTest('),end=source.indexOf('\n',start);
assert(start>=0&&end>start);
async function journey(sent,expected){
 const fields=[],dialogs=[],posts=[];
 const context={state:{user:{brand_id:'enbishan'},lang:'en'},tr:(ar,en)=>en,
  modal:(title,html,submit)=>dialogs.push({title,submit}),notice:x=>x,formEnd:x=>x,
  field:(label,name,value,attrs)=>{fields.push({label,name,attrs});return label;},
  mutation:async(path,body)=>{posts.push({path,body});return path.endsWith('/request')?sent:{test_only:true};},
  closeModal:()=>{},identitySettings:async()=>{}};
 vm.createContext(context);vm.runInContext(source.slice(start,end),context);
 context.ownerSmsTest();await dialogs[0].submit({get:()=>'+966500000000'});
 assert.equal(fields[1].attrs.minlength,expected);assert.equal(fields[1].attrs.maxlength,expected);
 await dialogs[1].submit({get:()=>expected===6?'123456':'1234'});
 assert.equal(posts.length,2);assert(posts[1].path.endsWith('/sms-channel-test/verify'));
 assert.equal(posts[1].body.challenge_id,sent.challenge_id);
 assert.equal(posts[1].body.code,expected===6?'123456':'1234');
 assert(!posts.some(p=>p.path.includes('activate')||p.path.includes('settings')));
}
(async()=>{await journey({challenge_id:'owner_probe_legacy',expires_in:299},4);
 await journey({challenge_id:'owner_probe_enbishan',expires_in:119,code_length:6},6);})().catch(e=>{console.error(e);process.exit(1);});
'''
    subprocess.run(['node','-e',program], cwd=Path(__file__).resolve().parents[1], check=True, timeout=15)
