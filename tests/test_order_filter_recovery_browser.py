"""Date-filter recovery using the real staff UI and isolated database."""
import os
import pytest
from test_browser import _context, _owner_login, _staff, chromium_browser, live_app, synthetic_mail

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Isolated browser acceptance')]

@pytest.mark.parametrize('lang',['ar','en'])
def test_order_filters_remain_usable(live_app,chromium_browser,lang):
    from playwright.sync_api import expect
    ctx,page,errors=_context(chromium_browser,live_app)
    _owner_login(ctx,live_app,_staff('delivery_manager'))
    ctx.add_init_script("localStorage.setItem('kps_ops_lang',"+repr(lang)+");")
    try:
        page.goto(live_app+'/ops')
        page.locator('#navigation [data-page=orders]').click()
        form=page.locator('#filters-form')
        form.locator('[name=period]').select_option('custom')
        form.locator('button[type=submit]').click()
        expect(form.locator('[name=period]')).to_have_value('today')
        form.locator('[name=period]').select_option('custom')
        form.locator('[name=start]').fill('2026-10-13')
        form.locator('button[type=submit]').click()
        expect(form.locator('.filter-error')).to_be_visible()
        expect(form.locator('[name=start]')).to_have_value('2026-10-13')
        form.locator('[name=end]').fill('2026-10-12')
        form.locator('button[type=submit]').click()
        expect(form.locator('.filter-error')).to_contain_text('النهاية' if lang=='ar' else 'end date')
        form.locator('[name=end]').fill('2026-10-13')
        form.locator('button[type=submit]').click()
        expect(form.locator('.filter-error')).to_have_count(0)
        expect(form.locator('[name=period]')).to_have_value('custom')
        # A rejected query must never remove the controls needed to recover.
        page.route('**/api/orders?*',lambda route:route.fulfill(status=422,content_type='application/json',body='{"detail":{"code":"PERIOD_DATES_REQUIRED","message":"TEST date error"}}'))
        form.locator('button[type=submit]').click()
        expect(page.locator('#content')).to_contain_text('TEST date error')
        expect(form).to_be_visible()
        page.unroute('**/api/orders?*')
        form.locator('[name=period]').select_option('today')
        form.locator('button[type=submit]').click()
        expect(form.locator('[name=start]')).to_have_value('')
        expect(page.locator('#content')).not_to_contain_text('TEST date error')
        assert not errors
    finally:ctx.close()
