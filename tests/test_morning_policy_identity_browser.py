"""Existing owner policy choices distinguish duplicate names by registered phone."""
import os
import pytest
from test_delivery_workspace_browser import static_customer

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit Chromium run')


@pytest.mark.parametrize('lang', ['ar', 'en'])
def test_owner_policy_choices_show_escaped_exact_identity_without_mutation(static_customer, lang):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=os.getenv('TEST_CHROMIUM_EXECUTABLE') or None)
        page = browser.new_page(viewport={'width': 390, 'height': 850})
        page.goto(static_customer+'/robots.txt')
        page.set_content('<main id="identity-test"></main>')
        page.add_script_tag(url=static_customer+'/assets/morning-delivery.js')
        page.evaluate("""async lang=>{
          const data={policy:{enabled:false,execution_branch_ids:[],preparer_user_ids:[],manager_user_ids:[],version:1},
            branches:[{id:'b2',branch_no:2,name:'Branch 2'}],users:[
              {id:'manager-exact',role:'delivery_manager',display_name:'أحمد <img src=x onerror=alert(1)>',phone:'+966531049596'},
              {id:'manager-other',role:'delivery_manager',display_name:'أحمد <img src=x onerror=alert(1)>',phone:'+966531041449'},
              {id:'preparer-exact',role:'branch_manager',display_name:'محضر',phone:'+966534301235'}]};
          window.identityCalls=[];
          await KPSMorningDelivery.open({user:{role:'owner'},tr:(ar,en)=>lang==='ar'?ar:en,
            esc:v=>String(v??'').replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c])),
            api:async(path,options)=>{identityCalls.push([path,options?.method||'GET']);return data;},
            modal:(_,html)=>document.querySelector('#identity-test').innerHTML=html,
            closeModal:()=>{},authorize:()=>{throw new Error('No authorization during a read');},onSaved:()=>{}});
        }""", lang)
        exact = page.locator('label').filter(has=page.locator('input[value="manager-exact"]'))
        other = page.locator('label').filter(has=page.locator('input[value="manager-other"]'))
        expect(exact).to_contain_text('+966531049596')
        expect(other).to_contain_text('+966531041449')
        expect(page.locator('label').filter(has=page.locator('input[value="preparer-exact"]'))).to_contain_text('+966534301235')
        expect(page.locator('#identity-test img')).to_have_count(0)
        page.locator('input[value="manager-exact"]').check()
        expect(page.locator('input[value="manager-other"]')).not_to_be_checked()
        assert page.evaluate('identityCalls') == [['/api/morning-delivery/policy', 'GET']]
        browser.close()
