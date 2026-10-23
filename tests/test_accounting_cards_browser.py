"""Isolated layout acceptance; mocked read data never touches operational state."""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser acceptance')
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('width,lang', [(1440,'ar'),(390,'ar'),(1440,'en'),(390,'en')])
def test_accounting_cards_and_filters_fit_and_work(width, lang):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        launch = {'headless': True, 'args': ['--no-sandbox']}
        if os.getenv('CHROMIUM_EXECUTABLE'): launch['executable_path'] = os.environ['CHROMIUM_EXECUTABLE']
        browser = runtime.chromium.launch(**launch)
        page = browser.new_page(viewport={'width': width, 'height': 1100})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.set_content(f'<html lang="{lang}" dir="{"rtl" if lang=="ar" else "ltr"}"><body><h1 id="heading"></h1><main id="content"></main></body></html>')
        page.add_style_tag(content='body{margin:0;padding:24px;background:#faf8f5;font-family:Arial}button,input,select,textarea{font:inherit;padding:10px;border:1px solid #ddd;border-radius:8px}button{cursor:pointer;background:#a64116;color:white}main{max-width:1280px;margin:auto}*{box-sizing:border-box}')
        page.add_style_tag(path=str(ROOT/'public/assets/accounting.css'))
        page.add_script_tag(path=str(ROOT/'public/assets/accounting.js'))
        page.evaluate('''async lang => {
          const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
          window.accountingQueries=[];
          const ctx={lang,user:{id:'accountant',role:'owner'},tr:(ar,en)=>lang==='en'?en:ar,esc,money:v=>(v/100).toFixed(2),cents:v=>Math.round(Number(v)*100),heading:t=>document.querySelector('#heading').textContent=t,modal:()=>{},closeModal:()=>{},mutation:async()=>({}),
          api:async url=>url.includes('/options')?{today:'2026-10-21',can_report:true,can_review:true,branches:[{id:'branch',branch_no:1,name:'حفر ١',city:'حفر الباطن'},{id:'branch2',branch_no:2,name:'حفر ٢',city:'حفر الباطن'},{id:'outside',branch_no:3,name:'الدمام',city:'الدمام'}],people:[{id:'driver',role:'driver',display_name:'سائق اختبار'}],products:[{id:'coffee',name:'قهوة'},{id:'unsold',name:'صنف بلا مبيعات'}],categories:{},states:{},responsibilities:{}}:url.includes('driver-settlements')?{settlements:[]}:url.includes('manager-closures')?{closures:[]}:(window.accountingQueries.push(url),{rows:[{label:'صنف بلا مبيعات',quantity:0}],columns:[{key:'label',label:'الصنف'},{key:'quantity',label:'الكمية'}],totals:{quantity:0},definitions:'TEST ONLY'})};
          await window.KPSAccounting.open(ctx);
        }''', lang)
        cards = page.locator('.ac-section-card')
        assert cards.count() == 6
        boxes = cards.evaluate_all('(els)=>els.map(e=>({w:e.getBoundingClientRect().width,h:e.getBoundingClientRect().height}))')
        assert max(b['w'] for b in boxes)-min(b['w'] for b in boxes) < 2
        assert page.locator('[data-ac=reports]').get_attribute('aria-pressed') == 'true'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('[name=start]').fill('2026-10-20')
        page.locator('#ac-filters button[type=submit]').click()
        assert page.locator('[name=start]').input_value() == '2026-10-20'
        page.locator('[name=group]').select_option('items')
        page.locator('[name=product_id]').select_option('unsold')
        page.locator('[data-ac=hafar-branches]').click()
        page.wait_for_function("window.accountingQueries.some(u=>new URL(u,'https://test.invalid').searchParams.get('branch_ids')==='branch,branch2')")
        applied = page.evaluate("new URL(window.accountingQueries.at(-1),'https://test.invalid').searchParams.toString()")
        from urllib.parse import parse_qs
        assert parse_qs(applied)['branch_ids']==['branch,branch2']
        assert parse_qs(applied)['product_id']==['unsold']
        assert parse_qs(applied)['group']==['items']
        assert page.locator('#ac-results').inner_text().find('صنف بلا مبيعات')>=0
        page.locator('[name=branch_ids]').select_option('branch2')
        page.locator('#ac-filters button[type=submit]').click()
        page.wait_for_function("new URL(window.accountingQueries.at(-1),'https://test.invalid').searchParams.get('branch_ids')==='branch2'")
        page.locator('[data-ac=all-branches]').click()
        page.wait_for_function("new URL(window.accountingQueries.at(-1),'https://test.invalid').searchParams.get('branch_ids')===''")
        page.locator('[data-ac=settlements]').click()
        page.wait_for_function("document.querySelector('[data-ac=settlements]').getAttribute('aria-pressed')==='true'")
        assert page.locator('#ac-filters').is_hidden()
        page.locator('[data-ac=reports]').click()
        assert page.locator('#ac-filters').is_visible()
        assert page.locator('[name=start]').input_value() == '2026-10-20'
        assert not errors
        output=ROOT/'test-artifacts';output.mkdir(exist_ok=True)
        page.screenshot(path=str(output/f'accounting-cards-{lang}-{width}.png'),full_page=True)
        browser.close()
