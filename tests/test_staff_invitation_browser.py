"""Owner resend confirmation against isolated PostgreSQL and real Chromium."""
import os

import pytest

from app import db
from test_browser import PASSWORD, _capture, _context, _owner_login, _staff, chromium_browser, live_app, synthetic_mail

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.getenv('TEST_BROWSER') != '1', reason='Requires explicit isolated browser acceptance.')]


@pytest.mark.parametrize('language', ['ar', 'en'])
def test_privileged_resend_requires_password_without_changing_staff_access(live_app, chromium_browser, language):
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
                    modal.locator('input[name=step_up_password]').fill('WrongSyntheticPassword!')
                    modal.locator('button[type=submit]').click()
                    expect(modal.locator('.form-error')).not_to_be_empty()
                    expect(page.locator('#app-shell')).to_be_visible()
                    expect(page.locator('#login-shell')).not_to_be_visible()
                    expect(modal.locator('input[name=step_up_password]')).to_have_value('')
                    with db.tx() as conn:
                        assert conn.execute('SELECT count(*) AS n FROM email_outbox WHERE recipient=%s', (target['email'],)).fetchone()['n'] == 0
                modal.locator('input[name=step_up_password]').fill(PASSWORD)
                modal.locator('button[type=submit]').click()
            expected = 'حُفظت الدعوة في انتظار إرسال البريد؛ التفعيل بعد تحقق الموظف.' if language == 'ar' else 'Invitation queued for email; activation requires staff verification.'
            expect(page.get_by_role('row').filter(has_text=target['email'])).to_contain_text(
                'بانتظار إثبات البريد' if language == 'ar' else 'Awaiting email proof')
            expect(page.locator('#status')).to_have_text(expected)
            expect(modal).not_to_be_visible()
            with db.tx() as conn:
                after = conn.execute('SELECT role,email,phone,branch_ids,permissions,active,email_verified_at FROM users WHERE id=%s', (target['id'],)).fetchone()
                assert after == before
                assert not after['active'] and after['email_verified_at'] is None
                assert conn.execute('SELECT invitation_status FROM users WHERE id=%s', (target['id'],)).fetchone()['invitation_status'] == 'pending_email'
                mail = conn.execute('SELECT status,recipient FROM email_outbox WHERE recipient=%s', (target['email'],)).fetchall()
                assert len(mail) == 1 and mail[0]['status'] == 'pending'
        assert errors == []
    finally:
        context.close()
