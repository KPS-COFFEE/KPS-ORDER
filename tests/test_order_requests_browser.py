"""Actual Chromium + HTTP/PostgreSQL; no intercepted application API responses."""
import os
from pathlib import Path

import pytest

from app import db
from test_browser import ARTIFACTS, _customer_session, _context, _staff, _owner_login, chromium_browser
from tests.browser_sms_helpers import sms_browser_app

pytestmark=[pytest.mark.postgres, pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1', reason='Explicit Chromium run')]


@pytest.mark.parametrize('lang,width,kind', [('ar',390,'cancel'),('ar',768,'duplicate'),('ar',1280,'cancel'),
                                         ('en',390,'duplicate'),('en',768,'cancel'),('en',1280,'duplicate')])
def test_customer_request_persists_in_manager_inbox_until_decision(sms_browser_app,chromium_browser,lang,width,kind):
    from playwright.sync_api import expect
    base=sms_browser_app
    customer,token,csrf=_customer_session(); manager=_staff('delivery_manager');oid=db.new_id('request_browser_')
    snapshot={'items':[{'product_id':'test-product','name':'قهوة اختبار','quantity':1,'units':[],'note':''}],
              'notes':'','branch_name':'فرع اختبار','order_reference':'TEST-REQUEST-001'}
    with db.tx() as c:
        c.execute("""INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,
            delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer)
            VALUES(%s,'kps','kps-branch-1',%s,'delivery','ready','pending','cash',1000,0,1000,%s,%s)""",
            (oid,customer,db.json(snapshot),db.json({'name':'عميل اختبار','phone':'+966500000001'})))
    ctx,page,errors=_context(chromium_browser,base,token,csrf)
    manager_ctx,manager_page,manager_errors=_context(chromium_browser,base)
    try:
        page.set_viewport_size({'width':width,'height':950})
        page.goto(base+'/',wait_until='networkidle')
        def open_customer():
            page.evaluate('''async({oid,csrf,lang})=>{
              document.documentElement.lang=lang;document.documentElement.dir=lang==='ar'?'rtl':'ltr';
              const api=async(path,options={})=>{
                const response=await fetch(path,{method:options.method||'GET',headers:{'Content-Type':'application/json',
                  'X-KPS-Context':'customer','X-CSRF-Token':csrf,...(options.method?{'Idempotency-Key':crypto.randomUUID()}:{})},
                  ...(options.method?{body:JSON.stringify(options.data)}:{})});
                const result=await response.json();if(!response.ok)throw new Error(result.detail?.message||JSON.stringify(result));return result;
              };
              await KPSAmendments.open({oid,api,mutation:(path,data)=>api(path,{method:'POST',data}),user:{role:'customer'}});
            }''',{'oid':oid,'csrf':csrf,'lang':lang})
        open_customer()
        page.locator(f'[data-request-kind={kind}]').click()
        page.locator('#service-request-form [name=note]').fill('ملاحظة اختبار <script>غير قابل للتنفيذ</script>')
        page.locator('#service-request-form [type=submit]').click()
        pending='بانتظار المعالجة' if lang=='ar' else 'Awaiting processing'
        expect(page.locator('.service-request h3')).to_contain_text(pending)
        page.locator('.amend-dialog [data-close]').click();open_customer()
        expect(page.locator('.service-request h3')).to_contain_text(pending)
        assert page.locator('.service-request script').count()==0
        page.screenshot(path=str(ARTIFACTS/f'customer-request-{lang}-{width}.png'),full_page=True)
        _owner_login(manager_ctx,base,manager)
        manager_ctx.add_init_script(f"localStorage.setItem('kps_ops_lang','{lang}')")
        manager_page.set_viewport_size({'width':width,'height':950})
        manager_page.goto(base+'/ops',wait_until='networkidle')
        alert=manager_page.locator(f'#operational-alerts [data-action=amend-order][data-id="{oid}"]')
        expect(alert).to_be_visible(timeout=20000)
        expect(manager_page.locator('#operational-alerts')).to_contain_text(pending)
        alert.click()
        expect(manager_page.locator('.service-request h3')).to_contain_text(pending)
        manager_page.locator('.amend-dialog [data-close]').click()
        expect(alert).to_be_visible();alert.click()
        # Current controlled requests enter the documented intervention flow;
        # opening the request alone must never resolve or cancel it.
        expect(manager_page.locator('.service-request [name=request_decision]')).to_have_count(0)
        manager_page.get_by_role('button',name='إلغاء أو بلاغ وتصعيد للمدير' if lang=='ar' else 'Documented cancellation or issue',exact=True).click()
        dialog=manager_page.locator('.intervention-dialog')
        create=dialog.locator('form:has([name=kind])')
        create.locator('[name=kind]').select_option('cancel')
        create.locator('[name=reason_code]').select_option('customer_cancel' if kind=='cancel' else 'duplicate')
        create.locator('[name=note]').fill('تمت مراجعة الطلب مع العميل')
        # Leave evidence selectors empty: the matching website request must
        # link automatically, without a duplicate screenshot.
        create.locator('button').first.click()
        approval=dialog.locator('.intervention-request form:has(button[value=approve])')
        expect(approval.locator('button[value=approve]')).to_be_visible()
        approval.locator('[name=contact_result]').select_option('agreed')
        approval.locator('[name=note]').fill('تمت مراجعة الطلب مع العميل واعتماد الإلغاء')
        assert dialog.evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        manager_page.screenshot(path=str(ARTIFACTS/f'manager-request-{lang}-{width}.png'),full_page=True)
        approval.locator('button[value=approve]').click()
        expect(dialog.locator('.intervention-state')).to_have_text('تم الاعتماد')
        page.locator('.amend-dialog [data-close]').click();open_customer()
        expect(page.locator('.service-request h3')).to_contain_text('تمت المعالجة' if lang=='ar' else 'Processed')
        with db.tx() as c:
            assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='cancelled'
            assert c.execute("SELECT actor_id FROM order_events WHERE order_id=%s AND event_type='customer_request.decided'",(oid,)).fetchone()['actor_id']==manager['id']
            evidence=c.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='intervention.requested'",(oid,)).fetchone()['details']
            assert evidence['customer_request_id'] and evidence['evidence_message_id']
            assert not evidence.get('evidence_attachment_id') and not evidence.get('attachment_unavailable')
        assert not errors and not manager_errors
    finally:
        ctx.close();manager_ctx.close()
