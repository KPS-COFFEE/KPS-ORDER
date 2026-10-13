"""Driver ending and manager exceptional approval through real forms and PostgreSQL."""
import os
from uuid import uuid4
import pytest
from app import db
from app.shift_vehicle import CHECKS
from test_browser import _capture,_context,_owner_login,_staff,_acknowledge_driver_end,chromium_browser,live_app,synthetic_mail
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
        if delegated:
            page.locator('[data-action=simple-driver-close][data-id="'+sid+'"]').click()
            form=page.locator('#simple-driver-close-form');expect(form).to_be_visible()
            expect(form.locator('[name=recipient_user_id]')).to_have_count(0)
            expect(form.locator('[name=inspection_odometer]')).to_have_count(0)
            expect(form.locator('[name=counted]')).to_have_value('')
            expect(form.locator('[name=received]')).to_have_value('')
            form.locator('[name=counted]').fill('10')
            form.locator('[name=terminal_total]').fill('0')
            form.locator('[name=received]').fill('10')
            form.locator('[name=exception_reason]').fill('TEST driver phone unavailable')
        else:
            page.locator('.shift-card [data-action=start-closing][data-id="'+sid+'"]').click()
            form=page.locator('#blind-closure-form');expect(form).to_be_visible()
            form.locator('[name=inspection_odometer]').fill('1020')
            form.locator('[name=inspection_fuel]').fill('40')
            for key in CHECKS:form.locator('[name=inspection_'+key+']').select_option('ok')
            form.locator('[name=counted]').fill('10')
            form.locator('[name=terminal_total]').fill('0')
            expect(form.locator('[name=handover]')).to_have_count(0)
            expect(form.locator('[name=recipient_user_id]')).to_have_count(0)
            expect(form.locator('[name=receiving_branch_id]')).to_have_count(0)
            form.locator('[name=confirm_closure]').check()
            form.locator('button[type=submit]').click()
            _acknowledge_driver_end(page)
            with db.tx() as c:
                assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='submitted'
                assert c.execute('SELECT count(*) n FROM custody_transfers WHERE from_shift_id=%s',(sid,)).fetchone()['n']==1
            _owner_login(ctx,live_app,manager)
            page.reload();page.locator('#navigation [data-page=shifts]').click()
            page.locator('[data-action=simple-driver-close][data-id="'+sid+'"]').click()
            form=page.locator('#simple-driver-close-form');expect(form).to_be_visible()
            expect(form.locator('[name=counted]')).to_have_count(0)
            expect(form.locator('[name=received]')).to_have_value('10.00')
        form.locator('[name=confirmed]').check()
        form.locator('button[type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible(timeout=20000)
        with db.tx() as c:
            assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='closed'
            assert c.execute("SELECT count(*) n FROM custody_events WHERE shift_id=%s AND kind='transfer_in'",(mid,)).fetchone()['n']==1
            assert c.execute('SELECT count(*) n FROM closure_reports WHERE shift_id=%s',(sid,)).fetchone()['n']==1
        expect(page.locator('.shift-card [data-action=shift-detail][data-id="'+sid+'"]')).to_have_count(0)
        page.locator('[data-action=manager-work-view][data-target=shifts][data-history="1"]').click()
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

@pytest.mark.parametrize('lang',['ar','en'])
@pytest.mark.parametrize('status',['open','closing','submitted'])
def test_owner_driver_closure_is_clear_read_only(live_app,chromium_browser,lang,status):
    from playwright.sync_api import expect
    manager=_staff('delivery_manager');driver=_staff('driver');owner=_staff('owner')
    mid='TEST-manager-'+uuid4().hex;sid='TEST-driver-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps','[\"kps-branch-1\"]')",(mid,manager['id']))
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id,status) VALUES(%s,'driver',%s,'kps','[\"kps-branch-1\"]',%s,%s)",(sid,driver['id'],mid,status))
    ctx,page,errors=_context(chromium_browser,live_app)
    _owner_login(ctx,live_app,owner)
    ctx.add_init_script("localStorage.setItem('kps_ops_lang',"+repr(lang)+");")
    try:
        page.goto(live_app+'/ops');page.locator('#navigation [data-page=shifts]').click()
        expect(page.locator('[data-action=simple-driver-close][data-id="'+sid+'"]')).to_have_count(0)
        page.locator('[data-action=shift-detail][data-id="'+sid+'"]').click()
        expect(page.locator('#modal-body')).to_contain_text('للمتابعة فقط' if lang=='ar' else 'View only')
        for selector in ['#closure-form','[data-action=approve-shift]','[data-action=return-shift]','[data-action=final-custody]']:
            expect(page.locator('#modal-body').locator(selector)).to_have_count(0)
        with db.tx() as c:
            assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']==status
            assert c.execute('SELECT count(*) n FROM custody_transfers WHERE from_shift_id=%s',(sid,)).fetchone()['n']==0
        assert not errors
    finally:ctx.close()
