"""Portal and provider contract tests; no network sends or production database."""
from datetime import datetime, timezone
from contextlib import contextmanager
import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from app import auth, employee_sms, portals


@pytest.fixture(autouse=True)
def enable_portals(monkeypatch):
    monkeypatch.setenv('KPS_PORTAL_HOST_ROUTING_ENABLED','true')


def request(host):
    return Request({'type':'http','method':'POST','scheme':'https','path':'/api/example','headers':[(b'host',host.encode())],'query_string':b'','server':(host,443)})


@pytest.mark.parametrize('host,allowed,denied',[
    ('order.kps.sa','customer','owner'),('admin.kps.sa','owner','driver'),('admin.kps.sa','admin','delivery_manager'),('staff.kps.sa','driver','customer'),('staff.kps.sa','delivery_manager','admin')])
def test_host_role_server_boundary(host,allowed,denied):
    portals.enforce_role(request(host),{'role':allowed})
    with pytest.raises(HTTPException) as exc:
        portals.enforce_role(request(host),{'role':denied})
    assert exc.value.status_code==403


def portal_client(host):
    app=FastAPI()
    @app.api_route('/{path:path}',methods=['GET','POST'])
    def ok(path):return {'ok':True}
    app.add_middleware(portals.PortalBoundaryMiddleware)
    return TestClient(app,base_url='https://'+host,follow_redirects=False)


def test_staff_root_customer_api_and_cross_origin_boundaries():
    with portal_client('staff.kps.sa') as client:
        assert client.get('/').headers['location']=='/ops'
        assert client.post('/api/identity/guest',headers={'Origin':'https://staff.kps.sa'}).status_code==403
        assert client.post('/api/example',headers={'Origin':'https://admin.kps.sa'}).status_code==403
        assert client.post('/api/example',headers={'Origin':'https://staff.kps.sa'}).status_code==200
        assert client.post('/api/example').status_code==403


def test_storefront_never_serves_employee_login_or_ops():
    with portal_client('order.kps.sa') as client:
        for path in ('/ops','/ops.html','/ops.js'):
            assert client.get(path).status_code==403
        assert client.get('/').status_code==200
        assert client.post('/api/auth/login',headers={'Origin':'https://order.kps.sa'}).status_code==403


@pytest.mark.parametrize('code',['1234','12345','1234567','١٢٣٤٥٦','12a456',' 123456'])
def test_employee_code_requires_exactly_six_ascii_digits(code):
    with pytest.raises(ValueError):employee_sms.EmployeeSMSVerify(challenge_id='employee_otp_fixture',code=code)


def test_employee_customer_challenges_are_not_interchangeable():
    employee_sms.EmployeeSMSVerify(challenge_id='employee_otp_fixture',code='012345')
    with pytest.raises(ValueError):employee_sms.EmployeeSMSVerify(challenge_id='otp_customer',code='012345')
    with pytest.raises(ValueError):auth.OTPVerifyBody(challenge_id='employee_otp_fixture',code='012345')


@pytest.mark.parametrize('length',[4,5,7,None,'6'])
def test_provider_service_length_is_checked_without_changing_it(monkeypatch,length):
    monkeypatch.setenv('EMPLOYEE_TWILIO_VERIFY_SERVICE_SID','VA'+'a'*32)
    original=httpx.Client
    seen=[]
    def handler(req):
        seen.append(req.method)
        return httpx.Response(200,json={'sid':'VA'+'a'*32,'code_length':length})
    monkeypatch.setattr(employee_sms.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(HTTPException) as exc:employee_sms.verify_service_length()
    assert exc.value.detail['code']=='EMPLOYEE_OTP_PROVIDER_CONFIGURATION'
    assert seen==['GET']


def test_provider_service_six_digit_configuration(monkeypatch):
    monkeypatch.setenv('EMPLOYEE_TWILIO_VERIFY_SERVICE_SID','VA'+'a'*32)
    original=httpx.Client
    monkeypatch.setattr(employee_sms.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'sid':'VA'+'a'*32,'code_length':6})),**kw))
    employee_sms.verify_service_length()


@pytest.mark.parametrize('state,enabled,proof',[('testing',False,None),('paused',False,True),('active',False,True),('active',True,None)])
def test_employee_channel_requires_certified_active_delivery(monkeypatch,state,enabled,proof):
    monkeypatch.setattr(auth,'_channel',lambda *a:{'state':state,'enabled':enabled,'delivery_proven_at':proof})
    with pytest.raises(HTTPException) as exc:employee_sms.provider_ready(None,'kps')
    assert exc.value.detail['code']=='EMPLOYEE_SMS_NOT_CERTIFIED'


def test_existing_four_digit_tawked_contract_is_explicit_blocker(monkeypatch):
    monkeypatch.setattr(auth,'_channel',lambda *a:{'state':'active','enabled':True,'delivery_proven_at':datetime.now(timezone.utc)})
    monkeypatch.setattr(auth,'_sms_provider',lambda *a:'tawked')
    with pytest.raises(HTTPException) as exc:employee_sms.provider_ready(None,'kps')
    assert exc.value.detail['code']=='EMPLOYEE_OTP_SIX_DIGIT_PROVIDER_REQUIRED'


def sms_setup(monkeypatch,phone='+966507778899',active=True):
    from app import db
    from tests.test_core import _make_user
    monkeypatch.setenv('KPS_PORTAL_HOST_ROUTING_ENABLED','false')
    monkeypatch.setenv('EMPLOYEE_SMS_LOGIN_ENABLED','true')
    monkeypatch.setenv('TWILIO_ACCOUNT_SID','AC'+'a'*32)
    monkeypatch.setenv('TWILIO_AUTH_TOKEN','synthetic-never-sent')
    monkeypatch.setenv('TWILIO_VERIFY_SERVICE_SID','VA'+'a'*32)
    monkeypatch.setenv('EMPLOYEE_TWILIO_VERIFY_SERVICE_SID','VA'+'b'*32)
    monkeypatch.setattr(employee_sms,'verify_service_length',lambda:None)
    user=_make_user(role='driver')
    with db.tx() as conn:
        conn.execute("UPDATE users SET phone=%s,active=%s,invitation_status='active' WHERE id=%s",(phone,active,user['id']))
        conn.execute("UPDATE identity_channels SET sms_provider='twilio_verify',state='active',enabled=true,delivery_proven_at=now() WHERE brand_id='kps' AND channel='sms'")
    calls=[]
    def provider(path,data,**kw):
        calls.append((path,data,kw))
        if path=='Verifications':return {'sid':'VEfixture','status':'pending','to':phone,'channel':'sms'}
        return {'status':'approved','to':phone,'channel':'sms'}
    monkeypatch.setattr(auth,'_provider_post',provider)
    return user,calls


@pytest.mark.postgres
def test_sms_canonical_employee_login_replay_and_no_activation(client,monkeypatch):
    from app import db
    user,calls=sms_setup(monkeypatch)
    result=client.post('/api/identity/staff/code/request',json={'phone':'٠٥٠٧٧٧٨٨٩٩'})
    assert result.status_code==200,result.text
    assert result.json()['channel']=='sms' and result.json()['code_length']==6
    assert 290<=result.json()['expires_in']<=300
    cid=result.json()['challenge_id']
    before=None
    with db.tx() as conn:before=conn.execute('SELECT active,role,branch_ids,phone_verified_at FROM users WHERE id=%s',(user['id'],)).fetchone()
    success=client.post('/api/identity/staff/code/verify',json={'challenge_id':cid,'code':'012345'})
    assert success.status_code==200,success.text
    assert success.json()['user']['id']==user['id'] and success.json()['user']['role']=='driver'
    assert calls[0][1]['To']=='+966507778899' and calls[0][2]['service_sid']=='VA'+'b'*32
    assert client.post('/api/identity/staff/code/verify',json={'challenge_id':cid,'code':'012345'}).status_code==400
    with db.tx() as conn:
        assert conn.execute('SELECT active,role,branch_ids,phone_verified_at FROM users WHERE id=%s',(user['id'],)).fetchone()==before
        assert conn.execute('SELECT count(*) n FROM sessions WHERE user_id=%s',(user['id'],)).fetchone()['n']==1
        assert conn.execute("SELECT count(*) n FROM users WHERE role='customer'").fetchone()['n']==0


@pytest.mark.postgres
@pytest.mark.parametrize('change',["active=false","invitation_status='revoked'","updated_at=now()"])
def test_sms_employee_revocation_or_change_during_challenge(client,monkeypatch,change):
    from app import db
    user,calls=sms_setup(monkeypatch)
    sent=client.post('/api/identity/staff/code/request',json={'phone':'0507778899'}).json()
    with db.tx() as conn:conn.execute('UPDATE users SET '+change+' WHERE id=%s',(user['id'],))
    verified=client.post('/api/identity/staff/code/verify',json={'challenge_id':sent['challenge_id'],'code':'012345'})
    assert verified.status_code==403,verified.text
    assert len(calls)==1
    with db.tx() as conn:assert conn.execute('SELECT count(*) n FROM sessions').fetchone()['n']==0


@pytest.mark.postgres
def test_sms_unknown_inactive_never_sends_and_cannot_create_account(client,monkeypatch):
    from app import db
    user,calls=sms_setup(monkeypatch,active=False)
    for phone in ('0507778899','0500000000'):
        sent=client.post('/api/identity/staff/code/request',json={'phone':phone})
        assert sent.status_code==200,sent.text
        assert sent.json()['accepted'] is True
        assert client.post('/api/identity/staff/code/verify',json={'challenge_id':sent.json()['challenge_id'],'code':'012345'}).status_code==400
    assert calls==[]
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM otp_challenges').fetchone()['n']==0
        assert conn.execute('SELECT count(*) n FROM sessions').fetchone()['n']==0


@pytest.mark.postgres
def test_sms_expiry_and_bad_code_attempt_limits(client,monkeypatch):
    from app import db
    user,calls=sms_setup(monkeypatch)
    sent=client.post('/api/identity/staff/code/request',json={'phone':'0507778899'}).json()
    monkeypatch.setattr(auth,'_provider_post',lambda *a,**kw:{'status':'pending','to':'+966507778899','channel':'sms'})
    for _ in range(4):
        assert client.post('/api/identity/staff/code/verify',json={'challenge_id':sent['challenge_id'],'code':'012345'}).status_code==400
    with db.tx() as conn:
        assert conn.execute('SELECT attempts FROM otp_challenges WHERE id=%s',(sent['challenge_id'],)).fetchone()['attempts']==3
        conn.execute("UPDATE otp_challenges SET expires_at=now()-interval '1 second',attempts=0 WHERE id=%s",(sent['challenge_id'],))
    assert client.post('/api/identity/staff/code/verify',json={'challenge_id':sent['challenge_id'],'code':'012345'}).status_code==400


@pytest.mark.postgres
def test_employee_challenge_cannot_be_consumed_by_customer_verifier(client,monkeypatch):
    from app import db
    user,calls=sms_setup(monkeypatch)
    sent=client.post('/api/identity/staff/code/request',json={'phone':'0507778899'}).json()
    # Even a permissive mocked provider must never see this cross-protocol code.
    result=client.post('/api/auth/otp/verify',json={'challenge_id':sent['challenge_id'],'code':'0123'})
    assert result.status_code==400,result.text
    assert result.json()['detail']['code']=='OTP_PURPOSE_MISMATCH'
    assert len(calls)==1
    with db.tx() as conn:
        challenge=conn.execute('SELECT attempts,verified_at FROM otp_challenges WHERE id=%s',(sent['challenge_id'],)).fetchone()
        assert challenge['attempts']==0 and challenge['verified_at'] is None
        assert conn.execute('SELECT count(*) n FROM sessions').fetchone()['n']==0
        assert conn.execute('SELECT count(*) n FROM customer_identity_links').fetchone()['n']==0
    # The challenge still works only in its intended employee verifier.
    assert client.post('/api/identity/staff/code/verify',json={'challenge_id':sent['challenge_id'],'code':'012345'}).status_code==200


@pytest.mark.postgres
def test_customer_challenge_cannot_be_consumed_by_employee_verifier(client,monkeypatch):
    from app import db
    user,calls=sms_setup(monkeypatch)
    with db.tx() as conn:
        conn.execute("INSERT INTO otp_challenges(id,brand_id,phone,provider_reference,delivery_channel,expires_at,provider_name) VALUES('otp_customer_fixture','kps','+966507778899','VEcustomer','sms',now()+interval '5 minutes','twilio_verify')")
    result=client.post('/api/identity/staff/code/verify',json={'challenge_id':'otp_customer_fixture','code':'012345'})
    assert result.status_code==400,result.text
    assert calls==[]
    with db.tx() as conn:
        challenge=conn.execute("SELECT attempts,verified_at FROM otp_challenges WHERE id='otp_customer_fixture'").fetchone()
        assert challenge['attempts']==0 and challenge['verified_at'] is None
        assert conn.execute('SELECT count(*) n FROM sessions').fetchone()['n']==0


def test_customer_verifier_rejects_employee_namespace_before_database(monkeypatch):
    from fastapi import Response
    monkeypatch.setattr(auth,'check_origin',lambda *a:None)
    monkeypatch.setattr(auth.db,'tx',lambda *a,**kw:pytest.fail('cross-purpose challenge must never reach storage'))
    with pytest.raises(HTTPException) as exc:
        auth.verify_otp(auth.OTPVerifyBody(challenge_id='employee_otp_fixture',code='0123'),request('order.kps.sa'),Response())
    assert exc.value.detail['code']=='OTP_PURPOSE_MISMATCH'
