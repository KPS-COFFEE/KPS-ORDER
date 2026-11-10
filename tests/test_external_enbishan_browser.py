import os
import pytest
from app import db
from test_browser import _staff, _context, _owner_login, chromium_browser
from tests.browser_sms_helpers import sms_browser_app
pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')]

@pytest.mark.parametrize('lang,width',[('ar',390),('en',1280)])
def test_manager_records_external_summary(sms_browser_app,chromium_browser,lang,width):
    from playwright.sync_api import expect
    manager=_staff('delivery_manager');driver=_staff('driver')
    with db.tx() as c:
        c.execute("UPDATE branches SET city='حفر الباطن' WHERE id='kps-branch-1'")
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES('external-manager','manager',%s,'kps','[\"kps-branch-1\"]')",(manager['id'],))
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id) VALUES('external-driver','driver',%s,'kps','[\"kps-branch-1\"]','external-manager')",(driver['id'],))
    context,page,errors=_context(chromium_browser,sms_browser_app)
    try:
        context.add_init_script(f"localStorage.setItem('kps_ops_lang','{lang}')")
        page.set_viewport_size({'width':width,'height':950})
        _owner_login(context,sms_browser_app,manager)
        page.goto(sms_browser_app+'/ops',wait_until='networkidle')
        if width <= 650:
            toggle=page.locator('.dispatch-nav-toggle')
            expect(toggle).to_be_visible()
            expect(toggle).to_have_attribute('aria-expanded','false')
            toggle.click()
            expect(toggle).to_have_attribute('aria-expanded','true')
            expect(page.locator('#navigation')).to_be_visible()
        page.locator('#navigation [data-page=shifts]').click()
        page.locator('#content [data-action=shift-detail][data-id=external-driver]').click()
        page.locator('#modal [data-action=external-enbishan]').click()
        modal=page.locator('#modal')
        modal.locator('[name=order_count]').fill('3')
        modal.locator('[name=external_cash]').fill('15')
        modal.locator('[name=external_card]').fill('25')
        modal.locator('[name=reason]').fill('Sapaad test summary')
        with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/external-enbishan')) as response:
            modal.locator('button[type=submit]').click()
        assert response.value.status==200,response.value.text()
        expect(modal.locator('[data-action=external-enbishan]')).to_be_visible()
        with db.tx() as c:
            value=c.execute("SELECT external_enbishan FROM shifts WHERE id='external-driver'").fetchone()['external_enbishan']
        assert value['order_count']==3 and value['total_halalas']==4000
        assert not errors
    finally:context.close()

