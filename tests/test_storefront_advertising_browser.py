"""Owner control UI with synthetic transport; no live promotion activation."""
import os
import pytest
from test_experience_entry_browser import static_customer

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit Chromium run')


def test_advertising_off_until_explicit_owner_save(static_customer):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(static_customer + '/assets/advertising-policy.js')
        page.set_content('<html><body><div id="modal"></div></body></html>')
        page.add_script_tag(url=static_customer + '/assets/advertising-policy.js')
        page.evaluate("""async()=>{
          window.calls=[];
          await KPSAdvertisingPolicy.open({user:{role:'owner',employee_sms_email_retired:true},
            tr:(ar,en)=>en,esc:v=>String(v),
            api:async(path,options)=>{if(options){calls.push('save');window.saved=options.body;return {}};
              return {policy:{version:3,advertising_enabled:false}};},
            modal:(title,html,submit)=>{document.querySelector('#modal').innerHTML=html;
              document.querySelector('form').onsubmit=async event=>{event.preventDefault();await submit(new FormData(event.target));};},
            authorize:async()=>calls.push('authorize'),closeModal:()=>calls.push('close'),onSaved:async()=>calls.push('reload')});
        }""")
        expect(page.locator('[name=advertising_enabled]')).not_to_be_checked()
        assert page.evaluate('calls') == []
        page.locator('[name=advertising_enabled]').check()
        assert page.evaluate('calls') == []
        page.locator('[name=reason]').fill('Owner approves this test advertisement')
        page.locator('button[type=submit]').click()
        page.wait_for_function('calls.length===4')
        assert page.evaluate('calls') == ['authorize', 'save', 'close', 'reload']
        assert page.evaluate('saved') == {'advertising_enabled':True, 'expected_version':3,
                                         'reason':'Owner approves this test advertisement'}
        browser.close()
