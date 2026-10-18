"""Real PostgreSQL/Chromium evidence for named capabilities and live withdrawal."""
import os
import pytest
from app import db
from test_browser import (
    ARTIFACTS, _staff, _context, _owner_login, chromium_browser,
)
from tests.browser_sms_helpers import sms_browser_app as live_app, complete_owner_sms_dialog

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires isolated PostgreSQL and actual Chromium')]


@pytest.mark.parametrize(('lang','width'),[('ar',390),('ar',768),('ar',1280),('en',390),('en',768),('en',1280)])
def test_employee_permission_switches_are_named_saved_and_visible(live_app,chromium_browser,lang,width):
    from playwright.sync_api import expect
    owner=_staff();target=_staff('delivery_manager')
    context,page,errors=_context(chromium_browser,live_app)
    try:
        _owner_login(context,live_app,owner)
        page.add_init_script(f"localStorage.setItem('kps_ops_lang','{lang}')")
        page.set_viewport_size({'width':width,'height':900})
        page.goto(live_app+'/ops',wait_until='networkidle')
        page.locator('#navigation [data-page=admin]').click()
        page.locator('[data-action=admin-tab][data-tab=users]').click()
        button=page.locator(f'[data-action=staff-permissions][data-id="{target["id"]}"]')
        expect(button).to_be_visible()
        button.click()
        picker=page.locator('#modal .employee-permission-picker')
        picker.locator('details').filter(has=page.locator('input[name="employee_permission:shifts_delegate_close"]')).locator('summary').click()
        toggle=picker.locator('input[name="employee_permission:shifts_delegate_close"]')
        expect(toggle).to_be_checked()
        toggle.uncheck()
        expect(toggle).not_to_be_checked()
        page.screenshot(path=str(ARTIFACTS/f'employee-permissions-{lang}-{width}.png'),full_page=True)
        expect(page.locator('#modal input[type=password]')).to_have_count(0)
        page.locator('#modal button[type=submit]').click()
        complete_owner_sms_dialog(page,owner)
        expect(page.locator('#modal')).not_to_be_visible()
        with db.tx() as conn:
            row=conn.execute('SELECT * FROM users WHERE id=%s',(target['id'],)).fetchone()
            assert row['permission_denials']==['shifts_delegate_close']
        button.click()
        expect(page.locator('#modal input[name="employee_permission:shifts_delegate_close"]')).not_to_be_checked()
        assert not errors,errors
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    finally:context.close()


def test_open_driver_browser_hides_withdrawn_page_without_signout(live_app,chromium_browser):
    from playwright.sync_api import expect
    owner=_staff();driver=_staff('driver')
    owner_context,owner_page,owner_errors=_context(chromium_browser,live_app)
    driver_context,page,errors=_context(chromium_browser,live_app)
    try:
        _owner_login(owner_context,live_app,owner)
        _owner_login(driver_context,live_app,driver)
        page.goto(live_app+'/ops',wait_until='networkidle')
        expect(page.locator('#navigation [data-page=maintenance]')).to_be_visible()
        owner_page.goto(live_app+'/ops',wait_until='networkidle')
        owner_page.locator('#navigation [data-page=admin]').click()
        owner_page.locator('[data-action=admin-tab][data-tab=users]').click()
        owner_page.locator(f'[data-action=staff-permissions][data-id="{driver["id"]}"]').click()
        picker=owner_page.locator('#modal .employee-permission-picker')
        toggle=picker.locator('input[name="employee_permission:vehicle_faults_report"]')
        toggle.locator('xpath=ancestor::details').locator('summary').click()
        toggle.uncheck()
        owner_page.locator('#modal [name=reason]').fill('Owner reviewed duties')
        owner_page.locator('#modal button[type=submit]').click()
        complete_owner_sms_dialog(owner_page,owner)
        expect(owner_page.locator('#modal')).not_to_be_visible()
        with db.tx() as conn:
            row=conn.execute('SELECT * FROM users WHERE id=%s',(driver['id'],)).fetchone()
            assert set(row['permission_denials'])=={'vehicle_faults_report'}
            assert row['role']==driver['role'] and row['branch_ids']==driver['branch_ids']
        expect(page.locator('#navigation [data-page=maintenance]')).to_have_count(0,timeout=25000)
        expect(page.locator('#app-shell')).to_be_visible()
        assert driver_context.request.get(live_app+'/api/vehicle-maintenance/options').status==403
        assert not errors and not owner_errors,(errors,owner_errors)
    finally:owner_context.close();driver_context.close()
