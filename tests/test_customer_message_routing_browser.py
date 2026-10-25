"""Owner selects recipients, completes synthetic SMS, recipient sees real inbox."""
import os
from pathlib import Path

import pytest
from app import db, customer_message_routing as routing
from test_browser import _context, _owner_login, _staff, chromium_browser
from tests.browser_sms_helpers import sms_browser_app as live_app, complete_owner_sms_dialog
from tests.test_core import _make_user
from tests.test_customer_message_routing import message


@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Isolated Chromium only')
def test_owner_routes_and_admin_replies_without_role_change(live_app, chromium_browser):
    from playwright.sync_api import expect
    owner = _staff()
    admin = _staff('admin')
    customer = _make_user('customer')
    cid = message(customer, category='privacy', route='privacy', branch=None)
    context, page, errors = _context(chromium_browser, live_app)
    _owner_login(context, live_app, owner)
    try:
        page.goto(live_app+'/ops')
        page.locator('#navigation [data-page=admin]').click()
        page.locator('[data-action=customer-message-routing]').click()
        modal = page.locator('#modal')
        for group in ('delivery', 'care'):
            modal.locator(f'input[name="routing:kps:{group}"][value="{admin["id"]}"]').check()
        modal.locator('[name=reason]').fill('Synthetic reviewed response responsibility')
        for width in (390, 1280):
            page.set_viewport_size({'width': width, 'height': 900})
            expect(modal.locator('button[type=submit]')).to_be_visible()
        modal.locator('button[type=submit]').click()
        with db.tx() as c:
            assert routing.config(c, 'kps') is None
        complete_owner_sms_dialog(page, owner)
        expect(modal).not_to_be_visible()
        with db.tx() as c:
            assert routing.config(c, 'kps') == {'delivery': [admin['id']], 'care': [admin['id']]}
            assert c.execute('SELECT role FROM users WHERE id=%s', (admin['id'],)).fetchone()['role'] == 'admin'
        assert not errors
    finally:
        context.close()
    context, page, errors = _context(chromium_browser, live_app)
    _owner_login(context, live_app, admin)
    try:
        # Hold the real read-receipt request, without substituting its response.
        # The visible reply form must work before that unrelated request finishes.
        held_reads = []
        page.route(f'**/api/conversations/{cid}/read', lambda route: held_reads.append(route))
        page.goto(live_app+'/ops')
        page.locator('#navigation [data-page=chat]').click()
        with page.expect_request(f'**/api/conversations/{cid}/read'):
            page.locator(f'.conversation[data-id="{cid}"]').click()
        expect(page.locator('#message-form')).to_be_visible()
        assert held_reads
        page.locator('#message-form textarea').fill('Synthetic customer care response')
        page.locator('#message-form button[type=submit]').click()
        expect(page.locator('#messages')).to_contain_text('Synthetic customer care response')
        expect(page).to_have_url(live_app+'/ops')
        for request in held_reads:
            request.continue_()
        expect(page.locator('#chat-detail [data-action=transfer-chat]')).to_have_count(0)
        Path('test-artifacts').mkdir(exist_ok=True)
        page.screenshot(path='test-artifacts/customer-message-routing-admin.png', full_page=True)
        assert not errors
    finally:
        context.close()
