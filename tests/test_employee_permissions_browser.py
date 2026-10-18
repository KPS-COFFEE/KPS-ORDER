"""Real PostgreSQL/Chromium evidence for named capabilities and live withdrawal."""
import os
import pytest
from app import db
from test_browser import (
    ARTIFACTS, PASSWORD, _staff, _context, _owner_login,
    live_app, chromium_browser, synthetic_mail,
)

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires isolated PostgreSQL and actual Chromium')]


@pytest.mark.parametrize(('lang','width'),[('ar',390),('ar',1280),('en',390),('en',1280)])
def test_employee_permission_switches_are_named_saved_and_visible(live_app,chromium_browser,lang,width):
    from playwright.sync_api import expect
    owner=_staff();target=_staff('delivery_manager')
    context,page,errors=_context(chromium_browser,live_app)
    try:
        headers=_owner_login(context,live_app,owner)
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
        page.locator('#modal input[name=step_up_password]').fill(PASSWORD)
        page.locator('#modal button[type=submit]').click()
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
        headers=_owner_login(owner_context,live_app,owner)
        _owner_login(driver_context,live_app,driver)
        page.goto(live_app+'/ops',wait_until='networkidle')
        expect(page.locator('#navigation [data-page=maintenance]')).to_be_visible()
        directory=owner_context.request.get(live_app+'/api/identity/staff').json()
        current=next(u for u in directory['users'] if u['id']==driver['id'])
        owner_context.request.post(live_app+'/api/identity/step-up',headers=headers,data={'password':PASSWORD})
        enabled=[p for p in current['effective_permissions'] if p!='vehicle_faults_report']
        saved=owner_context.request.put(live_app+f'/api/identity/staff/{driver["id"]}/permissions',headers=headers,data={
            'enabled_permissions':enabled,'expected_permissions':current['effective_permissions'],'reason':'Owner reviewed duties'})
        assert saved.ok,saved.text()
        expect(page.locator('#navigation [data-page=maintenance]')).to_have_count(0,timeout=25000)
        expect(page.locator('#app-shell')).to_be_visible()
        assert driver_context.request.get(live_app+'/api/vehicle-maintenance/options').status==403
        assert not errors,errors
    finally:owner_context.close();driver_context.close()
