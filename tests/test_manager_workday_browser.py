import os
from uuid import uuid4
import pytest
from app import db
from test_browser import _context,_owner_login,_staff,chromium_browser,live_app,synthetic_mail

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Isolated browser acceptance')]

@pytest.mark.parametrize('lang',['ar','en'])
def test_manager_starts_with_current_shift_and_separate_history(live_app,chromium_browser,lang):
    from playwright.sync_api import expect
    staff=_staff('delivery_manager');old='TEST-old-'+uuid4().hex;current='TEST-current-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,closed_at) VALUES(%s,'manager',%s,'kps','[\"kps-branch-1\"]','closed',now())",(old,staff['id']))
    ctx,page,errors=_context(chromium_browser,live_app);_owner_login(ctx,live_app,staff)
    ctx.add_init_script("localStorage.setItem('kps_ops_lang',"+repr(lang)+");")
    try:
        page.goto(live_app+'/ops')
        expect(page.locator('#content [data-action=open-shift]')).to_be_visible()
        expect(page.locator('#content [data-action=shift-detail]')).to_have_count(0)
        page.locator('[data-action=manager-work-view][data-target=shifts][data-history="1"]').click()
        expect(page.locator('[data-action=shift-detail][data-id="'+old+'"]').first).to_be_visible()
        with db.tx() as c:
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,opened_at,delivery_operational_date) VALUES(%s,'manager',%s,'kps','[\"kps-branch-1\"]',now()-interval '2 days',current_date-2)",(current,staff['id']))
        page.locator('[data-action=manager-work-view][data-target=shifts][data-history="0"]').click()
        expect(page.locator('[data-action=shift-detail][data-id="'+current+'"]').first).to_be_visible()
        expect(page.locator('[data-action=shift-detail][data-id="'+old+'"]')).to_have_count(0)
        expect(page.locator('#content')).to_contain_text('16:00')
        expect(page.locator('#content [data-action=open-shift]')).to_have_count(0)
        page.locator('#navigation [data-page=orders]').click()
        expect(page.locator('[data-action=manager-work-view][data-target=orders][data-history="1"]')).to_be_visible()
        assert not errors
    finally:ctx.close()
