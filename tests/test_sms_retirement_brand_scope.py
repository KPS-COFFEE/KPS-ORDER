"""KPS SMS policy cannot retire another brand's existing email identity."""
import pytest
from app import auth,db,sms_rollout
from tests.test_core import _make_user
from tests.test_v5_identity import _mail_setup
from tests.test_final_identity import _code


def test_kps_database_retirement_never_applies_to_secondary_brand(monkeypatch):
    monkeypatch.setattr(sms_rollout,'_read_policy',lambda brand='kps':{'mode':'active','email_retired':True})
    assert sms_rollout.email_retired('kps')
    assert not sms_rollout.email_retired('enbishan')
    assert auth.public_user({'role':'owner','brand_id':'enbishan'})['employee_sms_email_retired'] is False


def test_kps_environment_retirement_never_applies_to_secondary_brand(monkeypatch):
    monkeypatch.setenv('EMPLOYEE_SMS_LOGIN_ENABLED','true')
    monkeypatch.setenv('EMPLOYEE_SMS_EMAIL_RETIRED','true')
    assert auth.staff_email_retired('kps')
    assert not auth.staff_email_retired('enbishan')
    assert not auth.staff_sms_account_approved({'brand_id':'enbishan','identity_kind':'staff_owner_approved','invitation_status':'active','phone':'0507778899'})


@pytest.mark.postgres
def test_secondary_brand_email_request_and_existing_code_survive_kps_retirement(client,monkeypatch):
    monkeypatch.setenv('SESSION_CONTEXT_COOKIES_ENABLED','false')
    monkeypatch.setenv('KPS_PORTAL_HOST_ROUTING_ENABLED','false')
    _mail_setup(monkeypatch,state='active')
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('test-secondary','Synthetic secondary')")
        conn.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled,delivery_proven_at) VALUES('test-secondary','email','active',true,now())")
    owner=_make_user('owner',brand='test-secondary')
    with db.tx() as conn:
        conn.execute("UPDATE users SET email='secondary@fixture.invalid',email_verified_at=now(),identity_kind='staff_email_verified' WHERE id=%s",(owner['id'],))
        challenge=auth._create_email_token(conn,brand='test-secondary',email='secondary@fixture.invalid',purpose='staff_login',user_id=owner['id'])
        conn.execute("UPDATE app_settings SET settings=settings||'{\"employee_sms_rollout\":{\"mode\":\"active\",\"email_retired\":true}}'::jsonb WHERE brand_id='kps'")
    sent=client.post('/api/identity/email/request',json={'brand_id':'test-secondary','email':'secondary@fixture.invalid','purpose':'staff_login'})
    assert sent.status_code==200,sent.text
    # Request supersedes the earlier token normally; use its new current code.
    challenge=sent.json()['challenge_id']
    code=_code(challenge)
    verified=client.post('/api/identity/staff/code/verify',json={'challenge_id':challenge,'code':code})
    assert verified.status_code==200,verified.text
    assert verified.json()['user']['id']==owner['id']
    assert verified.json()['user']['employee_sms_email_retired'] is False
