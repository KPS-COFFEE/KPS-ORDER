"""AR/mobile and EN/desktop owner UI against PostgreSQL, without real money."""
import os
import pytest
from app import db
from test_browser import _staff,_context,_owner_login,_submit_modal,_capture,live_app,chromium_browser,synthetic_mail

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance')]


@pytest.mark.parametrize('unpaid_cancelled',[False,True])
@pytest.mark.parametrize('lang,width',[('ar',390),('en',1440)])
def test_owner_test_void_confirmation_and_retained_history(live_app,chromium_browser,lang,width,unpaid_cancelled):
    from playwright.sync_api import expect
    owner=_staff();manager=_staff('delivery_manager');driver=_staff('driver')
    with db.tx() as c:
        for sid,kind,person,parent in [('void-browser-manager','manager',manager['id'],None),('void-browser-driver','driver',driver['id'],'void-browser-manager')]:
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,manager_shift_id,delivery_window_snapshot) VALUES(%s,%s,%s,'kps','[\"kps-branch-1\"]','open',%s,'{\"period\":\"morning\"}')",(sid,kind,person,parent))
        snap={'delivery_period':'morning','order_reference':'TEST-MORNING-VOID','items':[{'product_id':'test-product','name':'اختبار فقط','quantity':1,'unit_price_halalas':2050,'line_total_halalas':2050,'units':[]}]}
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer,completed_at) VALUES('void-browser-order','kps','kps-branch-1',%s,'delivery',%s,%s,%s,'cash',2050,0,2050,%s,'{\"name\":\"اختبار فقط\"}',CASE WHEN %s THEN NULL ELSE now() END)",(owner['id'],'cancelled' if unpaid_cancelled else 'ready','cancelled' if unpaid_cancelled else 'delivered','unpaid' if unpaid_cancelled else 'paid',db.json(snap),unpaid_cancelled))
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,delivered_at) VALUES('void-browser-task','void-browser-order',%s,'void-browser-driver',%s,%s,CASE WHEN %s THEN NULL ELSE now() END)",(driver['id'],'cancelled' if unpaid_cancelled else 'delivered',manager['id'],unpaid_cancelled))
        if not unpaid_cancelled:
            c.execute("INSERT INTO collections(id,order_id,shift_id,collector_id,method,amount_halalas) VALUES('void-browser-col','void-browser-order','void-browser-driver',%s,'cash',2050)",(driver['id'],))
            c.execute("INSERT INTO custody_events(id,shift_id,amount_halalas,kind,reference_id,actor_id,reason) VALUES('void-browser-event','void-browser-driver',2050,'collection','void-browser-col',%s,'TEST fictitious cash')",(driver['id'],))
    if unpaid_cancelled:
        # Separate fixture path contains no fictitious collection to reverse.
        with db.tx() as c:
            c.execute("UPDATE shifts SET status='submitted' WHERE id='void-browser-driver'")
            c.execute("UPDATE shifts SET status='closing' WHERE id='void-browser-manager'")
    context,page,errors=_context(chromium_browser,live_app);_owner_login(context,live_app,owner)
    try:
        page.set_viewport_size({'width':width,'height':1000});page.goto(live_app+'/ops')
        # The global language button is visible before the initial workspace's
        # asynchronous load finishes. Its click starts another full render.
        # Act only on acknowledged UI states so a still-pending initialization
        # cannot replace the detail panel after its order request completes.
        expect(page.locator('#content')).to_have_attribute('aria-busy','false')
        if lang=='en':
            page.get_by_role('button',name='English',exact=True).click()
            expect(page.locator('#content')).to_have_attribute('aria-busy','false')
        expect(page.locator('#owner-dashboard')).to_be_visible()
        page.locator('#navigation [data-page=dashboard]').click()
        expect(page.locator('#content')).to_have_attribute('aria-busy','false')
        all_orders=page.locator('[data-action=dispatch-filter][data-scope=all][data-stage=""]')
        all_orders.click()
        expect(all_orders).to_have_attribute('aria-pressed','true')
        expect(page.locator('#content')).to_have_attribute('aria-busy','false')
        page.locator('[data-action=dispatch-inspect][data-id="void-browser-order"]').first.click()
        action=page.locator('[data-action=test-void-order][data-id="void-browser-order"]')
        expect(action).to_be_visible()
        action.click()
        expect(page.locator('#modal')).to_be_visible()
        expect(page.locator('#modal-body')).to_contain_text(('دون تغيير التحصيل' if lang=='ar' else 'without assigning fault') if unpaid_cancelled else ('لا يصرف أموالًا' if lang=='ar' else 'does not disburse cash'))
        _capture(page,f'owner-test-void-confirm-{lang}-{unpaid_cancelled}')
        _submit_modal(page,{'reason':'TEST morning activation only; no actual cash'},check=['confirmed'])
        with db.tx() as c:
            assert (c.execute("SELECT sum(amount_halalas) n FROM custody_events WHERE shift_id='void-browser-driver'").fetchone()['n'] or 0)==0
            if unpaid_cancelled:
                assert c.execute("SELECT count(*) n FROM refunds WHERE order_id='void-browser-order'").fetchone()['n']==0
                assert c.execute("SELECT payment_status FROM orders WHERE id='void-browser-order'").fetchone()['payment_status']=='unpaid'
            else:
                assert c.execute("SELECT record_kind FROM refunds WHERE order_id='void-browser-order'").fetchone()['record_kind']=='test_void'
                assert c.execute("SELECT amount_halalas FROM collections WHERE id='void-browser-col'").fetchone()['amount_halalas']==2050
        expect(page.locator('#content')).to_have_attribute('aria-busy','false')
        page.locator('[data-action=dispatch-inspect][data-id="void-browser-order"]').first.click()
        expect(page.locator('#dispatch-panel-details')).to_contain_text('ملغي تجريبيًا — لا صرف نقدي' if lang=='ar' else 'Test void — no cash disbursed')
        expect(page.locator('[data-action=test-void-order]')).to_have_count(0)
        _capture(page,f'owner-test-void-complete-{lang}-{unpaid_cancelled}')
        assert not errors
    finally:context.close()
