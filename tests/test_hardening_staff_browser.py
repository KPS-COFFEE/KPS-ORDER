"""A real committed custody entry survives a lost HTTP response and reload."""
import os
from uuid import uuid4

import pytest

from app import db
from test_browser import _context, _owner_login, _staff, chromium_browser, live_app, synthetic_mail

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.getenv('TEST_BROWSER') != '1', reason='Requires real Chromium and isolated PostgreSQL')]


@pytest.mark.parametrize('language', ['ar', 'en'])
@pytest.mark.parametrize('failure', ['disconnect', 'html_proxy'])
def test_cash_entry_lost_committed_response_replays_after_reload(live_app, chromium_browser, language, failure):
    from playwright.sync_api import expect

    owner, driver = _staff(), _staff('driver')
    sid = 'TEST-retry-' + uuid4().hex
    with db.tx() as conn:
        conn.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'driver',%s,'kps','[\"kps-branch-1\"]')",
                     (sid, driver['id']))
    context, page, errors = _context(chromium_browser, live_app)
    _owner_login(context, live_app, owner)
    path = '/api/shifts/' + sid + '/cash-event'
    observed_keys = []

    def lose_committed_response(route):
        observed_keys.append(route.request.headers['idempotency-key'])
        response = route.fetch()  # Real app writes and commits to real PostgreSQL.
        assert response.status == 200, response.text()
        if failure == 'disconnect':
            route.abort('connectionreset')  # Only drop delivery of the saved response.
        else:
            # A proxy page is not evidence that the actual mutation succeeded.
            route.fulfill(status=200, content_type='text/html', body='<p>TEST gateway page</p>')

    def open_and_fill():
        page.locator('#navigation [data-page=shifts]').click()
        page.locator('[data-action=shift-detail][data-id="' + sid + '"]').click()
        button = page.locator('[data-action=cash-event][data-id="' + sid + '"]')
        button.locator('xpath=ancestor::details').locator('summary').click()
        button.click()
        modal = page.locator('#modal')
        modal.locator('[name=amount]').fill('10')
        modal.locator('[name=reference]').fill('TEST-REAL-RECEIPT')
        modal.locator('[name=reason]').fill('TEST owner counted cash')
        modal.locator('[name=confirmed]').check()
        return modal

    try:
        page.goto(live_app + '/ops')
        if language == 'en':
            page.get_by_role('button', name='English', exact=True).click()
        page.route('**' + path, lose_committed_response)
        modal = open_and_fill()
        modal.locator('button[type=submit]').click()
        expect(modal.locator('.form-error')).not_to_be_empty()
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) n,sum(amount_halalas) total FROM custody_events WHERE shift_id=%s', (sid,)).fetchone() == {'n': 1, 'total': 1000}
        entries = page.evaluate("Object.entries(sessionStorage).filter(([k])=>k.startsWith('kps_ops_pending_v1:'))")
        assert len(entries) == 1 and entries[0][1] == observed_keys[0]
        assert 'TEST-REAL-RECEIPT' not in entries[0][0] and 'TEST owner counted cash' not in entries[0][0]
        page.unroute('**' + path, lose_committed_response)
        page.reload()
        modal = open_and_fill()
        with page.expect_request(lambda request: request.url.endswith(path) and request.method == 'POST') as retry:
            modal.locator('button[type=submit]').click()
        expect(modal).not_to_be_visible()
        assert retry.value.headers['idempotency-key'] == observed_keys[0]
        assert page.evaluate("Object.keys(sessionStorage).filter(k=>k.startsWith('kps_ops_pending_v1:')).length") == 0
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) n,sum(amount_halalas) total FROM custody_events WHERE shift_id=%s', (sid,)).fetchone() == {'n': 1, 'total': 1000}
            assert conn.execute("SELECT count(*) n FROM audit_events WHERE action='custody.authorized_event' AND entity_id=%s", (sid,)).fetchone()['n'] == 1
        # A subsequent deliberate, identical receipt is a separate operation.
        modal = open_and_fill()
        with page.expect_request(lambda request: request.url.endswith(path) and request.method == 'POST') as genuine:
            modal.locator('button[type=submit]').click()
        expect(modal).not_to_be_visible()
        assert genuine.value.headers['idempotency-key'] != observed_keys[0]
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) n,sum(amount_halalas) total FROM custody_events WHERE shift_id=%s', (sid,)).fetchone() == {'n': 2, 'total': 2000}
        assert errors == []
    finally:
        context.close()
