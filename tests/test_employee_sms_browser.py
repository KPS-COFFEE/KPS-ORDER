"""Employee SMS channel wording against the real app; only provider I/O is synthetic."""
import os
import socket
import threading
import time

import httpx
import uvicorn

import pytest

from tests.test_employee_sms_portals import sms_setup
from test_browser import _context, chromium_browser

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.getenv('TEST_BROWSER') != '1', reason='Requires isolated PostgreSQL and actual Chromium')]


@pytest.fixture
def employee_sms_app(database, monkeypatch):
    # Run the actual ASGI app in this process so only external provider calls are
    # synthetic. A subprocess would not inherit monkeypatched provider methods.
    sms_setup(monkeypatch)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    monkeypatch.setenv('PUBLIC_BASE_URL', base)
    monkeypatch.setenv('APP_ALLOWED_ORIGINS', base)
    from app.main import app
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                if httpx.get(base + '/ready', timeout=1, trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(.1)
        else:
            pytest.fail('Isolated in-process SMS test server did not become ready')
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive(), 'SMS test server must stop before fixture database cleanup'


@pytest.mark.parametrize('lang', ['ar', 'en'])
def test_employee_sms_labels_and_activation_boundary(employee_sms_app, chromium_browser, lang):
    from playwright.sync_api import expect

    context, page, errors = _context(chromium_browser, employee_sms_app)
    try:
        page.add_init_script(f"localStorage.setItem('kps_ops_lang','{lang}')")
        page.goto(employee_sms_app + '/ops', wait_until='networkidle')
        page.locator('#staff-code-request-form input[name=phone]').fill('0507778899')
        page.locator('#staff-code-request-submit').click()
        expect(page.locator('#staff-code-verify-form')).to_be_visible()
        expect(page.locator('#staff-code-note')).to_contain_text('SMS')
        expect(page.locator('#staff-code-verify-form input[name=code]')).to_have_attribute('maxlength', '4')
        expect(page.locator('#staff-code-label')).to_contain_text('4')
        expect(page.locator('#staff-code-security')).to_have_text(
            'الرمز صالح لمرة واحدة لمدة خمس دقائق. لا تشاركه مع الآخرين.' if lang == 'ar'
            else 'The code is valid once for five minutes. Do not share it.')
        expect(page.locator('#staff-code-verify-form input[name=display_name]')).not_to_be_visible()
        # Changing language while a code is pending preserves its actual channel.
        page.locator('#language').click()
        expect(page.locator('#staff-code-note')).to_contain_text('SMS')
        expect(page.locator('#staff-code-verify-form input[name=code]')).to_have_attribute('maxlength', '4')
        expect(page.locator('#staff-code-label')).to_contain_text('4')
        expect(page.locator('#staff-code-security')).not_to_contain_text('15')
        assert not errors, errors
    finally:
        context.close()


@pytest.mark.parametrize('lang', ['ar', 'en'])
def test_owner_sensitive_action_uses_session_bound_sms_dialog(employee_sms_app, chromium_browser, monkeypatch, lang):
    from fastapi.testclient import TestClient
    from playwright.sync_api import expect
    from app import auth, db, employee_sms
    from app.main import app
    from tests.test_core import _authenticate, _make_user
    monkeypatch.setenv('EMPLOYEE_SMS_EMAIL_RETIRED','true')
    monkeypatch.setenv('OWNER_SENSITIVE_TWILIO_VERIFY_SERVICE_SID','VA'+'c'*32)
    monkeypatch.setattr(employee_sms,'verify_service_length',lambda *args:None)
    with db.tx() as conn:
        owner=conn.execute("UPDATE users SET role='owner',email_verified_at=NULL WHERE phone='+966507778899' RETURNING *").fetchone()
    target=_make_user(role='delivery_manager')
    with TestClient(app) as local:
        token, csrf = _authenticate(local,owner)
    context,page,errors=_context(chromium_browser,employee_sms_app)
    try:
        context.add_cookies([{'name':auth.COOKIE,'value':token,'url':employee_sms_app}, {'name':auth.CSRF_COOKIE,'value':csrf,'url':employee_sms_app}])
        page.add_init_script(f"localStorage.setItem('kps_ops_lang','{lang}')")
        page.goto(employee_sms_app+'/ops',wait_until='networkidle')
        page.locator('#navigation [data-page=admin]').click()
        page.locator('[data-action=admin-tab][data-tab=users]').click()
        page.locator(f'[data-action=staff-permissions][data-id="{target["id"]}"]').click()
        expect(page.locator('#modal input[type=password]')).to_have_count(0)
        page.locator('#modal button[type=submit]').click()
        code=page.locator('dialog:not(#modal) input[name=code]')
        expect(code).to_be_visible()
        expect(code).to_have_attribute('maxlength','4')
        code.fill('٠١٢٣')
        page.locator('dialog:not(#modal) button[type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible()
        with db.tx() as conn:
            proof=conn.execute('SELECT step_up_at FROM sessions WHERE token_hash=%s',(auth.token_hash(token),)).fetchone()
            assert proof['step_up_at'] is not None
            assert conn.execute('SELECT email_verified_at FROM users WHERE id=%s',(owner['id'],)).fetchone()['email_verified_at'] is None
        assert not errors,errors
    finally:
        context.close()
