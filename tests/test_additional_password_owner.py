"""Explicit owner grant, unchanged existing accounts, and private password handling."""
import os
import pytest
from app import auth, db
from tests.test_core import _authenticate, _make_user
from test_browser import _context, _owner_login, _staff, chromium_browser, live_app, synthetic_mail, PASSWORD

PATH='/api/identity/owners/password'
NEW_PASSWORD='Test8!Aa'

def body(**overrides):
    return dict(display_name='عبدالله',username='KPS',password=NEW_PASSWORD,
                password_confirmation=NEW_PASSWORD,reason='Explicit full owner grant',**overrides)

def owner_login(client):
    user=_make_user('owner',username='original-owner',password=PASSWORD)
    _authenticate(client,user)
    return user

@pytest.mark.postgres
def test_full_owner_creation_stepup_login_and_no_existing_account_change(client):
    original=owner_login(client)
    assert client.post(PATH,json=body()).status_code==403
    assert client.post('/api/identity/step-up',json={'password':PASSWORD}).status_code==200
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
    assert result.status_code==200,result.text
    assert result.json()['user']['id']==user['id']

@pytest.mark.postgres
def test_nonowners_and_invalid_passwords_are_rejected(client):
    for role in ('driver','admin','delivery_manager','customer'):
        _authenticate(client,_make_user(role))
        assert client.post(PATH,json=body()).status_code==403
    owner_login(client);client.post('/api/identity/step-up',json={'password':PASSWORD})
    for changed in ({'password_confirmation':'DifferentPassword!'}, {'username':'bad name'}, {'display_name':' '}, {'reason':'   '}, {'password':'Test7!A','password_confirmation':'Test7!A'}):
        payload=body();payload.update(changed)
        response=client.post(PATH,json=payload)
        assert response.status_code==422,response.text
        assert NEW_PASSWORD not in response.text
    with db.tx() as conn:
        assert not conn.execute("SELECT 1 FROM users WHERE username='KPS'").fetchone()

@pytest.mark.postgres
def test_creation_scope_cannot_be_overridden(client):
    owner_login(client);client.post('/api/identity/step-up',json={'password':PASSWORD})
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
        form.locator('[name=new_password]').fill(NEW_PASSWORD)
        form.locator('[name=password_confirmation]').fill(NEW_PASSWORD)
        form.locator('[name=owner_password]').fill(PASSWORD)
        form.locator('[name=reason]').fill('Synthetic full owner approval')
        with db.tx() as conn:assert not conn.execute("SELECT 1 FROM users WHERE username='KPS'").fetchone()
        form.locator('[name=full_owner_confirmed]').check()
        form.locator('button[type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible()
        expect(page.get_by_role('cell',name='عبدالله',exact=False)).to_be_visible()
        with db.tx() as conn:
            row=conn.execute("SELECT role,active FROM users WHERE username='KPS'").fetchone()
            assert row['role']=='owner' and row['active']
        assert not errors
    finally:context.close()
