"""Real bilingual owner/customer screens; synthetic isolated configuration only."""
import os
import pytest
from playwright.sync_api import expect
from app import db
from test_branch_payment_policy import prepare
from test_core import _prepare_catalog
from test_browser import _context, _owner_login, _staff, chromium_browser, live_app, synthetic_mail

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Requires isolated browser acceptance')]


@pytest.mark.parametrize('lang', ['ar', 'en'])
def test_owner_selects_seller_without_activating_service(live_app, chromium_browser, lang):
    context, page, errors = _context(chromium_browser, live_app)
    context.add_init_script("localStorage.setItem('kps_ops_lang', '"+lang+"')")
    _owner_login(context, live_app, _staff())
    page.set_viewport_size({'width': 390, 'height': 900})
    try:
        page.goto('/ops')
        expect(page.locator('#owner-dashboard')).to_be_visible()
        admin_navigation=page.locator('#navigation [data-page=admin]')
        if not admin_navigation.is_visible():
            toggle=page.locator('.dispatch-nav-toggle')
            expect(toggle).to_be_visible()
            if toggle.get_attribute('aria-expanded') != 'true':
                toggle.click()
            expect(toggle).to_have_attribute('aria-expanded', 'true')
        expect(admin_navigation).to_be_visible()
        admin_navigation.click()
        page.locator('[data-action=admin-tab][data-tab=settings]').click()
        page.locator('[data-action=online-payment-setup]').click()
        page.locator('[data-action=branch-payment-readiness]').click()
        form = page.locator('#modal-body form')
        form.locator('[name=branch_id]').select_option('kps-branch-1')
        expect(form.locator('[name=seller_id]')).to_have_value('')
        form.locator('[name=seller_id]').select_option('sami')
        form.locator('[name=reason]').fill('Explicit synthetic owner selection only')
        with page.expect_response(lambda r: r.request.method == 'PATCH' and '/api/admin/branches/kps-branch-1' in r.url) as result:
            form.locator('button[type=submit]').click()
        assert result.value.status == 200
        expect(page.locator('#modal-body')).to_contain_text('Live payments remain disabled' if lang == 'en' else 'الدفع الحقيقي ما زال معطلًا')
        saved = context.request.get(live_app+'/api/admin/branch-payment-policy').json()
        row = next(b for b in saved['branches'] if b['id'] == 'kps-branch-1')
        assert row['seller_id'] == 'sami' and not row['payment_ready']
        assert not row['pickup_owner_enabled'] and not row['car_owner_enabled']
        assert not saved['live_payments_available'] and not errors
    finally: context.close()


@pytest.mark.parametrize('lang', ['ar', 'en'])
def test_current_customer_branch_services_hide_pay_on_arrival(live_app, chromium_browser, monkeypatch, lang):
    _prepare_catalog()
    prepare(monkeypatch)
    with db.tx() as c:
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'", (db.json({'branch_online_required': True, 'services': {'pickup': True, 'car_pickup': True, 'delivery': True}}),))
        c.execute("UPDATE branches SET settings=settings||'{\"online_payment_seller\":\"sami\"}'::jsonb WHERE id='kps-branch-1'")
    context, page, errors = _context(chromium_browser, live_app)
    context.add_init_script("localStorage.setItem('kps_customer_lang', '"+lang+"')")
    page.set_viewport_size({'width': 390, 'height': 900})
    try:
        page.goto('/')
        page.locator('#branch-select').select_option('kps-branch-1')
        for service in ('pickup', 'car'):
            page.locator('input[name=service][value='+service+']').check()
            expect(page.locator('.payment-options')).to_have_js_property('hidden', True)
            expect(page.locator('#online-payment-notice')).to_have_text('Pay online after cashier approval — no payment at handover.' if lang == 'en' else 'الدفع إلكترونيًا بعد موافقة الكاشير — لا دفع عند الاستلام.')
        page.locator('input[name=service][value=delivery]').check()
        expect(page.locator('.payment-options')).to_have_js_property('hidden', False)
        assert not errors
    finally: context.close()
