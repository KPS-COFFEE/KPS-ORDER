"""Driver ending and manager exceptional approval through real forms and PostgreSQL."""
import os
from uuid import uuid4
import pytest
from app import db
from app.shift_vehicle import CHECKS
from test_browser import _capture,_context,_owner_login,_staff,chromium_browser,live_app,synthetic_mail
pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Isolated browser acceptance')]

@pytest.mark.parametrize('delegated',[False,True])
@pytest.mark.parametrize('remote',[False,True])
def test_simple_driver_end_then_manager_receipt_and_final_result(live_app,chromium_browser,delegated,remote):
    from playwright.sync_api import expect
    manager=_staff('delivery_manager');driver=_staff('driver');owner=_staff('owner')
    mid='TEST-manager-'+uuid4().hex;sid='TEST-driver-'+uuid4().hex
    with db.tx() as c:
        c.execute("UPDATE branches SET city='حفر الباطن' WHERE id='kps-branch-1'")
        if remote:c.execute("UPDATE branches SET city='رفحاء' WHERE id='kps-branch-1'")
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps','[\"kps-branch-1\"]')",(mid,manager['id']))
        if delegated:c.execute("UPDATE shifts SET status='closing' WHERE id=%s",(mid,))
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id,opening_halalas,vehicle_log) VALUES(%s,'driver',%s,'kps','[\"kps-branch-1\"]',%s,1000,%s)",(sid,driver['id'],mid,db.json({'start':{'odometer_km':1000,'fuel_percent':50,'checks':{k:'ok' for k in CHECKS}},'refuels':[]})))
    ctx,page,errors=_context(chromium_browser,live_app)
    _owner_login(ctx,live_app,manager if delegated else driver)
    try:
        page.goto(live_app+'/ops');page.locator('#navigation [data-page=shifts]').click()
        page.locator('[data-action=start-closing][data-id="'+sid+'"]').click()
        form=page.locator('#closure-form');expect(form).to_be_visible()
        if delegated:
            form.locator('[name=exception_reason]').fill('TEST driver phone unavailable')
            expect(form.locator('[name=inspection_odometer]')).to_have_count(0)
        else:
            form.locator('[name=inspection_odometer]').fill('1020')
            form.locator('[name=inspection_fuel]').fill('40')
            for key in CHECKS:form.locator('[name=inspection_'+key+']').select_option('ok')
        form.locator('[name=counted]').fill('10')
        form.locator('[name=handover]').fill('10')
        expect(form.locator('[name=recipient_user_id]')).to_have_value(manager['id'])
        expect(form.locator('[name=receiving_branch_id]')).not_to_be_visible()
        form.locator('[name=confirm_closure]').check()
        form.locator('button[type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible(timeout=20000)
        with db.tx() as c:
            shift=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone()
            assert shift['status']=='submitted'
            assert c.execute('SELECT count(*) n FROM custody_transfers WHERE from_shift_id=%s',(sid,)).fetchone()['n']==1
        _owner_login(ctx,live_app,manager)
        page.reload();page.locator('#navigation [data-page=shifts]').click()
        page.locator('[data-action=shift-detail][data-id="'+sid+'"]').click()
        page.locator('[data-action=approve-shift]').click()
        page.once('dialog',lambda dialog:dialog.accept())
        page.locator('[data-action=review-confirm-custody]').click()
        expect(page.locator('[data-action=review-confirm-custody]')).to_have_count(0)
        page.locator('#modal [name=confirmed]').check()
        page.locator('#modal button[type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible(timeout=20000)
        card=page.locator('article').filter(has=page.locator('[data-action=shift-detail][data-id="'+sid+'"]'))
        expect(card).to_contain_text('متوافق')
        _capture(page,'TEST_ONLY_manager_driver_closure_'+str(delegated)+'_remote_'+str(remote))
        if delegated:
            _owner_login(ctx,live_app,owner);page.reload()
            expect(page.locator('#operational-alerts')).to_contain_text('تقفيلات باستثناء',timeout=15000)
            page.locator('#navigation [data-page=reports]').click()
            expect(page.locator('.owner-notifications')).to_contain_text('مقفل باستثناء من مدير التوصيل')
        assert not errors
    finally:ctx.close()
