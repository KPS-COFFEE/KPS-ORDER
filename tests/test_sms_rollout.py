"""Audited SMS pilot and activation; synthetic provider and isolated PostgreSQL."""
import hashlib
import pytest
from fastapi import HTTPException
from app import auth, db, employee_sms, sms_rollout, sms_verification
from tests.test_core import _authenticate, _make_user
from tests.test_owner_phone_link import setup as owner_setup


def setup(client,monkeypatch):
    monkeypatch.setenv('SESSION_CONTEXT_COOKIES_ENABLED','false')
    monkeypatch.setenv('KPS_PORTAL_HOST_ROUTING_ENABLED','false')
    user,token=owner_setup(client,monkeypatch)
    response=client.post('/api/identity/owner-phone/request',json={'phone':'0507778899'})
    assert response.status_code==200,response.text
    assert client.post('/api/identity/owner-phone/verify',json={'challenge_id':response.json()['challenge_id'],'code':'0123'}).status_code==200
    return user,token


def pilot(client):
    response=client.post('/api/identity/owner/sms/rollout/pilot')
    assert response.status_code==200,response.text
    return response.json()


def login(client):
    sent=client.post('/api/identity/staff/code/request',json={'phone':'0507778899'})
    assert sent.status_code==200,sent.text
    assert sent.json()['channel']=='sms'
    verified=client.post('/api/identity/staff/code/verify',json={'challenge_id':sent.json()['challenge_id'],'code':'0123'})
    assert verified.status_code==200,verified.text
    client.headers['X-CSRF-Token']=verified.json()['csrf_token']
    return sent.json()['challenge_id']


def sensitive(client):
    response=client.post('/api/identity/owner/sms/request')
    assert response.status_code==200,response.text
    verified=client.post('/api/identity/owner/sms/verify',json={'challenge_id':response.json()['challenge_id'],'code':'0123'})
    assert verified.status_code==200,verified.text


@pytest.mark.postgres
def test_pilot_only_owner_preserves_email_and_no_automatic_sms(client,monkeypatch):
    user,_=setup(client,monkeypatch)
    sends=[]
    monkeypatch.setattr(sms_verification,'start',lambda *a:sends.append(a))
    assert pilot(client)=={'mode':'pilot','email_retired':False,'sms_sent':False}
    assert sends==[]
    assert sms_rollout.employee_sms_enabled(user)
    assert not sms_rollout.employee_sms_enabled(phone='+966507778899')
    other=_make_user('driver')
    assert not sms_rollout.employee_sms_enabled(other)
    assert not sms_rollout.employee_sms_enabled(phone='+966500000000')
    assert not sms_rollout.email_retired()
    public=client.get('/api/identity/staff/login-policy').json()
    assert public=={'channel':'email','code_length':6}
    assert user['id'] not in str(public)


@pytest.mark.postgres
def test_pilot_requires_linked_proof_fresh_stepup_and_csrf(client,monkeypatch):
    user,_=owner_setup(client,monkeypatch)
    assert client.post('/api/identity/owner/sms/rollout/pilot').status_code==403
    user,_=setup(client,monkeypatch)
    with db.tx() as conn: conn.execute('UPDATE sessions SET step_up_at=NULL WHERE user_id=%s',(user['id'],))
    assert client.post('/api/identity/owner/sms/rollout/pilot').status_code==403
    client.headers.pop('X-CSRF-Token')
    assert client.post('/api/identity/owner/sms/rollout/pilot').status_code==403
    assert not sms_rollout.employee_sms_enabled(user)


@pytest.mark.postgres
def test_general_activation_needs_actual_pilot_login_and_sensitive_sms(client,monkeypatch):
    user,_=setup(client,monkeypatch)
    pilot(client)
    assert client.post('/api/identity/owner/sms/rollout/activate').status_code==403
    login(client)
    # Normal login is not fresh sensitive authorization.
    with db.tx() as conn:conn.execute('UPDATE sessions SET step_up_at=now() WHERE user_id=%s',(user['id'],))
    assert client.post('/api/identity/owner/sms/rollout/activate').status_code==403
    sensitive(client)
    activated=client.post('/api/identity/owner/sms/rollout/activate')
    assert activated.status_code==200,activated.text
    assert activated.json()=={'mode':'active','email_retired':True,'sms_sent':False}
    assert sms_rollout.employee_sms_enabled(_make_user('driver'))
    assert sms_rollout.email_retired()
    assert client.get('/api/identity/staff/login-policy').json()=={'channel':'sms','code_length':4}
    with db.tx() as conn:
        assert conn.execute('SELECT email_verified_at FROM users WHERE id=%s',(user['id'],)).fetchone()['email_verified_at'] is None


@pytest.mark.postgres
def test_activation_cancels_only_noncustomer_pending_identity_mail(client,monkeypatch):
    user,_=setup(client,monkeypatch)
    customer=_make_user('customer')
    pilot(client)
    login(client)
    sensitive(client)
    with db.tx() as conn:
        for name,principal,kind in [('staff',user,'identity'),('customer',customer,'identity'),('report',user,'report')]:
            conn.execute("INSERT INTO identity_tokens(id,brand_id,user_id,purpose,email,token_hash,expires_at) VALUES(%s,'kps',%s,'email_login','synthetic@example.invalid',%s,now()+interval '1 hour')",('rollout_'+name,principal['id'],'hash_'+name))
            conn.execute("INSERT INTO email_outbox(id,brand_id,recipient,kind,dedupe_key,encrypted_payload) VALUES(%s,'kps','synthetic@example.invalid',%s,%s,%s)",('mail_'+name,kind,'identity:rollout_'+name,b'synthetic'))
    response=client.post('/api/identity/owner/sms/rollout/activate')
    assert response.status_code==200,response.text
    with db.tx() as conn:
        statuses={r['id']:r['status'] for r in conn.execute("SELECT id,status FROM email_outbox WHERE id LIKE 'mail_%%'").fetchall()}
        assert statuses=={'mail_staff':'cancelled','mail_customer':'pending','mail_report':'pending'}
        assert conn.execute("SELECT revoked_at FROM identity_tokens WHERE id='rollout_customer'").fetchone()['revoked_at'] is None


@pytest.mark.postgres
def test_provider_send_failure_keeps_same_generic_request_response(client,monkeypatch):
    user,_=setup(client,monkeypatch)
    pilot(client)
    def fail(*a):raise db.error(503,'OTP_PROVIDER_FAILURE','provider unavailable')
    monkeypatch.setattr(sms_verification,'start',fail)
    response=client.post('/api/identity/staff/code/request',json={'phone':'0507778899'})
    assert response.status_code==200,response.text
    assert response.json()['accepted'] is True
    assert 'provider' not in response.text.lower()
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM otp_challenges').fetchone()['n']==0


def test_context_cookie_explicit_test_override(monkeypatch):
    monkeypatch.setenv('SESSION_CONTEXT_COOKIES_ENABLED','false')
    monkeypatch.setattr(sms_rollout,'_read_policy',lambda *a:{'mode':'active'})
    assert not sms_rollout.context_cookies_enabled()
    monkeypatch.delenv('SESSION_CONTEXT_COOKIES_ENABLED')
    assert sms_rollout.context_cookies_enabled()


@pytest.mark.postgres
def test_anonymous_pilot_requests_do_not_disclose_sms_enrollment(client,monkeypatch):
    setup(client,monkeypatch)
    pilot(client)
    client.cookies.clear()
    sends=[]
    monkeypatch.setattr(sms_verification,'start',lambda *a:sends.append(a))
    known=client.post('/api/identity/staff/code/request',json={'phone':'0507778899'})
    unknown=client.post('/api/identity/staff/code/request',json={'phone':'0500000000'})
    # The legacy email channel is unconfigured in this fixture; both requests
    # must follow that same path, and neither may send a pilot SMS.
    assert known.status_code==unknown.status_code
    assert known.json().get('code_length')!=4
    assert unknown.json().get('code_length')!=4
    assert sends==[]


@pytest.mark.postgres
def test_employee_send_account_budget_survives_approved_phone_change(client,monkeypatch):
    from tests.test_employee_sms_portals import sms_setup
    user,calls=sms_setup(monkeypatch)
    for _ in range(3):
        result=client.post('/api/identity/staff/code/request',json={'phone':'0507778899'})
        assert result.status_code==200,result.text
    assert len(calls)==3
    # Model the result of an authorized, separately verified phone change. No
    # production identity is used; the immutable account ID remains the same.
    with db.tx() as conn:
        conn.execute("UPDATE users SET phone='+966507778898',phone_sms_verified_at=now(),updated_at=now() WHERE id=%s",(user['id'],))
    result=client.post('/api/identity/staff/code/request',json={'phone':'0507778898'})
    assert result.status_code==200,result.text
    assert result.json()['accepted'] is True and result.json()['code_length']==4
    assert len(calls)==3  # Fresh phone and its unused budget cannot trigger send.
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM otp_challenges WHERE source_user_id=%s',(user['id'],)).fetchone()['n']==3
        assert conn.execute('SELECT sum(hits) n FROM rate_limits WHERE bucket=%s',(hashlib.sha256(('employee-sms-send-account:'+user['id']).encode()).hexdigest(),)).fetchone()['n']==4
        assert conn.execute('SELECT 1 FROM otp_challenges WHERE id=%s',(result.json()['challenge_id'],)).fetchone() is None
