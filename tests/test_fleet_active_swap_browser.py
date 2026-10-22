"""Actual fleet/driver JavaScript with synthetic transport; no live fleet changes."""
import os
import pytest
from test_experience_entry_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium acceptance')


@pytest.mark.parametrize('brand,lang,width',[(b,l,w) for b in ('kps','enbishan') for l,w in (('ar',390),('en',1366))])
def test_swap_requires_explicit_confirmation_and_sends_both_vehicle_revisions(static_customer,brand,lang,width):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':width,'height':1000})
        page.goto(static_customer+'/assets/fleet.js')
        page.set_content('<nav id="navigation"><button class="active" data-page="fleet"></button></nav><div id="page-heading"></div><main id="content"></main><dialog id="modal"><div id="modal-body"></div></dialog>')
        page.add_script_tag(url=static_customer+'/assets/fleet.js')
        page.evaluate("""async ({brand,lang})=>{
          const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
          window.KPSSaudiPlate={render:esc,text:String,field:()=>''};window.writes=[];
          const car={id:'car-a',brand_id:brand,plate:'TEST A',details:{management_revision:4}};
          await KPSFleet.open({lang,view:{mode:'cards'},tr:(ar,en)=>lang==='ar'?ar:en,esc,
            api:async path=>{
              if(path==='/api/fleet/vehicles')return {vehicles:[car]};
              if(path==='/api/fleet/options')return {columns:[],branches:[],drivers:[]};
              if(path==='/api/fleet/vehicles/car-a/management')return {vehicle:car,can_active_swap:true,assigned_driver_ids:['driver-a'],events:[],
                drivers:[{id:'driver-a',display_name:'First driver',plate:'TEST A',vehicle_id:'car-a',vehicle_revision:4},
                         {id:'driver-b',display_name:'Second driver',plate:'TEST B',vehicle_id:'car-b',vehicle_revision:8}]};
              throw Error('Unexpected path '+path);},
            mutation:async(path,body)=>{writes.push({path,body});return {};},
            heading:()=>{},error:message=>{window.testError=message;},
            modal:(title,html,submit)=>{document.querySelector('#modal-body').innerHTML=html;
              document.querySelector('#modal').showModal();document.querySelector('#modal form').onsubmit=async event=>{
                event.preventDefault();await submit(new FormData(event.target));};},
            closeModal:()=>document.querySelector('#modal').close()});
        }""",{'brand':brand,'lang':lang})
        page.locator('[data-fleet-manage]').click()
        page.locator('[name=action]').select_option('active_swap')
        page.locator('[name=driver_id]').select_option('driver-b')
        expect(page.locator('[data-active-swap-summary]')).to_contain_text('Second driver')
        for side,km in (('outgoing','3010'),('incoming','1010')):
            page.locator(f'[name={side}_km]').fill(km)
            page.locator(f'[name={side}_fuel]').fill('100')
            for key in ('tyres','brakes','lights','oil','damage','cleanliness'):
                page.locator(f'[name={side}_{key}]').select_option('ok')
        page.locator('[name=reason]').fill('Synthetic reviewed swap')
        page.locator('#modal [type=submit]').click()
        assert page.evaluate('writes.length')==0
        page.locator('[name=active_swap_confirmed]').check()
        page.locator('#modal [type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible()
        body=page.evaluate('writes[0].body')
        assert body['confirmed'] is True and body['expected_driver_ids']==['driver-a','driver-b']
        assert body['revision']==4 and body['counterpart_revision']==8 and body['counterpart_vehicle_id']=='car-b'
        assert body['incoming']['odometer_km']==1010 and body['outgoing']['odometer_km']==3010
        assert 'active_swap_confirmed' not in body
        assert page.evaluate('window.testError||null') is None
        browser.close()


@pytest.mark.parametrize('lang',['ar','en'])
def test_driver_notice_is_escaped_and_acknowledgement_does_not_certify_receipt(static_customer,lang):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page()
        page.goto(static_customer+'/assets/driver-checks.js')
        page.set_content('<div id="driver-checks"></div>')
        page.add_script_tag(url=static_customer+'/assets/driver-checks.js')
        page.evaluate("""async lang=>{
          const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
          window.writes=[];
          const data={server_time:new Date().toISOString(),requests:[],vehicle_notices:[{id:'notice-a',created_at:new Date().toISOString(),
            details:{old_plate:'TEST A',new_plate:'TEST B',actor_name:'Fawzi',reason:'<img src=x onerror=alert(1)>'}}]};
          const checks=KPSDriverChecks.init({getUser:()=>({id:'driver-a',role:'driver'}),getLang:()=>lang,can:()=>false,
            tr:(ar,en)=>lang==='ar'?ar:en,esc,api:async()=>data,
            mutation:async(path,body)=>{writes.push({path,body});},error:message=>{window.testError=message;}});
          await checks.refresh();
        }""",lang)
        expect(page.locator('[data-vehicle-notice]')).to_contain_text('Fawzi')
        expect(page.locator('[data-vehicle-notice] img')).to_have_count(0)
        expect(page.locator('[data-vehicle-notice]')).to_contain_text('توقيع استلام' if lang=='ar' else 'receipt signature')
        page.locator('[data-vehicle-notice-read]').click()
        expect(page.locator('[data-vehicle-notice]')).to_have_count(0)
        assert page.evaluate('writes')==[{'path':'/api/driver-checks/vehicle-notices/notice-a/read','body':{}}]
        browser.close()
