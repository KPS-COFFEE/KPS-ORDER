"""Mobile AR/EN home using real isolated application and synthetic staff."""
import os
from pathlib import Path
import pytest
from test_browser import (live_app,chromium_browser,synthetic_mail,_role_fixture,
                          _context,_staff_ui_login,_authenticated_post,_inspection_payload)
pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires isolated browser CI')]

@pytest.mark.parametrize('language',['ar','en'])
def test_mobile_driver_home(live_app,chromium_browser,language):
    from playwright.sync_api import expect
    users,branch,order=_role_fixture();contexts=[]
    try:
        mc,manager,_=_context(chromium_browser,live_app);contexts.append(mc)
        dc,driver,errors=_context(chromium_browser,live_app);contexts.append(dc)
        driver.set_viewport_size({'width':390,'height':844})
        driver.add_init_script(f"localStorage.setItem('kps_ops_lang','{language}')")
        _staff_ui_login(manager,users['manager']);_staff_ui_login(driver,users['driver'])
        mid=_authenticated_post(mc,live_app,'/api/shifts',{'kind':'manager','branch_ids':[branch]})['shift']['id']
        sid=_authenticated_post(dc,live_app,'/api/shifts',{'kind':'driver','branch_ids':[branch],'manager_shift_id':mid,'vehicle_inspection':_inspection_payload()})['shift']['id']
        _authenticated_post(mc,live_app,f'/api/orders/{order}/assign',{'driver_shift_id':sid})
        driver.locator('#navigation [data-page=tasks]').click()
        expect(driver.locator('.driver-home-summary')).to_be_visible()
        expect(driver.locator('.task-card')).to_have_count(1)
        expect(driver.locator('.driver-offer')).to_have_count(0)
        assert driver.locator('body').evaluate('(e)=>e.scrollWidth<=innerWidth')
        for text in ('إجمالي المبيعات','النقد المتوقع','Expected cash','Expected card'):
            assert text not in driver.locator('#content').inner_text()
        folder=Path('test-artifacts/driver-simple');folder.mkdir(parents=True,exist_ok=True)
        driver.screenshot(path=str(folder/f'mobile-{language}.png'),full_page=True)
        dc.set_offline(True);dc.set_offline(False);driver.reload()
        expect(driver.locator('.task-card')).to_have_count(1)
        assert errors==[]
    finally:
        for context in contexts:context.close()
