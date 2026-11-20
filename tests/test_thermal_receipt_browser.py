"""Actual order popup, both receipt types and narrow paper with long orders."""
import os
import pytest
from app import db
from test_browser import _context, _owner_login, _staff, _customer_session, chromium_browser, live_app, synthetic_mail

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit isolated browser run required')]

@pytest.mark.parametrize('language', ['ar', 'en'])
def test_thermal_receipts_expand_and_keep_options(live_app, chromium_browser, language):
    from playwright.sync_api import expect
    owner = _staff('owner')
    customer, _, _ = _customer_session()
    oid = db.new_id('test_receipt_')
    items = [{'name': f'TEST {i} صنف طويل مع إضافات كثيرة', 'quantity': 2,
              'line_total_halalas': 2000, 'note': '<img src=x onerror=alert(1)> بدون سكر',
              'units': [{'option_snapshot': {'milk': {'name': 'الحليب', 'choices': [{'name': 'شوفان'}]}}}]} for i in range(40)]
    items[0]['name']='كوكيز شوكليت'
    snapshot = {'items': items, 'branch_name': 'TEST branch', 'order_reference': 'HFR1-261011-10000', 'notes': 'TEST LAST NOTE'}
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps','kps-branch-1',%s,'pickup','ready','not_applicable','split',80000,0,80000,%s,%s)", (oid, customer, db.json(snapshot), db.json({'name': 'TEST customer'})))
    context, page, errors = _context(chromium_browser, live_app)
    _owner_login(context, live_app, owner)
    try:
        page.goto(live_app + '/ops')
        page.get_by_role('button', name='الطلبات', exact=True).click()
        if language == 'en':
            page.get_by_role('button', name='English', exact=True).click()
        page.locator(f'[data-action="order-detail"][data-id="{oid}"]').click()
        for kind in ('order', 'preparation'):
            with page.expect_popup() as popup:
                page.locator(f'[data-action="print-{kind}"][data-id="{oid}"]').click()
            receipt = popup.value
            expect(receipt.locator('body')).not_to_contain_text('TEST customer')
            expect(receipt.locator('.receipt-meta')).not_to_contain_text('العميل' if language == 'ar' else 'Customer')
            expect(receipt.locator('.receipt-item')).to_have_count(41)
            expect(receipt.locator('.reference')).to_have_text('HFR1-261011-10000')
            expect(receipt.locator('.receipt-item img')).to_have_count(0)
            expect(receipt.locator('h1')).to_have_text('KPS COFFEE')
            expect(receipt.locator('html')).to_have_attribute('lang','en')
            expect(receipt.locator('.receipt-item').first).to_contain_text('Chocolate Cookies')
            expect(page.locator('html')).to_have_attribute('lang',language)
            receipt.locator('.receipt-logo').evaluate('(img)=>img.decode()')
            assert receipt.locator('.receipt-logo').evaluate('(img)=>img.naturalWidth') == 1181
            expect(receipt.locator('.options').first).to_contain_text('شوفان')
            expect(receipt.locator('.totals')).to_have_count(1 if kind == 'order' else 0)
            expect(receipt.locator('img[alt="Receipt QR"]')).to_have_count(1 if kind == 'order' else 0)
            for width in ('80', '58'):
                receipt.locator('#paper-width').select_option(width)
                receipt.emulate_media(media='print')
                expect(receipt.locator('.print-tools')).not_to_be_visible()
                dimensions = receipt.locator('.receipt').evaluate('(e)=>({width:e.offsetWidth,height:e.offsetHeight,scroll:e.scrollWidth})')
                assert abs(dimensions['width'] - int(width)*96/25.4) < 2
                assert dimensions['scroll'] <= dimensions['width']
                if kind == 'order':
                    from io import BytesIO
                    from PIL import Image
                    import zxingcpp
                    qr = receipt.locator('img[alt="Receipt QR"]')
                    qr.evaluate('(img)=>img.decode()')
                    box = qr.bounding_box()
                    assert abs(box['width']-box['height']) < 1
                    decoded = zxingcpp.read_barcode(Image.open(BytesIO(qr.screenshot())))
                    assert decoded and oid in decoded.text
                assert dimensions['height'] > 1500
                assert receipt.locator('.receipt header').evaluate('(e)=>e.offsetHeight') < 230
                expect(receipt.locator('.receipt-item').last).to_contain_text('TEST LAST NOTE')
                receipt.emulate_media(media='screen')
            if kind=='order':
                from pathlib import Path
                evidence=Path(__file__).resolve().parents[1]/'test-artifacts'
                evidence.mkdir(exist_ok=True)
                receipt.screenshot(path=str(evidence/f'legal-thermal-safe-{language}.png'),full_page=True)
            receipt.close()
        assert not errors
    finally:
        context.close()
