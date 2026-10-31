"""Real manager screen and order assets with isolated API responses."""
import json
import os
import re
from urllib.parse import urlparse

import pytest
from test_delivery_workspace_browser import static_customer

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser run')


@pytest.mark.parametrize('brand', ['kps', 'enbishan'])
@pytest.mark.parametrize('screen', ['dashboard', 'orders'])
@pytest.mark.parametrize('lang,width', [('ar', 1366), ('en', 390)])
def test_failed_save_retains_draft_then_returns_to_manager_screen(static_customer, brand, screen, lang, width):
    from playwright.sync_api import sync_playwright, expect

    user = {'id': 'manager', 'brand_id': brand, 'role': 'delivery_manager',
            'display_name': 'Manager', 'branch_ids': ['branch'],
            'effective_permissions': ['dashboard_view', 'orders_view', 'orders_create_customer',
                                      'shifts_view', 'shifts_open']}
    shift = {'id': 'manager-shift', 'user_id': 'manager', 'kind': 'manager',
             'status': 'open', 'branch_ids': ['branch'], 'opened_at': '2026-10-22T05:00:00+03:00'}
    writes, reads, errors, external = [], [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={'width': width, 'height': 900})
        context.add_init_script(f"localStorage.setItem('kps_ops_lang',{json.dumps(lang)})")
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))

        def local_only(route):
            if urlparse(route.request.url).netloc != urlparse(static_customer).netloc:
                external.append(route.request.url)
                route.abort()
            else:
                route.continue_()

        def api(route):
            request = route.request
            path = urlparse(request.url).path
            status, payload = 200, {}
            if request.method == 'GET':
                reads.append(path)
            else:
                writes.append((path, request.post_data_json, request.headers.get('idempotency-key')))
            if path == '/api/session':
                payload = {'user': user, 'csrf_token': 'synthetic-token'}
            elif path == '/api/catalog':
                payload = {'brand': {'id': brand, 'name': brand, 'theme': {}}, 'products': [
                    {'id': 'coffee', 'name': 'Coffee', 'available': True, 'price_halalas': 1000,
                     'image_url': '/assets/kps-receipt-logo.png'}]}
            elif path == '/api/branches':
                payload = {'branches': [{'id': 'branch', 'brand_id': brand, 'name': 'Branch', 'branch_no': 2}]}
            elif path == '/api/shifts/current':
                payload = {'shifts': [shift]}
            elif path == '/api/orders':
                payload = {'orders': [], 'next_offset': None}
            elif path == '/api/operations/map-config':
                payload = {'enabled': False, 'provider': 'mapbox'}
            elif path == '/api/operations/drivers':
                payload = {'drivers': []}
            elif path == '/api/conversations':
                payload = {'conversations': []}
            elif path == '/api/orders/delivery-center/quote':
                payload = {'quote': {'subtotal_halalas': 1000, 'delivery_fee_halalas': 0, 'total_halalas': 1000}}
            elif path == '/api/orders/delivery-center':
                if sum(item[0] == path for item in writes) == 1:
                    status, payload = 503, {'error': {'code': 'TEST_SAVE_UNAVAILABLE', 'message': 'Temporary save failure'}}
                else:
                    payload = {'order': {'id': 'saved-synthetic-order'}}
            route.fulfill(status=status, content_type='application/json', body=json.dumps(payload))

        page.route('**/*', local_only)
        page.route('**/api/**', api)
        context.add_init_script("""window.__managerScrollTrace=[]; const previousScroll=window.scrollTo; window.scrollTo=function(...args){window.__managerScrollTrace.push({action:'scrollTo',args,from:scrollY,at:performance.now()});return previousScroll.apply(this,args)}; window.addEventListener('scroll',()=>{window.__managerScrollTrace.push({action:'scroll',y:scrollY,at:performance.now()});if(window.__managerScrollTrace.length>40)window.__managerScrollTrace.shift()});""")
        page.goto(static_customer + '/ops.html')
        expect(page.locator('#dispatch-orders')).to_be_visible()
        if screen == 'orders':
            if width < 1000:
                page.locator('.dispatch-nav-toggle').click()
            page.locator('#navigation [data-page=orders]').click()
            expect(page.locator('body')).not_to_have_class(re.compile(r'\bdelivery-room\b'))
        customer_order = page.locator('#page-heading [data-action=customer-order]')
        expect(customer_order).to_be_visible()
        heading = page.locator('#page-heading h1').inner_text()
        # Give both real views enough document height to check restored position.
        page.add_style_tag(content='body::after{content:"";display:block;height:1800px}' )
        page.evaluate('window.scrollTo(0, 280)')
        page.wait_for_function('Math.abs(window.scrollY-280)<=1')
        customer_order.evaluate('(button)=>button.click()')
        form = page.locator('#staff-order-form')
        expect(form).to_be_visible()
        form.locator('input[name=phone]').fill('0531041449')
        form.locator('input[name=neighborhood]').fill('Test neighborhood')
        form.locator('input[name=address]').fill('Test entrance')
        form.locator('input[name=lat]').fill('28.4')
        form.locator('input[name=lng]').fill('45.9')
        form.locator('.staff-menu-photo img').click()
        expect(form.locator('[data-product-count=coffee]')).to_have_text('1')
        submit = form.locator('button[type=submit]')
        submit.click()
        expect(form.locator('#staff-review')).not_to_be_empty()
        submit.click()
        expect(form.locator('.form-error')).not_to_be_empty()
        expect(form).to_be_visible()
        expect(form.locator('input[name=phone]')).to_have_value('0531041449')
        expect(form.locator('input[name=address]')).to_have_value('Test entrance')
        expect(form.locator('[data-product-count=coffee]')).to_have_text('1')
        expect(page.locator('#page-heading h1')).to_have_text(heading)
        submit.click()
        expect(page.locator('#modal')).not_to_be_visible()
        expect(page.locator('#status')).to_contain_text('saved-synthetic-order')
        expect(page.locator('#page-heading h1')).to_have_text(heading)
        # Refresh restores focus on its next animation frame; an already-restored
        # scroll position must not turn this into a synchronous focus check.
        expect(customer_order).to_be_focused()
        try:
            page.wait_for_function('Math.abs(window.scrollY-280)<=1')
        except Exception:
            from pathlib import Path
            diagnostic = page.evaluate('({y:scrollY,trace:window.__managerScrollTrace,contentTop:document.querySelector("#content").getBoundingClientRect().top,active:document.activeElement?.outerHTML,alerts:document.querySelector("#operational-alerts")?.outerHTML})')
            evidence = Path('test-results')
            evidence.mkdir(exist_ok=True)
            page.screenshot(path=str(evidence/f'manager-return-{brand}-{screen}-{lang}-{width}.png'), full_page=True)
            print('Manager screen position:', json.dumps(diagnostic, ensure_ascii=False))
            raise
        saved = [item for item in writes if item[0] == '/api/orders/delivery-center']
        assert len(saved) == 2 and saved[0][1] == saved[1][1]
        assert saved[0][2] and saved[0][2] == saved[1][2]
        assert saved[-1][1]['brand_id'] == brand
        assert saved[-1][1]['customer']['phone'] == '+966531041449'
        assert not any(path == '/api/orders/saved-synthetic-order' for path in reads)
        assert not errors and not external
        browser.close()
