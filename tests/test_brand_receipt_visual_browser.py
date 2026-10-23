"""Render approved source logos with the real receipt CSS on both paper widths."""
import io
import os
from pathlib import Path

import pytest
from test_delivery_workspace_browser import static_customer

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser run')


@pytest.mark.parametrize('language', ['ar', 'en'])
@pytest.mark.parametrize('preparation', [False, True])
def test_original_brand_logo_stays_visible_and_layout_matches(static_customer, language, preparation):
    from PIL import Image, ImageStat
    from playwright.sync_api import sync_playwright

    source = (Path(__file__).resolve().parents[1]/'public/ops.js').read_text()
    complimentary_helper = next(line for line in source.splitlines()
                                if line.startswith('  const complimentaryName='))
    helper = complimentary_helper + '\n' + source[source.index('  function receiptHTML('):source.index('  function bindReceiptWindow(')]
    order = {'brand_id': 'kps', 'created_at': '2026-10-23T10:00:00Z',
             'snapshot': {'order_reference': 'HFR1-261023-0001'}, 'service': 'delivery',
             'preparation_status': 'ready', 'payment_method': 'cash', 'payment_status': 'unpaid',
             'subtotal_halalas': 1000, 'delivery_fee_halalas': 1000, 'total_halalas': 2000,
             'items': [{'name': 'TEST Cookies', 'quantity': 1, 'line_total_halalas': 1000}]}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 800, 'height': 900})
        page.goto(static_customer+'/ops.html')
        dimensions = {}
        for brand in ('kps', 'enbishan'):
            order['brand_id'] = brand
            html = page.evaluate('''({helper,order,preparation,language})=>{
                const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
                const orderLabel=o=>o.snapshot.order_reference,branchLabel=()=> 'TEST branch';
                const serviceName=(s,tr)=>tr('توصيل','Delivery'),statusName=String;
                return eval(helper+';receiptHTML(order,preparation,language)');
            }''', {'helper': helper, 'order': order, 'preparation': preparation, 'language': language})
            page.set_content(html)
            logo = page.locator('.receipt-logo')
            logo.evaluate('(img)=>img.decode()')
            if brand == 'enbishan':
                assert logo.evaluate('(img)=>getComputedStyle(img).filter') == 'none'
                assert logo.get_attribute('src').endswith('/assets/enbishan-original.jpeg')
            # A black square would have no pixel variance. This inspects actual rendered CSS/image pixels.
            pixels = Image.open(io.BytesIO(logo.screenshot())).convert('L')
            assert ImageStat.Stat(pixels).var[0] > 100, 'Approved logo foreground must remain visibly distinct'
            dimensions[brand] = []
            for width in (80, 58):
                page.evaluate('(width)=>document.body.style.setProperty("--paper",width+"mm")',width)
                box = page.locator('.receipt').bounding_box()
                dimensions[brand].append((round(box['width'],1),round(box['height'],1)))
                assert page.locator('.receipt').evaluate('(node)=>node.scrollWidth<=node.clientWidth')
            folder = Path('test-artifacts');folder.mkdir(exist_ok=True)
            page.screenshot(path=str(folder/f'brand-receipt-{brand}-{language}-{preparation}.png'))
        assert dimensions['kps'] == dimensions['enbishan'], 'Original source logo retains identical receipt geometry'
        browser.close()
