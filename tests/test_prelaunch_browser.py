"""Real isolated Chromium checks for the new prelaunch operations slice."""
import os
from uuid import uuid4
import pytest
from app import db
from test_browser import _context,_owner_login,_staff,chromium_browser,live_app,synthetic_mail
pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires isolated browser')]

@pytest.mark.parametrize('language',['ar','en'])
def test_early_closure_request_and_operations_only_roster(live_app,chromium_browser,language):
    from playwright.sync_api import expect
    manager=_staff('delivery_manager');driver=_staff('driver')
    mid='PRELAUNCH-m-'+uuid4().hex;sid='PRELAUNCH-d-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps','[\"kps-branch-1\"]')",(mid,manager['id']))
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id,vehicle_log) VALUES(%s,'driver',%s,'kps','[\"kps-branch-1\"]',%s,%s)",(sid,driver['id'],mid,db.json({'start':{'odometer_km':1000,'fuel_percent':100},'refuels':[]})))
    ctx,page,errors=_context(chromium_browser,live_app);_owner_login(ctx,live_app,driver)
    mctx,mpage,merrors=_context(chromium_browser,live_app);_owner_login(mctx,live_app,manager)
    try:
        for p in (page,mpage):
            p.set_viewport_size({'width':390,'height':844});p.goto(live_app+'/ops')
            if language=='en':p.get_by_role('button',name='English',exact=True).click()
        page.locator('#navigation [data-page=shifts]').click()
        closing=page.locator('[data-action=start-closing][data-id="'+sid+'"]')
        expect(closing).to_have_count(1)
        closing.click()
        form=page.locator('#modal form');expect(form.locator('[name=reason]')).to_be_visible()
        form.locator('[name=reason]').fill('Synthetic emergency reason');form.locator('button[type=submit]').click()
        expect(page.locator('#modal')).to_contain_text('Awaiting' if language=='en' else 'بانتظار')
        with db.tx() as c: assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='open'
        page.locator('[data-action=withdraw-driver-close]').click()
        expect(page.locator('#modal')).not_to_be_visible()
        with db.tx() as c:
            row=c.execute('SELECT status,closure_authorization FROM shifts WHERE id=%s',(sid,)).fetchone()
            assert row['status']=='open' and row['closure_authorization']['status']=='withdrawn'
        page.locator('[data-action=start-closing][data-id="'+sid+'"]').click()
        form=page.locator('#modal form')
        form.locator('[name=reason]').fill('Synthetic renewed emergency reason')
        form.locator('button[type=submit]').click()
        expect(page.locator('#modal')).to_contain_text('Awaiting' if language=='en' else 'بانتظار')
        mpage.locator('#navigation [data-page=drivers]').click()
        expect(mpage.locator('[data-action=fuel-inbox]')).to_have_count(1)
        expect(mpage.locator('#content [data-action=invite-staff]')).to_have_count(0)
        expect(mpage.locator('#content [data-action=edit-staff]')).to_have_count(0)
        mpage.locator('#content [data-action=closure-inbox]').click()
        mpage.once('dialog',lambda dialog:dialog.accept('Synthetic manager approval'))
        mpage.locator('[data-action=closure-approve][data-id="'+sid+'"]').click()
        expect(mpage.locator('[data-action=closure-approve]')).to_have_count(0)
        page.locator('#modal button[type=submit]').click()
        expect(page.locator('#modal')).to_contain_text('Begin closure' if language=='en' else 'بدء التقفيلة')
        page.locator('#modal button[type=submit]').click()
        expect(page.locator('#blind-closure-form')).to_be_visible()
        with db.tx() as c: assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='closing'
        assert not errors and not merrors
    finally:ctx.close();mctx.close()
