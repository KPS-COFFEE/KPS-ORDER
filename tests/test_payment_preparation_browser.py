"""Real owner forms on isolated data; no gateway network requests or charges."""
from urllib.parse import urlsplit

from playwright.sync_api import expect
import pytest

from test_browser import (ARTIFACTS, _context, _owner_login, _staff,
                          chromium_browser, live_app, synthetic_mail)


@pytest.mark.postgres
@pytest.mark.parametrize('lang,width', [('ar', 390), ('en', 1280)])
def test_owner_prepares_multiple_providers_without_customer_activation(live_app, chromium_browser, lang, width):
    context, page, errors = _context(chromium_browser, live_app)
    context.add_init_script("localStorage.setItem('kps_ops_lang', '" + lang + "')")
    _owner_login(context, live_app, _staff())
    provider_requests = []
    page.on('request', lambda request: provider_requests.append(request.url)
            if urlsplit(request.url).hostname in {'api.moyasar.com', 'checkout.moyasar.com'} else None)
    try:
        page.set_viewport_size({'width': width, 'height': 900})
        page.goto('/ops')
        expect(page.locator('#app-shell')).to_be_visible()
        page.locator('#navigation [data-page=admin]').click()
        page.locator('[data-action=admin-tab][data-tab=settings]').click()
        page.locator('[data-action=online-payment-setup]').click()
        form = page.locator('#payment-preparation-form')
        expect(form).to_be_visible()
        form.locator('select[name="provider:reem"]').select_option('paytabs')
        form.locator('select[name="provider:sami"]').select_option('custom')
        form.locator('input[name="custom:sami"]').fill('fixture_gateway')
        form.locator('input[name=reason]').fill('Synthetic disabled gateway selection')
        form.locator('button[type=submit]').click()
        expect(form).not_to_be_visible()
        result = context.request.get(live_app + '/api/admin/online-payments').json()
        assert not result['enabled'] and not result['live_payments_available']
        assert result['customer_payment_methods'] == []
        assert {a['entity_id']: a['provider'] for a in result['accounts']} == {
            'reem': 'paytabs', 'sami': 'fixture_gateway'}
        page.locator('[data-action=online-payment-setup]').click()
        form = page.locator('#payment-preparation-form')
        expect(form.locator('select[name="provider:reem"]')).to_have_value('paytabs')
        expect(form.locator('input[name="custom:sami"]')).to_have_value('fixture_gateway')
        expect(form).to_contain_text('غير مفعل' if lang == 'ar' else 'Disabled')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2')
        page.screenshot(path=str(ARTIFACTS / f'payment-preparation-{lang}-{width}.png'), full_page=True)
        assert not errors and not provider_requests
    finally:
        context.close()
