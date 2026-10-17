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
                if httpx.get(base + '/ready', timeout=1).status_code == 200:
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
        expect(page.locator('#staff-code-security')).to_have_text(
            'الرمز صالح لمرة واحدة لمدة خمس دقائق. لا تشاركه مع الآخرين.' if lang == 'ar'
            else 'The code is valid once for five minutes. Do not share it.')
        expect(page.locator('#staff-code-verify-form input[name=display_name]')).not_to_be_visible()
        # Changing language while a code is pending preserves its actual channel.
        page.locator('#language').click()
        expect(page.locator('#staff-code-note')).to_contain_text('SMS')
        expect(page.locator('#staff-code-security')).not_to_contain_text('15')
        assert not errors, errors
    finally:
        context.close()
