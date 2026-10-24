"""Real owner approval, manager execution and customer print against PostgreSQL.

Only the SMS provider is synthetic; no application routes are intercepted.
This verifies browser output, not a physical thermal printer.
"""
import os
from uuid import uuid4

import pytest
from app import db
from test_core import _prepare_catalog
from test_browser import (_context, _customer_session, _owner_login, _staff,
                          chromium_browser, live_app, synthetic_mail)

pytestmark = [pytest.mark.postgres,
              pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit Chromium acceptance')]


@pytest.mark.parametrize('kind,lang,title', [
    ('compensation','ar','تعويض'), ('completion','en','Order completion')])
def test_owner_authorizes_manager_executes_once_and_customer_copy_has_no_amounts(
        live_app, chromium_browser, kind, lang, title):
    from playwright.sync_api import expect
    _prepare_catalog()
    owner,manager=_staff('owner'),_staff('delivery_manager')
    customer,_,_=_customer_session()
    original=db.new_id('courtesy_original_')
    with db.tx() as c:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps','[\"kps-branch-1\"]')",
                  (db.new_id('courtesy_manager_'),manager['id']))
        snapshot={'order_reference':'TEST-COURTESY-ORIGINAL','items':[{
            'product_id':'kps-01','name':'كولد برو','quantity':1,'unit_price_halalas':950,
            'line_total_halalas':950,'units':[{'options':{}}]}]}
        c.execute("""INSERT INTO orders(id,brand_id,branch_id,customer_id,service,
            preparation_status,delivery_status,payment_status,payment_method,
            subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer)
            VALUES(%s,'kps','kps-branch-1',%s,'delivery','ready','delivered','paid','cash',950,500,1450,%s,'{}')""",
                  (original,customer,db.json(snapshot)))
        reference=c.execute('SELECT snapshot FROM orders WHERE id=%s',(original,)).fetchone()['snapshot']['order_reference']
    owner_ctx,owner_page,owner_errors=_context(chromium_browser,live_app)
    manager_ctx,manager_page,manager_errors=_context(chromium_browser,live_app)
    try:
        for context in (owner_ctx,manager_ctx):
            context.add_init_script(f"localStorage.setItem('kps_ops_lang','{lang}')")
        _owner_login(owner_ctx,live_app,owner)
        manager_headers=_owner_login(manager_ctx,live_app,manager)
        owner_page.goto(live_app+'/ops')
        owner_page.locator('#navigation [data-page=orders]').click()
        owner_page.locator('#complimentary-order').click()
        form=owner_page.locator('#staff-order-form')
        expect(form).to_be_visible()
        form.locator('[name=branch_id]').select_option('kps-branch-1')
        form.locator('[name=kind]').select_option(kind)
        form.locator('[name=original_order_id]').fill(original)
        form.locator('[name=original_invoice_number]').fill(reference)
        form.locator('[name=name]').fill('TEST Courtesy Recipient')
        form.locator('[name=phone]').fill('0501234567')
        form.locator('[name=neighborhood]').fill('حي اختبار')
        form.locator('[name=address]').fill('TEST ONLY Address')
        form.locator('[name=lat]').fill('28.4')
        form.locator('[name=lng]').fill('45.9')
        form.locator('[name=reason]').fill('TEST PRIVATE missing or damaged item')
        form.locator('[data-menu-category=coffee]').click()
        form.locator('.staff-menu-photo[data-menu-add="kps-01"]').click()
        expect(form.locator('#staff-cart > div')).to_have_count(1)
        with owner_page.expect_response(lambda response:response.url.endswith('/api/orders/complimentary/quote')) as quoted:
            form.locator('[type=submit]').click()
        assert quoted.value.status==200,quoted.value.text()
        expect(form.locator('#staff-review')).to_contain_text(title)
        with owner_page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/api/orders/complimentary/authorizations')) as approved:
            form.locator('[type=submit]').click()
        assert approved.value.status==200,approved.value.text()
        approval=approved.value.json()['authorization']
        expect(owner_page.locator('#modal')).not_to_be_visible()
        with db.tx() as c:
            assert c.execute('SELECT executed_order_id FROM complimentary_authorizations WHERE id=%s',(approval['id'],)).fetchone()['executed_order_id'] is None

        manager_page.goto(live_app+'/ops')
        manager_page.locator('#navigation [data-page=orders]').click()
        manager_page.locator('#complimentary-order').click()
        inbox=manager_page.locator('#courtesy-approved')
        expect(inbox).to_contain_text(title)
        expect(inbox.locator('[data-courtesy-index]')).to_have_count(1)
        expect(manager_page.locator('#staff-order-form')).to_have_count(0)
        with manager_page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/api/orders/complimentary')) as executed:
            inbox.locator('[data-courtesy-index]').click()
        assert executed.value.status==200,executed.value.text()
        order_id=executed.value.json()['order']['id']
        expect(manager_page.locator('#modal')).not_to_be_visible()
        with db.tx() as c:
            order=c.execute('SELECT * FROM orders WHERE id=%s',(order_id,)).fetchone()
            evidence=order['snapshot']['complimentary']
            assert evidence['authorized_by']==owner['id'] and evidence['created_by']==manager['id']
            assert evidence['original_invoice_number']==reference
            assert order['total_halalas']==0 and order['payment_status']=='not_required'
            assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(order_id,)).fetchone()['n']==0
            assert c.execute('SELECT count(*) n FROM fiscal_documents WHERE order_id=%s',(order_id,)).fetchone()['n']==0
        # The actual execution request, replayed with a NEW key, cannot consume
        # the same authorization a second time even outside the UI.
        duplicate=manager_ctx.request.post(live_app+'/api/orders/complimentary',
            headers={**manager_headers,'Idempotency-Key':str(uuid4())},
            data=executed.value.request.post_data_json)
        assert duplicate.status==409,duplicate.text()
        manager_page.locator('#complimentary-order').click()
        expect(manager_page.locator('#courtesy-approved [data-courtesy-index]')).to_have_count(0)
        manager_page.keyboard.press('Escape')
        manager_page.locator(f'#content [data-action=order-detail][data-id="{order_id}"]').click()
        with manager_page.expect_popup() as printed:
            manager_page.locator(f'#modal [data-action=print-order][data-id="{order_id}"]').click()
        receipt=printed.value
        expect(receipt.locator('.receipt')).to_be_visible()
        expect(receipt.locator('.receipt')).to_contain_text(title)
        text=receipt.locator('.receipt').inner_text()
        assert 'TEST PRIVATE' not in text and reference not in text
        assert not any(value in text for value in ('ر.س','SAR','9.50','14.50','0.00'))
        assert not owner_errors and not manager_errors
    finally:
        owner_ctx.close()
        manager_ctx.close()
