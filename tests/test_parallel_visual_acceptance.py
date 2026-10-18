"""Real Chromium synthetic UI fixtures; no database/provider/physical-device claim."""
import json
import os
from pathlib import Path

import pytest
from test_browser import chromium_browser

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Requires explicit real Chromium')


@pytest.mark.parametrize('language', ['ar', 'en'])
@pytest.mark.parametrize('width', [390, 768, 1280])
def test_integrated_cards_tables_and_customer_lookup(chromium_browser, language, width):
    """Render the production asset with long synthetic customer/staff/branch values."""
    from playwright.sync_api import expect
    page = chromium_browser.new_page(viewport={'width': width, 'height': 1050})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.set_content('<html lang="'+language+'" dir="'+('rtl' if language == 'ar' else 'ltr')+'"><body><main><p>Synthetic visual acceptance only / بيانات صناعية للعرض فقط</p><div id="fixture"></div><dialog id="modal" open></dialog></main></body></html>')
        page.add_style_tag(path=str(ROOT / 'public/ops.css'))
        page.add_style_tag(path=str(ROOT / 'public/assets/driver-hours.css'))
        page.add_script_tag(path=str(ROOT / 'public/assets/customer-order.js'))
        page.evaluate('''async language => {
          const tr=(ar,en)=>language==='ar'?ar:en;
          const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
          const long=tr('اسم عميل تجريبي طويل لاختبار ظهور جميع البيانات دون قص أو إخفاء النص','Long synthetic customer and branch name to verify complete readable wrapping');
          document.querySelector('#fixture').innerHTML=`<section class="hours-kpis">${Array.from({length:4},(_,i)=>`<article class="panel"><h2>${tr('ساعات العمل','Work hours')} ${i+1}</h2><strong>123.45</strong></article>`).join('')}</section><section class="panel staff-directory"><div class="table-wrap"><table><thead><tr><th>${tr('العميل','Customer')}</th><th>${tr('الفرع','Branch')}</th><th>${tr('ملاحظات','Notes')}</th><th>${tr('المبلغ','Amount')}</th></tr></thead><tbody><tr><td>${long}</td><td>${long}</td><td>${long.repeat(5)}</td><td class="money">12,345.67</td></tr></tbody></table></div></section>`;
          window.fixtureCalls=[];
          const api=async path=>{fixtureCalls.push(path);if(path==='/api/shifts/current')return {shifts:[{user_id:'TEST_MANAGER',kind:'manager',status:'open',branch_ids:['TEST_BRANCH']}]};if(path.startsWith('/api/catalog'))return {products:[]};if(path.startsWith('/api/orders/customer-lookup'))return {contacts:[{name:long,phone:'+966500000001',addresses:[{address:long,neighborhood:long,location:{lat:28.4,lng:45.9}}]}],ambiguous:false,verified:false};throw Error('Unexpected fixture path: '+path);};
          await KPSCustomerOrder.open({api,mutation:async()=>{throw Error('This visual fixture cannot write')},tr,esc,money:String,user:{id:'TEST_MANAGER',brand_id:'kps'},branches:[{id:'TEST_BRANCH',name:long}],modal:(title,html)=>{document.querySelector('#modal').innerHTML='<h2>'+esc(title)+'</h2>'+html},closeModal:()=>{},onCreated:()=>{}});
        }''', language)
        phone = page.locator('#staff-order-form [name=phone]')
        phone.fill('0500000001')
        choice = page.locator('.customer-address-choice')
        expect(choice).to_be_visible()
        # Stored contact never replaces entered values until explicit staff selection.
        expect(page.locator('[name=address]')).to_have_value('')
        choice.click()
        expect(page.locator('[name=lat]')).to_have_value('28.4')
        expect(page.locator('[name=lng]')).to_have_value('45.9')
        assert '+966500000001' in page.evaluate('fixtureCalls.find(x=>x.includes("customer-lookup"))') or '%2B966500000001' in page.evaluate('fixtureCalls.find(x=>x.includes("customer-lookup"))')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert page.locator('dialog').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
        screenshots=ROOT/'test-artifacts'
        screenshots.mkdir(exist_ok=True)
        page.screenshot(path=str(screenshots/f'parallel-customer-{language}-{width}.png'), full_page=True)
        page.locator('#modal').evaluate('(e)=>e.close()')
        assert page.locator('td').first.evaluate('(e)=>getComputedStyle(e).whiteSpace') == 'normal'
        assert page.locator('td').first.evaluate('(e)=>getComputedStyle(e).textOverflow') == 'clip'
        assert page.locator('td').first.evaluate('(e)=>e.scrollHeight<=e.clientHeight+1')
        if width == 1280:
            boxes=page.locator('.hours-kpis article').all()
            assert len({round(e.bounding_box()['y']) for e in boxes})==1
        page.screenshot(path=str(screenshots/f'parallel-cards-tables-{language}-{width}.png'), full_page=True)
        assert not errors
    finally:
        page.close()
