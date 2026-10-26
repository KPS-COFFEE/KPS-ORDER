"""Synthetic development-only source-price form; no live invoices or balances."""
import os
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit isolated browser acceptance')


@pytest.mark.parametrize('lang,width',[('ar',390),('en',390),('ar',1280),('en',1280)])
def test_development_price_credit_form_keeps_original_and_actual_collection_visible(lang,width):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        launch={'headless':True,'args':['--no-sandbox']}
        if os.getenv('CHROMIUM_EXECUTABLE'):launch['executable_path']=os.environ['CHROMIUM_EXECUTABLE']
        browser=p.chromium.launch(**launch)
        page=browser.new_page(viewport={'width':width,'height':1000});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.set_content(f'<html lang="{lang}" dir="{"rtl" if lang=="ar" else "ltr"}"><body><p>ISOLATED DEVELOPMENT DATA — NOT PRODUCTION</p><dialog id="modal"><h2></h2><div id="modal-body"></div></dialog></body></html>')
        page.add_style_tag(content='*{box-sizing:border-box}body{font:15px Arial;padding:12px}.stack{display:grid;gap:12px}label{display:grid;gap:6px}input,select,textarea,button{font:inherit;max-width:100%;padding:9px}dialog{width:min(680px,calc(100vw - 24px));max-height:calc(100vh - 24px);overflow:auto;border:1px solid #ddd;padding:16px}dialog h2{font-size:18px}.panel{border:1px solid #ddd;padding:12px;border-radius:8px}p,dd{overflow-wrap:anywhere;margin-inline-start:0}dl{display:grid;gap:6px}[hidden]{display:none!important}')
        page.add_script_tag(path=str(ROOT/'public/assets/order-source-treatment.js'))
        page.evaluate('''async lang=>{
          const data={order_id:'synthetic-order',version:4,case_id:'synthetic-case',original_total_halalas:2500,effective_total_halalas:2500,effective_claim_halalas:2500,price_credit_halalas:0,refund_credit_halalas:0,actual_collected_halalas:1950,pending_amount_halalas:550,amounts_matched:false,financially_complete:false,financial_state:'pending_review',can_propose:true,can_approve:true,treatments:[],followup_users:[{id:'accountant',name:'Synthetic accountant'}]};
          window.sourceMutations=[];window.sourceReads=0;
          const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
          const ctx={oid:'synthetic-order',user:{id:'reviewer',role:'owner',order_source_treatments_enabled:true},enabled:false,tr:(ar,en)=>lang==='ar'?ar:en,esc,money:v=>(v/100).toFixed(2),api:async()=>{sourceReads++;return data;},mutation:async(path,body)=>{sourceMutations.push({path,body});data.treatments.push({id:'proposal',status:'pending',requested_by:'reviewer',requester_name:'Synthetic reviewer',created_at:'2026-10-26',payload:{...body,credit_halalas:550}});data.can_propose=false;},modal:(title,html,handler)=>{const d=document.querySelector('#modal');d.querySelector('h2').textContent=title;document.querySelector('#modal-body').innerHTML=html;const form=d.querySelector('form');if(form)form.onsubmit=async e=>{e.preventDefault();try{await handler(new FormData(form));}catch(error){form.querySelector('.form-error').textContent=error.message;}};d.showModal();},closeModal:()=>document.querySelector('#modal').close()};
          try{await KPSOrderSourceTreatment.open(ctx);}catch(e){window.sourceDisabledMessage=e.message;}
          if(sourceReads!==0)throw new Error('Disabled source flow must not read records');
          ctx.enabled=true;await KPSOrderSourceTreatment.open(ctx);
        }''',lang)
        expect(page.locator('#modal-body')).to_contain_text('25.00')
        expect(page.locator('#modal-body')).to_contain_text('19.50')
        expect(page.locator('#modal-body')).to_contain_text('5.50')
        page.locator('[data-source-treatment-new]').click()
        form=page.locator('[data-source-treatment-form]')
        form.locator('[name=corrected_total]').fill('19.50')
        form.locator('[name=evidence_reference]').fill('SYNTHETIC-ORIGINAL-PRICE-001')
        form.locator('[name=note]').fill('TEST ONLY approved historical source price was 19.50')
        output=ROOT/'test-artifacts';output.mkdir(exist_ok=True)
        page.screenshot(path=str(output/f'source-price-development-{lang}-{width}.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert page.locator('#modal').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
        form.locator('button[type=submit]').click()
        expect(page.locator('#modal-body')).to_contain_text('SYNTHETIC-ORIGINAL-PRICE-001')
        assert page.evaluate('sourceMutations[0].body.corrected_total_halalas')==1950
        assert page.evaluate('sourceMutations[0].body.expected_collected_halalas')==1950
        assert page.evaluate('sourceMutations.length')==1
        expect(page.locator('#modal-body')).to_contain_text('25.00')
        assert not errors
        browser.close()
