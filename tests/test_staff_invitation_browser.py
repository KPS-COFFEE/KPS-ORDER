"""Owner SMS resend confirmation against isolated PostgreSQL and real Chromium."""
import os

import pytest

from app import db
from test_browser import _capture, _context, _owner_login, _staff, chromium_browser, live_app, synthetic_mail

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.getenv('TEST_BROWSER') != '1', reason='Requires explicit isolated browser acceptance.')]


@pytest.mark.parametrize('language', ['ar', 'en'])
def test_kps_email_resend_stays_retired_after_owner_sms_without_changing_staff_access(live_app, chromium_browser, language):
    from playwright.sync_api import expect

    owner = _staff()
    targets = []
    for index, role in enumerate(('delivery_manager', 'admin', 'owner', 'driver')):
        target = _staff(role)
        target.update(email=f'resend-{role}@example.invalid', role=role)
        with db.tx() as conn:
            conn.execute("UPDATE users SET display_name=%s,email=%s,phone=%s,active=false,invitation_status='draft' WHERE id=%s",
                         ('TEST ONLY ' + role, target['email'], f'+96650000010{index}', target['id']))
        targets.append(target)
    with db.tx() as conn:
        conn.execute("UPDATE identity_channels SET state='testing',enabled=false WHERE brand_id='kps' AND channel='email'")
    context, page, errors = _context(chromium_browser, live_app)
    headers = _owner_login(context, live_app, owner)
    try:
        response = context.request.post(live_app + f"/api/identity/staff/{targets[0]['id']}/resend", headers=headers, data={})
        assert response.status == 403
        assert response.json()['detail']['code'] == 'OWNER_STEP_UP_REQUIRED'
        page.goto(live_app + '/ops')
        if language == 'en':
            page.get_by_role('button', name='English', exact=True).click()
        page.locator('#navigation [data-page=admin]').click()
        page.locator('[data-action=admin-tab][data-tab=users]').click()
        modal = page.locator('#modal')
        for index, target in enumerate(targets):
            with db.tx() as conn:
                conn.execute('UPDATE sessions SET step_up_at=NULL WHERE user_id=%s', (owner['id'],))
                before = conn.execute('SELECT role,email,phone,branch_ids,permissions,active,email_verified_at FROM users WHERE id=%s', (target['id'],)).fetchone()
            button = page.locator(f'[data-action=resend-invite][data-id="{target["id"]}"]')
            # Staff actions are behind a native disclosure. Follow the same
            # visible path as the owner before clicking its hidden descendant.
            button.locator('xpath=ancestor::details').locator('summary').click()
            if target['role'] == 'driver':
                with page.expect_response(lambda response: response.url.endswith(f"/api/identity/staff/{target['id']}/resend")) as resend:
                    button.click()
            else:
                button.click()
            if target['role'] != 'driver':
                expect(modal).to_be_visible()
                expect(modal.get_by_text(target['email'], exact=True)).to_be_visible()
                with db.tx() as conn:
                    assert conn.execute('SELECT count(*) AS n FROM email_outbox WHERE recipient=%s', (target['email'],)).fetchone()['n'] == 0
                if index == 0:
                    page.locator('#modal-close').click()
                    expect(modal).not_to_be_visible()
                    with db.tx() as conn:
                        assert conn.execute('SELECT invitation_status FROM users WHERE id=%s', (target['id'],)).fetchone()['invitation_status'] == 'draft'
                    button.click()
                    _capture(page, 'TEST_ONLY_owner_resend_confirmation_' + language)
                expect(modal.locator('input[name=step_up_password]')).to_have_count(0)
                modal.locator('button[type=submit]').click()
                sms_dialog = page.locator('dialog[open]').filter(has=page.locator('input[name=code]'))
                expect(sms_dialog).to_be_visible()
                expect(sms_dialog.locator('button[type=submit]')).to_be_enabled()
                if index == 0:
                    sms_dialog.locator('input[name=code]').fill('0000')
                    sms_dialog.locator('button[type=submit]').click()
                    expect(sms_dialog.locator('.form-error')).not_to_be_empty()
                    expect(page.locator('#app-shell')).to_be_visible()
                    expect(page.locator('#login-shell')).not_to_be_visible()
                    with db.tx() as conn:
                        assert conn.execute('SELECT count(*) AS n FROM email_outbox WHERE recipient=%s', (target['email'],)).fetchone()['n'] == 0
                        assert conn.execute('SELECT invitation_status FROM users WHERE id=%s', (target['id'],)).fetchone()['invitation_status'] == 'draft'
                sms_dialog.locator('input[name=code]').fill('0123')
                with page.expect_response(lambda response: response.url.endswith(f"/api/identity/staff/{target['id']}/resend")) as resend:
                    sms_dialog.locator('button[type=submit]').click()
                expect(sms_dialog).to_have_count(0)
            assert resend.value.status == 403
            assert resend.value.json()['detail']['code'] == 'WORK_SMS_REQUIRED'
            if target['role'] != 'driver':
                expect(modal.locator('.form-error')).to_contain_text('SMS')
                page.locator('#modal-close').click()
            else:
                expect(page.locator('#status')).to_contain_text('SMS')
            with db.tx() as conn:
                after = conn.execute('SELECT role,email,phone,branch_ids,permissions,active,email_verified_at FROM users WHERE id=%s', (target['id'],)).fetchone()
                assert after == before
                assert not after['active'] and after['email_verified_at'] is None
                assert conn.execute('SELECT invitation_status FROM users WHERE id=%s', (target['id'],)).fetchone()['invitation_status'] == 'draft'
                mail = conn.execute('SELECT status,recipient FROM email_outbox WHERE recipient=%s', (target['email'],)).fetchall()
                assert mail == []
                assert conn.execute('SELECT count(*) n FROM identity_tokens WHERE user_id=%s', (target['id'],)).fetchone()['n'] == 0
        assert errors == []
    finally:
        context.close()
