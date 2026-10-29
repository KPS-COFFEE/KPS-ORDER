"""Owner mobile login approves only its own revocable work session."""
import pytest
from app import auth, db
from tests.test_core import _make_user, _authenticate
from tests.sms_policy_helpers import sms_login, register_synthetic_mobile

pytestmark = pytest.mark.postgres


def staff(role='driver'):
    return register_synthetic_mobile(_make_user(role, branches=['kps-branch-1']))


def swap_body(left,right,**changes):
    return {'other_user_id':right['id'],'expected_phone':left['phone'],
            'expected_other_phone':right['phone'],'reason':'Owner corrected employee mobile assignment',**changes}


def test_sms_login_approves_profile_and_pause_without_another_code(client,monkeypatch):
    owner=staff('owner');target=staff()
    signed=sms_login(client,owner,monkeypatch)
    assert signed['user']['owner_session_approved'] is True
    assert client.get('/api/session').json()['user']['owner_session_approved'] is True
    with db.tx() as c:
        # A normal shift-length session stays approved after ten minutes.
        c.execute("UPDATE sessions SET created_at=created_at-interval '20 minutes' WHERE user_id=%s",(owner['id'],))
        c.execute("UPDATE otp_challenges SET verified_at=verified_at-interval '20 minutes' WHERE source_user_id=%s AND verified_at IS NOT NULL",(owner['id'],))
    result=client.patch('/api/identity/staff/'+target['id'],json={'phone':'0509123456','reason':'Correct registered employee mobile'})
    assert result.status_code==200,result.text
    result=client.post('/api/identity/staff/'+target['id']+'/manage',json={'action':'pause','reason':'Owner paused employee access'})
    assert result.status_code==200,result.text
    assert result.json()['user']['active'] is False
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM otp_challenges WHERE id LIKE 'owner_sms_%%'").fetchone()['n']==0
        assert c.execute("SELECT 1 FROM audit_events WHERE actor_id=%s AND entity_id=%s AND action='staff.pause'",(owner['id'],target['id'])).fetchone()


def test_proof_cannot_approve_another_owner_session_or_survive_revocation(client,monkeypatch):
    owner=staff('owner');sms_login(client,owner,monkeypatch)
    with db.tx() as c:
        original=c.execute('SELECT id FROM sessions WHERE user_id=%s',(owner['id'],)).fetchone()['id']
    _authenticate(client,owner)
    assert client.get('/api/session').json()['user']['owner_session_approved'] is False
    target=staff()
    denied=client.post('/api/identity/staff/'+target['id']+'/manage',json={'action':'pause','reason':'Missing proof on this session'})
    assert denied.status_code==403,denied.text
    with db.tx() as c:
        assert auth.owner_session_approved(c,dict(owner,session_id=original))
        c.execute('UPDATE sessions SET revoked_at=now() WHERE id=%s',(original,))
        assert not auth.owner_session_approved(c,dict(owner,session_id=original))


@pytest.mark.parametrize('change', ['expired', 'inactive', 'phone_changed', 'role_changed', 'wrong_owner'])
def test_session_approval_is_rechecked_against_current_identity(client,monkeypatch,change):
    owner=staff('owner');sms_login(client,owner,monkeypatch)
    alternative=staff('owner') if change=='wrong_owner' else owner
    with db.tx() as c:
        sid=c.execute('SELECT id FROM sessions WHERE user_id=%s',(owner['id'],)).fetchone()['id']
        assert auth.owner_session_approved(c,dict(owner,session_id=sid))
        candidate=owner
        if change=='expired':
            c.execute("UPDATE sessions SET expires_at=now()-interval '1 second' WHERE id=%s",(sid,))
        elif change=='inactive':
            c.execute('UPDATE users SET active=false WHERE id=%s',(owner['id'],))
        elif change=='phone_changed':
            c.execute("UPDATE users SET phone='+966509876543' WHERE id=%s",(owner['id'],))
        elif change=='role_changed':
            c.execute("UPDATE users SET role='driver' WHERE id=%s",(owner['id'],))
        else:
            candidate=alternative
        assert not auth.owner_session_approved(c,dict(candidate,session_id=sid))


def test_pause_revokes_all_existing_credentials_and_prevents_reopening(client,monkeypatch):
    target=staff()
    first,_=_authenticate(client,target)
    second,_=_authenticate(client,target)
    owner=staff('owner');sms_login(client,owner,monkeypatch)
    result=client.post('/api/identity/staff/'+target['id']+'/manage',json={
        'action':'pause','reason':'Owner stopped access from every open browser'})
    assert result.status_code==200,result.text
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM sessions WHERE user_id=%s AND revoked_at IS NULL',(target['id'],)).fetchone()['n']==0
    for token in (first,second):
        client.cookies.clear()
        client.cookies.set(auth.COOKIE,token)
        client.headers['X-KPS-Context']='work'
        assert client.get('/api/session').json()['user'] is None
        assert client.get('/api/identity/staff').status_code==401
    sent=client.post('/api/identity/staff/code/request',json={
        'phone':target['phone'],'brand_id':target['brand_id']})
    assert sent.status_code==200,sent.text
    challenge=sent.json()['challenge_id']
    with db.tx() as c:
        assert c.execute('SELECT 1 FROM otp_challenges WHERE id=%s',(challenge,)).fetchone() is None
    assert client.post('/api/identity/staff/code/verify',json={
        'challenge_id':challenge,'code':'0123'}).status_code==400


@pytest.mark.parametrize('role',['driver','delivery_manager','admin','customer'])
def test_nonowners_cannot_get_approval_or_swap(client,monkeypatch,role):
    user=staff(role);_authenticate(client,user)
    assert client.get('/api/session').json()['user']['owner_session_approved'] is False
    left,right=staff(),staff()
    result=client.post('/api/identity/staff/'+left['id']+'/swap-phone',json=swap_body(left,right))
    assert result.status_code==(401 if role=='customer' else 403),result.text


@pytest.mark.parametrize('central',[False,True])
def test_phone_swap_is_atomic_preserves_records_and_revokes_old_credentials(request,monkeypatch,central):
    if central:
        monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED','true')
        monkeypatch.setenv('ENBISHAN_ISOLATED_ENABLED','true')
    client=request.getfixturevalue('client')
    left,right=staff(),staff()
    _authenticate(client,left);_authenticate(client,right)
    owner=staff('owner');sms_login(client,owner,monkeypatch)
    prefix='/api/central-operations/staff/' if central else '/api/identity/staff/'
    result=client.post(prefix+left['id']+'/swap-phone',json=swap_body(left,right))
    assert result.status_code==200,result.text
    with db.tx() as c:
        for old,new_phone in ((left,right['phone']),(right,left['phone'])):
            fresh=c.execute('SELECT * FROM users WHERE id=%s',(old['id'],)).fetchone()
            assert fresh['phone']==new_phone
            assert all(fresh[k]==old[k] for k in ('id','role','display_name','brand_id','branch_ids','active'))
            assert fresh['phone_sms_verified_at'] is None
            assert c.execute('SELECT count(*) n FROM sessions WHERE user_id=%s AND revoked_at IS NULL',(old['id'],)).fetchone()['n']==0
        assert c.execute("SELECT count(*) n FROM audit_events WHERE actor_id=%s AND action='staff.phone.swapped'",(owner['id'],)).fetchone()['n']==2


def test_swap_stale_input_foreign_brand_and_owner_are_rejected_without_partial_write(client,monkeypatch):
    owner=staff('owner');sms_login(client,owner,monkeypatch)
    left,right=staff(),staff()
    path='/api/identity/staff/'+left['id']+'/swap-phone'
    assert client.post(path,json=swap_body(left,right,expected_other_phone='0509999999')).status_code==409
    assert client.post(path,json=swap_body(left,owner)).status_code==409
    with db.tx() as c:
        c.execute("INSERT INTO brands(id,name) VALUES('foreign-employee','Foreign')")
        c.execute("UPDATE users SET brand_id='foreign-employee' WHERE id=%s",(right['id'],))
    assert client.post(path,json=swap_body(left,right)).status_code==404
    with db.tx() as c:
        assert c.execute('SELECT phone FROM users WHERE id=%s',(left['id'],)).fetchone()['phone']==left['phone']
        assert c.execute('SELECT phone FROM users WHERE id=%s',(right['id'],)).fetchone()['phone']==right['phone']


def test_swap_rolls_back_both_phones_if_audit_fails(client,monkeypatch):
    owner=staff('owner');sms_login(client,owner,monkeypatch)
    left,right=staff(),staff()
    original=db.audit
    def fail(c,actor,action,*args,**kwargs):
        if action=='staff.phone.swapped':raise RuntimeError('synthetic transaction failure')
        return original(c,actor,action,*args,**kwargs)
    monkeypatch.setattr(db,'audit',fail)
    # The real security middleware converts the exception to an HTTP 500.
    response=client.post('/api/identity/staff/'+left['id']+'/swap-phone',json=swap_body(left,right))
    assert response.status_code==500,response.text
    with db.tx() as c:
        for old in (left,right):
            assert c.execute('SELECT phone FROM users WHERE id=%s',(old['id'],)).fetchone()['phone']==old['phone']
        assert c.execute("SELECT count(*) n FROM audit_events WHERE actor_id=%s AND action='staff.phone.swapped'",(owner['id'],)).fetchone()['n']==0


from tests.test_enbishan_dual_brand import dual


@pytest.mark.parametrize('central',[False,True])
def test_foreign_owner_cannot_swap_phones_outside_granted_branches(dual,monkeypatch,central):
    client,actor,_,_,_=dual
    if central:
        monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED','true')
    left=register_synthetic_mobile(_make_user('driver',brand='enbishan',branches=['enbishan-test-1']))
    right=register_synthetic_mobile(_make_user('driver',brand='enbishan',branches=['enbishan-test-2']))
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids=%s WHERE user_id='dual-owner' AND brand_id='enbishan'",(db.json(['enbishan-test-1']),))
    prefix='/api/central-operations/staff/' if central else '/api/identity/staff/'
    denied=actor('owner','enbishan').post(prefix+left['id']+'/swap-phone',json=swap_body(left,right))
    assert denied.status_code==404,denied.text
    with db.tx() as c:
        for old in (left,right):
            assert c.execute('SELECT phone FROM users WHERE id=%s',(old['id'],)).fetchone()['phone']==old['phone']
