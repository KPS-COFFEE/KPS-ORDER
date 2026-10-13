"""Current owner-approved mandatory-SMS policy, independent of legacy guest fixtures."""
from datetime import datetime,timedelta,timezone
import secrets
import pytest
from fastapi import HTTPException
from app import auth,db,sms_verification,chat
from tests.test_core import _authenticate,_make_user,_prepare_catalog,_body,_create
from tests.test_tawked_otp import setup_provider

@pytest.fixture
def required(client):
    with db.tx() as c:
        c.execute("UPDATE app_settings SET settings=settings||'{\"customer_phone_required\":true}'::jsonb WHERE brand_id='kps'")
    return client

def verify(client,monkeypatch,phone='0500000000',name='TEST customer'):
    setup_provider(monkeypatch)
    r=client.post('/api/auth/otp/request',json={'phone':phone})
    assert r.status_code==200,r.text
    r=client.post('/api/auth/otp/verify',json={'challenge_id':r.json()['challenge_id'],'code':'0123','display_name':name})
    assert r.status_code==200,r.text
    client.headers['X-CSRF-Token']=r.json()['csrf_token']
    return r.json()

def contact(client,category='complaint',details='TEST complaint',key='contact-test-key'):
    return client.post('/api/conversations',json={'category':category,'details':details,'branch_id':'kps-branch-1','service':'pickup'},headers={'Idempotency-Key':key})

@pytest.mark.postgres
def test_first_order_rejects_guest_and_email_sessions_and_typed_phone(required,monkeypatch):
    _prepare_catalog()
    r=required.post('/api/identity/guest',json={'display_name':'TEST','phone':'0500000000'})
    required.headers['X-CSRF-Token']=r.json()['csrf_token']
    r=_create(required)
    assert r.status_code==403 and r.json()['detail']['code']=='CUSTOMER_PHONE_VERIFICATION_REQUIRED'
    user=_make_user()
    with db.tx() as c:c.execute("UPDATE users SET email_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s",(user['id'],))
    _authenticate(required,user)
    assert _create(required).status_code==403  # proof on user alone is insufficient
    result=verify(required,monkeypatch)
    assert result['customer_verified']
    r=_create(required,key='verified-first-order')
    assert r.status_code==200,r.text
    assert _create(required,key='verified-first-order').json()==r.json()
    with db.tx() as c:assert c.execute('SELECT count(*) n FROM orders').fetchone()['n']==1
    body=_body();body['customer']['phone']='0509999999'
    assert _create(required,body,key='different-phone-order').status_code==403

@pytest.mark.postgres
def test_session_30_days_fixed_and_reverification_restores_customer(required,monkeypatch):
    _prepare_catalog();result=verify(required,monkeypatch);uid=result['user']['id']
    assert 'Max-Age=2592000' in str(required.cookies) or required.get('/api/session').json()['customer_verified']
    first=_create(required,key='history-order');assert first.status_code==200,first.text
    token=required.cookies.get(auth.COOKIE)
    with db.tx() as c:
        before=c.execute('SELECT expires_at,customer_verified_at FROM sessions WHERE token_hash=%s',(auth.token_hash(token),)).fetchone()
        c.execute("UPDATE sessions SET customer_verified_at=now()-interval '7 days',expires_at=now()+interval '23 days' WHERE token_hash=%s",(auth.token_hash(token),))
        expected=c.execute('SELECT expires_at FROM sessions WHERE token_hash=%s',(auth.token_hash(token),)).fetchone()['expires_at']
    for _ in range(3):assert required.get('/api/session').json()['customer_verified']
    with db.tx() as c:
        assert c.execute('SELECT expires_at FROM sessions WHERE token_hash=%s',(auth.token_hash(token),)).fetchone()['expires_at']==expected
        c.execute("UPDATE sessions SET customer_verified_at=now()-interval '30 days 1 second',expires_at=now()+interval '1 day' WHERE token_hash=%s",(auth.token_hash(token),))
    assert required.get('/api/orders').status_code==401
    assert required.get('/api/session').json()['user'] is None
    again=verify(required,monkeypatch)
    assert again['user']['id']==uid
    assert required.get('/api/orders').json()['orders'][0]['id']==first.json()['order']['id']

@pytest.mark.postgres
def test_forward_migration_preserves_existing_sms_login_and_fixed_deadline(required,monkeypatch):
    _prepare_catalog();verify(required,monkeypatch)
    token=required.cookies.get(auth.COOKIE)
    with db.tx() as c:
        c.execute("UPDATE sessions SET customer_verified_at=NULL,created_at=now()-interval '4 hours',expires_at=now()+interval '8 hours' WHERE token_hash=%s",(auth.token_hash(token),))
        c.execute('UPDATE otp_challenges o SET verified_at=s.created_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=%s AND o.phone=u.phone AND o.brand_id=u.brand_id',(auth.token_hash(token),))
        before=c.execute('SELECT * FROM sessions WHERE token_hash=%s',(auth.token_hash(token),)).fetchone()
        c.execute((db.ROOT/'migrations/046_customer_experience.sql').read_text())
        migrated=c.execute('SELECT * FROM sessions WHERE token_hash=%s',(auth.token_hash(token),)).fetchone()
        proof=migrated.pop('customer_verified_at');before.pop('customer_verified_at')
        assert migrated==before and proof==before['created_at']
    def unavailable(*args):raise AssertionError('Existing proof must not call the SMS provider')
    monkeypatch.setattr(sms_verification,'start',unavailable)
    r=required.get('/api/session');assert r.json()['customer_verified']
    assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
    assert 'Max-Age=2592000' not in r.headers['set-cookie']
    deadline=proof+timedelta(days=30)
    for _ in range(3):assert required.get('/api/session').json()['customer_verified']
    with db.tx() as c:
        assert c.execute('SELECT expires_at FROM sessions WHERE token_hash=%s',(auth.token_hash(token),)).fetchone()['expires_at']==deadline
    assert _create(required,key='existing-sms-migration-outage').status_code==200

@pytest.mark.postgres
@pytest.mark.parametrize('invalid_proof',['unverified','different_phone','email_session','expired_proof','revoked_session'])
def test_migration_never_promotes_a_session_without_its_live_sms_proof(required,monkeypatch,invalid_proof):
    verify(required,monkeypatch);token=required.cookies.get(auth.COOKIE)
    with db.tx() as c:
        c.execute('UPDATE sessions SET customer_verified_at=NULL WHERE token_hash=%s',(auth.token_hash(token),))
        if invalid_proof=='unverified':c.execute('UPDATE otp_challenges SET verified_at=NULL')
        elif invalid_proof=='different_phone':c.execute("UPDATE otp_challenges SET phone='+966509999999'")
        elif invalid_proof=='email_session':c.execute("UPDATE sessions SET created_at=created_at+interval '1 second' WHERE token_hash=%s",(auth.token_hash(token),))
        elif invalid_proof=='expired_proof':
            c.execute("UPDATE sessions SET created_at=now()-interval '31 days' WHERE token_hash=%s",(auth.token_hash(token),))
            c.execute('UPDATE otp_challenges o SET verified_at=s.created_at FROM sessions s WHERE s.token_hash=%s',(auth.token_hash(token),))
        else:c.execute('UPDATE sessions SET revoked_at=now() WHERE token_hash=%s',(auth.token_hash(token),))
        c.execute((db.ROOT/'migrations/046_customer_experience.sql').read_text())
        assert c.execute('SELECT customer_verified_at FROM sessions WHERE token_hash=%s',(auth.token_hash(token),)).fetchone()['customer_verified_at'] is None
    r=required.get('/api/session')
    assert not r.json().get('customer_verified')
    assert _create(required,key='invalid-migration-order').status_code in {401,403}

@pytest.mark.postgres
def test_outage_blocks_new_customer_but_not_valid_returning_customer(required,monkeypatch):
    _prepare_catalog();verify(required,monkeypatch)
    def unavailable(*args):raise db.error(503,'OTP_PROVIDER_FAILURE','حاول لاحقًا')
    monkeypatch.setattr(sms_verification,'start',unavailable)
    assert _create(required,key='outage-existing').status_code==200
    required.cookies.clear();required.headers.pop('X-CSRF-Token',None)
    assert required.post('/api/auth/otp/request',json={'phone':'0509999999'}).status_code==503
    assert _create(required,key='outage-new').status_code==401
    guest=required.post('/api/identity/guest',json={'display_name':'TEST','phone':'0509999999'}).json()
    required.headers['X-CSRF-Token']=guest['csrf_token']
    assert _create(required,key='outage-guest').status_code==403

@pytest.mark.postgres
def test_customer_profile_address_ownership_and_phone_change(required,monkeypatch):
    _prepare_catalog();one=verify(required,monkeypatch)
    aid='TEST-owned-address'
    with db.tx() as c:c.execute("INSERT INTO customer_addresses(id,customer_id,brand_id,label,address) VALUES(%s,%s,'kps','TEST','TEST address')",(aid,one['user']['id']))
    assert required.get('/api/customer/profile').json()['addresses'][0]['id']==aid
    assert required.patch('/api/customer/profile',json={'display_name':'TEST renamed','phone':'0509999999'}).status_code==422
    assert required.patch('/api/customer/profile',json={'display_name':'TEST renamed'}).status_code==200
    r=required.post('/api/auth/otp/request',json={'phone':'0501111111','purpose':'change_phone'})
    assert r.status_code==200,r.text
    old_token=required.cookies.get(auth.COOKIE)
    r=required.post('/api/auth/otp/verify',json={'challenge_id':r.json()['challenge_id'],'code':'0123','display_name':'TEST renamed'})
    assert r.status_code==200,r.text
    assert r.json()['user']['id']==one['user']['id']
    required.headers['X-CSRF-Token']=r.json()['csrf_token']
    assert required.get('/api/customer/profile').json()['addresses'][0]['id']==aid
    with db.tx() as c:assert c.execute('SELECT revoked_at FROM sessions WHERE token_hash=%s',(auth.token_hash(old_token),)).fetchone()['revoked_at']
    two=verify(required,monkeypatch,phone='0502222222')
    assert two['user']['id']!=one['user']['id']
    assert required.get('/api/customer/profile').json()['addresses']==[]
    assert required.delete('/api/customer/addresses/'+aid).status_code==404

@pytest.mark.postgres
@pytest.mark.parametrize(('category','service','expected'), [('help','pickup','branch'),('suggestion','car','branch'),('complaint','delivery','delivery'),('change','pickup','branch'),('refund','pickup','review'),('delivery_problem','pickup','delivery'),('privacy','delivery','privacy'),('other','pickup','branch')])
def test_contact_categories_route_and_repeat_safety(required,monkeypatch,category,service,expected):
    user=verify(required,monkeypatch)['user'];payload={'category':category,'service':service,'branch_id':'kps-branch-1','details':'TEST request'}
    r=required.post('/api/conversations',json=payload,headers={'Idempotency-Key':'test-category-contact'});assert r.status_code==200,r.text
    row=r.json()['conversation'];assert row['reference'].startswith('CS-')
    assert 'route' not in row and 'assignee_id' not in row
    assert required.post('/api/conversations',json=payload,headers={'Idempotency-Key':'test-category-contact'}).json()==r.json()
    with db.tx() as c:
        saved=c.execute('SELECT * FROM conversations WHERE id=%s',(row['id'],)).fetchone();assert saved['route']==expected
        assert c.execute('SELECT count(*) n FROM chat_messages WHERE conversation_id=%s',(row['id'],)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM refunds').fetchone()['n']==0
    _authenticate(required,_make_user(role='branch_manager',branches=['kps-branch-1']))
    assert required.get('/api/conversations/'+row['id']+'/messages').status_code==(200 if expected=='branch' else 404)
    _authenticate(required,_make_user(role='delivery_manager',branches=['kps-branch-1']))
    assert required.get('/api/conversations/'+row['id']+'/messages').status_code==(200 if expected=='delivery' else 404)
    _authenticate(required,_make_user(role='owner'))
    assert required.get('/api/conversations/'+row['id']+'/messages').status_code==200

@pytest.mark.postgres
def test_delivery_problem_transfers_full_context_and_other_customer_cannot_read(required,monkeypatch):
    one=verify(required,monkeypatch)['user']
    r=contact(required);assert r.status_code==200,r.text;cid=r.json()['conversation']['id']
    body={'text':'عندي شكوى توصيل وتأخير السائق'}
    r=required.post('/api/conversations/'+cid+'/messages',json=body,headers={'Idempotency-Key':'test-delivery-message'});assert r.status_code==200,r.text
    assert required.post('/api/conversations/'+cid+'/messages',json=body,headers={'Idempotency-Key':'test-delivery-message'}).json()==r.json()
    with db.tx() as c:
        assert c.execute('SELECT route FROM conversations WHERE id=%s',(cid,)).fetchone()['route']=='delivery'
        assert c.execute('SELECT count(*) n FROM conversation_transfers WHERE conversation_id=%s',(cid,)).fetchone()['n']==1
    r=required.get('/api/conversations/'+cid+'/messages');assert r.json()['transfers']==[]
    verify(required,monkeypatch,phone='0503333333')
    assert required.get('/api/conversations/'+cid+'/messages').status_code==404
    assert required.get('/api/conversations').json()['conversations']==[]

@pytest.mark.postgres
def test_legal_public_before_identity_and_missing_facts_are_visible(required):
    for lang in ('ar','en'):
        for path in ('privacy','terms'):
            r=required.get('/'+path+'?lang='+lang)
            assert r.status_code==200 and 'Render' in r.text if path=='privacy' else r.status_code==200
            assert 'checkbox' not in r.text
    assert not required.get('/api/customer/legal').json()['complete']
    assert required.put('/api/customer/legal',json={'controller_name':'TEST'}).status_code==401

@pytest.mark.postgres
def test_guest_contact_links_only_the_current_owned_session(required,monkeypatch):
    guest=required.post('/api/identity/guest',json={'display_name':'TEST contact','phone':'0500000000'}).json()
    required.headers['X-CSRF-Token']=guest['csrf_token']
    r=contact(required);assert r.status_code==200,r.text;cid=r.json()['conversation']['id']
    own=verify(required,monkeypatch)
    assert own['user']['id']!=guest['user']['id']
    assert required.get('/api/conversations/'+cid+'/messages').status_code==200
    with db.tx() as c:
        assert c.execute('SELECT customer_id FROM conversations WHERE id=%s',(cid,)).fetchone()['customer_id']==own['user']['id']
        assert not c.execute('SELECT 1 FROM sessions WHERE user_id=%s AND revoked_at IS NULL',(guest['user']['id'],)).fetchone()

@pytest.mark.postgres
def test_owned_guest_order_links_without_changing_immutable_snapshot(required,monkeypatch):
    _prepare_catalog()
    with db.tx() as c:c.execute("UPDATE app_settings SET settings=settings||'{\"customer_phone_required\":false}'::jsonb WHERE brand_id='kps'")
    guest=required.post('/api/identity/guest',json={'display_name':'TEST legacy','phone':'0500000000'}).json()
    required.headers['X-CSRF-Token']=guest['csrf_token']
    r=_create(required,key='test-owned-legacy');assert r.status_code==200,r.text
    oid=r.json()['order']['id']
    with db.tx() as c:
        before=c.execute('SELECT customer_id,snapshot,customer FROM orders WHERE id=%s',(oid,)).fetchone()
        c.execute("UPDATE app_settings SET settings=settings||'{\"customer_phone_required\":true}'::jsonb WHERE brand_id='kps'")
    assert _create(required,key='test-owned-legacy').status_code==403  # even a saved replay needs fresh proof
    verify(required,monkeypatch)
    assert required.get('/api/orders/'+oid).status_code==200
    assert required.get('/api/orders').json()['orders'][0]['id']==oid
    with db.tx() as c:assert c.execute('SELECT customer_id,snapshot,customer FROM orders WHERE id=%s',(oid,)).fetchone()==before

@pytest.mark.postgres
def test_full_restore_preserves_verified_addresses_and_owned_identity_links(required,monkeypatch,database,tmp_path):
    import os,uuid,psycopg
    from psycopg import sql
    from app.exports import build_archive,restore_archive,verify_archive
    verify(required,monkeypatch)
    guest=_make_user()
    owner=required.get('/api/session').json()['user']['id']
    with db.tx() as c:
        c.execute("INSERT INTO customer_addresses(id,customer_id,brand_id,label,address) VALUES('TEST-restore-address',%s,'kps','TEST','TEST restored address')",(owner,))
        c.execute("INSERT INTO customer_identity_links(source_id,customer_id,brand_id) VALUES(%s,%s,'kps')",(guest['id'],owner))
        data,manifest=build_archive(c,full=True)
    assert manifest['counts']['customer_addresses']==1 and manifest['counts']['customer_identity_links']==1
    schema='kps_restore_'+uuid.uuid4().hex
    target=os.environ['TEST_DATABASE_URL']
    try:
        result=restore_archive(data,target_url=target,target_schema=schema,asset_directory=tmp_path/'restored-assets')
        assert result['verified']
        with psycopg.connect(target) as c:
            assert c.execute(sql.SQL('SELECT address FROM {}.customer_addresses').format(sql.Identifier(schema))).fetchone()[0]=='TEST restored address'
            assert c.execute(sql.SQL('SELECT source_id,customer_id FROM {}.customer_identity_links').format(sql.Identifier(schema))).fetchone()==(guest['id'],owner)
    finally:
        with psycopg.connect(target,autocommit=True) as c:c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))

@pytest.mark.postgres
def test_api_cannot_disable_current_required_mobile_policy(required):
    _authenticate(required,_make_user(role='owner'))
    r=required.patch('/api/admin/settings',json={'customer_phone_required':False},headers={'Idempotency-Key':'TEST-no-disable-mobile-proof'})
    assert r.status_code==422,r.text
    assert required.get('/api/identity/channels').json()['customer_phone_required'] is True

@pytest.mark.postgres
def test_other_requires_details_and_each_new_contact_has_own_reference(required,monkeypatch):
    verify(required,monkeypatch)
    assert contact(required,category='other',details='').status_code==422
    first=contact(required,key='test-fresh-contact-1');second=contact(required,key='test-fresh-contact-2')
    assert first.status_code==second.status_code==200
    assert first.json()['conversation']['reference']!=second.json()['conversation']['reference']
