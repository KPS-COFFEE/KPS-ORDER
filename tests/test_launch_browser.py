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

from test_browser import ARTIFACTS, ROOT, _context, _owner_login, _staff, _approve_advertising, chromium_browser, live_app, synthetic_mail


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


def _evidence(page, name, full_page=True, **details):
    ARTIFACTS.mkdir(exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / (name + '.png')), full_page=full_page, animations='disabled')
    (ARTIFACTS / (name + '.json')).write_text(json.dumps({
        'synthetic': True, 'revision': os.getenv('RUNTIME_REVISION', 'local-unversioned'),
        'viewport': page.viewport_size, 'physical_device': False, **details}, ensure_ascii=False, indent=2))


def test_paused_strip_is_hidden_on_mobile_and_desktop_and_keeps_hero_logo_and_owner_text(live_app, chromium_browser):
    from playwright.sync_api import expect
    context, page, errors = _context(chromium_browser, live_app)
    headers = _owner_login(context, live_app, _staff())
    try:
        default = context.request.get(live_app + '/api/banners').json()['presentation']
        assert not default['ticker_enabled'] and default['ticker_lines'] == []
        lines = ['الأماكن والأوقات تجمّلها القهوة دائمًا', 'Speciality Coffee']
        _patch(context, live_app, headers, '/api/admin/settings', {
            'homepage_presentation': {'ticker_enabled': False, 'ticker_lines': lines, 'ticker_seconds': 28}})
        before = context.request.get(live_app + '/api/admin/config').json()
        page.goto('/')
        expect(page.locator('#brand-logo')).to_be_visible()
        expect(page.locator('#banner-area')).to_be_visible()
        for width in (390, 1280):
            page.set_viewport_size({'width': width, 'height': 900})
            expect(page.locator('#announcement-ticker')).not_to_be_visible()
            expect(page.locator('#announcement-track .ticker-text')).to_have_count(0)
            assert page.locator('.site-header').bounding_box()['y'] == 0
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            _evidence(page, f'text-strip-paused-{width}', full_page=False, strip_state='owner-paused; saved phrases retained')
        after = context.request.get(live_app + '/api/admin/config').json()
        assert after['settings']['homepage_presentation']['ticker_lines'] == lines
        assert after['settings']['homepage_presentation']['ticker_seconds'] == 28
        assert before['products'] == after['products'] and before['branches'] == after['branches']
        assert before['banners'] == after['banners']
        assert before['settings'].get('theme') == after['settings'].get('theme')
        assert not errors
    finally:
        context.close()


def test_editable_text_only_noto_ticker_above_header_left_to_right_and_dynamic_reduced_motion(live_app, chromium_browser):
    from playwright.sync_api import expect
    owner_context, _, _ = _context(chromium_browser, live_app)
    headers = _owner_login(owner_context, live_app, _staff())
    _patch(owner_context, live_app, headers, '/api/admin/settings', {'theme': {'profile': 'ORIGINAL_Light'}})
    # The owner has now paused the strip by default. This isolated test
    # explicitly enables it to retain text/motion acceptance coverage.
    _approve_advertising(owner_context, live_app, headers)
    _patch(owner_context, live_app, headers, '/api/admin/settings', {'homepage_presentation': {'ticker_enabled': True}})
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
        expect(page.locator('#announcement-ticker img')).to_have_count(0)
        expect(first_cycle.locator('.ticker-text').nth(0)).to_have_text('الأماكن والأوقات تجمّلها القهوة دائمًا')
        expect(first_cycle.locator('.ticker-text').nth(1)).to_have_text('Speciality Coffee')
        assert first_cycle.locator('.ticker-text').nth(0).get_attribute('dir') == 'auto'
        page.evaluate('() => document.fonts.ready')
        assert first_cycle.locator('.ticker-text').first.evaluate("el => getComputedStyle(el).fontFamily").startswith('"Noto Kufi Arabic"')
        assert page.evaluate('document.fonts.check(\'600 17px "Noto Kufi Arabic"\')')
        assert not page.evaluate("performance.getEntriesByType('resource').some(entry => entry.name.includes('kps-ticker-'))"), 'Text strip must not request lettering images.'
        for width in (390, 1280):
            page.set_viewport_size({'width': width, 'height': 900})
            layout = page.evaluate("""() => {
                const strip=document.querySelector('#announcement-ticker'),header=document.querySelector('.site-header');
                return {stripBottom:strip.getBoundingClientRect().bottom,headerTop:header.getBoundingClientRect().top,
                    background:getComputedStyle(strip).backgroundColor,overflow:document.documentElement.scrollWidth>innerWidth};
            }""")
            assert layout['stripBottom'] <= layout['headerTop'] + 1 and layout['background'] == 'rgb(233, 101, 22)'
            assert not layout['overflow']
            page.wait_for_function("document.querySelector('.ticker-cycle').getBoundingClientRect().width >= document.querySelector('.announcement-viewport').getBoundingClientRect().width")
            _evidence(page, f'text-only-strip-{width}', full_page=False, lettering='editable Noto Kufi Arabic text; zero strip images')
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
        expect(directions).to_have_attribute('href', 'https://www.google.com/maps/dir/?api=1&destination=28.42%2C45.92&destination_place_id=ChIJZdnl4lI51z8RDyi016qRLCA&travelmode=driving')
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


def test_owner_banner_city_speed_link_image_scope_and_ticker_edits_through_real_forms(live_app, chromium_browser):
    from playwright.sync_api import expect
    context, page, errors = _context(chromium_browser, live_app)
    headers = _owner_login(context, live_app, _staff())
    _approve_advertising(context, live_app, headers)
    try:
        page.goto('/ops')
        expect(page.locator('#app-shell')).to_be_visible()
        page.locator('#navigation [data-page=admin]').click()
        page.locator('[data-action=admin-tab][data-tab=banners]').click()
        page.locator('[data-action=create-banner]').click()
        form = page.locator('#banner-form')
        expect(form).to_be_visible()
        form.locator('input[name=title]').fill('إعلان اختبار لوحة المالك')
        form.locator('textarea[name=body]').fill('بيانات معزولة لا تظهر للعملاء الحقيقيين.')
        form.locator('input[name=button_label]').fill('تفاصيل الإعلان')
        form.locator('input[name=sort_order]').fill('2')
        form.locator('input[name=rotation_seconds]').fill('5.5')
        form.locator('select[name=cities]').select_option(label='حفر الباطن')
        form.locator('input[name=campaign_link]').fill('https://www.kps.sa/branches?test=owner-form')
        form.locator('input[name="banner:kps-branch-1"]').check()
        form.locator('input[name=active]').check()
        product = next(item for item in json.loads((ROOT / 'data/catalog.json').read_text())['products'] if item['id'] == 'kps-01')
        form.locator('input[name=desktop_image]').set_input_files(str(ROOT / 'public' / product['image_url'].lstrip('/')))
        with page.expect_response(lambda response: '/api/admin/banners' in response.url and response.request.method == 'POST') as created:
            form.locator('button[type=submit]').click()
        response = created.value
        assert response.status == 200, response.text()
        banner = response.json()
        assert banner['rotation_ms'] == 5500 and banner['cities'] == ['حفر الباطن']
        assert banner['branch_ids'] == ['kps-branch-1'] and banner['desktop_asset_id']
        assert banner['destination'] == {'kind': 'link', 'id': 'https://www.kps.sa/branches?test=owner-form'}
        expect(page.locator('#modal')).not_to_be_visible()
        page.locator(f'[data-action=edit-banner][data-id="{banner["id"]}"]').click()
        expect(form.locator('input[name=rotation_seconds]')).to_have_value('5.5')
        expect(form.locator('input[name=campaign_link]')).to_have_value(banner['destination']['id'])
        expect(form.locator('select[name=cities]')).to_have_values(['حفر الباطن'])
        expect(form.locator('input[name="banner:kps-branch-1"]')).to_be_checked()
        expect(form.locator('input[name="banner:kps-branch-9"]')).not_to_be_checked()
        expect(page.frame_locator('#desktop-preview iframe').locator('.campaign-banner picture img')).to_be_visible()
        form.locator('input[name=title]').scroll_into_view_if_needed()
        _evidence(page, 'launch-owner-banner-1280', full_page=False, owner_interface='real saved and reopened banner form', synthetic_campaign=banner['id'])
        page.locator('#modal-close').click()
        page.locator('[data-action=presentation-settings]').click()
        presentation = page.locator('#homepage-presentation-form')
        presentation.locator('textarea[name=ticker_lines]').fill('الأماكن والأوقات تجمّلها القهوة دائمًا\nSpeciality Coffee\nعبارة اختبار قابلة للتعديل')
        presentation.locator('input[name=ticker_seconds]').fill('34')
        presentation.locator('input[name=ticker_enabled]').check()
        assert presentation.evaluate('form => form.checkValidity()'), 'Ticker-only edits must save with an empty disabled literary phrase.'
        with page.expect_response(lambda response: '/api/admin/settings' in response.url and response.request.method == 'PATCH') as saved:
            presentation.locator('button[type=submit]').click()
        assert saved.value.status == 200, saved.value.text()
        expect(page.locator('#modal')).not_to_be_visible()
        page.locator('[data-action=presentation-settings]').click()
        expect(presentation.locator('input[name=ticker_seconds]')).to_have_value('34')
        expect(presentation.locator('textarea[name=ticker_lines]')).to_have_value('الأماكن والأوقات تجمّلها القهوة دائمًا\nSpeciality Coffee\nعبارة اختبار قابلة للتعديل')
        public = context.request.get(live_app + '/api/banners?branch_id=kps-branch-1').json()
        assert public['presentation']['ticker_seconds'] == 34
        assert public['presentation']['ticker_lines'][-1] == 'عبارة اختبار قابلة للتعديل'
        assert public['banners'][0]['id'] == banner['id']
        assert context.request.get(live_app + '/api/banners?branch_id=kps-branch-9').json()['banners'] == []
        # Reuse the same existing editor for text changes, ordering, deletion,
        # and hiding an empty strip; image banners are not modified.
        presentation.locator('textarea[name=ticker_lines]').fill('Speciality Coffee\nالقهوة تجمعنا دائمًا')
        presentation.locator('input[name=ticker_seconds]').fill('24')
        with page.expect_response(lambda response: '/api/admin/settings' in response.url and response.request.method == 'PATCH') as reordered:
            presentation.locator('button[type=submit]').click()
        assert reordered.value.status == 200, reordered.value.text()
        page.locator('[data-action=presentation-settings]').click()
        expect(presentation.locator('textarea[name=ticker_lines]')).to_have_value('Speciality Coffee\nالقهوة تجمعنا دائمًا')
        expect(presentation.locator('input[name=ticker_seconds]')).to_have_value('24')
        _evidence(page, 'text-only-strip-owner-1280', full_page=False, owner_interface='existing text editor, edited/reordered/deleted phrases and speed persisted')
        presentation.locator('input[name=ticker_enabled]').uncheck()
        presentation.locator('textarea[name=ticker_lines]').fill('')
        assert presentation.evaluate('form => form.checkValidity()'), 'An owner must be able to remove all phrases and stop the strip.'
        with page.expect_response(lambda response: '/api/admin/settings' in response.url and response.request.method == 'PATCH') as disabled:
            presentation.locator('button[type=submit]').click()
        assert disabled.value.status == 200, disabled.value.text()
        hidden=context.request.get(live_app + '/api/banners?branch_id=kps-branch-1').json()
        assert not hidden['presentation']['ticker_enabled'] and hidden['presentation']['ticker_lines'] == []
        assert hidden['banners'] == public['banners'], 'Text editing must preserve the independent image banner.'
        page.locator('[data-action=presentation-settings]').click()
        presentation.locator('input[name=ticker_enabled]').check()
        assert not presentation.evaluate('form => form.checkValidity()'), 'An enabled strip needs at least one phrase.'
        final_lines=['Speciality Coffee', 'القهوة تجمعنا دائمًا', '<strong>Editable text</strong>']
        presentation.locator('textarea[name=ticker_lines]').fill('\n'.join(final_lines))
        with page.expect_response(lambda response: '/api/admin/settings' in response.url and response.request.method == 'PATCH') as enabled:
            presentation.locator('button[type=submit]').click()
        assert enabled.value.status == 200, enabled.value.text()
        page.goto('/')
        first=page.locator('.ticker-cycle').first.locator('.ticker-text:not([aria-hidden=true])')
        expect(first).to_have_text(final_lines)
        expect(page.locator('#announcement-ticker img, #announcement-ticker strong')).to_have_count(0)
        assert page.locator('#announcement-track').evaluate("el => el.style.getPropertyValue('--ticker-duration')") == '24s'
        assert not errors
    finally:
        context.close()


def test_owner_service_visibility_delivery_only_and_pause_one_branch(live_app, chromium_browser):
    from playwright.sync_api import expect
    owner_context, owner_page, owner_errors=_context(chromium_browser,live_app)
    headers=_owner_login(owner_context,live_app,_staff('owner'))
    customer_context,customer,errors=_context(chromium_browser,live_app)
    try:
        _configure_branches(owner_context,live_app,headers)
        response=owner_context.request.post(
            live_app+'/api/admin/delivery-only',
            headers={**headers,'Idempotency-Key':'browser-delivery-only-'+uuid4().hex},
            data={'branch_ids':['kps-branch-1','kps-branch-9'],
                  'reason':'Synthetic browser acceptance: delivery only','confirm':True}
        )
        assert response.status==200,response.text()
        assert response.json()['saved'] is True
        customer.goto('/')
        customer.locator('#branch-select').select_option('kps-branch-1')
        expect(customer.locator('input[name=service][value=pickup]').locator('..')).to_be_hidden()
        expect(customer.locator('input[name=service][value=car]').locator('..')).to_be_hidden()
        expect(customer.locator('input[name=service][value=delivery]')).to_be_checked()
        assert not customer.locator('input[name=service][value=delivery]').is_disabled()

        # Owner pauses delivery on branch 1; branch 9 stays untouched.
        result=_patch(owner_context,live_app,headers,'/api/admin/branches/kps-branch-1',
                      {'settings':{'delivery_enabled':False},
                       'reason':'Owner test: stop branch-1 delivery only'})
        assert result['settings']['delivery_enabled'] is False
        customer.reload()
        customer.locator('#branch-select').select_option('kps-branch-1')
        for kind in ('pickup','car','delivery'):
            expect(customer.locator(f'input[name=service][value={kind}]').locator('..')).to_be_hidden()
        expect(customer.locator('#service-notice')).to_contain_text('موقوفة')
        customer.locator('#branch-select').select_option('kps-branch-9')
        expect(customer.locator('input[name=service][value=delivery]')).to_be_checked()

        # Global service shutdown hides options even without a chosen branch.
        _patch(owner_context,live_app,headers,'/api/admin/brand-services',
               {'services':{'pickup':False,'car_pickup':False,'delivery':True},
                'reason':'Synthetic visibility test'})
        customer.goto('/')
        for kind in ('pickup','car'):
            expect(customer.locator(f'input[name=service][value={kind}]').locator('..')).to_be_hidden()
        expect(customer.locator('input[name=service][value=delivery]')).to_be_checked()
        for width in (390,1280):
            customer.set_viewport_size({'width':width,'height':860})
            assert customer.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors and not owner_errors
    finally:
        customer_context.close()
        owner_context.close()
