"""Ticket creation, updates and daily report through the real manager UI."""
import os
from uuid import uuid4
import pytest
from app import db
from test_browser import _capture,_context,_owner_login,_staff,_customer_session,chromium_browser,live_app,synthetic_mail
pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance')]


def test_manager_ticket_create_update_report(live_app,chromium_browser):
    from playwright.sync_api import expect
    manager=_staff('delivery_manager')
    customer,_,_=_customer_session()
    oid='TEST-support-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps','kps-branch-1',%s,'delivery','new','pending','cash',1000,0,1000,%s,%s)",(oid,customer,db.json({'order_reference':'TEST-SUPPORT-001','items':[]}),db.json({'name':'TEST ONLY'})))
    context,page,errors=_context(chromium_browser,live_app)
    _owner_login(context,live_app,manager)
    try:
        page.goto(live_app+'/ops')
        page.locator('#navigation [data-page=orders]').click()
        page.locator('[data-action=order-detail][data-id="'+oid+'"]').click()
        page.locator('#modal [data-action=order-support]').click()
        page.locator('#modal select[name=category]').select_option('driver')
        page.locator('#modal textarea[name=note]').fill('TEST ONLY مشكلة مع السائق')
        page.locator('#modal button[type=submit]').click()
        expect(page.locator('#modal')).to_contain_text('TKT-')
        page.locator('#modal [data-action=support-update]').click()
        page.locator('#modal select[name=status]').select_option('in_progress')
        page.locator('#modal textarea[name=note]').fill('TEST ONLY جارٍ التواصل مع السائق')
        page.locator('#modal button[type=submit]').click()
        expect(page.locator('#modal')).to_contain_text('قيد المعالجة')
        _capture(page,'TEST_ONLY_order_support')
        page.keyboard.press('Escape')
        page.locator('#navigation [data-page=support]').click()
        expect(page.locator('#content')).to_contain_text('TEST-SUPPORT-001')
        expect(page.locator('#content')).to_contain_text('قيد المعالجة')
        _capture(page,'TEST_ONLY_support_daily')
        assert not errors
    finally:context.close()
