"""Owner delivery certification uses work credentials; customer OTP stays isolated."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import auth, db, portals, sms_verification
from tests.test_core import _authenticate, _make_user
from tests.test_tawked_otp import setup_provider

REQUEST='/api/identity/owner/sms-channel-test/request'
VERIFY='/api/identity/owner/sms-channel-test/verify'
BODY={'phone':'0500000000'}

@pytest.fixture
def probe(database, monkeypatch):
    monkeypatch.setenv('KPS_PORTAL_HOST_ROUTING_ENABLED','true')
    monkeypatch.setenv('SESSION_CONTEXT_COOKIES_ENABLED','true')
    monkeypatch.setenv('APP_ALLOWED_ORIGINS','https://admin.kps.sa')
    setup_provider(monkeypatch,'testing')
    app=FastAPI();app.include_router(auth.router);app.add_middleware(portals.PortalBoundaryMiddleware)
    with TestClient(app,base_url='https://admin.kps.sa',headers={'Origin':'https://admin.kps.sa'}) as client:
        yield client


def test_portal_owner_probe_preserves_work_session_and_public_boundary(probe):
    owner=_make_user(role='owner');_authenticate(probe,owner)
    cookies=dict(probe.cookies)
    assert probe.post('/api/auth/otp/request',json=BODY).status_code==403
    sent=probe.post(REQUEST,json=BODY);assert sent.status_code==200,sent.text
    result=probe.post(VERIFY,json={'challenge_id':sent.json()['challenge_id'],'code':'0123'})
    assert result.status_code==200 and result.json()=={'verified':True,'test_only':True},result.text
    assert dict(probe.cookies)==cookies and 'set-cookie' not in result.headers
    assert probe.post(VERIFY,json={'challenge_id':sent.json()['challenge_id'],'code':'0123'}).status_code==400
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM users WHERE role='customer' AND phone_verified_at IS NOT NULL").fetchone()['n']==0
        row=auth._channel(c,'kps','sms');assert row['delivery_proven_at'] and row['state']=='testing' and not row['enabled']
        assert c.execute('SELECT count(*) n FROM sessions WHERE user_id=%s AND revoked_at IS NULL',(owner['id'],)).fetchone()['n']==1


@pytest.mark.parametrize('role',['driver','admin','delivery_manager','customer'])
def test_probe_denies_nonowner_and_spoofed_context(probe,role):
    _authenticate(probe,_make_user(role=role));probe.headers['X-KPS-Context']='work'
    assert probe.post(REQUEST,json=BODY).status_code==403


def test_probe_requires_current_owner_csrf_brand_and_testing_state(probe):
    assert probe.post(REQUEST,json=BODY).status_code==403
    owner=_make_user(role='owner');_authenticate(probe,owner)
    csrf=probe.headers.pop('X-CSRF-Token')
    assert probe.post(REQUEST,json=BODY).status_code==403
    probe.headers['X-CSRF-Token']=csrf
    with db.tx() as c:
        c.execute("INSERT INTO brands(id,name) VALUES('probe-other','Probe other')")
        c.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled,sms_provider) VALUES('probe-other','sms','testing',false,'tawked')")
    assert probe.post(REQUEST,json={**BODY,'brand_id':'probe-other'}).status_code==403
    with db.tx() as c:
        c.execute("UPDATE identity_channels SET state='active',enabled=true,delivery_proven_at=now() WHERE brand_id='kps' AND channel='sms'")
    assert probe.post(REQUEST,json=BODY).status_code==403


def test_activation_during_check_cannot_turn_probe_into_customer_login(probe,monkeypatch):
    owner=_make_user(role='owner');_authenticate(probe,owner)
    sent=probe.post(REQUEST,json=BODY);assert sent.status_code==200,sent.text
    def approve_then_activate(*args):
        with db.tx() as c:
            c.execute("UPDATE identity_channels SET state='active',enabled=true,delivery_proven_at=now() WHERE brand_id='kps' AND channel='sms'")
        return True
    monkeypatch.setattr(sms_verification,'check',approve_then_activate)
    result=probe.post(VERIFY,json={'challenge_id':sent.json()['challenge_id'],'code':'0123'})
    assert result.status_code==403 and 'set-cookie' not in result.headers
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM users WHERE role='customer' AND phone_verified_at IS NOT NULL").fetchone()['n']==0
        assert c.execute('SELECT verified_at FROM otp_challenges WHERE id=%s',(sent.json()['challenge_id'],)).fetchone()['verified_at'] is None


def test_revoked_owner_cannot_finish_probe(probe):
    owner=_make_user(role='owner');_authenticate(probe,owner)
    sent=probe.post(REQUEST,json=BODY);assert sent.status_code==200,sent.text
    with db.tx() as c:c.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s',(owner['id'],))
    assert probe.post(VERIFY,json={'challenge_id':sent.json()['challenge_id'],'code':'0123'}).status_code==403
