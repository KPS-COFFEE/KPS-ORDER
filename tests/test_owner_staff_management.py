"""Owner lifecycle: retain history, revoke access and re-invite with real proof."""
import pytest
from app import db
from tests.test_core import _authenticate, _make_user as _core_make_user
from tests.legacy_identity_helpers import make_legacy_user as _make_user
from tests.test_v5_identity import _legacy_mail_setup as _mail_setup, _legacy_invite as _invite

PASSWORD='TestStaffControls!2026'

def owner_client(client):
    owner=_make_user('owner',username='staff-control-owner',password=PASSWORD)
    _authenticate(client,owner)
    assert client.post('/api/identity/step-up',json={'password':PASSWORD}).status_code==200
    return owner


@pytest.mark.postgres
def test_archive_restore_and_reinvite_preserve_identity_and_revoke_access(client,monkeypatch):
    _mail_setup(monkeypatch)
    owner=owner_client(client)
    invited=_invite(client).json()['user'];identifier=invited['id']
    path='/api/identity/staff/'+identifier
    # Existing sessions and invitation tokens must no longer authorize access.
    _authenticate(client,invited)
    _authenticate(client,owner)
    assert client.post(path+'/manage',json={'action':'archive','reason':'Duplicate employment ended'}).status_code==403
    client.post('/api/identity/step-up',json={'password':PASSWORD})
    for action,status in [('archive','archived'),('restore','paused'),('reinvite','pending_email')]:
        result=client.post(path+'/manage',json={'action':action,'reason':'Owner reviewed employee record'})
        assert result.status_code==200,result.text
        user=result.json()['user']
        assert not user['active'] and user['invitation_status']==status
        assert all(user[k]==invited[k] for k in ('id','phone','email','display_name','branch_ids','vehicle'))
        if action=='archive':
            assert client.patch(path,json={'display_name':'Unexpected change'}).status_code==409
            assert client.post(path+'/owner-activate',json={'reason':'Cannot bypass restore'}).status_code==409
            manager=_make_user('delivery_manager',branches=invited['branch_ids'])
            _authenticate(client,manager)
            # Account administration is owner-only in the approved delivery scope.
            directory=client.get('/api/identity/staff')
            assert directory.status_code==403
            assert 'users' not in directory.json()
            assert client.post(path+'/manage',json={'action':'restore','reason':'Forbidden restore'}).status_code==403
            _authenticate(client,owner);client.post('/api/identity/step-up',json={'password':PASSWORD})
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM users WHERE id=%s',(identifier,)).fetchone()['n']==1
        assert conn.execute('SELECT count(*) n FROM sessions WHERE user_id=%s AND revoked_at IS NULL',(identifier,)).fetchone()['n']==0
        assert conn.execute('SELECT count(*) n FROM identity_tokens WHERE user_id=%s AND revoked_at IS NULL',(identifier,)).fetchone()['n']==1
        assert conn.execute("SELECT count(*) n FROM audit_events WHERE entity_id=%s AND action IN ('staff.archive','staff.restore','staff.reinvite')",(identifier,)).fetchone()['n']==3


@pytest.mark.postgres
def test_owner_can_assign_role_permissions_and_remove_old_grants(client,monkeypatch):
    _mail_setup(monkeypatch);owner=owner_client(client)
    driver=_invite(client).json()['user'];path='/api/identity/staff/'+driver['id']
    result=client.patch(path,json={'role':'admin','permissions':['products_manage'],'reason':'Assign catalog responsibility'})
    assert result.status_code==200,result.text
    assert result.json()['user']['role']=='admin'
    assert result.json()['user']['permissions']==['products_manage']
    assert not result.json()['user']['active']  # promotion cannot fabricate email proof
    result=client.patch(path,json={'role':'driver','reason':'Return to delivery duty'})
    assert result.status_code==200,result.text
    assert result.json()['user']['permissions']==[]
    assert client.patch('/api/identity/staff/'+owner['id'],json={'role':'driver','reason':'Self downgrade'}).status_code==409
    assert client.post('/api/identity/staff/'+owner['id']+'/manage',json={'action':'archive','reason':'Self delete'}).status_code==409
    _authenticate(client,_make_user('delivery_manager',branches=driver['branch_ids']))
    assert client.patch(path,json={'role':'owner','reason':'Unauthorized promotion'}).status_code==403
    assert client.post(path+'/manage',json={'action':'pause','reason':'Owner action forbidden'}).status_code==403


@pytest.mark.postgres
def test_lifecycle_brand_and_last_owner_protection(client):
    owner_client(client)
    target=_make_user('driver')
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('staff-other','Other')")
        conn.execute("UPDATE users SET brand_id='staff-other' WHERE id=%s",(target['id'],))
    assert client.post('/api/identity/staff/'+target['id']+'/manage',json={'action':'archive','reason':'Cross brand request'}).status_code==404
    assert client.post('/api/identity/staff/'+target['id']+'/manage',json={'action':'archive','reason':'   '}).status_code in (404,409)


@pytest.mark.postgres
def test_pause_and_role_change_block_open_shift(ops):
    actor,post,order,shifts=ops
    shifts()
    with db.tx() as conn:
        row=conn.execute("SELECT u.id FROM users u JOIN shifts s ON s.user_id=u.id WHERE u.role='driver' AND s.status='open' LIMIT 1").fetchone()
    client=actor('owner')
    client.app.dependency_overrides.clear()
    owner=_core_make_user('owner',brand='ops-brand',username='ops-control-owner',password=PASSWORD)
    _authenticate(client,owner)
    assert client.post('/api/identity/step-up',json={'password':PASSWORD}).status_code==200
    for action in ('pause','archive','reinvite'):
        result=client.post('/api/identity/staff/'+row['id']+'/manage',json={'action':action,'reason':'Blocked while working'})
        assert result.status_code==409,result.text
        assert result.json()['detail']['code']=='STAFF_OPEN_SHIFT'

from tests.test_operations import ops

import os
from test_browser import _context, _owner_login, _staff, chromium_browser, synthetic_mail
from tests.browser_sms_helpers import sms_browser_app as live_app, complete_owner_sms_dialog

@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Isolated Chromium only')
def test_owner_employee_actions_in_browser(live_app,chromium_browser):
    from playwright.sync_api import expect
    owner=_staff();target=_staff('driver')
    context,page,errors=_context(chromium_browser,live_app)
    _owner_login(context,live_app,owner)
    try:
        page.goto(live_app+'/ops')
        page.locator('#navigation [data-page=admin]').click()
        page.locator('[data-action=admin-tab][data-tab=users]').click()
        for action in ('pause','archive','restore'):
            if action=='restore':page.locator('details summary').filter(has_text='الموظفون المحذوفون').click()
            if action!='restore':page.locator(f'.staff-card[data-staff-id="{target["id"]}"] .staff-actions summary').click()
            page.locator(f'[data-action=manage-staff][data-operation={action}][data-id="{target["id"]}"]').click()
            modal=page.locator('#modal')
            modal.locator('[name=reason]').fill('Synthetic owner action test')
            expect(modal.locator('[name=password]')).to_have_count(0)
            modal.locator('button[type=submit]').click()
            complete_owner_sms_dialog(page, owner)
            expect(modal).not_to_be_visible()
        page.locator(f'.staff-card[data-staff-id="{target["id"]}"] .staff-actions summary').click()
        expect(page.locator(f'[data-action=edit-staff][data-id="{target["id"]}"]')).to_be_visible()
        assert not errors
    finally:context.close()

@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Isolated Chromium only')
def test_staff_branch_dropdown_count_cancel_and_save(live_app,chromium_browser):
    from playwright.sync_api import expect
    owner=_staff();target=_staff('driver')
    context,page,errors=_context(chromium_browser,live_app)
    _owner_login(context,live_app,owner)
    try:
        page.goto(live_app+'/ops')
        page.locator('#navigation [data-page=admin]').click()
        page.locator('[data-action=admin-tab][data-tab=users]').click()
        form=page.locator(f'.staff-branch-form[data-staff-id="{target["id"]}"]')
        expect(form.locator('output')).to_have_text('1')
        expect(form.locator('.staff-branch-options')).not_to_be_visible()
        form.locator('summary').click()
        second=form.locator('input[type=checkbox]:not(:checked)').first
        branch=second.get_attribute('name').split(':',1)[1]
        second.check()
        expect(form.locator('output')).to_have_text('2')
        with db.tx() as conn:
            assert conn.execute('SELECT branch_ids FROM users WHERE id=%s',(target['id'],)).fetchone()['branch_ids']==['kps-branch-1']
        form.locator('button[type=reset]').click()
        expect(form.locator('output')).to_have_text('1')
        form.locator('summary').click()
        form.locator(f'input[name="scope:{branch}"]').check()
        form.locator('[name=reason]').fill('Synthetic branch assignment')
        form.locator('button[type=submit]').click()
        expect(form.locator('details')).not_to_have_attribute('open','')
        expect(form.locator('output')).to_have_text('2')
        with db.tx() as conn:
            row=conn.execute('SELECT branch_ids,role,active FROM users WHERE id=%s',(target['id'],)).fetchone()
            assert set(row['branch_ids'])=={'kps-branch-1',branch}
            assert row['role']=='driver' and row['active']
        for width in (390,1280):
            page.set_viewport_size({'width':width,'height':900})
            form.scroll_into_view_if_needed()
            expect(form.locator('summary')).to_be_visible()
            expect(form.locator('output')).to_have_text('2')
        page.locator(f'.staff-card[data-staff-id="{target["id"]}"] .staff-actions summary').click()
        page.locator(f'[data-action=edit-staff][data-id="{target["id"]}"]').click()
        expect(page.locator('#modal .staff-branch-picker output')).to_have_text('2')
        page.locator('#modal .staff-branch-picker summary').click()
        expect(page.locator('#modal .staff-branch-options input:checked')).to_have_count(2)
        assert not errors
    finally:context.close()

