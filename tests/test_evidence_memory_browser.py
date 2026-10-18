"""Preserve the real bilingual camera/delivery journey under mandatory SMS."""
import os

import pytest
from tests.browser_sms_helpers import sms_browser_app
from test_browser import chromium_browser, _owner_login

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.getenv('TEST_BROWSER') != '1', reason='Explicit isolated browser run')]


@pytest.mark.parametrize('language', ['ar', 'en'])
def test_saved_photos_cash_and_delivery(sms_browser_app, chromium_browser, monkeypatch, language):
    # Reuse all original camera, gallery, persisted stage/evidence, collection
    # and manager-read assertions. Only replace the obsolete password helper
    # with actual staff SMS request/verify and canonical work-context reads.
    import test_driver_camera_browser as journey
    from playwright.sync_api import expect
    from tests.sms_policy_helpers import configure_sms

    # The retained journey creates a synthetic second brand. Apply that
    # fixture's own active SMS provider instead of assuming it is KPS.
    original_fixture = journey._role_fixture
    def role_fixture():
        result = original_fixture()
        configure_sms(monkeypatch, 'browser-ops-test')
        return result
    monkeypatch.setenv('EMPLOYEE_SMS_LOGIN_ENABLED', 'true')
    monkeypatch.setattr(journey, '_role_fixture', role_fixture)

    def login(page, user):
        _owner_login(page.context, sms_browser_app, user)
        page.goto(sms_browser_app + '/ops')
        expect(page.locator('#app-shell')).to_be_visible()
        expect(page.locator('#content .loading')).to_have_count(0)

    monkeypatch.setattr(journey, '_staff_ui_login', login)
    # Manager photo reads need the same separated work-cookie context as every
    # other staff read; this changes no permissions or session issuance.
    original_context = journey._context
    def context(*args, **kwargs):
        ctx, page, errors = original_context(*args, **kwargs)
        ctx.set_extra_http_headers({'X-KPS-Context': 'work'})
        return ctx, page, errors
    monkeypatch.setattr(journey, '_context', context)
    journey.test_camera_first_gallery_fallback_and_saved_stage_photos(
        sms_browser_app, chromium_browser, language)
