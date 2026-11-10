"""Real Chromium component acceptance with synthetic API adapters, not live orders."""
import os
from pathlib import Path
import pytest
from test_browser import chromium_browser

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance only')
ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('language,width',[('ar',390),('en',1280)])
def test_editor_draft_review_and_offsite_consent(chromium_browser,language,width):
    context=chromium_browser.new_context(viewport={'width':width,'height':850})
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content(f'<html lang="{language}" dir="{"rtl" if language=="ar" else "ltr"}"><body></body></html>')
    page.add_style_tag(path=str(ROOT/'public/assets/order-amendments.css'))
    page.add_script_tag(path=str(ROOT/'public/assets/order-amendments.js'))
    page.evaluate('''async()=>{
      const item={product_id:'p',name:'قهوة اختبار',quantity:1,units:[],note:'',line_total_halalas:1000};
      const order={id:'test-order',order_no:'TEST-1',version:1,brand_id:'test',branch_id:'b',service:'delivery',preparation_status:'accepted',delivery_status:'pending',customer:{name:'عميل',phone:'0500000000',address:'عنوان اختبار',neighborhood:'حي اختبار',location:{lat:28,lng:45}},snapshot:{items:[item],notes:'',delivery_workflow:'manager_dispatch'},total_halalas:1000};
      window.calls=[];let amendment=null;
      const api=async path=>path.startsWith('/api/orders/')?{order}:path.startsWith('/api/catalog')?{products:[{id:'p',name:'قهوة اختبار',available:true,price_halalas:1000,option_groups:[]}]}:{amendments:amendment?[amendment]:[],active_id:amendment?.status==='applied'?null:amendment?.id,revision:0,due_halalas:1000,refund_due_halalas:0,receipts:[]};
      const mutation=async(path,body)=>{
        window.calls.push({path,body});
        if(path.endsWith('/preview'))return {quote:{items:body.items.map(x=>({...x,name:'قهوة اختبار'})),subtotal_halalas:2000,delivery_fee_halalas:0,total_halalas:2000}};
        if(path.endsWith('/decision')){amendment.status=body.action==='offer'?'offered':body.action==='accept'?'applied':'branch_confirmed';return {status:amendment.status};}
        amendment={id:'a',status:'requested',quote:{items:body.items.map(x=>({...x,name:'قهوة اختبار'})),subtotal_halalas:2000,delivery_fee_halalas:0,total_halalas:2000},customer:body.customer,created_at:new Date().toISOString(),expires_at:new Date(Date.now()+900000).toISOString()};return {amendment_id:'a'};
      };
      await KPSAmendments.open({oid:order.id,api,mutation,user:{id:'m',role:'delivery_manager'}});
    }''')
    page.locator('[data-amend=edit]').click()
    page.locator('[name=reason]').fill('طلب العميل عبر واتساب')
    page.locator('[name=quantity]').fill('2')
    page.locator('[data-amend=add]').click()
    page.locator('[data-remove="0"]').click()
    page.locator('#amend-edit [type=submit]').click()
    page.locator('#amend-review h3').wait_for()
    assert page.locator('[name=reason]').input_value()=='طلب العميل عبر واتساب'
    page.locator('#amend-edit [type=submit]').click()
    page.locator('[data-amend=offer]').click()
    page.locator('[name=consent_note]').fill('وافق العميل على الإجمالي 20 ريال')
    page.locator('[data-amend=accept]').click()
    page.locator('[data-amend=edit]').wait_for()
    calls=page.evaluate('window.calls')
    assert calls[-1]['body']['channel']=='whatsapp'
    assert calls[-1]['body']['note']=='وافق العميل على الإجمالي 20 ريال'
    assert calls[0]['body']['items'][0]['quantity']==2
    assert page.evaluate('document.querySelector(".amend-dialog").scrollWidth<=document.querySelector(".amend-dialog").clientWidth+1')
    assert not errors
    context.close()
