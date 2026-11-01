"""Real browser acceptance for independent approvals inside a manager card."""
import os
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from app import db
from test_browser import _staff,_context,_owner_login,_submit_modal,_capture,live_app,chromium_browser,synthetic_mail

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance')]


def _documented_snapshot(driver_id):
    return {'shift':{'id':'fixture-shift','user_id':driver_id,'opening_halalas':0,'submission':{
        'terminal_total_halalas':5000,'handover_halalas':10000,'expected_halalas':10000,
        'expected_card_halalas':5000,'counted_halalas':10000,'difference_halalas':0}},
        'orders':[{'id':'order-001','total_halalas':15000,'fulfilled_in_report':True}],
        'collections':[{'id':'cash-001','shift_id':'fixture-shift','amount_halalas':10000,'method':'cash'},
                       {'id':'card-001','shift_id':'fixture-shift','amount_halalas':5000,'method':'card'}],
        'refunds':[],'custody_events':[{'id':'custody-001','shift_id':'fixture-shift','kind':'collection',
            'reference_id':'cash-001','amount_halalas':10000}], 'transfers':[],
        'summary':{'cash_collected_halalas':10000,'card_collected_halalas':5000,
            'cash_refunded_halalas':0,'card_refunded_halalas':0,'net_collections_halalas':15000,
            'unique_orders':1,'completed_orders':1,'gross_completed_halalas':15000}}


@pytest.mark.parametrize('lang,width',[('ar',390),('en',1440)])
def test_accountant_card_cash_first_then_paper_and_saved_report(live_app,chromium_browser,lang,width):
    from playwright.sync_api import expect
    accountant=_staff('admin');manager=_staff('delivery_manager');driver=_staff('driver')
    snapshot=_documented_snapshot(driver['id'])
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
        expect(card).to_be_visible();card.locator('summary').click();expect(card.locator('[data-day-approve]')).to_be_disabled()
        expect(card.locator('.ac-completion')).to_have_text('0%')
        expect(card).not_to_contain_text('بانتظار الورقة')
        expect(card.locator('[data-driver-balance]')).to_be_visible()
        card.locator('[data-driver-receipt]').click()
        _submit_modal(page,{'cash_received':'100.00','receipt_reference':'TEST BROWSER RECEIPT','note':'TEST cash counted before paper arrives'})
        expect(card).to_contain_text('الكاش مستلم — بانتظار الموازنة' if lang=='ar' else 'Cash received — awaiting balance')
        expect(card.locator('[data-driver-receipt]')).to_have_count(0)
        expect(card.locator('.ac-completion')).to_have_text('50%')
        expect(card.locator('[data-driver-row] td').nth(4)).to_have_text('—')
        card.locator('[data-driver-balance]').click()
        expect(page.locator('#modal button[type=submit]')).to_have_text('اعتماد المبالغ دون تعديل' if lang=='ar' else 'Approve amounts unchanged')
        page.locator('#modal [name=bank]').fill('50.00')
        expect(page.locator('#modal button[type=submit]')).to_have_text('اعتماد المبالغ دون تعديل' if lang=='ar' else 'Approve amounts unchanged')
        expect(page.locator('#settlement-note-label')).to_have_text('ملاحظة الاعتماد' if lang=='ar' else 'Approval note')
        _submit_modal(page,{'bank':'50.00','cash_due':'100.00','note':'TEST paper reviewed independently'})
        expect(card.locator('.ac-completion')).to_have_text('100%')
        # Actual cash and card match the documented source channels separately.
        expect(card.locator('[data-driver-row] td').nth(4)).to_contain_text('٠٫٠٠' if lang=='ar' else '0.00')
        expect(card.locator('[data-driver-row] td').nth(5)).to_contain_text('٠٫٠٠' if lang=='ar' else '0.00')
        card.locator('[data-driver-history]').click()
        expect(page.locator('#modal-body')).to_contain_text('تسجيل استلام الكاش الفعلي' if lang=='ar' else 'Actual cash receipt')
        expect(page.locator('#modal-body')).to_contain_text('اعتماد المبالغ دون تعديل' if lang=='ar' else 'Amounts approved unchanged')
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
def test_variance_requires_evidenced_independent_treatment(live_app,chromium_browser,lang,width,actual_cash):
    from playwright.sync_api import expect
    accountant=_staff('admin');manager=_staff('delivery_manager');driver=_staff('driver')
    snapshot=_documented_snapshot(driver['id'])
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
        expect(card).to_be_visible();card.locator('summary').click()
        expect(card.locator('[data-variance-confirm]')).to_have_count(0)
        card.locator('[data-driver-balance]').click()
        # Hold the real post-save card GET: a committed approval must not
        # expose history/edit handlers still bound to the pre-save snapshot.
        pending_refresh=[]
        def hold_card_refresh(route):pending_refresh.append(route)
        page.route('**/api/accounting/manager-cards?*',hold_card_refresh)
        page.locator('#modal [name=bank]').fill('50.00')
        page.locator('#modal [name=cash_due]').fill('100.00')
        page.locator('#modal [name=note]').fill('TEST unchanged approval')
        with page.expect_request('**/api/accounting/manager-cards?*'):
            page.locator('#modal button[type=submit]').click()
        expect(page.locator('#modal button[type=submit]')).to_be_disabled()
        expect(page.locator('#modal')).to_be_visible()
        assert len(pending_refresh)==1
        pending_refresh.pop().continue_()
        page.unroute('**/api/accounting/manager-cards?*',hold_card_refresh)
        expect(page.locator('#modal')).not_to_be_visible()
        expect(card.locator('[data-driver-balance]')).to_have_text('تعديل الموازنة مع السبب' if lang=='ar' else 'Amend balance with reason')
        expect(card.locator('[data-variance-confirm]')).to_have_count(0)
        card.locator('[data-driver-history]').click()
        expect(page.locator('#modal-body')).to_contain_text('اعتماد المبالغ دون تعديل' if lang=='ar' else 'Amounts approved unchanged')
        page.locator('#modal-close').click()
        card.locator('[data-driver-receipt]').click()
        _submit_modal(page,{'cash_received':actual_cash,'receipt_reference':'TEST VARIANCE RECEIPT','note':'TEST actual cash counted'})
        expect(card).to_contain_text('فرق تحت المراجعة — يتطلب معالجة موثقة' if lang=='ar' else 'Difference under review — documented treatment required')
        expect(card.locator('[data-day-approve]')).to_be_disabled()
        card.locator('[data-variance-confirm]').click()
        page.locator('[data-treatment-propose]').click()
        page.locator('#modal [name=evidence_reference]').fill('TEST actual receipt evidence 001')
        page.locator('#modal [name=note]').fill('TEST documented customer receivable or overpayment')
        page.locator('#modal [name=liable_party_name]').fill('TEST documented customer reference')
        page.locator('#modal [name=followup_user_id]').select_option(accountant['id'])
        page.locator('#modal [name=due_on]').fill((datetime.now(ZoneInfo('Asia/Riyadh')).date()+timedelta(days=1)).isoformat())
        page.locator('#modal button[type=submit]').click()
        expect(page.locator('.ac-treatment-review')).to_contain_text('TEST actual receipt evidence 001')
        expect(page.locator('[data-treatment-review]')).to_have_count(0)
        page.locator('#modal-close').click()
        # The preparer cannot approve its own treatment. A separate owner reviews
        # evidence and explicitly permits the period while the debt remains open.
        reviewer=_staff('owner')
        independent,review_page,review_errors=_context(chromium_browser,live_app)
        try:
            _owner_login(independent,live_app,reviewer)
            review_page.set_viewport_size({'width':width,'height':1000});review_page.goto(live_app+'/ops')
            if lang=='en':review_page.get_by_role('button',name='English',exact=True).click()
            expect(review_page.locator('#navigation [data-page=ownerDashboard]')).to_have_attribute('aria-current','page')
            expect(review_page.locator('#owner-dashboard')).to_be_visible()
            accounting_navigation=review_page.locator('#navigation [data-page=accounting]')
            if not accounting_navigation.is_visible():
                # The owner overview exposes navigation directly; a compact
                # operations workspace may still require its visible menu.
                expect(review_page.locator('.dispatch-nav-toggle')).to_be_visible()
                review_page.locator('.dispatch-nav-toggle').click()
            accounting_navigation.click()
            review_page.locator('[data-ac=settlements]').click()
            review_card=review_page.locator('[data-manager-card=browser-variance-manager]')
            review_card.locator('summary').click()
            review_card.locator('[data-variance-confirm]').click()
            review_page.locator('[data-treatment-review][data-decision=approve]').click()
            expect(review_page.locator('#modal [name=confirmed]')).not_to_be_checked()
            review_page.locator('#modal [name=evidence_reference]').fill('TEST independent owner review 002')
            review_page.locator('#modal [name=note]').fill('TEST owner verified open amount and follow-up')
            review_page.locator('#modal [name=confirmed]').check()
            review_page.locator('#modal [name=allow_period_approval_with_outstanding]').check()
            review_page.locator('#modal button[type=submit]').click()
            expect(review_page.locator('.ac-treatment-review')).to_contain_text('TEST independent owner review 002')
            review_page.locator('#modal-close').click()
            expect(review_card).to_contain_text('المراجعة معتمدة — مبلغ مستحق تحت المتابعة' if lang=='ar' else 'Review approved — outstanding amount under follow-up')
            expect(review_card.locator('[data-day-approve]')).to_be_enabled()
            assert not review_errors
        finally:independent.close()
        _capture(page,f'accounting-driver-statement-variance-{lang}-{width}')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors
    finally:context.close()
