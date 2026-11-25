"""Shareable static training assets; no live API writes or operational data."""
import os
import pytest
from test_delivery_workspace_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')

@pytest.mark.parametrize('lang,width',[('ar',390),('en',390),('ar',1366),('en',1366)])
def test_static_role_training_complete_journey_and_guards(static_customer,lang,width):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':width,'height':1000})
        errors=[];forbidden=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('**/api/**',lambda route:(forbidden.append(route.request.url),route.abort()))
        page.goto(static_customer+'/assets/morning-training/index.html?lang='+lang)
        expect(page.locator('html')).to_have_attribute('dir','rtl' if lang=='ar' else 'ltr')
        page.locator('[data-action=approve]').click()
        page.locator('[data-role=manager]').click();page.locator('[data-action=openManager]').click()
        page.locator('[data-role=driver]').click();page.locator('#driver-proof').check();page.locator('[data-action=openDriver]').click()
        page.locator('[data-role=customer]').click();page.locator('#customer-branch').select_option('9');page.locator('[data-action=create]').click()
        page.locator('[data-role=preparer]').click();page.locator('[data-action=accept]').click()
        expect(page.locator('[data-action=prepare]')).to_be_disabled()
        page.locator('[data-role=driver]').click();expect(page.locator('[data-action=pickup]')).to_be_disabled();page.locator('[data-action=driverAccept]').click()
        page.locator('[data-role=manager]').click();page.locator('[data-action=prepare]').click()
        expect(page.locator('#training-toast')).to_contain_text('3')
        page.locator('#intervention-reason').fill('Branch response delayed')
        page.locator('[data-action=prepare]').click()
        page.locator('[data-role=preparer]').click();page.locator('[data-action=ready]').click()
        page.locator('[data-role=driver]').click();page.locator('#driver-proof').check();page.locator('[data-action=pickup]').click()
        page.locator('#driver-proof').check();page.locator('[data-action=deliver]').click()
        page.locator('[data-role=summary]').click()
        expect(page.locator('#role-panel')).to_contain_text('Practice journey completed' if lang=='en' else 'اكتملت الرحلة التجريبية')
        page.locator('[data-scenario=whatsapp]').click();page.locator('[data-action=create]').click()
        page.locator('[data-role=preparer]').click();expect(page.locator('[data-action=accept]')).to_be_enabled()
        page.locator('[data-action=accept]').click();expect(page.locator('[data-action=prepare]')).to_be_disabled()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert not errors and not forbidden
        browser.close()
