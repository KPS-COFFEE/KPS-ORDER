"""Real browser acceptance for independent approvals inside a manager card."""
import os
import pytest
from app import db
from test_browser import _staff,_context,_owner_login,_submit_modal,_capture,live_app,chromium_browser,synthetic_mail

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance')]


@pytest.mark.parametrize('lang,width',[('ar',390),('en',1440)])
def test_accountant_card_cash_first_then_paper_and_saved_report(live_app,chromium_browser,lang,width):
    from playwright.sync_api import expect
    accountant=_staff('admin');manager=_staff('delivery_manager');driver=_staff('driver')
    snapshot={'shift':{'user_id':driver['id'],'submission':{'terminal_total_halalas':5000,'handover_halalas':10000,'expected_halalas':10000,'expected_card_halalas':5000}},'summary':{}}
    with db.tx() as c:
        c.execute('UPDATE users SET permissions=%s WHERE id=%s',(db.json(['accounting_reports','accounting_review']),accountant['id']))
        for identifier,kind,person,manager_id in [('browser-statement-manager','manager',manager['id'],None),('browser-statement-driver','driver',driver['id'],'browser-statement-manager')]:
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,closed_at,manager_shift_id) VALUES(%s,%s,%s,'kps','[\"kps-branch-1\"]','closed',now()-interval '1 hour',now(),%s)",(identifier,kind,person,manager_id))
        c.execute("INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES('browser-statement-report','browser-statement-driver','driver','kps','[\"kps-branch-1\"]',%s,%s)",(driver['id'],db.json(snapshot)))
    context,page,errors=_context(chromium_browser,live_app);_owner_login(context,live_app,accountant)
    try:
        page.set_viewport_size({'width':width,'height':1000});page.goto(live_app+'/ops')
        if lang=='en':page.get_by_role('button',name='English',exact=True).click()
        page.locator('[data-ac=settlements]').click()
        card=page.locator('[data-manager-card=browser-statement-manager]')
        expect(card).to_be_visible();expect(card.locator('[data-day-approve]')).to_be_disabled()
        expect(card.locator('.ac-completion')).to_have_text('0%')
        expect(card).not_to_contain_text('بانتظار الورقة')
        expect(card.locator('[data-driver-balance]')).to_be_visible()
        card.locator('[data-driver-receipt]').click()
        _submit_modal(page,{'cash_received':'99.00','receipt_reference':'TEST BROWSER RECEIPT','note':'TEST cash counted before paper arrives'})
        expect(card).to_contain_text('الكاش مستلم — بانتظار الموازنة' if lang=='ar' else 'Cash received — awaiting balance')
        expect(card.locator('[data-driver-receipt]')).to_have_count(0)
        expect(card.locator('.ac-completion')).to_have_text('50%')
        expect(card.locator('[data-driver-row] td').nth(4)).to_have_text('—')
        card.locator('[data-driver-balance]').click()
        expect(page.locator('#modal button[type=submit]')).to_have_text('اعتماد المبالغ دون تعديل' if lang=='ar' else 'Approve amounts unchanged')
        page.locator('#modal [name=bank]').fill('51.00')
        expect(page.locator('#modal button[type=submit]')).to_have_text('حفظ التعديل واعتماد المبالغ' if lang=='ar' else 'Save correction and approve amounts')
        expect(page.locator('#settlement-note-label')).to_have_text('سبب تعديل المبالغ' if lang=='ar' else 'Reason for amount correction')
        _submit_modal(page,{'bank':'51.00','cash_due':'100.00','note':'TEST paper reviewed independently'})
        expect(card.locator('.ac-completion')).to_have_text('100%')
        # 99 actual cash + 51 actual card equals the original 150 total.
        expect(card.locator('[data-driver-row] td').nth(4)).to_contain_text('٠٫٠٠' if lang=='ar' else '0.00')
        expect(card.locator('[data-driver-row] td').nth(5)).to_contain_text('٠٫٠٠' if lang=='ar' else '0.00')
        card.locator('[data-driver-history]').click()
        expect(page.locator('#modal-body')).to_contain_text('تسجيل استلام الكاش الفعلي' if lang=='ar' else 'Actual cash receipt')
        expect(page.locator('#modal-body')).to_contain_text('تعديل المبالغ واعتمادها' if lang=='ar' else 'Amounts corrected and approved')
        page.locator('#modal-close').click()
        expect(card.locator('[data-day-approve]')).to_be_enabled();card.locator('[data-day-approve]').click()
        _submit_modal(page,{'note':'TEST final accounting daily approval'})
        expect(card.locator('.ac-day-report')).to_be_visible()
        with page.expect_download() as download:card.locator('.ac-day-report a',has_text='Excel').click()
        assert download.value.failure() is None
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        _capture(page,f'accounting-driver-statement-{lang}-{width}')
        assert not errors
    finally:context.close()


@pytest.mark.parametrize('lang,width,actual_cash', [('ar',390,'99.00'),('en',1440,'101.00')])
def test_variance_requires_separate_accountant_confirmation(live_app,chromium_browser,lang,width,actual_cash):
    from playwright.sync_api import expect
    accountant=_staff('admin');manager=_staff('delivery_manager');driver=_staff('driver')
    snapshot={'shift':{'user_id':driver['id'],'submission':{'terminal_total_halalas':5000,'handover_halalas':10000,'expected_halalas':10000,'expected_card_halalas':5000}},'summary':{}}
    with db.tx() as c:
        c.execute('UPDATE users SET permissions=%s WHERE id=%s',(db.json(['accounting_reports','accounting_review']),accountant['id']))
        for identifier,kind,person,manager_id in [('browser-variance-manager','manager',manager['id'],None),('browser-variance-driver','driver',driver['id'],'browser-variance-manager')]:
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,closed_at,manager_shift_id) VALUES(%s,%s,%s,'kps','[\"kps-branch-1\"]','closed',now()-interval '1 hour',now(),%s)",(identifier,kind,person,manager_id))
        c.execute("INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES('browser-variance-report','browser-variance-driver','driver','kps','[\"kps-branch-1\"]',%s,%s)",(driver['id'],db.json(snapshot)))
    context,page,errors=_context(chromium_browser,live_app);_owner_login(context,live_app,accountant)
    try:
        page.set_viewport_size({'width':width,'height':1000});page.goto(live_app+'/ops')
        if lang=='en':page.get_by_role('button',name='English',exact=True).click()
        page.locator('[data-ac=settlements]').click()
        card=page.locator('[data-manager-card=browser-variance-manager]')
        expect(card).to_be_visible()
        expect(card.locator('[data-variance-confirm]')).to_have_count(0)
        card.locator('[data-driver-balance]').click()
        _submit_modal(page,{'bank':'50.00','cash_due':'100.00','note':'TEST unchanged approval'})
        expect(card.locator('[data-variance-confirm]')).to_have_count(0)
        card.locator('[data-driver-history]').click()
        expect(page.locator('#modal-body')).to_contain_text('اعتماد المبالغ دون تعديل' if lang=='ar' else 'Amounts approved unchanged')
        page.locator('#modal-close').click()
        card.locator('[data-driver-receipt]').click()
        _submit_modal(page,{'cash_received':actual_cash,'receipt_reference':'TEST VARIANCE RECEIPT','note':'TEST actual cash counted'})
        expect(card).to_contain_text('فرق بانتظار تأكيد المحاسب — لم يثبت في ملف السائق' if lang=='ar' else 'Difference pending accountant confirmation — not recorded in driver file')
        expect(card.locator('[data-day-approve]')).to_be_disabled()
        expect(card.locator('[data-driver-balance]')).to_be_visible()
        card.locator('[data-variance-confirm]').click()
        # A confirmation with no explicit acknowledgment cannot submit.
        page.locator('#modal [name=note]').fill('TEST checked collection records')
        page.locator('#modal [name=action_taken]').fill('TEST documented review and follow-up')
        page.locator('#modal button[type=submit]').click()
        expect(page.locator('#modal')).to_be_visible()
        _submit_modal(page,check=['confirmed'])
        expect(card.locator('[data-variance-confirm]')).to_have_count(0)
        expect(card.locator('[data-driver-balance]')).to_have_count(0)
        expect(card).to_contain_text('TEST documented review and follow-up')
        expect(card.locator('.ac-completion')).to_have_text('100%')
        expect(card.locator('[data-day-approve]')).to_be_enabled()
        _capture(page,f'accounting-driver-statement-variance-{lang}-{width}')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors
    finally:context.close()
