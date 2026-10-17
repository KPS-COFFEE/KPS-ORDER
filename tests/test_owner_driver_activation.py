"""Owner activation never fabricates email proof or permits phone-only login."""
import pytest
from app import auth, db
from tests.test_core import _authenticate, _make_user
from tests.test_v5_identity import _mail_setup, _invite
from tests.test_final_identity import _request, _verify


@pytest.mark.postgres
def test_owner_activation_requires_step_up_preserves_scope_and_email_proof(client, monkeypatch):
    _mail_setup(monkeypatch)
    owner=_make_user('owner',username='activation-owner',password='TestActivation!Password1')
    _authenticate(client,owner)
    invited=_invite(client)
    assert invited.status_code==200,invited.text
    driver=invited.json()['user']; path='/api/identity/staff/'+driver['id']+'/owner-activate'
    body={'reason':'Verified employee approved by owner'}
    assert client.post(path,json=body).status_code==403
    assert client.post('/api/identity/step-up',json={'password':'TestActivation!Password1'}).status_code==200
    activated=client.post(path,json=body)
    assert activated.status_code==200,activated.text
    user=activated.json()['user']
    assert user['active'] and user['identity_kind']=='staff_owner_approved'
    assert user['email_verified_at'] is None and user['branch_ids']==driver['branch_ids']
    with db.tx() as conn:
        assert conn.execute("SELECT count(*) n FROM audit_events WHERE action='driver.owner_activated' AND entity_id=%s",(driver['id'],)).fetchone()['n']==1
    assert client.post(path,json=body).json()['already_active']
    client.cookies.clear();client.headers.pop('X-CSRF-Token',None)
    challenge=_request(client)
    assert _verify(client,challenge,'not-a-code').status_code==422
    result=_verify(client,challenge)
    assert result.status_code==200,result.text
    assert result.json()['user']['id']==driver['id']
    assert result.json()['user']['email_verified_at'] is not None
    assert result.json()['user']['identity_kind']=='staff_email_verified'


@pytest.mark.postgres
def test_nonowners_cannot_activate_and_owner_cannot_cross_brand_or_promote(client):
    driver=_make_user('driver'); path='/api/identity/staff/'+driver['id']+'/owner-activate'
    for role in ['driver','delivery_manager','admin','customer']:
        _authenticate(client,_make_user(role))
        assert client.post(path,json={'reason':'Denied request'}).status_code==403
    owner=_make_user('owner',username='activation-owner2',password='TestActivation!Password2')
    _authenticate(client,owner)
    assert client.post('/api/identity/step-up',json={'password':'TestActivation!Password2'}).status_code==200
    assert client.post('/api/identity/staff/'+owner['id']+'/owner-activate',json={'reason':'Not a driver'}).status_code==404
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('other-activation','Other')")
        conn.execute("UPDATE users SET brand_id='other-activation' WHERE id=%s",(driver['id'],))
    assert client.post(path,json={'reason':'Wrong brand'}).status_code==404
