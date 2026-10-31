"""Shipped intervention UI in Chromium with synthetic adapters; no live orders."""
import os
from pathlib import Path
import pytest
from test_browser import chromium_browser

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance only')
ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('brand',['kps','enbishan'])
@pytest.mark.parametrize('language,width',[('ar',390),('en',390),('ar',1280),('en',1280)])
def test_diagnosis_explicit_review_failed_retry_and_actor_history(chromium_browser,brand,language,width):
    context=chromium_browser.new_context(viewport={'width':width,'height':900})
    page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    label='معالجة الطلبات المتعثرة' if language=='ar' else 'Order intervention desk'
    page.set_content(f'<html lang="{language}" dir="{"rtl" if language=="ar" else "ltr"}"><body><nav id="navigation"><button data-page="recovery">{label}</button></nav><main id="content"></main></body></html>')
    page.add_style_tag(path=str(ROOT/'public/ops.css'))
    for room in ('','delivery-room'):
        for selected in ('','active'):
            page.evaluate('''({room,selected})=>{document.body.className=room;document.querySelector('[data-page=recovery]').className=selected;}''',{'room':room,'selected':selected})
            assert page.locator('[data-page=recovery]').evaluate('(e)=>getComputedStyle(e).color')=='rgb(180, 35, 24)'
    page.add_script_tag(path=str(ROOT/'public/assets/task-recovery.js'))
    page.evaluate('''async({brand,language})=>{
      const ar=language==='ar',tr=(a,b)=>ar?a:b;
      const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
      let reviewed=false,attempt=0;window.calls=[];
      const task={id:'task-'+brand,order_id:'order-'+brand,status:'assigned',created_at:'2026-10-23T00:00:00Z',driver_name:'TEST driver <img src=x onerror=alert(1)>',order_no:'TEST-'+brand};
      const api=async path=>path.includes('?')?{tasks:[task],next_offset:null}:{task,order:{id:task.order_id,order_no:task.order_no,version:3,revision:2,customer:{name:'TEST customer',neighborhood:'Test district',address:'Current entrance'},snapshot:{items:[{name:'Test coffee',quantity:1,units:[]}]},payment_status:'unpaid',total_halalas:1000},blockers:reviewed?['pickup_missing','payment_missing']:['driver_revision','pickup_missing','payment_missing'],can_review_revision:!reviewed,can_resolve_delivery:false,reviews:reviewed?[{actor_name:'TEST manager',created_at:'2026-10-23T00:02:00Z',details:{revision:2,reason:'Verified current address',evidence:'Called driver'}}]:[]};
      const mutation=async(path,body)=>{window.calls.push({path,body});if(++attempt===1)throw Error('TEST save failed; retry');reviewed=true;return {reviewed:true};};
      await KPSTaskRecovery.open({api,mutation,tr,esc,money:n=>String(n/100),datetime:v=>v,statusName:v=>v,heading:()=>{},active:()=>true,reviewOrder:async()=>{},resolveDelivery:async()=>{throw Error('Must not resolve without pickup or payment');}});
    }''',{'brand':brand,'language':language})
    page.locator('[data-recovery-id]').click()
    form=page.locator('[data-revision-review]');form.wait_for()
    assert page.locator('[data-resolve-delivery]').count()==0
    assert page.locator('img').count()==0
    page.locator('[name=reason]').fill('Verified current address')
    page.locator('[name=evidence]').fill('Called driver')
    form.locator('[type=submit]').click()
    assert page.evaluate('window.calls.length')==0
    page.locator('[name=confirmed]').check()
    form.locator('[type=submit]').click()
    page.get_by_text('TEST save failed; retry').wait_for()
    assert page.locator('[name=reason]').input_value()=='Verified current address'
    assert page.locator('[name=evidence]').input_value()=='Called driver'
    form.locator('[type=submit]').click()
    page.get_by_text('TEST manager',exact=True).wait_for()
    assert page.locator('[data-revision-review]').count()==0
    calls=page.evaluate('window.calls')
    assert calls[0]==calls[1]
    assert calls[1]['body']=={'revision':2,'version':3,'reason':'Verified current address','evidence':'Called driver','confirmed':True}
    assert page.locator('[data-resolve-delivery]').count()==0
    assert not errors
    context.close()
