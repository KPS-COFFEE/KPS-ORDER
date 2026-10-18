"""Synthetic submitted invoices: real SMS manager UI confirms recorded output.

This verifies the software confirmation workflow, not physical paper delivery.
"""
import os

import pytest

from app import db
from test_browser import ARTIFACTS, _customer_session, _context, _staff, _owner_login, chromium_browser
from tests.browser_sms_helpers import sms_browser_app

pytestmark = [pytest.mark.postgres,
              pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit Chromium run')]


@pytest.mark.parametrize('lang,width', [(lang, width) for lang in ('ar', 'en') for width in (390, 768, 1280)])
def test_manager_confirms_invoice_before_customer_preparation(sms_browser_app, chromium_browser, lang, width):
    from playwright.sync_api import expect
    base = sms_browser_app
    customer, token, csrf = _customer_session()
    manager = _staff('delivery_manager')
    oid, sid, device, job = [db.new_id(prefix) for prefix in ('print_progress_', 'manager_shift_', 'print_device_', 'print_job_')]
    with db.tx() as c:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps','[\"kps-branch-1\"]')", (sid, manager['id']))
        snapshot = {'branch_name': 'فرع اختبار', 'order_reference': 'TEST-PRINT-PROGRESS-001',
                    'items': [], 'delivery_workflow': 'manager_dispatch', 'manager_shift_id': sid}
        c.execute("""INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,
            delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer)
            VALUES(%s,'kps','kps-branch-1',%s,'delivery','accepted','pending','cash',1000,0,1000,%s,%s)""",
                  (oid, customer, db.json(snapshot), db.json({'name': 'عميل اختبار الطباعة'})))
        c.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,printer_name,created_by,last_seen_at) VALUES(%s,'kps','kps-branch-1','Synthetic browser device','Synthetic Windows printer',%s,now())", (device, manager['id']))
        # Simulate a durable Windows acknowledgement, not a physical printer.
        c.execute("""INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,order_id,kind,status,
            payload,requested_by,claimed_at,finished_at) VALUES(%s,'kps','kps-branch-1',%s,%s,'order',
            'submitted',%s,%s,now(),now())""", (job, device, oid, db.json({'kind':'order','order_revision':0,'lines':[]}), manager['id']))
    customer_ctx, customer_page, customer_errors = _context(chromium_browser, base, token, csrf)
    manager_ctx, manager_page, manager_errors = _context(chromium_browser, base)
    try:
        customer_ctx.add_init_script(f"localStorage.setItem('kps_customer_lang','{lang}')")
        manager_ctx.add_init_script(f"localStorage.setItem('kps_ops_lang','{lang}')")
        for page in (customer_page, manager_page):
            page.set_viewport_size({'width': width, 'height': 950})

        def open_customer():
            customer_page.goto(base+'/', wait_until='networkidle')
            customer_page.locator('#orders-button').click()
            action = 'View order and conversation' if lang == 'en' else 'عرض الطلب ومحادثته'
            customer_page.locator('.order-card').filter(has_text='TEST-PRINT-PROGRESS-001').get_by_role('button', name=action).click()
            return customer_page.locator('#order-detail [aria-current=step]')

        # A submitted Windows job alone must keep the customer at received.
        expect(open_customer()).to_have_text('Your order has been received' if lang == 'en' else 'تم استلام طلبك')
        _owner_login(manager_ctx, base, manager)
        manager_page.goto(base+'/ops', wait_until='networkidle')
        manager_page.locator('#navigation [data-page=orders]').click()
        manager_page.locator(f'#content [data-action=order-detail][data-id="{oid}"]').click()
        manager_page.locator(f'#modal-body [data-action=confirm-print-output][data-id="{oid}"]').click()
        modal = manager_page.locator('#modal')
        expect(modal.locator('select[name=job_id]')).to_have_value(job)
        checkbox = modal.locator('input[name=paper_confirmed]')
        expect(checkbox).not_to_be_checked()
        assert checkbox.get_attribute('required') is not None
        with db.tx() as c:
            assert not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='order.print_output_confirmed'", (oid,)).fetchone()
        checkbox.check()
        assert modal.evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        manager_page.screenshot(path=str(ARTIFACTS/f'order-print-confirmation-{lang}-{width}.png'), full_page=True)
        with manager_page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/printing/orders/'+oid+'/output')) as saved:
            modal.locator('button[type=submit]').click()
        assert saved.value.status == 200, saved.value.text()
        expect(modal).not_to_be_visible()
        expect(open_customer()).to_have_text('Your order is being prepared' if lang == 'en' else 'جاري تجهيز طلبك')
        with db.tx() as c:
            event = c.execute("SELECT actor_id,details FROM order_events WHERE order_id=%s AND event_type='order.print_output_confirmed'", (oid,)).fetchone()
            assert event['actor_id'] == manager['id']
            assert event['details'] == {'job_id':job, 'branch_id':'kps-branch-1', 'revision':0, 'source':'operator_paper_confirmation'}
            assert c.execute('SELECT preparation_status FROM orders WHERE id=%s', (oid,)).fetchone()['preparation_status'] == 'accepted'
        customer_page.screenshot(path=str(ARTIFACTS/f'order-print-confirmed-progress-{lang}-{width}.png'), full_page=True)
        assert not customer_errors and not manager_errors
    finally:
        customer_ctx.close()
        manager_ctx.close()
