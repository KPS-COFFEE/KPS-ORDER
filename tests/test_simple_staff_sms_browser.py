"""Focused AR/EN staff UX with actual sessions and synthetic external SMS only."""
import os
import pytest
from app import db
from test_browser import _staff, _context, _owner_login, chromium_browser, ARTIFACTS
from tests.browser_sms_helpers import sms_browser_app as live_app, complete_owner_sms_dialog

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires explicit isolated PostgreSQL and Chromium')]


@pytest.mark.parametrize(('language','width'),[('ar',390),('en',1280)])
def test_staff_sms_form_catalog_and_cross_role_printer_access(live_app,chromium_browser,language,width):
    from playwright.sync_api import expect
    owner=_staff();driver=_staff('driver')
    context,page,errors=_context(chromium_browser,live_app)
    driver_context=None
    try:
        _owner_login(context,live_app,owner)
        page.add_init_script(f"localStorage.setItem('kps_ops_lang','{language}')")
        page.set_viewport_size({'width':width,'height':900})
        page.goto(live_app+'/ops',wait_until='networkidle')
        page.locator('#navigation [data-page=admin]').click()
        page.locator('[data-action=admin-tab][data-tab=users]').click()
        page.locator(f'[data-action=staff-permissions][data-id="{driver["id"]}"]').click()
        modal=page.locator('#modal')
        expect(modal.locator('input[type=password]')).to_have_count(0)
        printer=modal.locator('input[name="employee_permission:branch_printers_manage"]')
        expect(printer).to_be_enabled()
        printer.locator('xpath=ancestor::details').locator('summary').click()
        printer.check()
        expect(modal.locator('input[name="employee_permission:accounting_review"]')).to_be_disabled()
        modal.locator('[name=reason]').fill('Owner assigns printer administration while retaining driver role')
        modal.locator('button[type=submit]').click()
        complete_owner_sms_dialog(page,owner)
        expect(modal).not_to_be_visible()
        with db.tx() as conn:
            row=conn.execute('SELECT * FROM users WHERE id=%s',(driver['id'],)).fetchone()
            assert row['role']=='driver' and row['branch_ids']==['kps-branch-1']
            assert row['extra_permissions']==['branch_printers_manage']
        # Approved full-text rows can grow vertically; no clipped names, badges or actions.
        for listing_width in (390,768,1280):
            page.set_viewport_size({'width':listing_width,'height':900})
            layout=page.locator('.staff-directory').evaluate('''root=>{
                const cells=[...root.querySelectorAll('td')];
                const visible=e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
                const name=root.querySelector('tbody td:first-child'),header=root.querySelector('thead th:first-child');
                return {nameReadableWidth:Math.min(name.clientWidth,header.clientWidth)>=220,readable:cells.filter(visible).every(e=>{
                    const s=getComputedStyle(e);
                    return s.whiteSpace==='normal'&&s.textOverflow!=='ellipsis'&&
                        e.scrollWidth<=e.clientWidth+1&&e.scrollHeight<=e.clientHeight+1;
                }),pageOverflow:document.documentElement.scrollWidth>innerWidth};
            }''')
            assert layout=={'nameReadableWidth':True,'readable':True,'pageOverflow':False}
            page.screenshot(path=str(ARTIFACTS/f'focused-staff-table-{language}-{listing_width}.png'),full_page=True)
        page.set_viewport_size({'width':width,'height':900})
        page.locator('[data-action=invite-staff]').click()
        form=modal.locator('form')
        expect(form.locator('input[type=password]')).to_have_count(0)
        assert form.locator('input,select').first.get_attribute('name')=='role'
        assert form.locator('[name=email]').get_attribute('required') is None
        expect(form.locator('[name=email]')).not_to_be_visible()
        expect(form.locator('[name=employee_ref]')).not_to_be_visible()
        form.locator('[name=display_name]').fill('Synthetic Simple Staff '+language)
        form.locator('[name=phone]').fill('+96650090211'+('1' if language=='ar' else '2'))
        # Branch choices remain explicit even with a role-first form.
        branch=form.locator('input[name="scope:kps-branch-1"]')
        branch.locator('xpath=ancestor::details').locator('summary').click()
        branch.check()
        ARTIFACTS.mkdir(exist_ok=True)
        page.screenshot(path=str(ARTIFACTS/f'focused-staff-{language}-{width}.png'),full_page=True)
        form.locator('button[type=submit]').click()
        complete_owner_sms_dialog(page,owner)
        expect(modal).not_to_be_visible()
        with db.tx() as conn:
            new=conn.execute('SELECT * FROM users WHERE display_name=%s',('Synthetic Simple Staff '+language,)).fetchone()
            assert new['role']=='driver' and new['active'] and new['email'] is None
            assert new['phone_verified_at'] is None and new['phone_sms_verified_at'] is None
        filters=page.locator('#staff-filters')
        filters.locator('[name=query]').fill('Synthetic Simple Staff '+language)
        filters.locator('button[type=submit]').click()
        expect(page.locator('.staff-directory')).to_contain_text('Synthetic Simple Staff '+language)
        expect(page.locator(f'[data-action=staff-permissions][data-id="{driver["id"]}"]')).to_have_count(0)
        driver_context,driver_page,driver_errors=_context(chromium_browser,live_app)
        _owner_login(driver_context,live_app,driver)
        driver_page.add_init_script(f"localStorage.setItem('kps_ops_lang','{language}')")
        driver_page.set_viewport_size({'width':width,'height':900})
        driver_page.goto(live_app+'/ops',wait_until='networkidle')
        expect(driver_page.locator('#navigation [data-page=printers]')).to_be_visible()
        driver_page.locator('#navigation [data-page=printers]').click()
        expect(driver_page.locator('#page-heading h1')).to_have_text('إدارة الطابعات' if language=='ar' else 'Printer management')
        expect(driver_page.locator('#content [data-action=branch-printers]')).to_be_visible()
        assert not errors and not driver_errors
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    finally:
        if driver_context:driver_context.close()
        context.close()
