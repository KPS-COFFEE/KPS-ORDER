"""Final phone-user / emailed-code flows on isolated PostgreSQL; no real email sends."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import auth, db, email_delivery
from tests.test_core import _authenticate, _make_user
from tests.test_v5_identity import _invite, _mail_setup, _secret


@pytest.mark.parametrize('phone',[
    '0507778899','+966507778899','00966507778899',
    '٠٥٠٧٧٧٨٨٩٩','+٩٦٦٥٠٧٧٧٨٨٩٩','۰۰۹۶۶۵۰۷۷۷۸۸۹۹',' (050) 777-8899 ',
])
def test_phone_formats_share_exact_username(phone):
    assert auth.normalize_phone(phone)=='+966507778899'


@pytest.mark.parametrize('phone',['+96650x778899','05077788990','+966５０７７７８８９９','0507778899\x00','++966507778899'])
def test_phone_username_rejects_ambiguous_text(phone):
    with pytest.raises(HTTPException):
        auth.normalize_phone(phone)


def _staff(role='driver',phone='+966507778899',email='driver@fixture.invalid',branches=None):
    user=_make_user(role,branches=branches or ['kps-branch-1','kps-branch-5'],
                    username='fixture-legacy-owner' if role=='owner' else None,
                    password='Preserved!OwnerPassword1' if role=='owner' else None)
    with db.tx() as conn:
        return conn.execute("UPDATE users SET phone=%s,email=%s,email_verified_at=now(),identity_kind='staff_email_verified' WHERE id=%s RETURNING *",(phone,email,user['id'])).fetchone()


def _request(client,phone='0507778899'):
    result=client.post('/api/identity/staff/code/request',json={'phone':phone})
    assert result.status_code==200,result.text
    return result.json()['challenge_id']


def _code(challenge):
    with db.tx() as conn:
        outbox=conn.execute('SELECT * FROM email_outbox WHERE dedupe_key=%s',('identity:'+challenge,)).fetchone()
    assert outbox
    match=re.search(r'كود التفعيل والدخول: ([0-9]{6})',email_delivery._payload(outbox)['text'])
    assert match
    return match.group(1)


def _verify(client,challenge,code=None):
    return client.post('/api/identity/staff/code/verify',json={'challenge_id':challenge,'code':code or _code(challenge)})


@pytest.mark.postgres
class TestFinalStaffIdentity:
    def test_code_login_uses_registered_email_and_never_accepts_alternate_recipient(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        driver=_staff(phone='٠٥٠٧٧٧٨٨٩٩')
        request=client.post('/api/identity/staff/code/request',json={'phone':'00966507778899'})
        unknown=client.post('/api/identity/staff/code/request',json={'phone':'0500000001'})
        assert request.status_code==unknown.status_code==200
        assert set(request.json())==set(unknown.json())=={'accepted','challenge_id','expires_in','code_length'}
        assert request.json()['code_length']==6 and '@' not in request.text and driver['email'] not in request.text
        assert client.post('/api/identity/staff/code/request',json={'phone':'0507778899','email':'attacker@fixture.invalid'}).status_code==422
        challenge=request.json()['challenge_id']
        code=_code(challenge)
        with db.tx() as conn:
            token=conn.execute('SELECT * FROM identity_tokens WHERE id=%s',(challenge,)).fetchone()
            mail=conn.execute('SELECT * FROM email_outbox WHERE dedupe_key=%s',('identity:'+challenge,)).fetchone()
            assert mail['recipient']=='driver@fixture.invalid'
            assert code.encode() not in bytes(mail['encrypted_payload'])
            assert token['code_hash'] not in {code,auth.token_hash(code)}
            assert token['code_hash']==auth._staff_code_hash(challenge,code)
            assert auth._staff_code_hash('different-challenge',code)!=token['code_hash']
            assert code not in str(conn.execute('SELECT details FROM audit_events').fetchall())
        arabic=code.translate(str.maketrans('0123456789','٠١٢٣٤٥٦٧٨٩'))
        accepted=_verify(client,challenge,arabic)
        assert accepted.status_code==200,accepted.text
        assert accepted.json()['user']['id']==driver['id']
        assert _verify(client,challenge,code).status_code==400
        assert client.get('/api/session').json()['user']['id']==driver['id']

    def test_activation_code_reuses_one_driver_with_multi_branch_scope_and_inviter(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        manager=_make_user('owner',branches=['kps-branch-1','kps-branch-5'])
        _authenticate(client,manager)
        invitation=_invite(client).json()
        challenge=invitation['challenge_id']
        result=_verify(client,challenge)
        assert result.status_code==200,result.text
        user=result.json()['user']
        assert user['id']==invitation['user']['id'] and user['active']
        assert user['branch_ids']==['kps-branch-1','kps-branch-5']
        assert user['email_verified_at'] and user['phone_verified_at'] is None
        with db.tx() as conn:
            assert conn.execute("SELECT count(*) n FROM users WHERE role='driver'").fetchone()['n']==1
        assert client.get('/api/identity/staff').status_code==403
        assert client.post('/api/identity/email/verify',json={'challenge_id':challenge,'token':_secret(challenge)}).status_code==400

    def test_one_attempt_budget_covers_codes_and_legacy_links(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        _staff()
        challenge=_request(client)
        correct=_code(challenge)
        wrong='000000' if correct!='000000' else '111111'
        for _ in range(3):assert _verify(client,challenge,wrong).status_code==400
        for _ in range(2):
            assert client.post('/api/identity/email/verify',json={'challenge_id':challenge,'token':'x'*43}).status_code==400
        assert _verify(client,challenge,correct).status_code==400
        with db.tx() as conn:
            assert conn.execute('SELECT attempts FROM identity_tokens WHERE id=%s',(challenge,)).fetchone()['attempts']==5
            assert conn.execute('SELECT count(*) n FROM sessions').fetchone()['n']==0

    def test_expiry_resend_and_ip_phone_limits_survive_persistence(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        _staff()
        first=_request(client)
        first_code=_code(first)
        second=_request(client,'٠٥٠٧٧٧٨٨٩٩')
        assert _verify(client,first,first_code).status_code==400
        with db.tx() as conn:
            assert conn.execute('SELECT status FROM email_outbox WHERE dedupe_key=%s',('identity:'+first,)).fetchone()['status']=='cancelled'
            conn.execute("UPDATE identity_tokens SET expires_at=now()-interval '1 second' WHERE id=%s",(second,))
        assert _verify(client,second).status_code==400
        _request(client,'00966507778899')
        db.migrate()
        assert client.post('/api/identity/staff/code/request',json={'phone':'+966507778899'}).status_code==429

    @pytest.mark.parametrize('mutation',[
        "active=false","invitation_status='paused'","email='other@fixture.invalid'",
        "branch_ids='[\"kps-branch-7\"]'::jsonb","role='owner'","phone='+966500000009'",
    ])
    def test_code_rechecks_current_account_and_original_grant(self,client,monkeypatch,mutation):
        _mail_setup(monkeypatch)
        driver=_staff()
        challenge=_request(client)
        with db.tx() as conn:
            conn.execute('UPDATE users SET '+mutation+' WHERE id=%s',(driver['id'],))
        result=_verify(client,challenge)
        assert result.status_code==400 and result.json()['detail']['code']=='STAFF_CODE_INVALID'
        with db.tx() as conn:assert conn.execute('SELECT count(*) n FROM sessions').fetchone()['n']==0

    @pytest.mark.parametrize('mutation',["active=false","role='delivery_manager'","role='branch_manager'"])
    def test_pending_driver_cannot_activate_after_inviter_loses_authority(self,client,monkeypatch,mutation):
        _mail_setup(monkeypatch)
        manager=_make_user('owner',branches=['kps-branch-1','kps-branch-5'])
        _authenticate(client,manager)
        invited=_invite(client).json()
        with db.tx() as conn:conn.execute('UPDATE users SET '+mutation+' WHERE id=%s',(manager['id'],))
        assert _verify(client,invited['challenge_id']).status_code==400
        assert client.post('/api/identity/staff/code/request',json={'phone':'0507778899'}).status_code==200
        with db.tx() as conn:
            assert not conn.execute('SELECT active FROM users WHERE id=%s',(invited['user']['id'],)).fetchone()['active']
            assert conn.execute('SELECT count(*) n FROM identity_tokens WHERE user_id=%s',(invited['user']['id'],)).fetchone()['n']==1

    @pytest.mark.parametrize('channel_state',['paused','active'])
    def test_paused_email_and_disabled_brand_invalidate_existing_code(self,client,monkeypatch,channel_state):
        _mail_setup(monkeypatch)
        _staff()
        challenge=_request(client)
        with db.tx() as conn:conn.execute("UPDATE identity_channels SET state=%s,enabled=false WHERE brand_id='kps' AND channel='email'",(channel_state,))
        assert _verify(client,challenge).status_code==400
        assert client.post('/api/identity/staff/code/request',json={'phone':'0507778899'}).status_code==503
        def blocked_send(row):
            pytest.fail('A disabled active/paused email channel must not dispatch an existing queued code.')
        monkeypatch.setattr(email_delivery,'_send',blocked_send)
        assert email_delivery.dispatch_once()['attempted']==0
        with db.tx() as conn:
            conn.execute("UPDATE identity_channels SET state='testing' WHERE brand_id='kps' AND channel='email'")
            conn.execute("UPDATE brands SET active=false WHERE id='kps'")
        assert _verify(client,challenge).status_code==400

    def test_duplicate_legacy_phone_fails_closed_and_new_invitation_rejects_duplicate(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        _staff()
        owner=_make_user('owner',password='Preserved!OwnerPassword1')
        _authenticate(client,owner)
        assert _invite(client,email='duplicate@fixture.invalid').status_code==409
        _staff(phone='00966507778899',email='other@fixture.invalid')
        challenge=_request(client)
        with db.tx() as conn:
            assert not conn.execute('SELECT 1 FROM identity_tokens WHERE id=%s',(challenge,)).fetchone()
            assert conn.execute('SELECT count(*) n FROM email_outbox').fetchone()['n']==0
        assert _verify(client,challenge,'123456').status_code==400

    def test_owner_code_login_keeps_original_password_and_session_and_add_owner_is_guarded(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        owner=_staff('owner',email='owner@fixture.invalid')
        old_token,_=_authenticate(client,owner)
        assert _invite(client,email='extra@fixture.invalid',role='owner').status_code==403
        assert client.post('/api/identity/step-up',json={'password':'Preserved!OwnerPassword1'}).status_code==200
        body={'display_name':'مالك إضافي','email':'extra@fixture.invalid','phone':'0507778800','branch_ids':[],'role':'owner'}
        invited=client.post('/api/identity/staff/invitations',json=body)
        assert invited.status_code==200,invited.text
        extra=_verify(client,invited.json()['challenge_id'])
        assert extra.status_code==200 and extra.json()['user']['role']=='owner'
        challenge=_request(client)
        accepted=_verify(client,challenge)
        assert accepted.status_code==200 and accepted.json()['user']['id']==owner['id']
        with db.tx() as conn:
            assert conn.execute('SELECT password_hash FROM users WHERE id=%s',(owner['id'],)).fetchone()['password_hash']==owner['password_hash']
            assert conn.execute('SELECT revoked_at FROM sessions WHERE token_hash=%s',(auth.token_hash(old_token),)).fetchone()['revoked_at'] is None
        password_login=client.post('/api/auth/login',json={'username':'fixture-legacy-owner','password':'Preserved!OwnerPassword1'})
        assert password_login.status_code==200 and password_login.json()['user']['id']==owner['id']

    def test_owner_without_phone_enrolls_from_current_session_and_proven_registered_email(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        owner=_make_user('owner',username='legacy-owner',password='Preserved!OwnerPassword1')
        _,csrf=_authenticate(client,owner)
        saved_cookies=dict(client.cookies)
        link=client.post('/api/identity/email/request',json={'email':'original@fixture.invalid','purpose':'owner_link'}).json()
        body={'challenge_id':link['challenge_id'],'token':_secret(link['challenge_id']),'phone':'٠٥٠٧٧٧٨٨٩٩'}
        client.cookies.clear()
        assert client.post('/api/identity/email/verify',json=body).status_code==403
        for key,value in saved_cookies.items():client.cookies.set(key,value)
        client.headers['X-CSRF-Token']=csrf
        enrolled=client.post('/api/identity/email/verify',json=body)
        assert enrolled.status_code==200,enrolled.text
        assert enrolled.json()['user']['id']==owner['id'] and enrolled.json()['user']['phone']=='+966507778899'
        assert enrolled.json()['user']['phone_verified_at'] is None
        with db.tx() as conn:assert conn.execute("SELECT count(*) n FROM users WHERE role='owner'").fetchone()['n']==1
        challenge=_request(client)
        assert _verify(client,challenge).status_code==200

    def test_customer_email_link_remains_session_bound_and_has_no_staff_code(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        guest=client.post('/api/identity/guest',json={'display_name':'ضيف','phone':'0507778899'}).json()
        client.headers['X-CSRF-Token']=guest['csrf_token']
        linked=client.post('/api/identity/email/request',json={'email':'guest@fixture.invalid','purpose':'customer_link'}).json()
        assert _verify(client,linked['challenge_id'],'123456').status_code==400
        result=client.post('/api/identity/email/verify',json={'challenge_id':linked['challenge_id'],'token':_secret(linked['challenge_id'])})
        assert result.status_code==200 and result.json()['user']['id']==guest['user']['id']
        challenge=_request(client)
        with db.tx() as conn:assert not conn.execute('SELECT 1 FROM identity_tokens WHERE id=%s',(challenge,)).fetchone()

    def test_concurrent_use_establishes_only_one_session(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        driver=_staff()
        challenge=_request(client)
        code=_code(challenge)
        from app.main import app
        def consume(_):
            test_client=TestClient(app,base_url='http://testserver',headers={'Origin':'http://testserver'})
            try:return _verify(test_client,challenge,code).status_code
            finally:test_client.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(consume,range(2)))==[200,400]
        with db.tx() as conn:assert conn.execute('SELECT count(*) n FROM sessions WHERE user_id=%s',(driver['id'],)).fetchone()['n']==1


@pytest.mark.postgres
def test_delivery_manager_cannot_manage_employee_identity(client):
    manager=_make_user('delivery_manager',branches=['kps-branch-1','kps-branch-5'])
    driver=_make_user('driver',branches=['kps-branch-1'])
    _authenticate(client,manager)
    assert _invite(client).status_code==403
    assert client.get('/api/identity/staff').status_code==403
    assert client.patch('/api/identity/staff/'+driver['id'],json={'display_name':'Forbidden change'}).status_code==403
    assert client.post('/api/identity/staff/'+driver['id']+'/resend').status_code==403
    assert client.post('/api/identity/staff/'+driver['id']+'/revoke-invitation').status_code==403
