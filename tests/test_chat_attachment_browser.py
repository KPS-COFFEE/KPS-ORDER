"""Real SMS work session plus independent customer cookie; no API interception."""
import os
from uuid import uuid4

import pytest

from app import db
from test_browser import ARTIFACTS, _context, _customer_session, _owner_login, _staff, chromium_browser
from tests.browser_sms_helpers import sms_browser_app
from tests.test_chat_attachment_sessions import create_image_conversation

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1',
                                                     reason='Explicit real browser acceptance')]


@pytest.fixture(autouse=True)
def context_mode(monkeypatch):
    monkeypatch.setenv('SESSION_CONTEXT_COOKIES_ENABLED', 'true')


@pytest.mark.parametrize('lang,width', [('ar', 1280), ('ar', 390), ('en', 1280), ('en', 390)])
def test_thumbnail_and_opened_image_use_the_correct_session(sms_browser_app, chromium_browser, lang, width):
    from playwright.sync_api import expect
    base = sms_browser_app
    manager = _staff('delivery_manager')
    customer, token, csrf = _customer_session()
    context, page, errors = _context(chromium_browser, base)
    try:
        _owner_login(context, base, manager)
        context.add_cookies([
            {'name': 'kps_customer_session', 'value': token, 'url': base, 'httpOnly': True, 'sameSite': 'Strict'},
            {'name': 'kps_customer_csrf', 'value': csrf, 'url': base, 'sameSite': 'Strict'},
        ])

        def post(path, body):
            response = context.request.post(base + path, data=body, headers={
                'Origin': base, 'X-KPS-Context': 'customer', 'X-CSRF-Token': csrf,
                'Idempotency-Key': str(uuid4())})
            assert response.ok, response.text()
            return response.json()

        cid, uploaded, picture = create_image_conversation(post)
        context.add_init_script(f"localStorage.setItem('kps_ops_lang','{lang}');localStorage.setItem('kps_customer_lang','{lang}')")
        page.set_viewport_size({'width': width, 'height': 900})
        page.goto(base + '/ops')
        page.locator('#navigation [data-page=chat]').click()
        page.locator(f'.conversation[data-id="{cid}"]').click()
        staff_image = page.locator('#messages .message img')
        expect(staff_image).to_be_visible()
        expect(staff_image).to_have_js_property('naturalWidth', 24)
        expect(staff_image).to_have_js_property('naturalHeight', 18)
        with context.expect_page() as opened:
            staff_image.click()
        popup = opened.value
        popup.wait_for_load_state()
        assert popup.url.endswith('?context=work')
        expect(popup.locator('img')).to_have_js_property('naturalWidth', 24)
        assert context.request.get(popup.url).body() == picture
        popup.screenshot(path=str(ARTIFACTS / f'chat-image-open-{lang}-{width}.png'))
        popup.close()
        page.screenshot(path=str(ARTIFACTS / f'chat-image-staff-{lang}-{width}.png'))

        # Both cookies remain in the same browser; customer UI selects its own.
        page.goto(base + '/')
        page.locator('#legal-contact-button').click()
        page.locator('#public-contact-support').click()
        page.locator(f'#contacts-list [data-conversation-id="{cid}"]').click()
        customer_image = page.locator('#chat-messages img')
        expect(customer_image).to_have_js_property('naturalWidth', 24)
        with context.expect_page() as opened:
            customer_image.click()
        popup = opened.value
        popup.wait_for_load_state()
        assert popup.url.endswith('?context=customer')
        expect(popup.locator('img')).to_have_js_property('naturalWidth', 24)
        popup.close()
        with db.tx() as c:
            assert bytes(c.execute('SELECT data FROM chat_attachments WHERE id=%s',
                                   (uploaded['id'],)).fetchone()['data']) == picture
        assert not errors, errors
    finally:
        context.close()
