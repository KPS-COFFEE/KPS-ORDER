"""Explicit owner grant, unchanged existing accounts, and private password handling."""
import os
import pytest
from app import auth, db
from tests.sms_policy_helpers import sms_step_up, sms_login
from tests.test_core import _authenticate, _make_user
from test_browser import _context, _owner_login, _staff, chromium_browser, synthetic_mail, PASSWORD
from tests.browser_sms_helpers import sms_browser_app as live_app, complete_owner_sms_dialog

PATH='/api/identity/owners/password'
NEW_PASSWORD='Test8!Aa'

def test_eight_character_hashing_is_explicit_and_preserves_default_policy():
    assert auth.verify_password(NEW_PASSWORD,auth.hash_password(NEW_PASSWORD,min_length=8))
    with pytest.raises(Exception) as rejected:
        auth.hash_password(NEW_PASSWORD)
    assert rejected.value.status_code==422

def body(**overrides):
    values = dict(display_name='عبدالله',username='KPS',password=NEW_PASSWORD,
                  phone='0501234567', password_confirmation=NEW_PASSWORD,reason='Explicit full owner grant')
    values.update(overrides)
    return values

def owner_login(client):
    user=_make_user('owner',username='original-owner',password=PASSWORD)
    _authenticate(client,user)
    return user

@pytest.mark.postgres
def test_full_owner_creation_stepup_login_and_no_existing_account_change(client, monkeypatch):
    original=owner_login(client)
    assert client.post(PATH,json=body()).status_code==403
    assert client.post('/api/identity/step-up',json={'password':PASSWORD}).status_code==403
    sms_step_up(client, original, monkeypatch)
    response=client.post(PATH,json=body())
    assert response.status_code==200,response.text
    user=response.json()['user']
    assert user['role']=='owner' and user['active'] and user['brand_id']=='kps'
    assert user['email_verified_at'] is None and user['identity_kind']=='staff_password'
    assert NEW_PASSWORD not in response.text and 'password_hash' not in response.text
    with db.tx() as conn:
        assert conn.execute('SELECT password_hash FROM users WHERE id=%s',(original['id'],)).fetchone()['password_hash']==original['password_hash']
        row=conn.execute('SELECT * FROM users WHERE id=%s',(user['id'],)).fetchone()
        assert auth.verify_password(NEW_PASSWORD,row['password_hash'])
        event=conn.execute("SELECT details FROM audit_events WHERE action='owner.password_account.created' AND entity_id=%s",(user['id'],)).fetchone()
        assert NEW_PASSWORD not in str(event) and 'password_hash' not in str(event)
    duplicate=body();duplicate['username']='kps'
    assert client.post(PATH,json=duplicate).status_code==409
    client.cookies.clear();client.headers.pop('X-CSRF-Token',None)
    result=client.post('/api/auth/login',json={'username':'KPS','password':NEW_PASSWORD})
    assert result.status_code==403 and result.json()['detail']['code']=='WORK_SMS_REQUIRED'
    assert client.get('/api/session').json()['user'] is None
    assert sms_login(client, user, monkeypatch)['user']['id']==user['id']

@pytest.mark.postgres
def test_nonowners_and_invalid_passwords_are_rejected(client, monkeypatch):
    for role in ('driver','admin','delivery_manager','customer'):
        _authenticate(client,_make_user(role))
        # Customer credentials cannot authenticate a work route; work roles are forbidden.
        assert client.post(PATH,json=body()).status_code == (401 if role == 'customer' else 403)
    owner = owner_login(client)
    sms_step_up(client, owner, monkeypatch)
    for changed in ({'password_confirmation':'DifferentPassword!'}, {'username':'bad name'}, {'display_name':' '}, {'reason':'   '}, {'password':'Test7!A','password_confirmation':'Test7!A'}):
        payload=body();payload.update(changed)
        response=client.post(PATH,json=payload)
        assert response.status_code==422,response.text
        assert NEW_PASSWORD not in response.text
    with db.tx() as conn:
        assert not conn.execute("SELECT 1 FROM users WHERE username='KPS'").fetchone()

@pytest.mark.postgres
def test_creation_scope_cannot_be_overridden(client, monkeypatch):
    owner = owner_login(client)
    sms_step_up(client, owner, monkeypatch)
    payload=body();payload['brand_id']='another-brand'
    assert client.post(PATH,json=payload).status_code==422

@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires isolated Chromium')
def test_owner_password_form_creates_only_after_confirmation(live_app,chromium_browser):
    from playwright.sync_api import expect
    owner=_staff();context,page,errors=_context(chromium_browser,live_app)
    _owner_login(context,live_app,owner)
    try:
        page.goto(live_app+'/ops')
        page.locator('#navigation [data-page=admin]').click()
        page.locator('[data-action=admin-tab][data-tab=users]').click()
        page.locator('[data-action=add-password-owner]').click()
        form=page.locator('#modal form')
        form.locator('[name=display_name]').fill('عبدالله')
        form.locator('[name=username]').fill('KPS')
        form.locator('[name=phone]').fill('0501234567')
        expect(form.locator('input[type=password]')).to_have_count(0)
        expect(form.locator('[name=owner_password]')).to_have_count(0)
        form.locator('[name=reason]').fill('Synthetic full owner approval')
        with db.tx() as conn:assert not conn.execute("SELECT 1 FROM users WHERE username='KPS'").fetchone()
        form.locator('button[type=submit]').click()
        with db.tx() as conn:assert not conn.execute("SELECT 1 FROM users WHERE username='KPS'").fetchone()
        expect(page.locator('dialog:not(#modal)')).to_have_count(0)
        form.locator('[name=full_owner_confirmed]').check()
        form.locator('button[type=submit]').click()
        complete_owner_sms_dialog(page, owner)
        expect(page.locator('#modal')).not_to_be_visible()
        expect(page.locator('.staff-card').get_by_role('heading',name='عبدالله',exact=True)).to_be_visible()
        with db.tx() as conn:
            row=conn.execute("SELECT role,active,phone,phone_verified_at,phone_sms_verified_at,password_hash FROM users WHERE username='KPS'").fetchone()
            assert row['role']=='owner' and row['active'] and row['phone']=='+966501234567'
            assert row['phone_verified_at'] is None and row['phone_sms_verified_at'] is None
            assert row['password_hash'] is None
            assert conn.execute('SELECT role,active FROM users WHERE id=%s',(owner['id'],)).fetchone()=={'role':'owner','active':True}
        assert not errors
    finally:context.close()


@pytest.mark.postgres
def test_sms_owner_requires_explicit_phone_not_numeric_username(client,monkeypatch):
    owner = owner_login(client)
    sms_step_up(client, owner, monkeypatch)
    monkeypatch.setenv('EMPLOYEE_SMS_LOGIN_ENABLED','true')
    monkeypatch.setenv('EMPLOYEE_SMS_EMAIL_RETIRED','true')
    payload=body(phone=None);payload['username']='0501234567'
    response=client.post(PATH,json=payload)
    assert response.status_code==422
    assert response.json()['detail']['code']=='OWNER_PHONE_REQUIRED'
    with db.tx() as conn:
        assert not conn.execute("SELECT 1 FROM users WHERE username='0501234567'").fetchone()


@pytest.mark.postgres
def test_owner_phone_normalized_unique_and_not_marked_verified(client, monkeypatch):
    owner = owner_login(client)
    sms_step_up(client, owner, monkeypatch)
    response=client.post(PATH,json=body(phone='٠٥٠١٢٣٤٥٦٧'))
    assert response.status_code==200,response.text
    owner=response.json()['user']
    assert owner['phone']=='+966501234567'
    assert owner['phone_verified_at'] is None and owner['phone_sms_verified_at'] is None
    payload=body(phone='+966501234567');payload['username']='another-owner'
    assert client.post(PATH,json=payload).status_code==409
    payload['phone']='+12025550123'
    assert client.post(PATH,json=payload).status_code==422


@pytest.mark.postgres
def test_existing_password_owner_mobile_link_requires_stepup_without_email(client, monkeypatch):
    original=owner_login(client)
    sms_step_up(client, original, monkeypatch)
    target=client.post(PATH,json=body(phone='0501234568')).json()['user']
    _authenticate(client,original)  # A new session cannot inherit prior proof.
    path='/api/identity/staff/'+target['id']
    payload={'phone':'0501234567','active':True,'role':'owner','reason':'Approved owner phone link'}
    assert client.patch(path,json=payload).status_code==403
    sms_step_up(client, original, monkeypatch)
    response=client.patch(path,json=payload)
    assert response.status_code==200,response.text
    linked=response.json()['user']
    assert linked['id']==target['id'] and linked['role']=='owner' and linked['active']
    assert linked['phone']=='+966501234567' and linked['email_verified_at'] is None
    assert linked['phone_sms_verified_at'] is None


@pytest.mark.postgres
def test_new_owner_can_complete_sms_login_without_creating_another_account(client,monkeypatch):
    from datetime import datetime,timedelta,timezone
    from app import employee_sms
    owner = owner_login(client)
    sms_step_up(client, owner, monkeypatch)
    monkeypatch.setenv('EMPLOYEE_SMS_LOGIN_ENABLED','true')
    monkeypatch.setenv('EMPLOYEE_SMS_EMAIL_RETIRED','true')
    created=client.post(PATH,json=body(phone='0501234567'))
    assert created.status_code==200,created.text
    target=created.json()['user']
    calls=[]
    def start(phone,reference,lang):
        calls.append(phone)
        return 'synthetic-owner-reference',datetime.now(timezone.utc)+timedelta(minutes=5)
    monkeypatch.setattr(employee_sms.sms_verification,'start',start)
    monkeypatch.setattr(employee_sms.sms_verification,'check',lambda reference,code:code=='0123')
    client.cookies.clear();client.headers.pop('X-CSRF-Token',None)
    sent=client.post('/api/identity/staff/code/request',json={'phone':'0501234567'})
    assert sent.status_code==200,sent.text
    verified=client.post('/api/identity/staff/code/verify',json={'challenge_id':sent.json()['challenge_id'],'code':'0123'})
    assert verified.status_code==200,verified.text
    assert verified.json()['user']['id']==target['id']
    assert verified.json()['user']['role']=='owner'
    assert calls==['+966501234567']
    with db.tx() as conn:
        assert conn.execute("SELECT count(*) n FROM users WHERE username='KPS'").fetchone()['n']==1
