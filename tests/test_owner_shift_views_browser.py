"""Isolated real owner navigation in Arabic/English, desktop/mobile."""
import os
from uuid import uuid4
import pytest
from app import db
from test_browser import _context,_owner_login,_staff,chromium_browser,live_app,synthetic_mail

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Isolated browser acceptance')]

@pytest.mark.parametrize('lang,width',[('ar',390),('en',1280)])
def test_owner_current_review_history_and_blank_dates(live_app,chromium_browser,lang,width):
    from playwright.sync_api import expect
    owner=_staff();manager=_staff('delivery_manager')
    ids={status:'TEST-'+uuid4().hex for status in ['open','submitted','closed']}
    with db.tx() as c:
        for status,sid in ids.items():
            user=manager if status=='open' else _staff('delivery_manager')
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,delivery_operational_date) VALUES(%s,'manager',%s,'kps','[\"kps-branch-1\"]',%s,'2026-10-13T18:00:00Z','2026-10-13')",(sid,user['id'],status))
    ctx,page,errors=_context(chromium_browser,live_app);_owner_login(ctx,live_app,owner)
    ctx.add_init_script("localStorage.setItem('kps_ops_lang',"+repr(lang)+");")
    try:
        page.set_viewport_size({'width':width,'height':900});page.goto(live_app+'/ops')
        expect(page.locator('#owner-dashboard')).to_be_visible()
        shift_navigation=page.locator('#navigation [data-page=shifts]')
        if not shift_navigation.is_visible():
            toggle=page.locator('.dispatch-nav-toggle')
            expect(toggle).to_be_visible()
            toggle.click()
            expect(toggle).to_have_attribute('aria-expanded','true')
            expect(page.locator('#navigation')).to_be_visible()
        shift_navigation.click()
        card=lambda status:page.locator('#content [data-action=shift-detail][data-id="'+ids[status]+'"]')
        expect(card('open')).to_be_visible();expect(card('closed')).to_have_count(0);expect(card('submitted')).to_have_count(0)
        page.locator('[data-action=owner-shift-view][data-view=review]').click()
        expect(card('submitted')).to_be_visible();expect(card('open')).to_have_count(0)
        page.locator('[data-action=owner-shift-view][data-view=history]').click()
        expect(card('closed')).to_be_visible();expect(card('submitted')).to_have_count(0)
        page.locator('#owner-shift-filter button[type=submit]').click()
        expect(card('closed')).to_be_visible()
        page.locator('#owner-shift-filter [name=start]').fill('2026-10-14')
        page.locator('#owner-shift-filter [name=end]').fill('2026-10-13')
        page.locator('#owner-shift-filter button[type=submit]').click()
        expect(page.locator('#owner-shift-filter .form-error')).not_to_be_empty()
        page.locator('[data-action=owner-shift-reset]').click();expect(card('closed')).to_be_visible()
        page.locator('#owner-shift-filter [name=search]').fill('TEST-NO-MATCH')
        page.locator('#owner-shift-filter button[type=submit]').click();expect(card('closed')).to_have_count(0)
        page.locator('[data-action=owner-shift-view][data-view=current]').click();expect(card('open')).to_be_visible()
        card('open').click();expect(page.locator('#modal')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors
    finally:ctx.close()

