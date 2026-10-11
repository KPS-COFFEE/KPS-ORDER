"""V5 proof-bound identities, invitations and guest ownership on real isolated PostgreSQL."""
from __future__ import annotations

import base64
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException

from app import auth, db, email_delivery
from tests.test_core import _authenticate, _body, _create, _make_user, _prepare_catalog


def _mail_setup(monkeypatch,state='testing'):
    # Synthetic isolated fixture: this never calls a live provider or claims deployment readiness.
    monkeypatch.setenv('EMAIL_PROVIDER','resend')
    monkeypatch.setenv('EMAIL_FROM','noreply@fixture.invalid')
    monkeypatch.setenv('RESEND_API_KEY','synthetic-test-key-never-sent')
    monkeypatch.setenv('MAIL_OUTBOX_KEY',base64.urlsafe_b64encode(b'x'*32).decode())
    with db.tx() as conn:
        conn.execute('UPDATE identity_channels SET state=%s,enabled=%s,delivery_proven_at=CASE WHEN %s THEN now() ELSE NULL END WHERE brand_id=%s AND channel=%s',(state,state=='active',state=='active','kps','email'))


def _secret(challenge):
    with db.tx() as conn:
        row=conn.execute('SELECT * FROM email_outbox WHERE dedupe_key=%s',('identity:'+challenge,)).fetchone()
    body=email_delivery._payload(row)
    fragment=parse_qs(urlsplit(body['text'].splitlines()[-1]).fragment)
    assert fragment['identity_challenge']==[challenge]
    return fragment['identity_token'][0]


def _guest(client,phone='0501112233'):
    client.cookies.clear()
    client.headers.pop('X-CSRF-Token',None)
    result=client.post('/api/identity/guest',json={'display_name':'ضيف اختبار','phone':phone})
    assert result.status_code==200,result.text
    client.headers['X-CSRF-Token']=result.json()['csrf_token']
    return result.json()['user']


def _invite(client,email='driver@fixture.invalid',branches=None,role='driver'):
    return client.post('/api/identity/staff/invitations',json={
        'display_name':'سائق اختبار','email':email,'phone':'0507778899',
        'branch_ids':branches or ['kps-branch-1','kps-branch-5'],'role':role})


def test_email_validation_and_no_implicit_sender_configuration(monkeypatch):
    assert email_delivery.normalize_email(' Person@EXAMPLE.com ')=='person@example.com'
    for invalid in ('plain','x@example.com\nBcc:attacker@example.com','a..b@example.com','x@localhost'):
        with pytest.raises(HTTPException):
            email_delivery.normalize_email(invalid)
    monkeypatch.setenv('EMAIL_PROVIDER','resend')
    monkeypatch.setenv('EMAIL_FROM','owner@example.com')
    assert email_delivery.configuration()['configured'] is False
    assert email_delivery.configuration()['delivery_proven'] is False


@pytest.mark.postgres
class TestV5Identity:
    def test_guest_returns_without_login_but_expired_or_revoked_cookie_cannot_recover(self,client):
        user = _guest(client)
        with db.tx() as conn:
            row = conn.execute('SELECT * FROM sessions WHERE user_id=%s', (user['id'],)).fetchone()
            conn.execute("UPDATE sessions SET expires_at=now()+interval '1 hour' WHERE id=%s", (row['id'],))
        result = client.get('/api/session')
        assert result.json()['user']['id'] == user['id']
        assert 'Max-Age=2592000' in result.headers['set-cookie']
        with db.tx() as conn:
            assert conn.execute("SELECT expires_at>now()+interval '29 days' AS remembered FROM sessions WHERE id=%s", (row['id'],)).fetchone()['remembered']
            assert conn.execute('SELECT count(*) AS n FROM users WHERE id=%s', (user['id'],)).fetchone()['n'] == 1
        cookies = dict(client.cookies)
        assert client.post('/api/auth/logout', json={}).status_code == 200
        client.cookies.update(cookies)
        assert client.get('/api/session').json()['user'] is None
        assert client.get('/api/orders').status_code == 401
        another = _guest(client)
        assert another['id'] != user['id']
        with db.tx() as conn:
            conn.execute("UPDATE sessions SET expires_at=now()-interval '1 minute' WHERE user_id=%s", (another['id'],))
        assert client.get('/api/session').json()['user'] is None

    @pytest.mark.parametrize('service',['pickup','car','delivery'])
    def test_guest_completes_services_with_no_messaging_provider(self,client,service):
        _prepare_catalog()
        manager=_make_user('delivery_manager',branches=['kps-branch-1'])
        with db.tx() as conn:
            conn.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps',%s)",(db.new_id('shift_'),manager['id'],db.json(['kps-branch-1'])))
        user=_guest(client)
        assert user['role']=='customer' and user['identity_kind']=='guest'
        assert user['email_verified_at'] is None and user['phone_verified_at'] is None
        channels=client.get('/api/identity/channels').json()['channels']
        assert channels['guest']['available'] and not channels['sms']['available'] and not channels['email']['available']
        first=_create(client,_body(service),key='guest-service-'+service)
        assert first.status_code==200,first.text
        order=first.json()['order']
        assert order['customer_id']==user['id'] and order['customer']['phone_verified'] is False
        assert _create(client,_body(service),key='guest-service-'+service).json()['order']['id']==order['id']
        assert client.post('/api/shifts',json={'kind':'manager','branch_ids':['kps-branch-1']},headers={'Idempotency-Key':'guest-forbidden-shift'}).status_code==403
        assert client.get('/api/admin/config').status_code==403

    def test_same_unverified_phone_never_joins_known_or_other_guest(self,client):
        _prepare_catalog()
        known=_make_user()
        with db.tx() as conn:
            conn.execute("UPDATE users SET phone='+966501112233',phone_verified_at=now() WHERE id=%s",(known['id'],))
        _authenticate(client,known)
        old=_create(client,key='old-known-order').json()['order']
        guest_a=_guest(client)
        own=_create(client,key='guest-a-order').json()['order']
        assert guest_a['id']!=known['id'] and client.get('/api/orders/'+old['id']).status_code==404
        guest_b=_guest(client)
        assert guest_b['id'] not in {known['id'],guest_a['id']}
        assert client.get('/api/orders/'+own['id']).status_code==404
        assert client.get('/api/orders').json()['orders']==[]
        with db.tx() as conn:
            assert conn.execute("SELECT count(*) AS n FROM users WHERE phone='+966501112233'").fetchone()['n']==3

    def test_guest_email_link_requires_both_proofs_and_preserves_order(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        _prepare_catalog()
        user=_guest(client)
        order=_create(client,key='email-link-order').json()['order']
        cookies=dict(client.cookies)
        csrf=client.headers['X-CSRF-Token']
        requested=client.post('/api/identity/email/request',json={'email':'guest@fixture.invalid','purpose':'customer_link'}).json()
        secret=_secret(requested['challenge_id'])
        _guest(client,'0504445566')
        body={'challenge_id':requested['challenge_id'],'token':secret}
        assert client.post('/api/identity/email/verify',json=body).status_code==403
        client.cookies.clear()
        for key,value in cookies.items():client.cookies.set(key,value)
        client.headers['X-CSRF-Token']=csrf
        linked=client.post('/api/identity/email/verify',json=body)
        assert linked.status_code==200,linked.text
        assert linked.json()['user']['id']==user['id'] and linked.json()['user']['email_verified_at']
        assert linked.json()['user']['phone_sms_verified_at'] is None and linked.json()['user']['phone_verified_at'] is None
        assert client.get('/api/orders/'+order['id']).status_code==200
        assert client.post('/api/identity/email/verify',json=body).status_code==400
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) n FROM orders WHERE customer_id=%s',(user['id'],)).fetchone()['n']==1
            token=conn.execute('SELECT * FROM identity_tokens WHERE id=%s',(requested['challenge_id'],)).fetchone()
            assert token['token_hash']!=secret
            outbox=conn.execute('SELECT * FROM email_outbox WHERE dedupe_key=%s',('identity:'+requested['challenge_id'],)).fetchone()
            assert secret.encode() not in bytes(outbox['encrypted_payload'])
            assert outbox['status']=='delivered' and outbox['delivery_at']

    def test_tokens_expire_attempt_limit_and_resend_revokes_old(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        manager=_make_user('delivery_manager',branches=['kps-branch-1','kps-branch-5'])
        _authenticate(client,manager)
        invited=_invite(client).json()
        old=invited['challenge_id']
        resent=client.post('/api/identity/staff/'+invited['user']['id']+'/resend')
        assert resent.status_code==200
        assert client.post('/api/identity/email/verify',json={'challenge_id':old,'token':_secret(old)}).status_code==400
        challenge=resent.json()['challenge_id']
        for _ in range(5):
            assert client.post('/api/identity/email/verify',json={'challenge_id':challenge,'token':'z'*43}).status_code==400
        assert client.post('/api/identity/email/verify',json={'challenge_id':challenge,'token':_secret(challenge)}).status_code==400
        with db.tx() as conn:
            assert conn.execute('SELECT attempts FROM identity_tokens WHERE id=%s',(challenge,)).fetchone()['attempts']==5
        resent2=client.post('/api/identity/staff/'+invited['user']['id']+'/resend').json()['challenge_id']
        with db.tx() as conn:conn.execute("UPDATE identity_tokens SET expires_at=now()-interval '1 second' WHERE id=%s",(resent2,))
        assert client.post('/api/identity/email/verify',json={'challenge_id':resent2,'token':_secret(resent2)}).status_code==400

    def test_manager_driver_invitation_scopes_and_self_grant_denial(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        manager=_make_user('delivery_manager',branches=['kps-branch-1','kps-branch-5'])
        _authenticate(client,manager)
        assert _invite(client,branches=['kps-branch-1','kps-branch-7']).status_code==403
        assert _invite(client,role='owner').status_code==403
        malicious={'display_name':'اسم','email':'bad@fixture.invalid','phone':'0507778899','branch_ids':['kps-branch-1'],'active':True,'email_verified':True}
        assert client.post('/api/identity/staff/invitations',json=malicious).status_code==422
        invitation=_invite(client)
        assert invitation.status_code==200,invitation.text
        pending=invitation.json()['user']
        assert pending['active'] is False and pending['email_verified_at'] is None
        assert client.patch('/api/identity/staff/'+pending['id'],json={'active':True}).status_code==403
        body={'challenge_id':invitation.json()['challenge_id'],'token':_secret(invitation.json()['challenge_id'])}
        verified=client.post('/api/identity/email/verify',json=body)
        assert verified.status_code==200,verified.text
        driver=verified.json()['user']
        assert driver['id']==pending['id'] and driver['branch_ids']==['kps-branch-1','kps-branch-5']
        client.headers['X-CSRF-Token']=verified.json()['csrf_token']
        assert client.patch('/api/identity/staff/'+driver['id'],json={'branch_ids':['kps-branch-7']}).status_code==403
        assert client.get('/api/identity/staff').status_code==403
        assert client.post('/api/identity/email/verify',json=body).status_code==400

    def test_owner_link_does_not_duplicate_owner_and_add_owner_requires_stepup(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        owner=_make_user('owner',username='fixture-owner',password='OnlyFixture!Password1')
        _authenticate(client,owner)
        assert _invite(client,email='extra@fixture.invalid',role='owner').status_code==403
        assert client.post('/api/identity/step-up',json={'password':'OnlyFixture!Password1'}).status_code==200
        invited=_invite(client,email='extra@fixture.invalid',role='owner')
        assert invited.status_code==200,invited.text
        request=client.post('/api/identity/email/request',json={'email':'original@fixture.invalid','purpose':'owner_link'})
        assert request.status_code==200
        verified=client.post('/api/identity/email/verify',json={'challenge_id':request.json()['challenge_id'],'token':_secret(request.json()['challenge_id'])})
        assert verified.status_code==200 and verified.json()['user']['id']==owner['id']
        client.headers['X-CSRF-Token']=verified.json()['csrf_token']
        with db.tx() as conn:
            assert conn.execute("SELECT count(*) n FROM users WHERE role='owner' AND active").fetchone()['n']==1
            assert conn.execute('SELECT password_hash FROM users WHERE id=%s',(owner['id'],)).fetchone()['password_hash']==owner['password_hash']
        assert client.patch('/api/identity/staff/'+owner['id'],json={'active':False}).status_code==409
        extra=client.post('/api/identity/email/verify',json={'challenge_id':invited.json()['challenge_id'],'token':_secret(invited.json()['challenge_id'])})
        assert extra.status_code==200 and extra.json()['user']['role']=='owner'
        assert extra.json()['user']['id']!=owner['id']

    def test_channel_activation_requires_delivery_not_provider_acceptance(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        owner=_make_user('owner')
        _authenticate(client,owner)
        assert client.patch('/api/identity/settings',json={'channel':'email','state':'active','enabled':True}).status_code==409
        request=client.post('/api/identity/email/request',json={'email':'owner@fixture.invalid','purpose':'owner_link'}).json()
        monkeypatch.setattr(email_delivery,'_send',lambda row:'synthetic-provider-acceptance')
        assert email_delivery.dispatch_once()['provider_accepted']==1
        with db.tx() as conn:
            assert conn.execute('SELECT status,delivery_at FROM email_outbox WHERE dedupe_key=%s',('identity:'+request['challenge_id'],)).fetchone()=={'status':'provider_accepted','delivery_at':None}
        assert client.patch('/api/identity/settings',json={'channel':'email','state':'active','enabled':True}).status_code==409
        accepted=client.post('/api/identity/email/verify',json={'challenge_id':request['challenge_id'],'token':_secret(request['challenge_id'])})
        client.headers['X-CSRF-Token']=accepted.json()['csrf_token']
        assert client.patch('/api/identity/settings',json={'channel':'email','state':'active','enabled':True}).status_code==200
        assert client.get('/api/identity/channels').json()['channels']['email']['available']
        unknown=client.post('/api/identity/email/request',json={'email':'unknown@fixture.invalid','purpose':'staff_login'})
        known=client.post('/api/identity/email/request',json={'email':'owner@fixture.invalid','purpose':'staff_login'})
        assert unknown.status_code==known.status_code==200
        assert set(unknown.json())==set(known.json())=={'accepted','challenge_id','expires_in'}

    def test_scope_revocation_preserves_open_tasks_and_custody(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        owner=_make_user('owner')
        driver=_make_user('driver',branches=['kps-branch-1','kps-branch-5'])
        with db.tx() as conn:
            conn.execute("UPDATE users SET email='driver@fixture.invalid',email_verified_at=now(),identity_kind='staff_email_verified' WHERE id=%s",(driver['id'],))
            manager_shift=db.new_id('shift_')
            conn.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps',%s)",(manager_shift,owner['id'],db.json(['kps-branch-1','kps-branch-5'])))
            driver_shift=db.new_id('shift_')
            conn.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id) VALUES(%s,'driver',%s,'kps',%s,%s)",(driver_shift,driver['id'],db.json(driver['branch_ids']),manager_shift))
        _prepare_catalog()
        _guest(client)
        order=_create(client,_body('delivery'),key='scope-protected-order').json()['order']
        with db.tx() as conn:
            conn.execute('INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,assigned_by) VALUES(%s,%s,%s,%s,%s)',(db.new_id('task_'),order['id'],driver['id'],driver_shift,owner['id']))
        _authenticate(client,owner)
        assert client.patch('/api/identity/staff/'+driver['id'],json={'branch_ids':['kps-branch-5']}).status_code==409
        assert client.patch('/api/identity/staff/'+driver['id'],json={'active':False}).status_code==409
        with db.tx() as conn:
            conn.execute("UPDATE driver_tasks SET status='transferred' WHERE driver_id=%s",(driver['id'],))
        assert client.patch('/api/identity/staff/'+driver['id'],json={'branch_ids':['kps-branch-5']}).status_code==409
        assert client.patch('/api/identity/staff/'+driver['id'],json={'active':False}).status_code==409
        with db.tx() as conn:conn.execute("UPDATE shifts SET status='closed',closed_at=now() WHERE id=%s",(driver_shift,))
        assert client.patch('/api/identity/staff/'+driver['id'],json={'branch_ids':['kps-branch-5']}).status_code==200
        assert client.patch('/api/identity/staff/'+driver['id'],json={'active':False}).status_code==200

    def test_revoke_pending_invitation_invalidates_token_and_cancels_outbox(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        manager=_make_user('delivery_manager',branches=['kps-branch-1','kps-branch-5'])
        _authenticate(client,manager)
        invitation=_invite(client).json()
        secret=_secret(invitation['challenge_id'])
        assert client.post('/api/identity/staff/'+invitation['user']['id']+'/revoke-invitation').status_code==200
        assert client.post('/api/identity/email/verify',json={'challenge_id':invitation['challenge_id'],'token':secret}).status_code==400
        with db.tx() as conn:
            assert conn.execute('SELECT status FROM email_outbox WHERE dedupe_key=%s',('identity:'+invitation['challenge_id'],)).fetchone()['status']=='cancelled'

    def test_unconfigured_email_never_blocks_guest_and_cookie_tampering_rejected(self,client):
        user=_guest(client)
        assert client.post('/api/identity/email/request',json={'email':'person@fixture.invalid','purpose':'customer_link'}).status_code==503
        assert client.get('/api/session').json()['user']['id']==user['id']
        csrf=client.headers['X-CSRF-Token']
        assert client.post('/api/identity/guest',json={'display_name':'آخر','phone':'0504445566'},headers={'X-CSRF-Token':'forged'}).status_code==403
        client.cookies.clear()
        client.cookies.set(auth.COOKIE,secrets.token_urlsafe(32))
        assert client.get('/api/orders').status_code==401
        client.headers['X-CSRF-Token']=csrf

    def test_staff_draft_with_no_sender_is_inactive_and_resend_requires_channel(self,client):
        manager=_make_user('delivery_manager',branches=['kps-branch-1','kps-branch-5'])
        _authenticate(client,manager)
        result=_invite(client)
        assert result.status_code==200,result.text
        assert result.json()['delivery_status']=='channel_unavailable'
        assert result.json()['challenge_id'] is None
        user=result.json()['user']
        assert user['invitation_status']=='draft' and not user['active'] and user['email_verified_at'] is None
        assert client.post('/api/identity/staff/'+user['id']+'/resend').status_code==503
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) n FROM identity_tokens WHERE user_id=%s',(user['id'],)).fetchone()['n']==0

    def test_signed_delivery_webhook_replay_and_forgery(self,client,monkeypatch):
        import hashlib
        import hmac
        import json
        import time
        _mail_setup(monkeypatch)
        owner=_make_user('owner')
        _authenticate(client,owner)
        request=client.post('/api/identity/email/request',json={'email':'owner@fixture.invalid','purpose':'owner_link'}).json()
        monkeypatch.setattr(email_delivery,'_send',lambda row:'provider-reference-123')
        email_delivery.dispatch_once()
        signing=b'fixture-webhook-secret-12345'
        monkeypatch.setenv('RESEND_WEBHOOK_SECRET','whsec_'+base64.b64encode(signing).decode())
        raw=json.dumps({'type':'email.delivered','data':{'email_id':'provider-reference-123'}},separators=(',',':')).encode()
        event_id='msg_fixture_123'
        timestamp=str(int(time.time()))
        expected=base64.b64encode(hmac.new(signing,event_id.encode()+b'.'+timestamp.encode()+b'.'+raw,hashlib.sha256).digest()).decode()
        headers={'svix-id':event_id,'svix-timestamp':timestamp,'svix-signature':'v1,'+expected,'Content-Type':'application/json'}
        bad=headers|{'svix-signature':'v1,'+base64.b64encode(b'invalid').decode()}
        assert client.post('/mailhooks/resend',content=raw,headers=bad).status_code==401
        assert client.post('/mailhooks/resend',content=raw,headers=headers).status_code==200
        assert client.post('/mailhooks/resend',content=raw,headers=headers).status_code==200
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) n FROM email_webhook_events WHERE id=%s',(event_id,)).fetchone()['n']==1
            assert conn.execute('SELECT status FROM email_outbox WHERE dedupe_key=%s',('identity:'+request['challenge_id'],)).fetchone()['status']=='delivered'
            # Mail server delivery alone never marks the channel as inbox-proven.
            assert conn.execute("SELECT delivery_proven_at FROM identity_channels WHERE brand_id='kps' AND channel='email'").fetchone()['delivery_proven_at'] is None

    @pytest.mark.parametrize('state', ['unconfigured', 'paused', 'fault'])
    def test_optional_otp_respects_disabled_channel_and_guest_stays_available(self,client,monkeypatch,state):
        monkeypatch.setattr(auth,'provider_config',lambda: ('AC'+'1'*32,'test-only','VA'+'1'*32))
        calls=[]
        monkeypatch.setattr(auth,'_provider_post',lambda path,data: calls.append((path,data)))
        with db.tx() as conn:
            conn.execute("UPDATE identity_channels SET state=%s,enabled=false WHERE brand_id='kps' AND channel='sms'",(state,))
        result=client.post('/api/auth/otp/request',json={'phone':'0500000000'})
        assert result.status_code==503 and result.json()['detail']['code']=='OTP_CHANNEL_UNAVAILABLE'
        assert calls==[]
        guest=_guest(client)
        assert guest['identity_kind']=='guest' and guest['phone_verified_at'] is None

    def test_pausing_otp_revokes_pending_authentication_not_existing_guest(self,client,monkeypatch):
        monkeypatch.setattr(auth,'provider_config',lambda: ('AC'+'1'*32,'test-only','VA'+'1'*32))
        calls=[]
        def provider(path,data):
            calls.append(path)
            return {'status':'pending','sid':'VE'+'2'*32} if path=='Verifications' else {'status':'approved','to':'+966500000000'}
        monkeypatch.setattr(auth,'_provider_post',provider)
        with db.tx() as conn:
            conn.execute("UPDATE identity_channels SET state='testing',enabled=false WHERE brand_id='kps' AND channel='sms'")
        sent=client.post('/api/auth/otp/request',json={'phone':'0500000000'})
        assert sent.status_code==200,sent.text
        guest=_guest(client)
        with db.tx() as conn:
            conn.execute("UPDATE identity_channels SET state='paused',enabled=false WHERE brand_id='kps' AND channel='sms'")
        denied=client.post('/api/auth/otp/verify',json={'challenge_id':sent.json()['challenge_id'],'code':'123456'})
        assert denied.status_code==503 and calls==['Verifications']
        assert client.get('/api/session').json()['user']['id']==guest['id']
        with db.tx() as conn:
            assert conn.execute("SELECT count(*) n FROM users WHERE phone_verified_at IS NOT NULL").fetchone()['n']==0

    def test_otp_proof_uses_actual_send_channel_after_runtime_setting_change(self,client,monkeypatch):
        monkeypatch.setattr(auth,'provider_config',lambda: ('AC'+'1'*32,'test-only','VA'+'1'*32))
        monkeypatch.setenv('OTP_CHANNEL','sms')
        def provider(path,data):
            if path=='Verifications':
                assert data['Channel']=='sms'
                return {'status':'pending','sid':'VE'+'2'*32}
            return {'status':'approved','to':'+966500000000'}
        monkeypatch.setattr(auth,'_provider_post',provider)
        with db.tx() as conn:
            conn.execute("UPDATE identity_channels SET state='testing',enabled=false WHERE brand_id='kps' AND channel IN ('sms','whatsapp')")
        sent=client.post('/api/auth/otp/request',json={'phone':'0500000000'}).json()
        monkeypatch.setenv('OTP_CHANNEL','whatsapp')
        result=client.post('/api/auth/otp/verify',json={'challenge_id':sent['challenge_id'],'code':'123456'})
        assert result.status_code==200,result.text
        user=result.json()['user']
        assert user['identity_kind']=='phone_sms_verified'
        assert user['phone_sms_verified_at'] and user['phone_whatsapp_verified_at'] is None
        with db.tx() as conn:
            assert conn.execute('SELECT delivery_channel FROM otp_challenges WHERE id=%s',(sent['challenge_id'],)).fetchone()['delivery_channel']=='sms'
            channels={r['channel']:r['delivery_proven_at'] for r in conn.execute("SELECT channel,delivery_proven_at FROM identity_channels WHERE brand_id='kps' AND channel IN ('sms','whatsapp')").fetchall()}
            assert channels['sms'] and channels['whatsapp'] is None

    def test_legacy_pending_otp_without_channel_cannot_fabricate_proof(self,client,monkeypatch):
        monkeypatch.setattr(auth,'provider_config',lambda: ('AC'+'1'*32,'test-only','VA'+'1'*32))
        calls=[]
        monkeypatch.setattr(auth,'_provider_post',lambda path,data: calls.append(path))
        identifier=db.new_id('otp_')
        with db.tx() as conn:
            conn.execute("INSERT INTO otp_challenges(id,brand_id,phone,provider_reference,expires_at) VALUES(%s,'kps','+966500000000',%s,now()+interval '10 minutes')",(identifier,'VE'+'2'*32))
        result=client.post('/api/auth/otp/verify',json={'challenge_id':identifier,'code':'123456'})
        assert result.status_code==503 and result.json()['detail']['code']=='OTP_CHANNEL_UNAVAILABLE'
        assert calls==[]

    def test_provider_cannot_verify_different_channel_than_original_send(self,client,monkeypatch):
        monkeypatch.setattr(auth,'provider_config',lambda: ('AC'+'1'*32,'test-only','VA'+'1'*32))
        def provider(path,data):
            return {'status':'pending','sid':'VE'+'2'*32,'channel':'sms'} if path=='Verifications' else {'status':'approved','to':'+966500000000','channel':'whatsapp'}
        monkeypatch.setattr(auth,'_provider_post',provider)
        with db.tx() as conn:
            conn.execute("UPDATE identity_channels SET state='testing',enabled=false WHERE brand_id='kps' AND channel='sms'")
        sent=client.post('/api/auth/otp/request',json={'phone':'0500000000'}).json()
        result=client.post('/api/auth/otp/verify',json={'challenge_id':sent['challenge_id'],'code':'123456'})
        assert result.status_code==400 and result.json()['detail']['code']=='OTP_INVALID'
        with db.tx() as conn:
            assert conn.execute("SELECT count(*) n FROM users WHERE phone_verified_at IS NOT NULL").fetchone()['n']==0
            assert conn.execute('SELECT verified_at FROM otp_challenges WHERE id=%s',(sent['challenge_id'],)).fetchone()['verified_at'] is None

    @pytest.mark.parametrize('neighborhood',['','   ','...','\u200b\u200f','\x00الحزم'])
    @pytest.mark.parametrize('identity',['guest','email_verified'])
    def test_delivery_quote_and_checkout_require_meaningful_neighborhood(self,client,neighborhood,identity):
        _prepare_catalog()
        if identity=='guest':
            _guest(client)
        else:
            user=_make_user()
            with db.tx() as conn:
                conn.execute("UPDATE users SET identity_kind='email_verified',email_verified_at=now(),phone='+966500000000' WHERE id=%s",(user['id'],))
            _authenticate(client,user)
        body=_body('delivery')
        body['customer']['neighborhood']=neighborhood
        for route in ('/api/quote','/api/orders'):
            result=client.post(route,json=body,headers={'Idempotency-Key':'missing-delivery-district'})
            assert result.status_code==422 and result.json()['detail']['code']=='DELIVERY_NEIGHBORHOOD_REQUIRED'
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) n FROM orders').fetchone()['n']==0

    @pytest.mark.parametrize('service',['pickup','car'])
    def test_non_delivery_neighborhood_remains_optional(self,client,service):
        _prepare_catalog()
        _guest(client)
        body=_body(service)
        body['customer']['neighborhood']=''
        assert client.post('/api/quote',json=body).status_code==200
        result=_create(client,body,key='non-delivery-no-district')
        assert result.status_code==200,result.text

    def test_delivery_keeps_trimmed_neighborhood_in_durable_order(self,client):
        _prepare_catalog()
        manager=_make_user('delivery_manager',branches=['kps-branch-1'])
        with db.tx() as conn:
            conn.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps',%s)",(db.new_id('shift_'),manager['id'],db.json(['kps-branch-1'])))
        _guest(client)
        body=_body('delivery')
        body['customer']['neighborhood']='  حي الحزم  '
        result=_create(client,body,key='district-snapshot')
        assert result.status_code==200,result.text
        order=result.json()['order']
        assert order['customer']['neighborhood']=='حي الحزم'
        db.migrate()
        assert client.get('/api/orders/'+order['id']).json()['order']['customer']['neighborhood']=='حي الحزم'
