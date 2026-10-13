"""Accounting UI uses a real authenticated account and scoped PostgreSQL data."""
import os
import pytest
from app import db
from test_browser import _capture,_context,_owner_login,_staff,chromium_browser,live_app,synthetic_mail
pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance')]

def test_accountant_reports_exports_case_and_bilingual_layout(live_app,chromium_browser):
    from playwright.sync_api import expect
    staff=_staff('admin');driver=_staff('driver')
    with db.tx() as c:c.execute('UPDATE users SET permissions=%s WHERE id=%s',(db.json(['accounting_reports','accounting_review']),staff['id']))
    context,page,errors=_context(chromium_browser,live_app);_owner_login(context,live_app,staff)
    try:
        page.goto(live_app+'/ops')
        expect(page.locator('#navigation [data-page=accounting]')).to_be_visible()
        expect(page.locator('#navigation button')).to_have_count(1)
        expect(page.locator('#ac-results table')).to_be_visible()
        page.locator('#ac-filters [name=group]').select_option('driver_daily')
        page.locator('#ac-filters button[type=submit]').click()
        expect(page.locator('#ac-results')).to_contain_text('العدد التراكمي للفترة')
        for fmt in ('pdf','xlsx'):
            with page.expect_download() as got:page.locator(f'[data-ac={fmt}]').click()
            download=got.value
            assert download.suggested_filename.endswith('.'+fmt)
            assert download.failure() is None
        page.locator('[data-ac=new-case]').click()
        page.locator('#modal [name=subject_id]').select_option(driver['id'])
        page.locator('#modal [name=category]').select_option('damage')
        page.locator('#modal [name=note]').fill('TEST ONLY vehicle damage accounting note')
        page.locator('#modal button[type=submit]').click()
        expect(page.locator('#modal')).to_contain_text('TEST ONLY vehicle damage')
        expect(page.locator('#modal [name=responsibility]')).to_have_count(0)
        page.keyboard.press('Escape')
        _capture(page,'TEST_ONLY_accounting_ar')
        page.get_by_role('button',name='English',exact=True).click()
        expect(page.locator('#page-heading')).to_contain_text('Accounting')
        expect(page.locator('#ac-results table')).to_be_visible()
        _capture(page,'TEST_ONLY_accounting_en')
        assert not errors
    finally:context.close()
