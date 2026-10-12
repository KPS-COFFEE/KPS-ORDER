"""Real Chromium/private owner journeys. No API mocking or production writes."""
import os
from pathlib import Path
import pytest
from app import db
from test_browser import live_app,synthetic_mail,chromium_browser,_context,_staff,_owner_login,_capture

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance')]


def test_menu_owner_mobile_desktop_catalog_branches_social_options_unavailable_and_handoff(live_app,chromium_browser):
    from playwright.sync_api import expect
    staff=_staff();context,page,errors=_context(chromium_browser,live_app)
    headers=_owner_login(context,live_app,staff)
    requests=[]
    page.on('request',lambda r:requests.append(r.url))
    try:
        page.goto(live_app+'/menu/preview')
        expect(page.locator('.product')).to_have_count(34)
        assert page.locator('#branch-select').count()==0
        assert page.evaluate("typeof navigator.geolocation.getCurrentPosition")=='function'  # No invocation or permission.
        assert not any('nearest-branches' in r for r in requests)
        assert context.request.get(live_app+'/menu/preview').headers['permissions-policy'].endswith('geolocation=()')
        _capture(page,'menu-home-private')
        page.locator('[data-product-id="kps-01"] button').click()
        expect(page.locator('#detail')).to_be_visible()
        _capture(page,'menu-product-private')
        page.locator('#close-detail').click()
        page.locator('[data-product-id="kps-33"] button').click()
        expect(page.locator('#detail-body')).to_contain_text('كلاسيك')
        expect(page.locator('#detail-body')).to_contain_text('بيكان')
        _capture(page,'menu-options-private');page.locator('#close-detail').click()
        changed=context.request.patch(live_app+'/api/admin/products/kps-01',headers={**headers,'Idempotency-Key':'menu-browser-global-pause'},data={'settings':{'enabled':False}})
        assert changed.ok,changed.text()
        page.reload();expect(page.locator('[data-product-id="kps-01"]')).to_have_class('product unavailable')
        _capture(page,'menu-unavailable-private')
        restored=context.request.patch(live_app+'/api/admin/products/kps-01',headers={**headers,'Idempotency-Key':'menu-browser-global-restore'},data={'settings':{'enabled':True}})
        assert restored.ok
        page.locator('#nav-branches').click()
        expect(page.locator('.branch')).to_have_count(20)
        page.locator('[data-branch-id="kps-branch-1"] summary').click()
        expect(page.locator('[data-branch-id="kps-branch-1"] a').last).to_have_attribute('href','https://g.page/r/CUb4BF-OvI-3EBM/review')
        _capture(page,'menu-branches-and-review-link-private')
        assert page.locator('.rating').count()==0
        # Approved cached rating is synthetic and explicitly dated, never presented as live.
        review='https://g.page/r/CUb4BF-OvI-3EBM/review'
        rate=context.request.patch(live_app+'/api/admin/branches/kps-branch-1',headers={**headers,'Idempotency-Key':'menu-browser-rating-fixture'},data={'settings':{'customer_information':{'google_rating':{'value':4.6,'review_count':10,'as_of':'2026-01-01T00:00:00+03:00','profile_url':review,'source':'approved_cached'}}}})
        assert rate.ok,rate.text()
        page.reload();page.locator('#nav-branches').click();page.locator('[data-branch-id="kps-branch-1"] summary').click()
        expect(page.locator('.rating')).to_contain_text('تقييم محفوظ')
        _capture(page,'menu-cached-rating-SYNTHETIC-TEST-ONLY')
        page.locator('#nav-contact').click();expect(page.locator('#contact-list a')).to_have_count(4)
        _capture(page,'menu-contact-private')
        page.locator('#language').click();expect(page.locator('html')).to_have_attribute('dir','ltr')
        expect(page.locator('#nav-contact')).to_have_text('Follow Us')
        _capture(page,'menu-contact-english-private')
        page.locator('#nav-menu').click();page.locator('#order-now').click()
        expect(page.locator('.product-card')).to_have_count(34)
        assert 'source=menu_qr' in page.url and 'menu_ref=' in page.url
        _capture(page,'menu-order-handoff-private')
        assert not errors,errors
    finally:context.close()


def test_menu_analytics_owner_truthful_preview_mode_responsive(live_app,chromium_browser):
    from playwright.sync_api import expect
    context,page,errors=_context(chromium_browser,live_app);staff=_staff();headers=_owner_login(context,live_app,staff)
    try:
        page.goto(live_app+'/menu/preview');expect(page.locator('.product')).to_have_count(34)
        page.locator('[data-product-id="kps-01"] button').click();page.locator('#close-detail').click()
        page.goto(live_app+'/menu/analytics');expect(page.locator('.stat')).to_have_count(10)
        expect(page.locator('#accuracy')).to_contain_text('لا نعرف فرع المسح')
        _capture(page,'menu-admin-analytics-private')
        page.locator('#mode').select_option('false');page.locator('#refresh').click()
        expect(page.locator('#mode-label')).to_contain_text('غير مفعل')
        assert page.locator('.stat strong').first.inner_text()=='0'
        page.locator('#language').click();expect(page.locator('html')).to_have_attribute('dir','ltr')
        _capture(page,'menu-admin-analytics-english-private')
        assert not errors,errors
    finally:context.close()
