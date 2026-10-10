"""Real Chromium and HTTP launch acceptance, using isolated synthetic data.

Browser permission changes are real Chromium settings. External Maps
navigation is captured locally, without sending test fixture pins to Google.
Application API responses are never intercepted or mocked.
"""
from __future__ import annotations

import json
import os
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest

from test_browser import ARTIFACTS, _context, _owner_login, _staff, chromium_browser, live_app, synthetic_mail


pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.getenv('TEST_BROWSER') != '1', reason='Launch browser acceptance requires explicit TEST_BROWSER=1.')]


def _patch(context, base, headers, path, body):
    response = context.request.patch(base + path, headers={**headers, 'Idempotency-Key': 'launch-ui-' + uuid4().hex}, data=body)
    assert response.status == 200, response.text()
    return response.json()


def _branch_settings(lat, lng, **changes):
    # These are explicitly synthetic test pins and hours in a UUID schema.
    return {'active': True, 'ordering_verified': True, 'pickup_enabled': True, 'car_enabled': True,
            'delivery_enabled': True, 'payment_methods': ['cash', 'card'], 'location_verified': True,
            'location': {'lat': lat, 'lng': lng},
            'opening_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
            'delivery_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
            'coverage': {'type': 'radius', 'center': {'lat': 28.4, 'lng': 45.9}, 'radius_meters': 10000},
            'delivery_fee_halalas': 500, **changes}


def _configure_branches(context, base, headers):
    settings = {
        'kps-branch-1': _branch_settings(28.4, 45.9),
        'kps-branch-9': _branch_settings(28.42, 45.92),
        'kps-branch-2': _branch_settings(28.40001, 45.90001, active=False),
        'kps-branch-3': _branch_settings(28.41, 45.91),
        'kps-branch-5': _branch_settings(28.43, 45.93, coverage={
            'type': 'radius', 'center': {'lat': 29.4, 'lng': 46.9}, 'radius_meters': 50}),
    }
    for identifier, configuration in settings.items():
        _patch(context, base, headers, '/api/admin/branches/' + identifier, {'settings': configuration})
    _patch(context, base, headers, '/api/admin/products/kps-01', {'settings': {'unavailable_branch_ids': ['kps-branch-3']}})


def _evidence(page, name, **details):
    ARTIFACTS.mkdir(exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / (name + '.png')), full_page=True, animations='disabled')
    (ARTIFACTS / (name + '.json')).write_text(json.dumps({
        'synthetic': True, 'revision': os.getenv('RUNTIME_REVISION', 'local-unversioned'),
        'viewport': page.viewport_size, 'physical_device': False, **details}, ensure_ascii=False, indent=2))


def test_approved_original_lettering_orange_ticker_above_header_left_to_right_and_dynamic_reduced_motion(live_app, chromium_browser):
    from playwright.sync_api import expect
    owner_context, _, _ = _context(chromium_browser, live_app)
    headers = _owner_login(owner_context, live_app, _staff())
    _patch(owner_context, live_app, headers, '/api/admin/settings', {'theme': {'profile': 'ORIGINAL_Light'}})
    for number in (1, 2):
        response = owner_context.request.post(live_app + '/api/admin/banners', headers={
            **headers, 'Idempotency-Key': 'launch-banner-' + uuid4().hex},
            data={'title': f'إعلان اختبار واضح {number}', 'body': 'بيانات معزولة لا تمثل إعلانًا للعملاء.',
                  'active': True, 'sort_order': number, 'rotation_ms': 4000})
        assert response.status == 200, response.text()
    context, page, errors = _context(chromium_browser, live_app)
    try:
        page.goto('/')
        expect(page.locator('#announcement-ticker')).to_be_visible()
        expect(page.locator('#brand-logo')).to_be_visible()
        expect(page.locator('.banner-mark')).to_have_count(0)
        expect(page.locator('.banner-dot')).to_have_count(2)
        first_cycle = page.locator('.ticker-cycle').first
        expect(first_cycle.locator('img[alt="الأماكن والأوقات تجمّلها القهوة دائمًا"]')).to_have_attribute('src', '/assets/kps-ticker-arabic-original.jpeg')
        expect(first_cycle.locator('img[alt="Speciality Coffee"]')).to_have_attribute('src', '/assets/kps-ticker-english-original.jpeg')
        page.wait_for_function("[...document.querySelectorAll('.ticker-art')].every(img => img.complete && img.naturalWidth > 0)")
        for width in (390, 1280):
            page.set_viewport_size({'width': width, 'height': 900})
            layout = page.evaluate("""() => {
                const strip=document.querySelector('#announcement-ticker'),header=document.querySelector('.site-header');
                return {stripBottom:strip.getBoundingClientRect().bottom,headerTop:header.getBoundingClientRect().top,
                    background:getComputedStyle(strip).backgroundColor,overflow:document.documentElement.scrollWidth>innerWidth};
            }""")
            assert layout['stripBottom'] <= layout['headerTop'] + 1 and layout['background'] == 'rgb(233, 101, 22)'
            assert not layout['overflow']
            _evidence(page, f'launch-orange-ticker-{width}', approved_lettering='original supplied JPEG bytes')
        page.mouse.move(5, 890)
        direction = page.locator('#announcement-track').evaluate("""element => {
            const animation=element.getAnimations()[0];if(!animation)return null;
            const duration=animation.effect.getTiming().duration;animation.pause();animation.currentTime=duration*.25;
            const first=new DOMMatrixReadOnly(getComputedStyle(element).transform).m41;
            animation.currentTime=duration*.75;
            const last=new DOMMatrixReadOnly(getComputedStyle(element).transform).m41;animation.play();return {first,last};
        }""")
        assert direction and direction['last'] > direction['first'], direction
        page.locator('#ticker-pause').click()
        expect(page.locator('#ticker-pause')).to_have_attribute('aria-pressed', 'true')
        assert 'is-paused' in page.locator('#announcement-track').get_attribute('class')
        page.locator('#ticker-pause').click()
        page.emulate_media(reduced_motion='reduce')
        expect(page.locator('#ticker-pause')).to_be_disabled()
        assert page.locator('#announcement-track').evaluate('el => getComputedStyle(el).animationName') == 'none'
        page.locator('.banner-dot').last.click()
        expect(page.locator('#banner-content')).to_contain_text('إعلان اختبار واضح 2')
        page.locator('#branch-select').focus()
        page.mouse.move(5, 890)
        page.wait_for_timeout(4400)
        expect(page.locator('#banner-content')).to_contain_text('إعلان اختبار واضح 2')
        page.emulate_media(reduced_motion='no-preference')
        expect(page.locator('#ticker-pause')).to_be_enabled()
        assert page.locator('#announcement-track').evaluate('el => getComputedStyle(el).animationName') != 'none'
        expect(page.locator('#banner-content')).to_contain_text('إعلان اختبار واضح 1', timeout=6000)
        assert not errors
    finally:
        context.close()
        owner_context.close()


def test_customer_geolocation_ranks_eligible_branches_filters_cart_delivery_and_opens_selected_maps(live_app, chromium_browser):
    from playwright.sync_api import expect
    owner_context, _, _ = _context(chromium_browser, live_app)
    headers = _owner_login(owner_context, live_app, _staff())
    _configure_branches(owner_context, live_app, headers)
    context, page, errors = _context(chromium_browser, live_app)
    try:
        context.grant_permissions(['geolocation'], origin=live_app)
        context.set_geolocation({'latitude': 28.4001, 'longitude': 45.9001, 'accuracy': 5})
        # Capture only the external navigation destination. App HTTP is real.
        context.route('https://www.google.com/maps/dir/**', lambda route: route.fulfill(
            status=200, content_type='text/html', body='<p>Google Maps destination captured for isolated test.</p>'))
        page.goto('/')
        expect(page.locator('#branch-select option')).to_have_count(21)
        expect(page.locator('#locate-button')).to_be_visible()
        with page.expect_response(lambda response: '/api/branches/nearest' in response.url and response.status == 200) as nearest:
            page.locator('#locate-button').click()
        ranked = nearest.value.json()['branches']
        assert ranked[0]['id'] == 'kps-branch-1'
        assert 'kps-branch-2' not in [branch['id'] for branch in ranked]
        expect(page.locator('.nearest-branch-card').first).to_have_attribute('data-branch-id', 'kps-branch-1')
        expect(page.locator('#nearest-state')).to_contain_text('تقريبية')
        expect(page.locator('#branch-select')).to_have_value('')  # Never changes the order silently.
        page.locator('.nearest-branch-card[data-branch-id="kps-branch-1"] button').click()
        expect(page.locator('#branch-select')).to_have_value('kps-branch-1')
        directions = page.locator('#branch-directions')
        expect(directions).to_be_visible()
        expect(directions).to_have_text('الاتجاهات إلى الفرع')
        assert parse_qs(urlsplit(directions.get_attribute('href')).query)['destination'] == ['28.4,45.9']
        with page.expect_popup() as popup:
            directions.click()
        opened = popup.value
        opened.wait_for_load_state()
        assert parse_qs(urlsplit(opened.url).query)['destination'] == ['28.4,45.9']
        opened.close()
        page.locator('input[name=service][value=car]').check()
        expect(page.locator('#car-fields')).to_be_visible()
        expect(directions).to_be_visible()
        page.locator('#branch-select').select_option('kps-branch-9')
        expect(directions).to_have_attribute('href', 'https://www.google.com/maps/dir/?api=1&destination=28.42%2C45.92&travelmode=driving')
        # Required location does not force selection or hide manual controls.
        page.locator('#branch-select').select_option('kps-branch-1')
        page.locator('input[name=service][value=pickup]').check()
        page.locator('.product-card[data-product-id="kps-01"] .product-image-button').click()
        page.locator('#product-add').click()
        expect(page.locator('#product-dialog')).not_to_be_visible()
        expect(page.locator('#nearest-branches .nearest-branch-card[data-branch-id="kps-branch-3"]')).to_have_count(0)
        with page.expect_response(lambda response: '/api/branches/nearest' in response.url and response.status == 200) as delivery:
            page.locator('input[name=service][value=delivery]').check()
        result = delivery.value.json()
        assert {branch['id'] for branch in result['branches']} == {'kps-branch-1', 'kps-branch-9'}
        exclusions = {branch['id']: branch['code'] for branch in result['excluded']}
        assert exclusions['kps-branch-5'] == 'OUTSIDE_COVERAGE'
        assert exclusions['kps-branch-3'] == 'PRODUCT_UNAVAILABLE'
        assert exclusions['kps-branch-2'] == 'BRANCH_DISABLED'
        page.set_viewport_size({'width': 390, 'height': 900})
        _evidence(page, 'launch-nearest-delivery-390', location='synthetic fixture pin', qualified=['kps-branch-1', 'kps-branch-9'])
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert not errors
    finally:
        context.close()
        owner_context.close()


def test_denied_geolocation_retains_city_branch_manual_choice_and_car_directions(live_app, chromium_browser):
    from playwright.sync_api import expect
    owner_context, _, _ = _context(chromium_browser, live_app)
    headers = _owner_login(owner_context, live_app, _staff())
    _configure_branches(owner_context, live_app, headers)
    context, page, errors = _context(chromium_browser, live_app)
    try:
        # A real browser denied permission, without replacing application API.
        # Chromium grants the explicit list and denies other permissions for
        # this origin and browser context. An empty list denies geolocation.
        context.grant_permissions([], origin=live_app)
        page.set_viewport_size({'width': 390, 'height': 900})
        page.goto('/')
        assert page.evaluate("navigator.permissions.query({name:'geolocation'}).then(permission => permission.state)") == 'denied'
        expect(page.locator('#branch-select option')).to_have_count(21)
        page.locator('#locate-button').click()
        expect(page.locator('#location-state')).to_contain_text('يدويًا')
        expect(page.locator('#city-select')).to_be_enabled()
        city_value = page.locator('#city-select option').evaluate_all("options => options.find(option => option.textContent.includes('حفر')).value")
        page.locator('#city-select').select_option(city_value)
        page.locator('#branch-select').select_option('kps-branch-9')
        expect(page.locator('#branch-select')).to_have_value('kps-branch-9')
        expect(page.locator('#branch-directions')).to_be_visible()
        assert parse_qs(urlsplit(page.locator('#branch-directions').get_attribute('href')).query)['destination'] == ['28.42,45.92']
        page.locator('input[name=service][value=car]').check()
        expect(page.locator('#branch-directions')).to_be_visible()
        expect(page.locator('#nearest-panel')).not_to_be_visible()
        _evidence(page, 'launch-location-denied-manual-390', geolocation='real Chromium permission denied', selected_branch='kps-branch-9')
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert not errors
    finally:
        context.close()
        owner_context.close()
