"""Restricted diagnostics with synthetic providers; no DB or network connection."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import httpx
import pytest
from fastapi import HTTPException, Request
from app import auth, db, employee_sms, sms_verification


@pytest.mark.parametrize('kind,expected', [
    ('reject','provider_rejected'), ('transport','provider_transport_error'),
    ('json','provider_invalid_response'), ('shape','provider_invalid_response')])
def test_adapter_safe_reason_never_in_public_detail(monkeypatch,kind,expected):
    monkeypatch.setenv('TAWKED_API_KEY','tk_test_synthetic_secret')
    def handle(request):
        if kind == 'transport':
            raise httpx.ConnectError('SENSITIVE_BODY_OR_CREDENTIAL',request=request)
        if kind == 'reject':
            return httpx.Response(403,json={'secret':'SENSITIVE_BODY_OR_CREDENTIAL'})
        if kind == 'json':
            return httpx.Response(200,text='SENSITIVE_BODY_OR_CREDENTIAL')
        return httpx.Response(200,json=['SENSITIVE_BODY_OR_CREDENTIAL'])
    original=httpx.Client
    monkeypatch.setattr(sms_verification.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    with pytest.raises(HTTPException) as result:
        sms_verification._call('start',{})
    assert result.value.sms_failure_reason == expected
    assert result.value.detail['code'] == 'OTP_PROVIDER_FAILURE'
    assert 'SENSITIVE' not in str(result.value.detail)
    assert expected not in str(result.value.detail)


@pytest.mark.parametrize('mode,expected', [
    ('unknown','account_ineligible_or_ambiguous'),('failure','provider_transport_error'),
    ('rollout','rollout_denied'),('portal','portal_denied'),('budget','account_send_rate_limited')])
def test_internal_reason_with_same_generic_public_response(monkeypatch,mode,expected):
    audits=[]
    @contextmanager
    def transaction():
        yield object()
    monkeypatch.setattr(db,'tx',transaction)
    monkeypatch.setattr(db,'audit',lambda conn,actor,action,kind,cid,details:audits.append((actor,action,kind,cid,details)))
    monkeypatch.setattr(db,'rate_limit',lambda conn,key,*a:not(mode=='budget' and 'send-account' in key))
    monkeypatch.setattr(auth,'check_origin',lambda request:None)
    monkeypatch.setattr(employee_sms,'provider_ready',lambda *a:'tawked')
    user={'id':'synthetic-user','role':'owner'}
    monkeypatch.setattr(employee_sms,'_eligible',lambda *a:None if mode=='unknown' else user)
    monkeypatch.setattr(employee_sms.sms_rollout,'employee_sms_enabled',lambda u,*,conn:mode!='rollout')
    monkeypatch.setattr(employee_sms.portals,'enabled',lambda:mode=='portal')
    calls=[]
    def fail(*args):
        calls.append(True)
        raise sms_verification._failure('provider_transport_error')
    monkeypatch.setattr(sms_verification,'start',fail)
    request=Request({'type':'http','method':'POST','scheme':'https','path':'/api/identity/staff/code/request',
        'headers':[(b'host',b'order.kps.sa')],'query_string':b'', 'client':('127.0.0.1',123)})
    result=employee_sms.request_sms(employee_sms.EmployeeSMSRequest(phone='0500000000'),request)
    assert result['accepted'] is True and result['code_length']==4
    assert set(result)=={'accepted','challenge_id','expires_in','channel','code_length'}
    assert 290 <= result['expires_in'] <= 300
    assert any(a[4]['outcome']==expected and a[3]==result['challenge_id'] for a in audits)
    assert calls == ([True] if mode=='failure' else [])
    assert all('0500000000' not in str(a) and '+966500000000' not in str(a) for a in audits)
    assert expected not in str(result)


def test_reference_only_when_safe_and_unknown_reason_allowlisted(monkeypatch):
    audits=[]
    monkeypatch.setattr(db,'audit',lambda *args:audits.append(args[-1]))
    employee_sms._diagnostic('employee_otp_fixture','provider_accepted',conn=object(),provider='tawked',reference='ref-safe-1')
    employee_sms._diagnostic('employee_otp_fixture','secret_unexpected',conn=object(),reference='sensitive body / +966500000000')
    assert audits==[{'outcome':'provider_accepted','provider':'tawked','provider_reference':'ref-safe-1'},
                    {'outcome':'provider_failure_unclassified'}]
