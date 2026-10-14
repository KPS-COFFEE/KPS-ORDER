"""Synthetic UI-only tests of the real timing asset; not live delivery certification."""
import os
from pathlib import Path
import pytest
from test_browser import chromium_browser

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires explicit real browser UI test')
ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('lang,width',[('ar',390),('ar',1440),('en',390)])
def test_timing_full_text_scoped_filters_paging_and_live_clock(chromium_browser,lang,width):
    from playwright.sync_api import expect
    page=chromium_browser.new_page(viewport={'width':width,'height':900})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    try:
        page.set_content(f'<html lang="{lang}" dir="'+('rtl' if lang=='ar' else 'ltr')+'"><body><div id="page-heading"></div><main style="min-width:0"><div id="content"></div></main></body></html>')
        page.add_style_tag(path=str(ROOT/'public/ops.css'))
        page.add_style_tag(path=str(ROOT/'public/assets/order-timing.css'))
        page.add_script_tag(path=str(ROOT/'public/assets/order-timing.js'))
        page.evaluate('''async lang=>{
            const tr=(ar,en)=>lang==='ar'?ar:en,esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
            const row={id:'TEST_ONLY_1',order_no:471,created_at:'2026-10-14T00:00:00Z',branch_id:'TEST_ONLY_branch',driver_id:'TEST_ONLY_driver',driver_name:'اسم السائق الكامل بدون اختصار',neighborhood:'اسم الحي الكامل بدون اختصار',preparation_status:'handed_over',delivery_status:'picked_up',snapshot:{notes:'ملاحظة طويلة جدًا للعميل تظهر بالكامل بدون قص الكلام أو إخفاء نهايته <script>bad()</script>',items:[{name:'القهوة',note:'بدون سكر'}]},timings:{acceptance_wait:{seconds:60,state:'completed'},preparation:{seconds:120,state:'completed'},driver_wait:{seconds:30,state:'completed'},delivery:{seconds:601,state:'running'},total:{seconds:811,state:'running'}},performance_snapshot:{policies:{delivery:{t1_seconds:600,t2_seconds:1200},total:{t1_seconds:1200,t2_seconds:1800}}}};
            window.requests=[];window.view={};window.row=row;
            window.ctx={tr,esc,api:async path=>{requests.push(path);return {orders:[structuredClone(row)],next_offset:50}},heading:title=>document.querySelector('#page-heading').textContent=title,view,user:{role:'delivery_manager'},branches:[{id:'TEST_ONLY_branch',name:'فرع حفرالباطن — المروج — درايف ثرو'}],branchLabel:()=> 'فرع حفرالباطن — المروج — درايف ثرو',orderLabel:o=>String(o.order_no),statusName:x=>x,datetime:x=>x,active:()=>true,reload:()=>KPSOrderTiming.open(ctx)};
            await KPSOrderTiming.open(ctx);
        }''',lang)
        assert 'manager_view=current' in page.evaluate('requests[0]')
        expect(page.locator('[data-timing-order]')).to_have_count(1)
        expect(page.locator('.timing-notes')).to_contain_text('إخفاء نهايته <script>bad()</script>')
        assert page.locator('.timing-notes script').count()==0
        assert page.locator('.timing-duration').nth(0).inner_text().startswith('3:00')
        assert page.locator('.timing-duration').nth(2).inner_text().startswith('10:01')
        assert page.locator('.timing-duration').nth(2).get_attribute('class').endswith('timing-good')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        if width<1350:
            assert page.locator('.timing-scroll').evaluate('(e)=>e.scrollWidth>e.clientWidth')
        assert page.locator('.timing-notes').evaluate('(e)=>getComputedStyle(e).textOverflow')=='clip'
        (ROOT/'test-artifacts').mkdir(exist_ok=True)
        page.screenshot(path=str(ROOT/'test-artifacts'/f'timing-{lang}-{width}.png'),full_page=True)
        page.wait_for_function('document.querySelectorAll(".timing-duration")[2].innerText.startsWith("10:02")')
        page.locator('[name=view]').select_option('history')
        page.locator('[name=period]').select_option('yesterday')
        page.locator('#timing-filters button').click()
        page.wait_for_function('requests.at(-1).includes("manager_view=history")')
        assert 'period=yesterday' in page.evaluate('requests.at(-1)')
        page.locator('#timing-next').click()
        page.wait_for_function('requests.at(-1).includes("offset=50")')
        assert errors==[]
    finally:
        page.close()
