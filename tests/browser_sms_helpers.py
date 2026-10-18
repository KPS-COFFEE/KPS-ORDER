"""Real HTTP browser fixtures; only external SMS provider I/O is synthetic."""
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from app import db
from tests.sms_policy_helpers import configure_sms


@pytest.fixture
def sms_browser_app(database, monkeypatch):
    # In-process ASGI preserves the provider monkeypatch, unlike live_app's
    # subprocess. No auth routes, session creation or authorization are mocked.
    configure_sms(monkeypatch)
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
            pytest.fail('Isolated SMS browser server did not become ready')
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive(), 'Stop HTTP server before isolated database cleanup'


def complete_owner_sms_dialog(page, owner):
    """Complete the UI's actual session-bound step-up without fabricating proof."""
    from playwright.sync_api import expect
    dialog = page.locator('dialog:not(#modal)').filter(has=page.locator('input[name=code]'))
    expect(dialog).to_be_visible()
    # The dialog also handles provider-send failures. A visible input is not
    # evidence that a challenge was successfully issued; await the enabled gate.
    expect(dialog.locator('button[type=submit]')).to_be_enabled()
    code = dialog.locator('input[name=code]')
    expect(code).to_have_attribute('maxlength', '4')
    expect(dialog.locator('input[type=password]')).to_have_count(0)
    with db.tx() as conn:
        challenge = conn.execute("SELECT * FROM otp_challenges WHERE source_user_id=%s AND id LIKE 'owner_sms_%%' AND verified_at IS NULL ORDER BY created_at DESC LIMIT 1", (owner['id'],)).fetchone()
        assert challenge and challenge['delivery_channel'] == 'sms'
        session = conn.execute('SELECT * FROM sessions WHERE user_id=%s AND revoked_at IS NULL', (owner['id'],)).fetchall()
        assert len(session) == 1
        assert challenge['source_session_id'] == session[0]['id']
    code.fill('0123')
    dialog.locator('button[type=submit]').click()
    expect(dialog).to_have_count(0)
    with db.tx() as conn:
        proof = conn.execute('SELECT step_up_at FROM sessions WHERE id=%s', (challenge['source_session_id'],)).fetchone()
        assert proof['step_up_at'] is not None
        consumed = conn.execute('SELECT verified_at FROM otp_challenges WHERE id=%s', (challenge['id'],)).fetchone()
        assert consumed['verified_at'] is not None
