"""Real staff order form, isolated API doubles; no customer orders or messages."""
import json
import os

import pytest
from test_delivery_workspace_browser import static_customer

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser run')


@pytest.mark.parametrize('engine', ['chromium', 'webkit'])
@pytest.mark.parametrize('morning', [False, True])
@pytest.mark.parametrize('lang', ['ar', 'en'])
def test_whatsapp_paste_middle_edit_and_canonical_submission(static_customer, engine, morning, lang):
    from playwright.sync_api import sync_playwright, expect

    with sync_playwright() as p:
        browser = getattr(p, engine).launch()
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        page.route('**/phone-edit-test', lambda route: route.fulfill(
            content_type='text/html; charset=utf-8', body='<html><head><meta charset="utf-8"></head><body><main id="modal"></main></body></html>'))
        page.goto(static_customer + '/phone-edit-test')
        page.add_script_tag(url=static_customer + '/assets/customer-order.js')
        page.evaluate('''async ({morning, lang}) => {
          window.calls=[]; window.submitError=null;
          const user={id:'m',brand_id:'kps',role:'delivery_manager'};
          if(morning)user.delivery_context={branch_ids:['b'],period:'morning'};
          await KPSCustomerOrder.open({user,branches:[{id:'b',name:'Branch 2'}],
            tr:(ar,en)=>lang==='ar'?ar:en,esc:value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),money:String,
            api:async path=>{
              calls.push({path});
              if(path==='/api/shifts/current')return {shifts:[{user_id:'m',kind:'manager',status:'open',branch_ids:['b']}]};
              if(path.startsWith('/api/catalog'))return {products:[{id:'coffee',name:'Coffee',available:true,price_halalas:1000}]};
              return {contacts:[]};
            },
            mutation:async(path,body)=>{calls.push({path,body});return path==='/api/quote'?{quote:{subtotal_halalas:1000,delivery_fee_halalas:0,total_halalas:1000}}:{order:{id:'synthetic'}};},
            modal:(title,html,submit)=>{
              document.querySelector('#modal').innerHTML=html;
              const form=document.querySelector('form');
              form.addEventListener('submit',async event=>{event.preventDefault();try{await submit(new FormData(form),form);}catch(error){window.submitError=error.message;}});
            },closeModal:()=>{},onCreated:()=>{}
          });
        }''', {'morning': morning, 'lang': lang})
        phone = page.locator('input[name=phone]')

        def paste(value, start=0, end=None):
            phone.evaluate('''(input,{value,start,end})=>{
              input.focus();input.setSelectionRange(start,end??input.value.length);
              const data=new DataTransfer();data.setData('text/plain',value);
              input.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));
            }''', {'value': value, 'start': start, 'end': end})

        for value in ['+966 53 104 1449', '\u200e+966\u00a053\u202f104\u00a01449\u200f',
                      '٠٥٣ ١٠٤ ١٤٤٩', '۰۵۳ ۱۰۴ ۱۴۴۹', '00966 (53) 104-1449']:
            paste(value)
            assert phone.input_value() in ['+966531041449', '0531041449', '00966531041449']
            assert phone.evaluate('(e)=>e.selectionStart') == len(phone.input_value())
        # Long clipboard presentation must be cleaned before maxlength truncates it.
        paste('\u200e' * 35 + '+966 53 104 1449' + '\u200f' * 35)
        expect(phone).to_have_value('+966531041449')
        # A caret in the middle must stay there after an actual keypress.
        phone.evaluate('(e)=>e.setSelectionRange(8,9)')
        phone.press('Backspace')
        assert phone.evaluate('(e)=>e.selectionStart') == 8
        phone.press('0')
        expect(phone).to_have_value('+966531001449')
        assert phone.evaluate('(e)=>e.selectionStart') == 9
        # Replacing a selection keeps the rest of the number and the caret.
        paste('٤', start=8, end=9)
        expect(phone).to_have_value('+966531041449')
        assert phone.evaluate('(e)=>e.selectionStart') == 9
        phone.fill('0531041449')
        phone.evaluate('(e)=>e.setSelectionRange(0,1)')
        phone.press('Backspace')
        expect(phone).to_have_value('531041449')
        assert phone.evaluate('(e)=>e.selectionStart') == 0
        phone.press('0')
        expect(phone).to_have_value('0531041449')
        # Outgoing quote/create values use the same canonical phone as lookup.
        page.locator('input[name=neighborhood]').fill('Test')
        page.locator('input[name=address]').fill('Test')
        page.locator('input[name=lat]').fill('28.4')
        page.locator('input[name=lng]').fill('45.9')
        page.locator('#staff-add').click()
        submit=page.locator('button[type=submit]')
        submit.click()
        expect(page.locator('#staff-review')).not_to_be_empty()
        submit.click()
        page.wait_for_function("calls.some(c=>c.path==='/api/orders/delivery-center')")
        payloads=page.evaluate("calls.filter(c=>c.body).map(c=>c.body.customer.phone)")
        assert payloads == ['+966531041449', '+966531041449']
        # Arbitrary text or two pasted numbers must never become a valid phone.
        for invalid in ['call 0531041449', '0531041449 / 0531041448', '++966531041449']:
            paste(invalid)
            page.evaluate('window.submitError=null')
            submit.click()
            page.wait_for_function('window.submitError!==null')
        assert len(page.evaluate('calls.filter(c=>c.body)')) == 2
        if morning:
            assert not page.evaluate("calls.some(c=>c.path.includes('customer-lookup'))")
        browser.close()
