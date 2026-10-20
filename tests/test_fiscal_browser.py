"""Owner tax preview in the existing actual thermal popup, with real QR decoding."""
import os
from io import BytesIO
from pathlib import Path
import pytest
from PIL import Image
import zxingcpp
from app import db, fiscal
from test_browser import _context, _owner_login, _staff, _customer_session, chromium_browser, synthetic_mail
from tests.browser_sms_helpers import sms_browser_app as live_app
from test_fiscal import sample, decode

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit isolated browser run required')]

@pytest.mark.parametrize('language',['ar','en'])
def test_owner_fiscal_preview_preserves_thermal_layout(live_app,chromium_browser,language):
    from playwright.sync_api import expect
    owner=_staff('owner');customer,_,_=_customer_session();oid=db.new_id('fiscal_browser_')
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps','kps-branch-1',%s,'delivery','ready','delivered','paid','card',3950,0,3950,%s,'{}')",(oid,customer,db.json(sample()['snapshot'])))
    context,page,errors=_context(chromium_browser,live_app)
    _owner_login(context,live_app,owner)
    try:
        page.goto(live_app+'/ops')
        page.get_by_role('button',name='الطلبات',exact=True).click()
        if language=='en':page.get_by_role('button',name='English',exact=True).click()
        page.locator(f'[data-action="order-detail"][data-id="{oid}"]').click()
        with page.expect_popup() as opened:
            page.locator(f'[data-action="fiscal-preview"][data-id="{oid}"]').click()
        popup=opened.value
        expect(popup.locator('html')).to_have_attribute('lang',language)
        expect(popup.locator('header h1')).to_have_text('كبز قهوة مختصة' if language=='ar' else 'KPS COFFEE')
        expect(popup.locator('header p').filter(has_text='كبز قهوة مختصة')).to_have_count(0)
        expect(popup.locator('body')).not_to_contain_text(fiscal.SELLER['legal_name'])
        expect(popup.locator('body')).to_contain_text('311887526600003')
        expect(popup.locator('body')).to_contain_text('39912')
        expect(popup.locator('body')).not_to_contain_text('الدمام')
        expect(popup.locator('body')).to_contain_text('معاينة فقط' if language=='ar' else 'PREVIEW ONLY')
        popup.locator('.receipt-logo').evaluate('(img)=>img.decode()')
        assert popup.locator('.receipt-logo').evaluate('(img)=>img.naturalWidth')==1181
        with db.tx() as c:
            document=c.execute('SELECT document FROM fiscal_previews WHERE order_id=%s',(oid,)).fetchone()['document']
        for width in (80,58):
            popup.emulate_media(media="screen")
            popup.locator('#paper-width').select_option(str(width));popup.emulate_media(media='print')
            dims=popup.locator('.receipt').evaluate('(e)=>({width:e.offsetWidth,scroll:e.scrollWidth})')
            assert dims['scroll']<=dims['width']
            assert abs(dims['width']-width*96/25.4)<2
            qr=popup.locator('img[alt="Receipt QR"]');qr.evaluate('(img)=>img.decode()')
            result=zxingcpp.read_barcode(Image.open(BytesIO(qr.screenshot())))
            assert result and result.text==document['qr_text']
            assert decode(result.text)=={1:fiscal.SELLER['legal_name'],2:'311887526600003',3:document['issued_at'],4:'39.50',5:'5.15'}
            output=Path('test-artifacts');output.mkdir(exist_ok=True)
            popup.locator('.receipt').screenshot(path=str(output/f'fiscal-preview-{language}-{width}mm.png'))
        assert not errors
    finally:context.close()
