"""Driver ending and manager exceptional approval through real forms and PostgreSQL."""
import os
from uuid import uuid4
import pytest
from app import db
from app.shift_vehicle import CHECKS
from test_browser import _capture,_context,_owner_login,_staff,_acknowledge_driver_end,_approve_driver_closure_from_browser,chromium_browser,live_app,synthetic_mail
pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Isolated browser acceptance')]

@pytest.mark.parametrize('language',['ar','en'])
def test_low_fuel_closure_requests_approval_without_losing_counted_cash(live_app,chromium_browser,language):
    from playwright.sync_api import expect
    manager=_staff('delivery_manager');driver=_staff('driver')
    mid='TEST-fuel-manager-'+uuid4().hex;sid='TEST-fuel-driver-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps','[\"kps-branch-1\"]')",(mid,manager['id']))
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id,status,vehicle_log) VALUES(%s,'driver',%s,'kps','[\"kps-branch-1\"]',%s,'closing',%s)",(sid,driver['id'],mid,db.json({'start':{'odometer_km':1000,'fuel_percent':100},'refuels':[]})))
    ctx,page,errors=_context(chromium_browser,live_app);_owner_login(ctx,live_app,driver)
    mctx,mpage,merrors=_context(chromium_browser,live_app);_owner_login(mctx,live_app,manager)
    try:
        page.set_viewport_size({'width':390,'height':844});page.goto(live_app+'/ops')
        if language=='en':page.get_by_role('button',name='English',exact=True).click()
        page.locator('#navigation [data-page=shifts]').click()
        page.locator('.shift-card [data-action=shift-detail][data-id="'+sid+'"]').click()
        form=page.locator('#blind-closure-form');expect(form).to_be_visible()
        form.locator('[name=inspection_odometer]').fill('1025')
        # Direct numeric entry, without first choosing the FULL dropdown.
        form.locator('[name=inspection_fuel]').fill('70')
        for key in CHECKS:form.locator('[name=inspection_'+key+']').select_option('ok')
        form.locator('[name=counted]').fill('211');form.locator('[name=terminal_total]').fill('80')
        form.locator('[name=confirm_closure]').check()
        expect(form.locator('.fuel-submit-help')).to_be_visible()
        form.locator('button[type=submit]').click()
        expect(form.locator('.form-error')).to_contain_text('طلب موافقة' if language=='ar' else 'Request delivery manager approval')
        assert ' / ' not in form.locator('.form-error').inner_text()
        form.locator('[data-action=fuel-submit-request]').click()
        expect(form.locator('.fuel-submit-help')).to_contain_text('بانتظار' if language=='ar' else 'Waiting')
        form.locator('[data-action=fuel-submit-request]').click()
        expect(form.locator('[data-action=fuel-submit-request]')).to_be_enabled()
        with db.tx() as c:
            assert c.execute('SELECT count(*) n FROM fuel_exceptions WHERE shift_id=%s',(sid,)).fetchone()['n']==1
            assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']=='closing'
        mpage.goto(live_app+'/ops')
        expect(mpage.locator('#operational-alerts [data-action=fuel-inbox]')).to_have_count(1)
        mpage.locator('#operational-alerts [data-action=fuel-inbox]').click()
        mpage.locator('[data-action=fuel-reject]').click()
        expect(mpage.locator('[data-action=fuel-reject]')).to_have_count(0)
        form.locator('button[type=submit]').click()
        expect(form.locator('.form-error')).to_contain_text('مرفوض' if language=='ar' else 'rejected')
        expect(form.locator('[name=counted]')).to_have_value('211')
        expect(form.locator('[name=terminal_total]')).to_have_value('80')
        form.locator('[data-action=fuel-submit-request]').click()
        expect(form.locator('.fuel-submit-help')).to_contain_text('بانتظار' if language=='ar' else 'Waiting')
        mpage.locator('#modal-close').click() if mpage.locator('#modal-close').count() else mpage.keyboard.press('Escape')
        mpage.locator('#operational-alerts [data-action=fuel-inbox]').click();mpage.locator('[data-action=fuel-approve]').click()
        expect(mpage.locator('[data-action=fuel-approve]')).to_have_count(0)
        # No manual approval refresh: submission retrieves the exact approved reading.
        form.locator('button[type=submit]').click();_acknowledge_driver_end(page,language)
        with db.tx() as c:
            s=c.execute('SELECT status,submission,vehicle_log FROM shifts WHERE id=%s',(sid,)).fetchone()
            assert s['status']=='submitted' and s['submission']['counted_halalas']==21100
            assert s['submission']['terminal_total_halalas']==8000 and s['vehicle_log']['end']['fuel_percent']==70
            assert c.execute("SELECT count(*) n FROM fuel_exceptions WHERE shift_id=%s AND status='consumed'",(sid,)).fetchone()['n']==1
        assert not errors and not merrors
    finally:ctx.close();mctx.close()

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
            form.locator('[name=inspection_odometer]').fill('1020')
            form.locator('[name=inspection_fuel]').fill('100')
            for key in CHECKS:form.locator('[name=inspection_'+key+']').select_option('ok')
            expect(form.locator('[name=counted]')).to_have_value('')
            expect(form.locator('[name=received]')).to_have_value('')
            form.locator('[name=counted]').fill('10')
            form.locator('[name=terminal_total]').fill('0')
            form.locator('[name=received]').fill('10')
            form.locator('[name=exception_reason]').fill('TEST driver phone unavailable')
        else:
            page.locator('.shift-card [data-action=start-closing][data-id="'+sid+'"]').click()
            manager_context=chromium_browser.new_context(base_url=live_app)
            try:
                _owner_login(manager_context,live_app,manager)
                _approve_driver_closure_from_browser(page,manager_context,live_app,sid)
            finally:manager_context.close()
            form=page.locator('#blind-closure-form');expect(form).to_be_visible()
            form.locator('[name=inspection_odometer]').fill('1020')
            form.locator('[name=inspection_fuel]').fill('100')
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
            # The manager enters the independent amount actually counted; the
            # sender's10.00 declaration is not copied and zero is not assumed.
            expect(form.locator('[name=received]')).to_have_value('')
            expect(form.locator('[name=exception_reason]')).to_have_count(0)
            form.locator('[name=received]').fill('10.00')
        form.locator('[name=confirmed]').check()
        form.locator('button[type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible(timeout=20000)
        with db.tx() as c:
            closed=c.execute('SELECT status,submission FROM shifts WHERE id=%s',(sid,)).fetchone()
            assert closed['status']=='closed'
            assert closed['submission']['counted_halalas']==1000
            assert closed['submission']['terminal_total_halalas']==0
            assert closed['submission']['actual_handover_halalas']==1000
            assert closed['submission']['receipt_difference_halalas']==0
            assert closed['submission']['financial_state']=='pending_accounting'
            confirmed=c.execute("SELECT * FROM custody_transfers WHERE from_shift_id=%s AND closure AND status='confirmed'",(sid,)).fetchall()
            assert len(confirmed)==1 and confirmed[0]['amount_halalas']==1000
            assert confirmed[0]['recipient_user_id']==manager['id']
            assert c.execute("SELECT count(*) n FROM custody_events WHERE shift_id=%s AND kind='transfer_in' AND amount_halalas=1000",(mid,)).fetchone()['n']==1
            assert c.execute("SELECT count(*) n FROM custody_events WHERE shift_id=%s AND kind='adjustment'",(sid,)).fetchone()['n']==0
            assert c.execute('SELECT count(*) n FROM closure_reports WHERE shift_id=%s',(sid,)).fetchone()['n']==1
        expect(page.locator('.shift-card [data-action=shift-detail][data-id="'+sid+'"]')).to_have_count(0)
        page.locator('[data-action=manager-work-view][data-target=shifts][data-history="1"]').click()
        card=page.locator('article').filter(has=page.locator('[data-action=shift-detail][data-id="'+sid+'"]'))
        expect(card).to_contain_text('انتهت تشغيليًا — بانتظار المراجعة المحاسبية')
        expect(card).not_to_contain_text('متوافق')
        expect(card).not_to_contain_text('مكتمل ماليًا')
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
        if status!='open':
            page.locator('[data-action=owner-shift-view][data-view=review]').click()
        expect(page.locator('[data-action=simple-driver-close][data-id="'+sid+'"]')).to_have_count(0)
        page.locator('[data-action=shift-detail][data-id="'+sid+'"]').click()
        expect(page.locator('#modal-body')).to_contain_text('للمتابعة فقط' if lang=='ar' else 'View only')
        expect(page.locator('#modal-body .driver-closure-identity')).to_contain_text('تقفيلة السائق' if lang=='ar' else 'Driver closure')
        expect(page.locator('#modal-body .driver-closure-identity')).to_contain_text('يوم التشغيل' if lang=='ar' else 'Operational day')
        expect(page.locator('#modal-body .driver-closure-identity')).to_contain_text('لم تسجل' if lang=='ar' else 'Not recorded')
        _capture(page,'TEST_ONLY_closure_identity_'+lang+'_'+status)
        for selector in ['#closure-form','[data-action=approve-shift]','[data-action=return-shift]','[data-action=final-custody]']:
            expect(page.locator('#modal-body').locator(selector)).to_have_count(0)
        with db.tx() as c:
            assert c.execute('SELECT status FROM shifts WHERE id=%s',(sid,)).fetchone()['status']==status
            assert c.execute('SELECT count(*) n FROM custody_transfers WHERE from_shift_id=%s',(sid,)).fetchone()['n']==0
        assert not errors
    finally:ctx.close()
