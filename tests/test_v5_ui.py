"""Focused V5 UI acceptance: real Chromium + isolated PostgreSQL/HTTP.

All locations, accounts and configuration below are synthetic test-only data.
Browser viewport emulation is not a physical-phone acceptance claim.
"""
from __future__ import annotations

import json

import pytest

from app import db
from test_browser import (ARTIFACTS, _capture, _context, _staff, _staff_ui_login,
                          _submit_modal, chromium_browser, live_app, synthetic_mail)

pytestmark = [pytest.mark.postgres,
              pytest.mark.skipif(__import__('os').getenv('TEST_BROWSER') != '1',
                                 reason='Requires explicit real Chromium acceptance; no physical-device claim.')]


def _configured_branch(enabled=True):
    point = {'lat': 28.4001, 'lng': 45.9001}
    settings = {'active': enabled, 'ordering_verified': True,
                'pickup_enabled': enabled, 'car_enabled': enabled, 'delivery_enabled': enabled,
                'opening_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
                'delivery_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
                'location': point, 'location_verified': True,
                'coverage': {'type': 'radius', 'center': point, 'radius_meters': 5000},
                'payment_methods': ['cash', 'card'], 'delivery_fee_halalas': 0,
                'minimum_order_halalas': 0, 'map_url_verified': True,
                'map_url': 'https://www.google.com/maps?q=28.4001,45.9001'}
    with db.tx() as conn:
        conn.execute("UPDATE products SET settings=settings||'{\"source_verified\":true}'::jsonb")
        conn.execute('UPDATE branches SET settings=settings||%s WHERE id=%s',
                     (db.json(settings), 'kps-branch-1'))


def _assert_real_fonts(page):
    evidence = page.evaluate("""async () => {
        const counts=await Promise.all([400,600,700].map(async weight =>
            (await document.fonts.load(weight+' 18px "Noto Kufi Arabic"')).length));
        await document.fonts.ready;
        return {counts,family:getComputedStyle(document.body).fontFamily,
            synthesis:getComputedStyle(document.body).fontSynthesis};
    }""")
    assert evidence['counts'] == [1, 1, 1]
    assert 'Noto Kufi Arabic' in evidence['family']
    assert evidence['synthesis'] == 'none'
    return evidence


def test_v5_guest_zoom_focus_orientation_and_delivery_neighborhood(live_app, chromium_browser):
    from playwright.sync_api import expect
    _configured_branch()
    # This guest scenario covers deliberately unconfigured customer SMS.
    # Staff authentication in other scenarios still uses certified synthetic SMS.
    with db.tx() as conn:
        conn.execute("UPDATE identity_channels SET enabled=false WHERE brand_id='kps' AND channel='sms'")
    context, page, errors = _context(chromium_browser, live_app)
    try:
        page.goto('/')
        expect(page.locator('.product-card')).to_have_count(34)
        fonts = _assert_real_fonts(page)
        page.locator('#branch-select').select_option('kps-branch-1')
        page.wait_for_timeout(150)
        zoom = page.get_by_role('button', name='تكبير صورة اسبريسو', exact=True)
        if not zoom.count():
            zoom = page.locator('.image-zoom-button').first
        zoom.click()
        expect(page.locator('#image-dialog')).to_be_visible()
        page.wait_for_function("() => {const image=document.querySelector('#image-full'); return image.complete&&image.naturalWidth>0;}")
        _capture(page, 'TEST_ONLY_v5-customer-image-zoom')
        page.keyboard.press('Escape')
        expect(page.locator('#image-dialog')).not_to_be_visible()
        expect(zoom).to_be_focused()
        zoom.click()
        page.mouse.click(1, 1)
        expect(page.locator('#image-dialog')).not_to_be_visible()
        expect(zoom).to_be_focused()
        # Add an original source item without options or restricted cities.
        catalog = context.request.get(live_app + '/api/catalog?branch_id=kps-branch-1').json()['products']
        product = next(p for p in catalog if not p.get('option_groups') and not p.get('available_cities'))
        card = page.locator(f'.product-card[data-product-id="{product["id"]}"]')
        expect(card).to_have_count(1)
        card.locator('.product-content h3 button').click()
        expect(page.locator('#product-title')).to_have_text(product['name'])
        page.locator('#product-add').click()
        expect(page.locator('.cart-line')).to_have_count(1)
        page.locator('#customer-name').fill('عميل اختبار واجهة V5 فقط')
        page.locator('#customer-phone').fill('0500000099')
        page.locator('input[name=service][value=delivery]').check()
        page.locator('#address').fill('')
        page.locator('#manual-location-button').click()
        page.locator('#manual-location summary').click()
        page.locator('#latitude').fill('28.4002')
        page.locator('#longitude').fill('45.9002')
        page.locator('#save-location').click()
        expect(page.locator('#basket-error')).to_be_visible()
        expect(page.locator('#checkout-button')).to_be_disabled()
        expect(page.locator('#address')).to_have_value('')
        expect(page.locator('#neighborhood')).to_have_attribute('required','')
        expect(page.locator('#neighborhood')).to_have_value('')
        page.locator('#neighborhood').fill('حي اختبار فقط')
        expect(page.locator('#checkout-button')).to_be_enabled()
        _capture(page, 'TEST_ONLY_v5-delivery-basket-required-neighborhood-optional-description')
        # Changing orientation and a shorter viewport keep key controls reachable.
        for viewport in ({'width': 844, 'height': 390}, {'width': 390, 'height': 620}):
            page.set_viewport_size(viewport)
            page.locator('#customer-phone').focus()
            page.locator('#checkout-button').scroll_into_view_if_needed()
            page.screenshot(path=str(ARTIFACTS / f'TEST_ONLY_v5-checkout-reachability-{viewport["width"]}x{viewport["height"]}.png'))
            assert page.locator('#basket').evaluate('''basket =>
                basket.offsetHeight <= (visualViewport?.height||innerHeight)-40 ||
                getComputedStyle(basket).position === 'static'
            ''')
            expect(page.locator('#checkout-button')).to_be_in_viewport()
            expect(page.locator('#mobile-basket')).not_to_be_visible()
            assert page.locator('#checkout-button').evaluate('''button => {
                const r=button.getBoundingClientRect();
                return button.contains(document.elementFromPoint(r.left+r.width/2,r.top+r.height/2));
            }''')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert page.locator('.cart-line').count() == 1
        page.locator('#account-button').click()
        expect(page.locator('#guest-form')).to_be_visible()
        expect(page.locator('#email-login-button')).not_to_be_visible()
        expect(page.locator('#otp-request-form')).not_to_be_visible()
        page.locator('#guest-submit').click()
        expect(page.locator('#account-dialog')).not_to_be_visible()
        session = context.request.get(live_app + '/api/session', headers={'X-KPS-Context':'customer'}).json()
        assert session['user']['identity_kind'] == 'guest'
        assert session['user']['phone_sms_verified_at'] is None
        assert session['user']['phone_whatsapp_verified_at'] is None
        assert session['user']['email_verified_at'] is None
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) AS n FROM orders').fetchone()['n'] == 0
        assert not errors
        (ARTIFACTS / 'TEST_ONLY_v5-ui-font-and-orientation.json').write_text(json.dumps({
            'test_only': True, 'physical_device': False, 'fonts': fonts,
            'viewports': [360, 390, 768, 1280, 1440],
            'orientation_viewports': [{'width': 844, 'height': 390}, {'width': 390, 'height': 620}],
            'guest_phone_verification_claimed': False, 'orders_created': 0,
        }, ensure_ascii=False, indent=2) + '\n')
    finally:
        context.close()


def test_v5_owner_explicitly_clears_disabled_branch_configuration(live_app, chromium_browser):
    from playwright.sync_api import expect
    _configured_branch(enabled=False)
    owner = _staff()
    context, page, errors = _context(chromium_browser, live_app)
    try:
        _staff_ui_login(page, owner)
        _assert_real_fonts(page)
        page.locator('[data-action=operations-report]').click()
        expect(page.locator('#content')).to_contain_text('عمر الطلب الجاري تجاوز الحد')
        expect(page.locator('#delivery-performance')).to_contain_text('معيار المحافظة المحفوظ')
        page.locator('[data-page=admin]').click()
        page.locator('[data-action=edit-branch][data-id=kps-branch-1]').click()
        for name in ('active', 'pickup_enabled', 'car_enabled', 'delivery_enabled', 'cash', 'card'):
            page.locator(f'#modal-body [name={name}]').uncheck()
        for selector in ('#opening_hours-rows [data-action=remove-hours]', '#delivery_hours-rows [data-action=remove-hours]'):
            while page.locator(selector).count():
                page.locator(selector).first.click()
        page.locator('#modal-body [name=coverage_type]').select_option('')
        for name in ('lat', 'lng', 'map_url', 'fee', 'minimum'):
            page.locator(f'#modal-body [name={name}]').fill('')
        _capture(page, 'TEST_ONLY_v5-owner-explicit-clear-branch')
        _submit_modal(page)
        with db.tx() as conn:
            branch = conn.execute('SELECT settings FROM branches WHERE id=%s', ('kps-branch-1',)).fetchone()['settings']
            assert branch['opening_hours'] == [] and branch['delivery_hours'] == []
            assert branch['payment_methods'] == []
            assert branch['coverage'] is None and branch['location'] is None
            assert branch['map_url'] is None
            assert branch['location_verified'] is False and branch['map_url_verified'] is False
            assert branch['delivery_fee_halalas'] is None and branch['minimum_order_halalas'] is None
            assert all(branch[k] is False for k in ('active', 'pickup_enabled', 'car_enabled', 'delivery_enabled'))
        assert not errors
    finally:
        context.close()

