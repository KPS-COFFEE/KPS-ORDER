"""Mandatory owner-controlled bells; real PostgreSQL and authenticated owner confirmation."""
import pytest
from app import db
from tests.test_core import _make_user
from tests.sms_policy_helpers import sms_login, sms_step_up

POLICY={'first_after_seconds':5,'second_after_seconds':30,'continuous_after_seconds':60,
        'first_ring_seconds':2,'second_ring_seconds':3}
pytestmark=pytest.mark.postgres


def test_owner_policy_without_extra_sms_is_idempotent_audited_and_brand_scoped(client,monkeypatch):
    owner=_make_user('owner');sms_login(client,owner,monkeypatch)
    before=client.get('/api/operations/alert-policy').json()
    assert before['mandatory'] is True and before['policy']['continuous_after_seconds']==60
    path='/api/admin/alert-policy';headers={'Idempotency-Key':'synthetic-alert-policy'}
    first=client.patch(path,json=POLICY,headers=headers)
    assert first.status_code==200,first.text
    assert client.patch(path,json=POLICY,headers=headers).json()==first.json()
    assert client.get('/api/operations/alert-policy').json()=={'mandatory':True,'policy':POLICY}
    with db.tx() as c:
        rows=c.execute("SELECT * FROM audit_events WHERE action='alerts.policy.updated' AND actor_id=%s",(owner['id'],)).fetchall()
        assert len(rows)==1
        c.execute("INSERT INTO brands(id,name) VALUES('alert-other-brand','Synthetic other brand')")
        c.execute("INSERT INTO app_settings(brand_id,settings) VALUES('alert-other-brand','{}')")
    other=_make_user('owner',brand='alert-other-brand')
    monkeypatch.setenv('EMPLOYEE_SMS_LOGIN_ENABLED','true')
    sms_login(client,other,monkeypatch)
    assert client.get('/api/operations/alert-policy').json()['policy']['first_after_seconds']==0


@pytest.mark.parametrize('role',['admin','delivery_manager','delivery_staff','branch_manager','driver','customer'])
def test_nonowners_cannot_change_or_disable_bells(client,monkeypatch,role):
    user=_make_user(role)
    if role=='customer':
        from tests.test_core import _authenticate
        _authenticate(client,user)
    else:sms_login(client,user,monkeypatch)
    # Work endpoints do not reuse the separate authenticated customer cookie jar.
    assert client.patch('/api/admin/alert-policy',json=POLICY,headers={'Idempotency-Key':'nonowner-alert-policy'}).status_code==(401 if role=='customer' else 403)
    if role=='customer':assert client.get('/api/operations/alert-policy').status_code==401
    else:assert client.get('/api/operations/alert-policy').json()['mandatory'] is True


@pytest.mark.parametrize('patch',[{'mandatory':False},{'first_after_seconds':True},
    {'second_after_seconds':1},{'continuous_after_seconds':29},{'first_ring_seconds':0},
    {'second_ring_seconds':11},{'brand_id':'other'}])
def test_invalid_or_cross_scope_policy_fields_are_rejected(client,monkeypatch,patch):
    owner=_make_user('owner');sms_login(client,owner,monkeypatch);sms_step_up(client,owner,monkeypatch)
    result=client.patch('/api/admin/alert-policy',json=POLICY|patch,headers={'Idempotency-Key':'invalid-alert-policy'})
    assert result.status_code==422,result.text
    assert client.get('/api/operations/alert-policy').json()['policy']['first_after_seconds']==0
