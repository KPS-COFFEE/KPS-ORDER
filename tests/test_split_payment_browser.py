"""Bilingual real-browser split collection, with synthetic local orders only."""
import os
import pytest
from app import db
from test_browser import _context, _owner_login, _staff, _customer_session, chromium_browser, synthetic_mail

from tests.browser_sms_helpers import sms_browser_app as live_app

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit isolated browser run required')]

@pytest.mark.parametrize('language', ['ar', 'en'])
def test_split_collection_and_change_in_browser(live_app, chromium_browser, language):
    from playwright.sync_api import expect
    owner = _staff('owner')
    customer, _, _ = _customer_session()
    oid = db.new_id('test_split_')
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps','kps-branch-1',%s,'pickup','ready','not_applicable','split',2000,0,2000,%s,%s)",
                  (oid, customer, db.json({'items': [], 'branch_name': 'TEST branch'}), db.json({'name': 'TEST customer'})))
    context, page, errors = _context(chromium_browser, live_app)
    _owner_login(context, live_app, owner)
    try:
        page.goto(live_app + '/ops')
        page.get_by_role('button', name='الطلبات', exact=True).click()
        if language == 'en':
            page.get_by_role('button', name='English', exact=True).click()
        page.locator(f'[data-action="order-detail"][data-id="{oid}"]').click()
        page.locator(f'[data-action="collect"][data-id="{oid}"]').click()
        modal = page.locator('#modal')
        modal.locator('select[name=method]').select_option('split')
        expect(modal.locator('#split-fields')).to_be_visible()
        modal.locator('input[name=cash_part]').fill('7')
        modal.locator('input[name=tendered]').fill('10')
        modal.locator('input[name=reference]').fill('TEST-TERMINAL-ONLY')
        assert '13' in modal.locator('#card-preview').inner_text() or '١٣' in modal.locator('#card-preview').inner_text()
        assert '3' in modal.locator('#change-preview').inner_text() or '٣' in modal.locator('#change-preview').inner_text()
        modal.locator('input[name=confirmed]').check()
        modal.get_by_role('button', name='حفظ التحصيل' if language == 'ar' else 'Save collection', exact=True).click()
        expect(modal).not_to_be_visible()
        with db.tx() as c:
            rows = c.execute('SELECT method,amount_halalas,change_halalas FROM collections WHERE order_id=%s ORDER BY method', (oid,)).fetchall()
            assert [(r['method'], r['amount_halalas'], r['change_halalas']) for r in rows] == [('card', 1300, 0), ('cash', 700, 300)]
        assert not errors
    finally:
        context.close()
