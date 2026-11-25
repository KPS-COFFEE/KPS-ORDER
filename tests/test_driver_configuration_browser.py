import os
import pytest
from app import db
from test_browser import _context, _owner_login, _staff, chromium_browser, live_app, synthetic_mail

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit isolated browser run required')]

@pytest.mark.parametrize('language', ['ar','en'])
def test_driver_has_no_scope_or_availability_controls(live_app, chromium_browser, language):
    from playwright.sync_api import expect
    driver = _staff('driver')
    manager = _staff('delivery_manager')
    context, page, errors = _context(chromium_browser,live_app)
    _owner_login(context,live_app,driver)
    try:
        page.goto(live_app+'/ops')
        page.get_by_role('button',name='الشفتات والتقفيلات',exact=True).click()
        if language == 'en':
            page.get_by_role('button',name='English',exact=True).click()
        page.locator('[data-action="open-shift"]').click()
        expect(page.locator('#modal input[type="checkbox"]')).to_have_count(0)
        expect(page.locator('#modal')).to_contain_text('فروعك معتمدة' if language=='ar' else 'Your branches are approved')
        manager_shift, driver_shift = db.new_id('test_manager_'), db.new_id('test_driver_')
        with db.tx() as c:
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps',%s)",(manager_shift,manager['id'],db.json(['kps-branch-1'])))
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id) VALUES(%s,'driver',%s,'kps',%s,%s)",(driver_shift,driver['id'],db.json(['kps-branch-1']),manager_shift))
        page.reload()
        expect(page.get_by_role('heading',name='مهامي' if language=='ar' else 'My tasks',exact=True)).to_be_visible()
        expect(page.locator('[data-action="driver-availability"]')).to_have_count(0)
        assert not errors
    finally:
        context.close()
    manager_context, manager_page, manager_errors = _context(chromium_browser,live_app)
    _owner_login(manager_context,live_app,manager)
    try:
        manager_page.goto(live_app+'/ops')
        manager_page.get_by_role('button',name='السائقون',exact=True).click()
        control = manager_page.locator(f'[data-action="driver-availability"][data-id="{driver_shift}"]')
        expect(control).to_be_visible()
        control.click()
        expect(manager_page.locator(f'[data-action="driver-availability"][data-id="{driver_shift}"]')).to_have_attribute('data-available','true')
        with db.tx() as c:
            assert c.execute('SELECT available FROM shifts WHERE id=%s',(driver_shift,)).fetchone()['available'] is False
        assert not manager_errors
    finally:
        manager_context.close()
