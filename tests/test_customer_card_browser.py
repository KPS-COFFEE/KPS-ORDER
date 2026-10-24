"""Real customer assets with explicitly synthetic APIs; no production writes or SMS."""
import json
import os
from pathlib import Path
import pytest
from test_experience_entry_browser import static_customer

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser acceptance')


@pytest.mark.parametrize('brand', ['kps', 'enbishan'])
@pytest.mark.parametrize('lang', ['ar', 'en'])
def test_photo_taps_quantity_required_choices_and_neighborhood(static_customer, brand, lang):
    from playwright.sync_api import sync_playwright, expect
    branch = {'id': 'synthetic-branch', 'brand_id': brand, 'name': 'Synthetic test branch',
              'branch_no': 1, 'city': 'حفر الباطن', 'payment_methods': ['cash', 'card'],
              'services': {'pickup': {'available': True}, 'delivery': {'available': True}, 'car': {'available': False}}}
    plain = {'id': 'synthetic-plain', 'brand_id': brand, 'name': 'صنف اختبار', 'name_en': 'Synthetic item',
             'category': 'اختبار', 'price_halalas': 1000, 'source_verified': True, 'available': True, 'option_groups': []}
    variant = {**plain, 'id': 'synthetic-options', 'name': 'صنف اختبار بخيار', 'name_en': 'Synthetic options',
               'option_groups': [{'code': 'FLAVOUR', 'required': True, 'name': 'النكهة', 'name_en': 'Flavour',
                                  'values': [{'code': 'A', 'name': 'خيار أ', 'name_en': 'Option A', 'price_delta_halalas': 0}]}]}
    errors, requests = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
        context.add_init_script("localStorage.setItem('kps_customer_lang'," + json.dumps(lang) + ")")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        def api(route):
            path = route.request.url.split('/api/', 1)[1].split('?', 1)[0]
            requests.append(path)
            responses = {
                'catalog': {'brand': {'id': brand, 'name': brand.upper()}, 'products': [plain, variant], 'source_verified': True},
                'branches': {'branches': [branch], 'brand_services': {'pickup': True, 'delivery': True, 'car_pickup': False}},
                'session': {'user': None, 'csrf_token': None},
                'identity/channels': {'customer_phone_required': True, 'channels': {'sms': {'available': True}}},
                'experience/public': {'customer_entry_enabled': False, 'gift_offer_active': False},
                'customer-map/config': {'enabled': False}, 'banners': {'banners': []},
                'products/synthetic-options/availability': {'available': True},
                'products/synthetic-plain/availability': {'available': True},
                'quote': {'quote': {'subtotal_halalas': 1000, 'delivery_fee_halalas': 0, 'total_halalas': 1000, 'items': []}},
                'branches/nearest': {'branches': [], 'excluded': []},
            }
            assert path in responses, 'Unexpected API operation: ' + path
            route.fulfill(status=200, content_type='application/json', body=json.dumps(responses[path]))
        context.route('**/api/**', api)
        page.goto(static_customer + '/?brand_id=' + brand, wait_until='networkidle')
        page.locator('#branch-select').select_option('synthetic-branch')
        plain_card = page.locator('[data-product-id=synthetic-plain]')
        expect(plain_card.locator('.product-add-button')).to_be_enabled()
        # Dispatch a burst on the same real image button: re-rendering cannot lose taps.
        plain_card.locator('.product-image-button').evaluate('(button)=>{for(let i=0;i<7;i++)button.click();}')
        expect(page.locator('#product-dialog')).not_to_be_visible()
        expect(plain_card.locator('.product-card-quantity')).to_have_text('٧' if lang == 'ar' else '7')
        plain_card.locator('.product-minus-button').click()
        expect(plain_card.locator('.product-card-quantity')).to_have_text('٦' if lang == 'ar' else '6')
        option_card = page.locator('[data-product-id=synthetic-options]')
        option_card.locator('.product-image-button').click()
        expect(page.locator('#product-dialog')).to_be_visible()
        page.locator('#product-add').click()
        expect(page.locator('#product-error')).to_be_visible()
        expect(option_card.locator('.product-card-quantity')).to_have_text('٠' if lang == 'ar' else '0')
        if brand == 'enbishan':
            page.locator('#product-options').get_by_role('button', name='خيار أ' if lang == 'ar' else 'Option A', exact=True).click()
        else:
            page.locator('#product-options select').select_option('A')
        page.locator('#product-add').click()
        expect(page.locator('#product-dialog')).not_to_be_visible()
        expect(option_card.locator('.product-card-quantity')).to_have_text('١' if lang == 'ar' else '1')
        page.locator('input[name=service][value=delivery]').check()
        expect(page.locator('#neighborhood')).to_have_attribute('required', '')
        assert not page.locator('#address').evaluate('input=>input.required')
        assert 'orders' not in requests and 'auth/otp/request' not in requests
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        output = Path('test-results'); output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / f'TEST_ONLY-customer-cards-{brand}-{lang}.png'), full_page=True)
        assert not errors, errors
        context.close(); browser.close()
