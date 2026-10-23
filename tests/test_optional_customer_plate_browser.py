"""Customer input and cashier cards, plus real HTTP order creation on CI."""
import os
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright, expect
from app import db
from test_browser import live_app, chromium_browser, synthetic_mail, _context, _owner_login, _staff
from test_launch_browser import _configure_branches

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('engine', ['chromium','webkit'])
@pytest.mark.parametrize('lang', ['ar','en'])
def test_flexible_input_explicit_saudi_preview_and_mobile_cashier(engine,lang):
    with sync_playwright() as p:
        options = {}
        if engine == 'chromium' and os.getenv('KPS_TEST_CHROMIUM_EXECUTABLE'):
            options['executable_path'] = os.environ['KPS_TEST_CHROMIUM_EXECUTABLE']
        browser = getattr(p,engine).launch(**options)
        try:
            page=browser.new_page(viewport={'width':390,'height':844})
            page.set_content(f'<html lang="{lang}" dir="'+('rtl' if lang=='ar' else 'ltr')+'"><body><form><label><span>Plate</span><input id="car-plate" maxlength="30"></label><label><input id="car-color" value="لؤلؤي خاص"></label><div id="cashier"></div></form></body></html>')
            page.add_style_tag(path=str(ROOT/'public/customer.css'))
            for asset in ['saudi-plate.js','customer-car-plate.js','car-color.js']:
                page.add_script_tag(path=str(ROOT/'public/assets'/asset))
            plate=page.locator('#car-plate')
            assert plate.evaluate('e=>e.checkValidity()')
            expect(page.locator('#car-plate-format')).to_have_value('flexible')
            for entered,expected in [('', ''),('١٢۳٤٥','12345'),('1234567','1234567'),('ح م د ١٢٣٤','ح م د 1234'),('ABC 1234','ABC 1234'),('A / ۰۱۲۳۴۵','A / 012345')]:
                plate.fill(entered)
                expect(plate).to_have_value(expected)
                assert plate.evaluate('e=>e.checkValidity()')
                expect(page.locator('[data-saudi-plate]')).to_have_count(0)
            plate.fill('DZJ 1234')
            page.locator('#car-plate-format').select_option('saudi')
            expect(page.locator('[data-saudi-plate]')).to_have_count(1)
            plate.fill('1234567')
            assert plate.evaluate('e=>e.checkValidity()')
            expect(page.locator('[data-saudi-plate]')).to_have_count(0)
            page.locator('#car-color-custom').fill('Special Pearl')
            page.evaluate("document.documentElement.lang=document.documentElement.lang==='ar'?'en':'ar'")
            expect(page.locator('#car-color-custom')).to_have_value('Special Pearl')
            expect(plate).to_have_value('1234567')
            # Execute the actual shipped cashier renderer without booting operations.
            source=(ROOT/'public/ops.js').read_text()
            function=source[source.index('  function customerCarDetails('):source.index('  const printStatus')]
            for value in ['', '1234567', 'ح م د ١٢٣٤', '<img src=x onerror=alert(1)>']:
                car={'model':'Corolla','color':'Pearl','plate':value}
                result=page.evaluate('''({code,car,lang})=>{
                  const tr=(ar,en)=>lang==='en'?en:ar;
                  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
                  eval(code);const o={service:'car',customer:{car}};const before=JSON.stringify(o);
                  document.querySelector('#cashier').innerHTML=customerCarDetails(o);
                  return {unchanged:before===JSON.stringify(o),other:customerCarDetails({...o,service:'delivery'})};
                }''',{'code':function,'car':car,'lang':lang})
                assert result == {'unchanged':True,'other':''}
                card=page.locator('[data-customer-car]')
                expect(card).to_contain_text('Corolla')
                expect(card).to_contain_text(value or ('لم يُدخل رقم اللوحة' if lang=='ar' else 'No plate entered'))
                expect(card.locator('img')).to_have_count(0)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        finally:
            browser.close()

@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires isolated HTTP/PostgreSQL browser acceptance')
@pytest.mark.parametrize('lang,entered,expected', [('ar','',''),('en','١٢٣٤٥٦','123456'),('ar','ح م د ١٢٣٤','ح م د 1234')])
def test_mobile_car_checkout_and_cashier_without_mandatory_plate(live_app,chromium_browser,lang,entered,expected):
    owner,_,_=_context(chromium_browser,live_app)
    context,page,errors=_context(chromium_browser,live_app)
    page.set_viewport_size({'width':390,'height':844})
    try:
        headers=_owner_login(owner,live_app,_staff())
        _configure_branches(owner,live_app,headers)
        page.goto('/')
        expect(page.locator('.product-card')).to_have_count(34)
        if lang=='en':page.locator('#customer-language').click()
        page.locator('#branch-select').select_option('kps-branch-1')
        page.locator('input[name=service][value=car]').check()
        page.locator('#car-model').fill('Corolla')
        page.locator('#car-color-choice').select_option('__custom__')
        page.locator('#car-color-custom').fill('Special Pearl')
        page.locator('#car-plate').fill(entered)
        page.locator('.product-card[data-product-id="kps-01"] .product-content h3 button').click()
        page.locator('#product-add').click()
        expect(page.locator('#product-dialog')).not_to_be_visible()
        page.locator('#customer-name').fill('Synthetic optional plate')
        page.locator('#customer-phone').fill('0500000001')
        expect(page.locator('#checkout-button')).to_be_enabled()
        page.locator('#checkout-button').click()
        expect(page.locator('#guest-form')).to_be_visible()
        page.locator('#guest-name').fill('Synthetic optional plate')
        page.locator('#guest-phone').fill('0500000001')
        page.locator('#guest-submit').click()
        expect(page.locator('#confirm-dialog')).to_be_visible()
        expect(page.locator('#confirm-content')).to_contain_text(expected or ('لم يُدخل رقم اللوحة' if lang=='ar' else 'No plate entered'))
        page.locator('#confirm-submit').click()
        expect(page.locator('#order-detail')).to_be_visible()
        with db.tx() as c:
            order=c.execute("SELECT * FROM orders WHERE customer->>'name'='Synthetic optional plate'").fetchone()
            assert order['customer']['car']=={'model':'Corolla','color':'Special Pearl','plate':expected}
        staff,staff_page,staff_errors=_context(chromium_browser,live_app)
        try:
            _owner_login(staff,live_app,_staff('branch_manager'))
            staff_page.goto('/ops.html')
            if lang=='en':staff_page.locator('#language').click()
            staff_page.locator('[data-page=orders]').click()
            staff_page.locator('[data-action=order-detail][data-id="'+order['id']+'"]').click()
            expect(staff_page.locator('[data-customer-car]')).to_contain_text(expected or ('لم يُدخل رقم اللوحة' if lang=='ar' else 'No plate entered'))
            assert not staff_errors
        finally:staff.close()
        assert not errors
    finally:
        context.close();owner.close()
