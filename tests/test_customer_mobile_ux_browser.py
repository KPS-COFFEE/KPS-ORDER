"""Mobile storefront regression with synthetic API data and no live writes."""
import os
import re
from urllib.parse import urlsplit

import pytest

from test_delivery_workspace_browser import static_customer

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser run')


@pytest.mark.parametrize('brand,lang,width', [('kps', 'ar', 320), ('enbishan', 'en', 390)])
def test_mobile_review_requote_and_confirmation(static_customer, brand, lang, width, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    user = {'id': 'qa-customer', 'brand_id': brand, 'role': 'customer',
            'identity_kind': 'phone_sms_verified', 'display_name': 'QA Customer', 'phone': '+966500000000'}
    point = {'lat': 28.4, 'lng': 45.9}
    branch = {'id': 'qa-branch', 'brand_id': brand, 'branch_no': 1, 'name': 'QA Branch', 'city': 'حفر الباطن',
              'available': True, 'location': point, 'location_verified': True, 'direct_distance_meters': 500,
              'services': {'delivery': {'available': True}, 'pickup': {'available': True}},
              'payment_methods': ['cash', 'card']}
    product = {'id': 'qa-item', 'brand_id': brand, 'name': 'صنف اختبار', 'name_en': 'QA Item',
               'category': 'المنيو', 'price_halalas': 1200, 'enabled': True, 'available': True,
               'source_verified': True, 'option_groups': []}
    order = {'id': 'qa-order', 'number': 'QA-123', 'brand_id': brand, 'branch_id': branch['id'],
             'service': 'delivery', 'preparation_status': 'new', 'payment_status': 'unpaid',
             'total_halalas': 1700, 'items': [{'name': product['name'], 'quantity': 1}]}
    payloads = []
    pending = {}
    errors = []

    def respond(route):
        request = route.request
        path = urlsplit(request.url).path.removeprefix('/api/')
        if path == 'identity/channels':
            pending['channels'] = route
            return
        if path in ('quote', 'gifts/quote'):
            route.fulfill(json={'quote': {'subtotal_halalas': 1200, 'delivery_fee_halalas': 500,
                                          'total_halalas': 1700, 'items': []}})
            return
        if path == 'orders' and request.method == 'POST':
            payloads.append(request.post_data_json)
            if len(payloads) == 1:
                route.fulfill(status=409, json={'detail': {'code': 'PRICE_CHANGED', 'message': 'تغيّر السعر.'}})
            else:
                route.fulfill(status=201, json={'order': order})
            return
        responses = {
            'catalog': {'brand': {'id': brand, 'name': brand.upper()}, 'source_verified': True, 'products': [product]},
            'branches': {'branches': [branch], 'brand_services': {'delivery': True, 'pickup': brand == 'kps', 'car_pickup': False}},
            'session': {'user': user, 'csrf_token': 'qa-csrf', 'customer_verified': True},
            'branches/nearest': {'branches': [branch], 'excluded': []},
            'location/parse-map-url': {'location': point, 'source': 'customer_selected'},
            'products/qa-item/availability': {'available': True},
            'orders': {'orders': [order]}, 'orders/qa-order': {'order': order, 'events': []},
            'experience/public': {}, 'banners': {'banners': []}, 'customer/profile': {'addresses': []},
        }
        route.fulfill(json=responses.get(path, {}))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv('TEST_CHROMIUM_EXECUTABLE') or None)
        context = browser.new_context(viewport={'width': width, 'height': 800}, is_mobile=True,
                                      has_touch=True, reduced_motion='reduce')
        page = context.new_page()
        page.add_init_script(f"localStorage.setItem('kps_customer_lang', '{lang}')")
        page.set_default_timeout(8000)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/api/**', respond)
        page.goto(f'{static_customer}/?brand_id={brand}&lang={lang}', wait_until='domcontentloaded')
        expect(page.locator('.product-card')).to_be_visible()
        expect(page.locator('#products')).to_have_attribute('inert', '')
        assert 'channels' in pending
        pending['channels'].fulfill(json={'channels': {'sms': {'available': True}}})
        expect(page.locator('#products')).not_to_have_attribute('inert', '')
        page.locator('.product-add-button').click()
        page.locator('#product-add').click()
        expect(page.locator('#mobile-basket-estimate')).to_have_text('مبلغ تقديري' if lang == 'ar' else 'Estimated')
        page.evaluate('window.scrollTo(0, 0)')
        page.locator('#mobile-basket').click()
        if brand == 'enbishan':
            page.locator('#enbishan-continue').click()
            page.locator('#enbishan-checkout-location').click()
        else:
            page.locator('input[name=service][value=delivery]').check()
            page.locator('#basket-service').click()
            expect(page.locator('#locate-button')).to_be_focused()
        page.locator('#manual-location-button').click()
        page.locator('#map-url').fill('https://maps.google.com/?q=28.4,45.9')
        if brand == 'kps':
            page.locator('#branch-select').select_option(branch['id'])
        else:
            expect(page.locator('#branch-select')).to_have_value(branch['id'])
            page.locator('#enbishan-location-done').click()
        page.locator('#neighborhood').fill('QA district')
        page.locator('#customer-name').fill('QA Customer')
        page.locator('input[name=payment][value=card]').check()
        expect(page.locator('#mobile-basket-estimate')).to_be_hidden()
        expect(page.locator('#mobile-basket-total')).to_contain_text('١٧' if lang == 'ar' else '17')
        page.locator('#checkout-button').click()
        expect(page.locator('#confirm-dialog')).to_be_visible()
        page.locator('#confirm-back').click()
        expect(page.locator('#confirm-dialog')).to_be_hidden()
        assert not payloads
        expect(page.locator('input[name=payment][value=card]')).to_be_checked()
        page.locator('#checkout-button').click()
        page.locator('#confirm-submit').click()
        expect(page.locator('#basket-error')).to_be_visible()
        expect(page.locator('#checkout-button')).to_be_enabled()
        page.locator('#checkout-button').click()
        page.locator('#confirm-submit').click()
        expect(page.locator('#order-detail')).to_contain_text('QA-123')
        expect(page.locator('.customer-progress')).to_be_visible()
        expect(page.locator('.customer-order-actions button')).to_have_count(2)
        expect(page.locator('.customer-order-actions')).to_contain_text(re.compile('محادثة|conversation', re.I))
        assert len(payloads) == 2 and payloads[0] == payloads[1]
        assert payloads[1]['payment_method'] == 'card'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert not errors
        page.screenshot(path=str(tmp_path / f'{brand}-{lang}-{width}.png'))
        browser.close()
