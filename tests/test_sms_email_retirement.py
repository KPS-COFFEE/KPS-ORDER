"""Explicitly gated employee email retirement; never contacts providers."""
from contextlib import contextmanager
import pytest
from fastapi import HTTPException, Response
from app import auth
from tests.test_employee_sms_portals import request


@pytest.mark.parametrize('sms,retired,expected',[
    ('false','false',False),('true','false',True),('false','true',False),('true','true',True)])
def test_kps_email_retirement_cannot_be_disabled_by_rollout_switches(monkeypatch,sms,retired,expected):
    monkeypatch.setenv('EMPLOYEE_SMS_LOGIN_ENABLED',sms)
    monkeypatch.setenv('EMPLOYEE_SMS_EMAIL_RETIRED',retired)
    assert auth.staff_email_retired('kps') is True
    assert auth.staff_email_retired('synthetic-legacy') is expected


def retirement(monkeypatch):
    monkeypatch.setenv('EMPLOYEE_SMS_LOGIN_ENABLED','true')
    monkeypatch.setenv('EMPLOYEE_SMS_EMAIL_RETIRED','true')


def test_retired_numeric_email_code_uses_challenge_brand_before_acceptance(monkeypatch):
    retirement(monkeypatch)
    monkeypatch.setattr(auth,'check_origin',lambda *a:None)
    monkeypatch.setattr(auth.db,'rate_limit',lambda *a:True)
    class Connection:
        def execute(self,*a):return self
        def fetchone(self):return {'brand_id':'kps'}
    @contextmanager
    def tx():yield Connection()
    monkeypatch.setattr(auth.db,'tx',tx)
    with pytest.raises(HTTPException) as exc:
        auth.verify_staff_code(auth.StaffCodeVerifyBody(challenge_id='email_fixture',code='123456'),request('admin.kps.sa'),Response())
    assert exc.value.detail['code']=='STAFF_EMAIL_LOGIN_RETIRED'


@pytest.mark.parametrize('purpose',['staff_login','invitation','add_owner','owner_link','recovery'])
def test_pending_owner_email_cannot_login_activate_or_grant_step_up(monkeypatch,purpose):
    retirement(monkeypatch)
    statements=[]
    class Result:
        def __init__(self,row):self.row=row
        def fetchone(self):return self.row
    class Connection:
        def execute(self,query,args):
            statements.append(query)
            assert query.startswith('SELECT'), 'Retired token cannot mutate account, session, or token'
            return Result({'id':'email_fixture','brand_id':'kps','purpose':purpose,'user_id':'synthetic_owner','session_id':None} if 'identity_tokens' in query else {'id':'synthetic_owner','role':'owner'})
    @contextmanager
    def tx():yield Connection()
    monkeypatch.setattr(auth.db,'tx',tx)
    with pytest.raises(HTTPException) as exc:
        auth._accept_email_identity({'id':'email_fixture'},request('admin.kps.sa'),Response())
    assert exc.value.detail['code']=='STAFF_EMAIL_LOGIN_RETIRED'
    assert len(statements)==2


@pytest.mark.parametrize('verifier',['customer','staff'])
def test_sensitive_sms_cannot_be_consumed_as_login(monkeypatch,verifier):
    monkeypatch.setattr(auth,'check_origin',lambda *a:None)
    monkeypatch.setattr(auth.db,'tx',lambda *a,**k:pytest.fail('Sensitive OTP must not reach login storage'))
    with pytest.raises(HTTPException) as exc:
        if verifier=='customer':
            auth.verify_otp(auth.OTPVerifyBody(challenge_id='owner_sms_fixture',code='0123'),request('order.kps.sa'),Response())
        else:
            auth.verify_staff_code(auth.StaffCodeVerifyBody(challenge_id='owner_sms_fixture',code='0123'),request('admin.kps.sa'),Response())
    assert exc.value.detail['code']=='OTP_PURPOSE_MISMATCH'


@pytest.mark.parametrize('role',['owner','admin','delivery_manager','delivery_staff','driver','branch_manager'])
def test_owner_approved_sms_invitation_needs_fresh_owner_proof_and_no_email(monkeypatch,role):
    retirement(monkeypatch)
    actor={'id':'synthetic_owner','role':'owner','brand_id':'kps','active':True,'session_id':'synthetic_session'}
    row={'id':'synthetic_staff','role':role,'brand_id':'kps','active':False}
    statements=[]
    proofs=[]
    class Result:
        def __init__(self,value):self.value=value
        def fetchone(self):return self.value
    class Connection:
        def execute(self,query,args):
            statements.append(query)
            if query.startswith('SELECT u.*'):return Result(actor)
            if query.startswith('SELECT 1'):return Result(None)
            if 'SET active=true' in query:row.update(active=True,identity_kind='staff_owner_approved',invitation_status='active')
            return Result(row)
    monkeypatch.setattr(auth,'_channel',lambda *a:{'state':'unconfigured','enabled':False})
    monkeypatch.setattr(auth,'_staff_scope',lambda *a:['synthetic_branch'])
    monkeypatch.setattr(auth,'_vehicle',lambda value:value)
    monkeypatch.setattr(auth,'_assert_staff_phone_available',lambda *a:None)
    monkeypatch.setattr(auth,'require_owner_step_up',lambda conn,user:proofs.append(user['session_id']))
    monkeypatch.setattr(auth,'_create_email_token',lambda **kw:pytest.fail('SMS approval must never queue email'))
    monkeypatch.setattr(auth,'staff_public',lambda conn,row:dict(row))
    monkeypatch.setattr(auth.db,'audit',lambda *a:None)
    body=auth.StaffInvitationBody(display_name='Synthetic',phone='0507778899',role=role)
    if role=='owner':
        with pytest.raises(HTTPException) as exc:
            auth.create_staff_invitation(Connection(),body,actor)
        assert exc.value.detail['code']=='OWNER_FULL_ACCESS_CONFIRMATION'
        assert not proofs and not row['active']
        body=body.model_copy(update={'full_owner_confirmed':True})
    result=auth.create_staff_invitation(Connection(),body,actor)
    assert proofs==['synthetic_session']
    assert result['user']['active'] and result['user']['identity_kind']=='staff_owner_approved'
    assert result['challenge_id'] is None and result['invitation_status']=='active'
    assert result['delivery_status']=='sms_on_demand'
    assert not any('identity_tokens' in query for query in statements)


@pytest.mark.parametrize('kind,status,phone,expected',[
    ('staff_owner_approved','paused','0507778899',True),
    ('pending_email','pending_email','0507778899',False),
    ('staff_owner_approved','draft','0507778899',False),
    ('staff_owner_approved','paused',None,False),
    ('staff_owner_approved','paused','invalid',False)])
def test_sms_reactivation_requires_preapproved_identity_and_registered_phone(monkeypatch,kind,status,phone,expected):
    retirement(monkeypatch)
    assert auth.staff_sms_account_approved({'identity_kind':kind,'invitation_status':status,'phone':phone}) is expected


@pytest.mark.postgres
def test_sms_invitation_and_reactivation_without_email_on_postgresql(client,monkeypatch):
    from app import db
    from tests.test_core import _make_user,_authenticate
    retirement(monkeypatch)
    owner=_make_user('owner')
    _authenticate(client,owner)
    from tests.sms_policy_helpers import sms_step_up
    sms_step_up(client, owner, monkeypatch)
    response=client.post('/api/identity/staff/invitations',json={'display_name':'Synthetic SMS Driver','phone':'0507778899','role':'driver','branch_ids':['kps-branch-1']})
    assert response.status_code==200,response.text
    result=response.json()
    identifier=result['user']['id']
    assert result['user']['email'] is None and result['user']['email_verified_at'] is None
    assert result['user']['active'] and result['challenge_id'] is None
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM identity_tokens WHERE user_id=%s',(identifier,)).fetchone()['n']==0
    paused=client.patch('/api/identity/staff/'+identifier,json={'active':False})
    assert paused.status_code==200,paused.text
    activated=client.patch('/api/identity/staff/'+identifier,json={'active':True})
    assert activated.status_code==200,activated.text
    assert activated.json()['user']['active'] and activated.json()['user']['invitation_status']=='active'
