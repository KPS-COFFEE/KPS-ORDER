"""Canonical SMS owner creation requires explicit full-owner approval.

No production writes or provider requests: isolated PostgreSQL and the real
staff/owner-SMS request/verify paths with a synthetic SMS transport only.
"""
import pytest
from app import auth, db
from tests.test_core import _make_user
from tests.sms_policy_helpers import sms_login, sms_step_up

PATH='/api/identity/owners/sms'


def body(**overrides):
    result={'display_name':'Synthetic additional owner','username':'SMS.owner',
            'phone':'0509031111','reason':'Owner approved full brand ownership',
            'full_owner_confirmed':True}
    result.update(overrides)
    return result


def test_sms_owner_schema_requires_a_real_boolean_and_accepts_no_password_or_scope():
    assert auth.AdditionalSMSOwnerBody(**body()).full_owner_confirmed is True
    for value in ('true',1,None):
        with pytest.raises(ValueError):
            auth.AdditionalSMSOwnerBody(**body(full_owner_confirmed=value))
    for forbidden in ('brand_id','role','permissions','branch_ids','password','password_hash'):
        with pytest.raises(ValueError):
            auth.AdditionalSMSOwnerBody(**{**body(),forbidden:'forbidden'})


@pytest.mark.postgres
def test_sms_owner_creation_requires_separate_proof_and_preserves_every_existing_user(client,monkeypatch):
    owner=_make_user('owner',username='original.sms.owner',password='OriginalOnly!NoPasswordLogin')
    _make_user('driver',branches=['kps-branch-1'])
    sms_login(client,owner,monkeypatch)
    assert client.post(PATH,json=body()).status_code==403
    sms_step_up(client,owner,monkeypatch)
    with db.tx() as conn:
        before=conn.execute('SELECT * FROM users ORDER BY id').fetchall()
    created=client.post(PATH,json=body())
    assert created.status_code==200,created.text
    target=created.json()['user']
    assert target['role']=='owner' and target['active'] and target['brand_id']==owner['brand_id']
    assert target['identity_kind']=='staff_owner_approved' and target['branch_ids']==[]
    assert target['phone']=='+966509031111'
    assert target['phone_verified_at'] is None and target['phone_sms_verified_at'] is None
    assert target['email_verified_at'] is None and created.json()['username']=='SMS.owner'
    assert created.json()['sign_in']=='registered_mobile_sms'
    with db.tx() as conn:
        row=conn.execute('SELECT * FROM users WHERE id=%s',(target['id'],)).fetchone()
        assert row['password_hash'] is None and row['username']=='SMS.owner'
        assert conn.execute('SELECT * FROM users WHERE id<>%s ORDER BY id',(target['id'],)).fetchall()==before
        event=conn.execute("SELECT details FROM audit_events WHERE action='owner.sms_account.created' AND entity_id=%s",(target['id'],)).fetchone()['details']
        assert event['full_owner_confirmed'] is True and event['phone_ownership_verified'] is False
        assert event['brand_id']==owner['brand_id']
    with db.tx() as conn:
        sessions_before=conn.execute('SELECT count(*) n FROM sessions WHERE user_id=%s',(target['id'],)).fetchone()['n']
    denied=client.post('/api/auth/login',json={'username':'SMS.owner','password':'FakePassword!DoesNotExist'})
    # This passwordless account has no credential to validate: the password
    # endpoint rejects 401 before reaching the mandatory-SMS policy check.
    assert denied.status_code==401 and denied.json()['detail']['code']=='INVALID_CREDENTIALS'
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM sessions WHERE user_id=%s',(target['id'],)).fetchone()['n']==sessions_before
        assert conn.execute('SELECT password_hash FROM users WHERE id=%s',(target['id'],)).fetchone()['password_hash'] is None
    signed=sms_login(client,target,monkeypatch)
    assert signed['user']['id']==target['id'] and signed['user']['role']=='owner'
    # The real verifier stores SMS proof in canonical OTP challenges and the
    # authenticated session; legacy profile timestamps are not fabricated.
    with db.tx() as conn:
        assert conn.execute("SELECT 1 FROM otp_challenges WHERE source_user_id=%s AND brand_id=%s AND phone=%s AND id LIKE 'employee_otp_%%' AND delivery_channel='sms' AND verified_at IS NOT NULL",(target['id'],target['brand_id'],target['phone'])).fetchone()
        assert conn.execute('SELECT 1 FROM sessions WHERE user_id=%s AND revoked_at IS NULL',(target['id'],)).fetchone()


@pytest.mark.postgres
def test_sms_owner_confirmation_identity_duplicates_and_no_role_or_brand_override(client,monkeypatch):
    owner=_make_user('owner');sms_login(client,owner,monkeypatch);sms_step_up(client,owner,monkeypatch)
    invalid=[body(full_owner_confirmed=False),body(full_owner_confirmed='true'),body(reason='   '),
             body(username='bad username'),body(phone='+12025550123'),body(display_name=' ')]
    for field,value in (('brand_id','other-brand'),('role','driver'),('branch_ids',['kps-branch-1']),('password','CannotSetPassword')):
        invalid.append({**body(),field:value})
    for payload in invalid:
        rejected=client.post(PATH,json=payload)
        assert rejected.status_code==422,rejected.text
    first=client.post(PATH,json=body())
    assert first.status_code==200,first.text
    assert client.post(PATH,json=body(username='sms.OWNER',phone='0509031112')).status_code==409
    assert client.post(PATH,json=body(username='another.owner')).status_code==409
    with db.tx() as conn:
        assert conn.execute("SELECT count(*) n FROM users WHERE username='SMS.owner'").fetchone()['n']==1
        assert not conn.execute("SELECT 1 FROM users WHERE username='another.owner'").fetchone()


@pytest.mark.postgres
@pytest.mark.parametrize('role',['driver','admin','delivery_manager','delivery_staff','branch_manager'])
def test_only_owner_can_create_sms_owner(client,monkeypatch,role):
    user=_make_user(role,branches=['kps-branch-1']);sms_login(client,user,monkeypatch)
    rejected=client.post(PATH,json=body())
    assert rejected.status_code==403,rejected.text
    with db.tx() as conn:
        assert not conn.execute("SELECT 1 FROM users WHERE username='SMS.owner'").fetchone()


@pytest.mark.postgres
def test_generic_sms_owner_creation_cannot_bypass_explicit_confirmation(client,monkeypatch):
    owner=_make_user('owner');sms_login(client,owner,monkeypatch);sms_step_up(client,owner,monkeypatch)
    payload={'display_name':'Canonical SMS owner','phone':'0509031113','role':'owner'}
    for value in (None,False,'true',1):
        rejected=client.post('/api/identity/staff/invitations',json={**payload,**({} if value is None else {'full_owner_confirmed':value})})
        assert rejected.status_code==422,rejected.text
    approved=client.post('/api/identity/staff/invitations',json={**payload,'full_owner_confirmed':True})
    assert approved.status_code==200,approved.text
    assert approved.json()['user']['role']=='owner'
    assert approved.json()['user']['phone_sms_verified_at'] is None


@pytest.mark.postgres
def test_unverified_owner_mobile_assignment_allowed_but_verified_owner_uses_independent_flow(client,monkeypatch):
    owner=_make_user('owner');sms_login(client,owner,monkeypatch);sms_step_up(client,owner,monkeypatch)
    target=client.post(PATH,json=body()).json()['user']
    path='/api/identity/staff/'+target['id']
    changed=client.patch(path,json={'phone':'0509031114','reason':'Register corrected unverified owner mobile'})
    assert changed.status_code==200,changed.text
    assert changed.json()['user']['phone_sms_verified_at'] is None
    # Establish real SMS proof on the target's registered phone before testing
    # the separate verified-owner replacement boundary.
    sms_login(client,changed.json()['user'],monkeypatch)
    sms_step_up(client,changed.json()['user'],monkeypatch)
    with db.tx() as conn:
        assert conn.execute("SELECT 1 FROM otp_challenges WHERE source_user_id=%s AND phone='+966509031114' AND id LIKE 'employee_otp_%%' AND verified_at IS NOT NULL",(target['id'],)).fetchone()
        assert conn.execute("SELECT 1 FROM otp_challenges WHERE source_user_id=%s AND phone='+966509031114' AND id LIKE 'owner_sms_%%' AND verified_at IS NOT NULL",(target['id'],)).fetchone()
    rejected=client.patch(path,json={'phone':'0509031115','reason':'Cannot replace a proven owner mobile with profile update'})
    assert rejected.status_code==409,rejected.text
    assert rejected.json()['detail']['code']=='OWNER_PHONE_VERIFICATION_REQUIRED'
    with db.tx() as conn:
        assert conn.execute('SELECT phone FROM users WHERE id=%s',(target['id'],)).fetchone()['phone']=='+966509031114'
