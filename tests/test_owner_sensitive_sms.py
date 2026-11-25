"""Session-bound sensitive proof; provider calls are synthetic and never send SMS."""
import pytest
from app import auth, db, employee_sms
from tests.test_core import _make_user, _authenticate
from tests.test_employee_sms_portals import sms_setup


@pytest.mark.parametrize('code,expected',[('0123','0123'),('٠١٢٣','0123'),('۰۱۲۳','0123')])
def test_owner_code_normalizes_without_losing_zero(code,expected):
    assert employee_sms.OwnerSMSVerify(challenge_id='owner_sms_fixture',code=code).code == expected


@pytest.mark.parametrize('challenge',['employee_otp_fixture','otp_customer'])
def test_login_challenge_never_sensitive(challenge):
    with pytest.raises(ValueError):
        employee_sms.OwnerSMSVerify(challenge_id=challenge,code='0123')


def setup(client,monkeypatch):
    sms_setup(monkeypatch)
    monkeypatch.setenv('OWNER_SENSITIVE_TWILIO_VERIFY_SERVICE_SID','VA'+'c'*32)
    monkeypatch.setattr(employee_sms,'verify_service_length',lambda *a: None)
    user=_make_user(role='owner')
    with db.tx() as conn:
        conn.execute("UPDATE users SET phone='+966507778899',invitation_status='active',email_verified_at=NULL WHERE id=%s",(user['id'],))
    _authenticate(client,user)
    return user


@pytest.mark.postgres
def test_sensitive_sms_updates_only_requesting_session_without_email(client,monkeypatch):
    user=setup(client,monkeypatch)
    sent=client.post('/api/identity/owner/sms/request')
    assert sent.status_code==200,sent.text
    cid=sent.json()['challenge_id']
    assert sent.json()['code_length']==4
    with db.tx() as conn:
        row=conn.execute('SELECT * FROM otp_challenges WHERE id=%s',(cid,)).fetchone()
        assert row['source_session_id'] and row['source_user_id']==user['id']
        assert conn.execute('SELECT step_up_at FROM sessions WHERE id=%s',(row['source_session_id'],)).fetchone()['step_up_at'] is None
    verified=client.post('/api/identity/owner/sms/verify',json={'challenge_id':cid,'code':'٠١٢٣'})
    assert verified.status_code==200,verified.text
    assert verified.json()['verified'] is True
    with db.tx() as conn:
        assert conn.execute('SELECT step_up_at FROM sessions WHERE id=%s',(row['source_session_id'],)).fetchone()['step_up_at'] is not None
        assert conn.execute('SELECT email_verified_at FROM users WHERE id=%s',(user['id'],)).fetchone()['email_verified_at'] is None
        assert conn.execute('SELECT count(*) n FROM sessions WHERE user_id=%s',(user['id'],)).fetchone()['n']==1
    assert client.post('/api/identity/owner/sms/verify',json={'challenge_id':cid,'code':'0123'}).status_code==400


@pytest.mark.postgres
def test_sensitive_sms_not_transferable_to_second_owner_session(client,monkeypatch):
    user=setup(client,monkeypatch)
    sent=client.post('/api/identity/owner/sms/request').json()
    _authenticate(client,user)
    assert client.post('/api/identity/owner/sms/verify',json={'challenge_id':sent['challenge_id'],'code':'0123'}).status_code==400
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM sessions WHERE user_id=%s AND step_up_at IS NOT NULL',(user['id'],)).fetchone()['n']==0


@pytest.mark.postgres
def test_sensitive_sms_needs_csrf_and_current_authorization(client,monkeypatch):
    user=setup(client,monkeypatch)
    sent=client.post('/api/identity/owner/sms/request').json()
    csrf=client.headers.pop('X-CSRF-Token')
    assert client.post('/api/identity/owner/sms/request').status_code==403
    client.headers['X-CSRF-Token']=csrf
    with db.tx() as conn: conn.execute('UPDATE users SET active=false WHERE id=%s',(user['id'],))
    assert client.post('/api/identity/owner/sms/verify',json={'challenge_id':sent['challenge_id'],'code':'0123'}).status_code==401


@pytest.mark.parametrize('service',['', 'VA'+'a'*32, 'VA'+'b'*32])
def test_sensitive_twilio_cannot_share_login_service(monkeypatch,service):
    from fastapi import HTTPException
    monkeypatch.setenv('TWILIO_VERIFY_SERVICE_SID','VA'+'a'*32)
    monkeypatch.setenv('EMPLOYEE_TWILIO_VERIFY_SERVICE_SID','VA'+'b'*32)
    monkeypatch.setenv('OWNER_SENSITIVE_TWILIO_VERIFY_SERVICE_SID',service)
    with pytest.raises(HTTPException) as exc:
        employee_sms._owner_provider_service()
    assert exc.value.detail['code']=='OWNER_SMS_DEDICATED_SERVICE_REQUIRED'
