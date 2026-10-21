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
        expect(card.locator('[data-driver-balance]')).to_be_visible()
        card.locator('[data-driver-receipt]').click()
        _submit_modal(page,{'cash_received':'99.00','receipt_reference':'TEST BROWSER RECEIPT','note':'TEST cash counted before paper arrives'})
        expect(card).to_contain_text('الكاش مستلم — بانتظار الموازنة' if lang=='ar' else 'Cash received — awaiting balance')
        expect(card.locator('[data-driver-receipt]')).to_have_count(0)
        card.locator('[data-driver-balance]').click()
        _submit_modal(page,{'bank':'51.00','cash_due':'100.00','note':'TEST paper reviewed independently'})
        expect(card.locator('[data-day-approve]')).to_be_enabled();card.locator('[data-day-approve]').click()
        _submit_modal(page,{'note':'TEST final accounting daily approval'})
        expect(card.locator('.ac-day-report')).to_be_visible()
        with page.expect_download() as download:card.locator('.ac-day-report a',has_text='Excel').click()
        assert download.value.failure() is None
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        _capture(page,f'accounting-driver-statement-{lang}-{width}')
        assert not errors
    finally:context.close()
