"""Scoped wall-display behavior against synthetic APIs; no external map calls."""
import os
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser run')


def test_tracking_city_rotation_completed_drilldown_and_cleanup():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        options = {'headless': True}
        if os.getenv('TEST_CHROMIUM_EXECUTABLE'):
            options['executable_path'] = os.environ['TEST_CHROMIUM_EXECUTABLE']
        browser = p.chromium.launch(**options)
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.set_content('<main id="host"></main>')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.add_style_tag(path=str(ROOT / 'public/assets/dispatch-map.css'))
        page.add_style_tag(path=str(ROOT / 'public/ops.css'))
        page.evaluate('''() => {
          window.requests=[];window.mapData=[];window.timers=[];window.stopped=false;
          window.clock=Date.now();Date.now=()=>window.clock;
          window.setInterval=fn=>{window.timers.push(fn);return window.timers.length;};window.clearInterval=()=>{};
          window.KPSDispatchMap={mount:async(host,options)=>{window.mapData.push(options);host.classList.add('dispatch-map-host');return {update(data){window.mapData.push(data);},fit(){},resize(){},destroy(){window.stopped=true;}};}};
          window.api=async path=>{
            window.requests.push(path);
            if(path.includes('driver-locations'))return {drivers:[{id:'d1',display_name:'Driver A',branch_ids:['a']},{id:'d2',display_name:'Driver B',branch_ids:['b']}]};
            const q=new URL(path,'https://test').searchParams;
            const completed=q.get('dispatch_stage')==='delivered';
            return {orders:completed?[{id:'old',order_no:'Old order',created_at:'2026-10-20T01:00:00Z',delivery_status:'delivered'},{id:'new',order_no:'New order',created_at:'2026-10-21T01:00:00Z',delivery_status:'delivered'}]:[{id:'active',order_no:'Active order',created_at:'2026-10-21T01:00:00Z',preparation_status:'new'}],next_offset:null,workspace_counts:{current:{total:5,new:1,preparing:1,delivered:2,cancelled:1}}};
          };
        }''')
        page.add_script_tag(path=str(ROOT / 'public/assets/order-tracking.js'))
        page.evaluate("async()=>window.controller=await KPSOrderTracking.mount(document.querySelector('#host'),{api:window.api,branches:[{id:'a',city:'Hafar'},{id:'b',city:'Khafji'}],tr:(ar,en)=>en,lang:'en'})")
        assert page.get_by_role('button', name='Active: 2').is_visible()
        assert 'dispatch_stage=active' in page.evaluate('window.requests[0]')
        page.get_by_label('City', exact=True).select_option('Hafar')
        page.wait_for_function('window.mapData.at(-1).drivers.length===1')
        assert page.evaluate('window.mapData.at(-1).drivers[0].id') == 'd1'
        assert page.locator('.tracking-driver strong').all_text_contents() == ['Driver A']
        assert any('dispatch_city=Hafar' in q for q in page.evaluate('window.requests'))
        page.get_by_role('button', name='Completed: 2').click()
        page.wait_for_selector('text=Completed — newest first')
        assert page.locator('.tracking-order strong').all_text_contents() == ['New order', 'Old order']
        assert page.evaluate('window.mapData.at(-1).orders.length') == 0
        assert any('dispatch_stage=delivered' in q and 'sort=newest' in q for q in page.evaluate('window.requests'))
        page.get_by_role('button', name='Active: 2').click()
        page.get_by_label('Rotate city every 30 seconds').check()
        page.evaluate('window.clock+=31000;window.timers[0]()')
        page.wait_for_function('window.mapData.at(-1).branches[0].city==="Khafji"')
        assert page.get_by_label('City', exact=True).input_value() == 'Khafji'
        # Map and list fit the desktop TV viewport; optional city rotation never calls Directions.
        assert page.locator('.tracking-body').bounding_box()['width'] > 1000
        assert all('directions' not in path for path in page.evaluate('window.requests'))
        page.evaluate('window.controller.destroy();window.timers[0]()')
        assert page.evaluate('window.stopped')
        assert not errors
        browser.close()
