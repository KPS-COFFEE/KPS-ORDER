"""Synthetic contacts and provider; isolated PostgreSQL only."""
import pytest
from app import auth, db, owner_phone, sms_verification
from tests.test_core import _authenticate
from tests.legacy_identity_helpers import make_legacy_user as _make_user
from tests.sms_policy_helpers import configure_sms


def setup(client,monkeypatch,role='owner'):
    user=_make_user(role)
    configure_sms(monkeypatch, user['brand_id'])
    token,_=_authenticate(client,user)
    with db.tx() as conn:
        conn.execute('UPDATE sessions SET step_up_at=now() WHERE user_id=%s',(user['id'],))
    return user,token


def request(client):
    r=client.post('/api/identity/owner-phone/request',json={'phone':'٠٥٠٧٧٧٨٨٩٩'})
    assert r.status_code==200,r.text
    return r.json()['challenge_id']


@pytest.mark.postgres
def test_link_only_after_sms_preserves_role_email_and_current_session(client,monkeypatch):
    user,token=setup(client,monkeypatch)
    with db.tx() as conn:
        before=conn.execute('SELECT * FROM users WHERE id=%s',(user['id'],)).fetchone()
    cid=request(client)
    with db.tx() as conn:
        assert conn.execute('SELECT phone FROM users WHERE id=%s',(user['id'],)).fetchone()['phone'] is None
    result=client.post('/api/identity/owner-phone/verify',json={'challenge_id':cid,'code':'٠١٢٣'})
    assert result.status_code==200,result.text
    assert result.json()['phone_masked']=='*******899'
    with db.tx() as conn:
        after=conn.execute('SELECT * FROM users WHERE id=%s',(user['id'],)).fetchone()
        assert after['phone']=='+966507778899' and after['phone_sms_verified_at']
        for field in ('role','active','permissions','permission_denials','branch_ids','email','email_verified_at','identity_kind'):
            assert after[field]==before[field]
        assert conn.execute('SELECT revoked_at FROM sessions WHERE token_hash=%s',(auth.token_hash(token),)).fetchone()['revoked_at'] is None
        assert conn.execute("SELECT count(*) n FROM users WHERE role='customer'").fetchone()['n']==0
        assert conn.execute("SELECT count(*) n FROM audit_events WHERE action='owner.phone.sms_linked'").fetchone()['n']==1
    assert client.post('/api/identity/owner-phone/verify',json={'challenge_id':cid,'code':'0123'}).status_code==400


@pytest.mark.postgres
def test_fresh_owner_proof_and_csrf_required(client,monkeypatch):
    user,_=setup(client,monkeypatch)
    with db.tx() as conn:conn.execute('UPDATE sessions SET step_up_at=NULL WHERE user_id=%s',(user['id'],))
    assert client.post('/api/identity/owner-phone/request',json={'phone':'0507778899'}).status_code==403
    client.headers.pop('X-CSRF-Token')
    assert client.post('/api/identity/owner-phone/request',json={'phone':'0507778899'}).status_code==403


@pytest.mark.postgres
@pytest.mark.parametrize('change',["active=false","role='driver'","updated_at=now()"])
def test_revocation_during_challenge_cannot_link(client,monkeypatch,change):
    user,_=setup(client,monkeypatch)
    cid=request(client)
    with db.tx() as conn:conn.execute('UPDATE users SET '+change+' WHERE id=%s',(user['id'],))
    result=client.post('/api/identity/owner-phone/verify',json={'challenge_id':cid,'code':'0123'})
    assert result.status_code in {401,403,409},result.text
    with db.tx() as conn:assert conn.execute('SELECT phone FROM users WHERE id=%s',(user['id'],)).fetchone()['phone'] is None


@pytest.mark.postgres
def test_phone_conflicts_checked_before_send_and_after_proof(client,monkeypatch):
    owner,_=setup(client,monkeypatch)
    cid=request(client)
    other=_make_user('driver')
    with db.tx() as conn:conn.execute("UPDATE users SET phone='+966507778899' WHERE id=%s",(other['id'],))
    assert client.post('/api/identity/owner-phone/request',json={'phone':'0507778899'}).status_code==409
    assert client.post('/api/identity/owner-phone/verify',json={'challenge_id':cid,'code':'0123'}).status_code==409
    with db.tx() as conn:assert conn.execute('SELECT phone FROM users WHERE id=%s',(owner['id'],)).fetchone()['phone'] is None


@pytest.mark.postgres
def test_proof_is_session_bound_and_guess_limits_survive_resend(client,monkeypatch):
    user,_=setup(client,monkeypatch)
    cid=request(client)
    monkeypatch.setattr(sms_verification,'check',lambda *a:False)
    for _ in range(3):assert client.post('/api/identity/owner-phone/verify',json={'challenge_id':cid,'code':'0123'}).status_code==400
    cid=request(client)
    assert client.post('/api/identity/owner-phone/verify',json={'challenge_id':cid,'code':'0123'}).status_code==429


@pytest.mark.postgres
def test_proof_cannot_move_to_another_owner_session(client,monkeypatch):
    user,_=setup(client,monkeypatch)
    cid=request(client)
    _authenticate(client,user)
    with db.tx() as conn:conn.execute('UPDATE sessions SET step_up_at=now() WHERE user_id=%s',(user['id'],))
    assert client.post('/api/identity/owner-phone/verify',json={'challenge_id':cid,'code':'0123'}).status_code==400


@pytest.mark.postgres
def test_driver_cannot_link_or_open_owner_page(client,monkeypatch):
    setup(client,monkeypatch,'driver')
    assert client.post('/api/identity/owner-phone/request',json={'phone':'0507778899'}).status_code==403
    assert client.get('/owner-phone').status_code==403


@pytest.mark.parametrize('cid',['otp_customer','employee_otp_fixture','owner_sms_fixture'])
def test_other_purposes_cannot_link_owner_phone(cid):
    with pytest.raises(ValueError):owner_phone.PhoneVerify(challenge_id=cid,code='0123')
