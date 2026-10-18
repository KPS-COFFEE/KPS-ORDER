"""Unified truthful progress in actual customer UI, with separate payment."""
import os
import pytest
from app import db
from test_browser import ARTIFACTS, _customer_session, _context, chromium_browser
from tests.browser_sms_helpers import sms_browser_app as live_app

pytestmark=[pytest.mark.postgres, pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')]


@pytest.mark.parametrize('lang,width',[(lang,width) for lang in ('ar','en') for width in (390,768,1280)])
def test_customer_progress_truthful_handover_and_responsive(live_app,chromium_browser,lang,width):
    from playwright.sync_api import expect
    customer,token,csrf=_customer_session()
    oid=db.new_id('progress_browser_')
    with db.tx() as c:
        snapshot={'branch_name':'فرع اختبار','order_reference':'TEST-PROGRESS-001','items':[],
                  'delivery_workflow':'manager_dispatch'}
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps','kps-branch-1',%s,'delivery','handed_over','picked_up','cash',1000,0,1000,%s,'{}')",(oid,customer,db.json(snapshot)))
        db.event(c,oid,None,'order.print_output_confirmed',{'branch_id':'kps-branch-1','revision':0})
    context,page,errors=_context(chromium_browser,live_app,token,csrf)
    try:
        context.add_init_script(f"localStorage.setItem('kps_customer_lang','{lang}')")
        page.set_viewport_size({'width':width,'height':950})
        page.goto(live_app+'/',wait_until='networkidle')
        page.locator('#orders-button').click()
        button='View order and conversation' if lang=='en' else 'عرض الطلب ومحادثته'
        page.locator('.order-card').filter(has_text='TEST-PROGRESS-001').get_by_role('button',name=button).click()
        steps=page.locator('#order-detail .customer-progress-steps')
        expect(steps.locator('li')).to_have_count(4)
        expect(steps.locator('[aria-current=step]')).to_have_text('Your order is being prepared' if lang=='en' else 'جاري تجهيز طلبك')
        expect(page.locator('#order-detail .order-status-grid')).to_contain_text('Payment' if lang=='en' else 'الدفع')
        assert page.locator('.customer-progress').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.screenshot(path=str(ARTIFACTS/f'customer-progress-{lang}-{width}.png'),full_page=True)
        assert not errors
    finally:
        context.close()
