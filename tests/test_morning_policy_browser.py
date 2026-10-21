import os
import pytest
from test_experience_entry_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')


@pytest.mark.parametrize('lang',['ar','en'])
def test_owner_draft_requires_explicit_authorized_save(static_customer,lang):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':390,'height':850})
        page.goto(static_customer+'/assets/morning-delivery.js')
        page.set_content('<html><body><div id="modal"></div></body></html>')
        page.add_script_tag(url=static_customer+'/assets/morning-delivery.js')
        page.evaluate("""async lang=>{
          const esc=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
          window.calls=[];
          await KPSMorningDelivery.open({user:{role:'owner',employee_sms_email_retired:true},
            tr:(ar,en)=>lang==='ar'?ar:en,esc,
            api:async(path,options)=>{if(options){calls.push('save');window.saved=options.body;return {}};
              return {policy:{version:4,enabled:false,execution_branch_ids:['branch'],preparer_user_ids:['prep'],manager_user_ids:['manager']},
                branches:[{id:'branch',branch_no:1,name:'<img src=x onerror=alert(1)>'}],
                users:[{id:'prep',role:'branch_manager',display_name:'محضر'},{id:'manager',role:'delivery_manager',display_name:'مدير'}]};},
            modal:(title,html,submit)=>{document.querySelector('#modal').innerHTML=html;window.saveDraft=submit;},
            authorize:async()=>calls.push('authorize'),closeModal:()=>calls.push('close'),onSaved:async()=>calls.push('reload')});
        }""",lang)
        expect(page.locator('[name=enabled]')).not_to_be_checked()
        assert page.locator('[onerror]').count()==0
        assert page.evaluate('calls')==[]
        page.locator('[name=enabled]').check()
        page.locator('[name=reason]').fill('اعتماد الفترة الصباحية')
        page.evaluate('saveDraft(new FormData(document.querySelector("form")))')
        assert page.evaluate('calls')==['authorize','save','close','reload']
        assert page.evaluate('saved.expected_version')==4
        assert page.evaluate('saved.policy.enabled') is True
        assert page.evaluate('saved.policy.execution_branch_ids')==['branch']
        browser.close()
