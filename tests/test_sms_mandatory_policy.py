"""Post-fix AUTH-OBS certificates using synthetic identities and SMS provider I/O."""
import os
import base64
from pathlib import Path
import secrets

import pytest
from fastapi import HTTPException, Response
from app import auth, db, sms_rollout
from tests.test_core import _authenticate, _make_user
from tests.sms_policy_helpers import configure_sms, sms_login, sms_step_up


@pytest.mark.parametrize('setting',[None,'false','true'])
def test_kps_policy_cannot_be_disabled_by_provider_or_legacy_rollout_switch(monkeypatch, setting):
    if setting is None:
        monkeypatch.delenv('EMPLOYEE_SMS_LOGIN_ENABLED', raising=False)
    else:
        monkeypatch.setenv('EMPLOYEE_SMS_LOGIN_ENABLED', setting)
    monkeypatch.setenv('EMPLOYEE_SMS_EMAIL_RETIRED', 'false')
    assert sms_rollout.work_sms_required('kps')
    assert sms_rollout.employee_sms_enabled({'brand_id':'kps','id':'synthetic'})
    assert auth.staff_email_retired('kps')
    assert sms_rollout.login_policy('kps') == {
        'channel':'sms','code_length':4,'sms_required':True,
        'password_login_allowed':False,'password_step_up_allowed':False}


def test_global_required_policy_covers_other_brands_without_inheriting_kps_policy(monkeypatch):
    monkeypatch.delenv('EMPLOYEE_SMS_LOGIN_ENABLED', raising=False)
    assert not sms_rollout.work_sms_required('synthetic-legacy')
    assert not sms_rollout.email_retired('synthetic-legacy')
    monkeypatch.setenv('EMPLOYEE_SMS_LOGIN_ENABLED', 'true')
    assert sms_rollout.work_sms_required('synthetic-legacy')
    assert sms_rollout.email_retired('synthetic-legacy')


@pytest.mark.postgres
def test_secondary_brand_active_policy_stays_required_when_global_flag_removed(client, monkeypatch):
    monkeypatch.delenv('EMPLOYEE_SMS_LOGIN_ENABLED',raising=False)
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('synthetic-active','Synthetic active'),('synthetic-legacy','Synthetic legacy')")
        conn.execute("INSERT INTO app_settings(brand_id,settings) VALUES('synthetic-active','{\"employee_sms_rollout\":{\"mode\":\"active\"}}') ON CONFLICT(brand_id) DO UPDATE SET settings=EXCLUDED.settings")
        assert sms_rollout.work_sms_required('synthetic-active',conn=conn)
        assert not sms_rollout.work_sms_required('synthetic-legacy',conn=conn)
    assert sms_rollout.work_sms_required('synthetic-active')
    assert auth.staff_email_retired('synthetic-active')
    assert not sms_rollout.work_sms_required('synthetic-legacy')
    for brand, expected in [('synthetic-active',403),('synthetic-legacy',200)]:
        user = _make_user('owner',brand=brand,username='account-'+brand,password='Synthetic-Only-Password-847!')
        result = client.post('/api/auth/login',json={'username':user['username'],'password':'Synthetic-Only-Password-847!'})
        assert result.status_code == expected, result.text
        if expected == 403:
            assert result.json()['detail']['code'] == 'WORK_SMS_REQUIRED'
            verified = sms_login(client,user,monkeypatch)
            assert verified['user']['brand_id'] == brand


@pytest.mark.postgres
@pytest.mark.parametrize('role',['owner','admin','delivery_manager','delivery_staff','branch_manager','driver'])
def test_valid_password_cannot_create_work_session_but_sms_can_without_email(client, monkeypatch, role):
    user = _make_user(role, username='synthetic-'+role, password='Synthetic-Only-Password-847!')
    before_hash = user['password_hash']
    denied = client.post('/api/auth/login', json={'username':user['username'],'password':'Synthetic-Only-Password-847!'})
    assert denied.status_code == 403, denied.text
    assert denied.json()['detail']['code'] == 'WORK_SMS_REQUIRED'
    assert not any('session=' in value for value in denied.headers.get_list('set-cookie'))
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM sessions WHERE user_id=%s',(user['id'],)).fetchone()['n'] == 0
    signed = sms_login(client, user, monkeypatch)
    assert signed['user']['role'] == role
    assert signed['user']['email_verified_at'] is None
    with db.tx() as conn:
        unchanged = conn.execute('SELECT password_hash,email_verified_at FROM users WHERE id=%s',(user['id'],)).fetchone()
        assert unchanged['password_hash'] == before_hash and unchanged['email_verified_at'] is None
        session = conn.execute('SELECT step_up_at FROM sessions WHERE user_id=%s',(user['id'],)).fetchone()
        assert session['step_up_at'] is None, 'Ordinary SMS sign-in is not sensitive proof'


@pytest.mark.postgres
@pytest.mark.parametrize('extra',[{'owner_step_up':True},{'employee_sms_challenge':'employee_otp_forged'}])
def test_http_password_body_cannot_supply_internal_proof_override(client, extra):
    response = client.post('/api/auth/login',json={'username':'synthetic','password':'synthetic',**extra})
    assert response.status_code == 422


@pytest.mark.postgres
def test_central_session_factory_rejects_staff_without_verified_employee_challenge(database):
    owner = _make_user('owner')
    with db.tx() as conn:
        for forged in (None,'owner_sms_forged','employee_otp_nonexistent'):
            with pytest.raises(HTTPException) as error:
                auth.establish_session(conn,owner,Response(),owner_step_up=True,employee_sms_challenge=forged)
            assert error.value.detail['code'] == 'WORK_SMS_REQUIRED'
        assert conn.execute('SELECT count(*) n FROM sessions').fetchone()['n'] == 0


@pytest.mark.postgres
def test_preexisting_session_survives_but_password_and_old_timestamp_cannot_authorize_sensitive_action(client, monkeypatch):
    owner = _make_user('owner',password='Synthetic-Only-Password-847!')
    _authenticate(client,owner)
    with db.tx() as conn:
        conn.execute('UPDATE sessions SET step_up_at=now() WHERE user_id=%s',(owner['id'],))
        before = conn.execute('SELECT step_up_at FROM sessions WHERE user_id=%s',(owner['id'],)).fetchone()['step_up_at']
    continued = client.get('/api/session',headers={'X-KPS-Context':'work'})
    assert continued.status_code == 200 and continued.json()['user']['id'] == owner['id']
    denied = client.post('/api/identity/step-up',json={'password':'Synthetic-Only-Password-847!'})
    assert denied.status_code == 403 and denied.json()['detail']['code'] == 'WORK_SMS_REQUIRED'
    body = {'display_name':'Synthetic new driver','phone':'0501234567','role':'driver','branch_ids':['kps-branch-1']}
    denied = client.post('/api/identity/staff/invitations',json=body)
    assert denied.status_code == 403 and denied.json()['detail']['code'] == 'OWNER_STEP_UP_REQUIRED'
    with db.tx() as conn:
        assert conn.execute('SELECT step_up_at FROM sessions WHERE user_id=%s',(owner['id'],)).fetchone()['step_up_at'] == before
        assert conn.execute('SELECT count(*) n FROM users').fetchone()['n'] == 1
    sms_step_up(client,owner,monkeypatch)
    allowed = client.post('/api/identity/staff/invitations',json=body)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()['user']['active']
    assert allowed.json()['user']['email_verified_at'] is None


@pytest.mark.postgres
@pytest.mark.parametrize('role',['driver','delivery_manager','admin','customer'])
def test_nonowner_cannot_use_sensitive_sms_or_password_step_up(client, role):
    user = _make_user(role)
    _authenticate(client,user)
    for path, body in [('/api/identity/step-up',{'password':'synthetic'}),('/api/identity/owner/sms/request',{})]:
        result = client.post(path,json=body)
        assert result.status_code == (401 if role=='customer' else 403), result.text


def _pending_email(user, purpose):
    identifier = db.new_id('email_')
    secret = secrets.token_urlsafe(32)
    with db.tx() as conn:
        conn.execute("UPDATE users SET email='synthetic-owner@fixture.invalid',email_verified_at=now() WHERE id=%s",(user['id'],))
        conn.execute("INSERT INTO identity_tokens(id,brand_id,user_id,purpose,email,token_hash,expires_at,code_hash) VALUES(%s,%s,%s,%s,'synthetic-owner@fixture.invalid',%s,now()+interval '15 minutes',%s)",(identifier,user['brand_id'],user['id'],purpose,auth.token_hash(secret),auth._staff_code_hash(identifier,'123456')))
    return identifier, secret


@pytest.mark.postgres
@pytest.mark.parametrize('purpose',['staff_login','invitation','add_owner','owner_link','recovery','email_login'])
def test_preexisting_email_paths_cannot_create_work_sessions_and_mail_worker_refuses_send(client, monkeypatch, purpose):
    monkeypatch.setenv('MAIL_OUTBOX_KEY',base64.urlsafe_b64encode(b's'*32).decode())
    owner = _make_user('owner')
    identifier, secret = _pending_email(owner,purpose)
    before = None
    with db.tx() as conn:
        before = conn.execute('SELECT * FROM users WHERE id=%s',(owner['id'],)).fetchone()
        assert not auth.identity_mail_authorized(conn,{'dedupe_key':'identity:'+identifier,'brand_id':'kps','recipient':'synthetic-owner@fixture.invalid'})
    denied = client.post('/api/identity/email/verify',json={'challenge_id':identifier,'token':secret})
    assert denied.status_code == 400 and denied.json()['detail']['code'] == 'STAFF_EMAIL_LOGIN_RETIRED'
    if purpose in {'staff_login','invitation','add_owner'}:
        numeric = client.post('/api/identity/staff/code/verify',json={'challenge_id':identifier,'code':'123456'})
        assert numeric.status_code == 400 and numeric.json()['detail']['code'] == 'STAFF_EMAIL_LOGIN_RETIRED'
    with db.tx() as conn:
        assert conn.execute('SELECT * FROM users WHERE id=%s',(owner['id'],)).fetchone() == before
        assert conn.execute('SELECT consumed_at FROM identity_tokens WHERE id=%s',(identifier,)).fetchone()['consumed_at'] is None
        assert conn.execute('SELECT count(*) n FROM sessions').fetchone()['n'] == 0


@pytest.mark.postgres
@pytest.mark.parametrize('purpose',['staff_login','owner_link','recovery'])
def test_new_employee_email_requests_never_queue_work_authentication(client, monkeypatch, purpose):
    user = _make_user('owner')
    with db.tx() as conn:
        conn.execute("UPDATE users SET email='owner@fixture.invalid',email_verified_at=now() WHERE id=%s",(user['id'],))
    monkeypatch.setattr(auth,'_email_ready',lambda *args,**kwargs:None)
    result = client.post('/api/identity/email/request',json={'email':'owner@fixture.invalid','purpose':purpose})
    assert result.status_code == 200 and result.json()['accepted']
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM identity_tokens').fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) n FROM email_outbox').fetchone()['n'] == 0
        with pytest.raises(HTTPException) as error:
            auth._create_email_token(conn,brand='kps',email='owner@fixture.invalid',purpose=purpose,user_id=user['id'])
        assert error.value.detail['code'] == 'WORK_SMS_REQUIRED'


@pytest.mark.postgres
def test_same_mobile_customer_and_sms_work_sessions_are_separate(client, monkeypatch):
    owner = _make_user('owner')
    work = sms_login(client,owner,monkeypatch,phone='+966507778899')
    customer = client.post('/api/identity/guest',json={'display_name':'Synthetic customer','phone':'0507778899'})
    assert customer.status_code == 200, customer.text
    assert customer.json()['user']['role'] == 'customer'
    assert customer.json()['user']['id'] != owner['id']
    assert client.get('/api/session',headers={'X-KPS-Context':'work'}).json()['user']['id'] == owner['id']
    assert client.get('/api/session',headers={'X-KPS-Context':'customer'}).json()['user']['id'] == customer.json()['user']['id']
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM users WHERE normalized_identity_phone(phone)=%s',('+966507778899',)).fetchone()['n'] == 2
    client.headers['X-CSRF-Token'] = customer.json()['csrf_token']
    assert client.post('/api/identity/owner/sms/request').status_code == 403
    client.headers['X-CSRF-Token'] = work['csrf_token']
    assert client.get('/api/session',headers={'X-KPS-Context':'work'}).json()['user']['role'] == 'owner'


def test_password_ui_starts_hidden_and_policy_guard_is_present():
    markup = Path('public/ops.html').read_text()
    source = Path('public/ops.js').read_text()
    assert '<details class="legacy-login" hidden>' in markup
    assert 'workSmsRequired:true' in source
    assert "$('.legacy-login').hidden=state.workSmsRequired" in source
    assert "if(state.workSmsRequired)throw new Error" in source


# Real browser verification is deliberately opt-in; never implies a source-only
# assertion above is equivalent to exercising actual DOM and network behavior.
@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires isolated PostgreSQL and real Chromium')
@pytest.mark.parametrize('lang',['ar','en'])
def test_real_browser_hides_password_and_blocks_forced_submit(employee_sms_app, chromium_browser, lang):
    from playwright.sync_api import expect
    from test_browser import _context
    context,page,errors = _context(chromium_browser,employee_sms_app)
    calls=[]
    try:
        page.add_init_script(f"localStorage.setItem('kps_ops_lang','{lang}')")
        page.on('request',lambda request:calls.append(request.url) if request.url.endswith('/api/auth/login') else None)
        page.goto(employee_sms_app+'/ops',wait_until='networkidle')
        expect(page.locator('.legacy-login')).not_to_be_visible()
        expect(page.locator('#staff-code-note')).to_contain_text('SMS')
        page.evaluate("document.querySelector('#login-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))")
        assert calls == [], 'Forced password-form submission must not call password auth'
        assert not errors, errors
    finally:
        context.close()


from tests.test_employee_sms_browser import employee_sms_app
from test_browser import chromium_browser
