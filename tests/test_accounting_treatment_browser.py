"""Actual AR/EN mobile treatment forms with isolated mocked API responses."""
import os
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance')


@pytest.mark.parametrize('lang,width',[('ar',390),('en',1440)])
def test_treatment_forms_preserve_evidence_and_independent_decisions(lang,width):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as runtime:
        browser=runtime.chromium.launch(headless=True,args=['--no-sandbox'],executable_path=os.getenv('TEST_CHROMIUM_EXECUTABLE') or None)
        page=browser.new_page(viewport={'width':width,'height':1000})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.set_content(f'<html lang="{lang}" dir="{"rtl" if lang=="ar" else "ltr"}"><main id="content"></main><dialog id="modal"><h2></h2><div id="modal-body"></div></dialog></html>')
        page.add_style_tag(content='*{box-sizing:border-box}body{margin:0;font-family:Arial;padding:12px}button,input,textarea,select{font:inherit;max-width:100%;padding:8px}dialog{width:min(700px,calc(100vw - 24px));max-height:calc(100vh - 24px);overflow:auto}label{display:block;margin:10px 0}label>input:not([type=checkbox]),label>select,label>textarea{display:block;width:100%}')
        page.add_style_tag(path=str(ROOT/'public/assets/accounting.css'))
        page.add_script_tag(path=str(ROOT/'public/assets/accounting.js'))
        page.evaluate('''async lang=>{
          window.testMutations=[];window.testUser={id:'accountant',role:'admin'};
          window.testTreatment={expected_revision:2,source:{cash_variance_halalas:-550,card_variance_halalas:0},followup_users:[{id:'followup',name:'Actual accountant'}],can_propose:true,can_approve:true,treatments:[]};
          const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
          const row={id:'report',user_id:'driver',driver_name:'TEST DRIVER',day:'2026-10-26',bank_halalas:0,cash_due_halalas:2500,cash_received_halalas:1950,system_total_halalas:2500,system_bank_halalas:0,system_cash_halalas:2500,cash_variance_halalas:-550,bank_difference_halalas:0,balance_approved:true,cash_recorded:true,reconciliation_complete:true,net_difference_halalas:-550,shortage_halalas:550,surplus_halalas:0,stage:'cash_received',variance_pending:true,revision:2,history:[]};
          const ctx={lang,user:window.testUser,tr:(ar,en)=>lang==='en'?en:ar,esc,money:v=>(v/100).toFixed(2),cents:v=>Math.round(Number(v)*100),heading:()=>{},closeModal:()=>document.querySelector('#modal').close(),modal:(title,html,fn)=>{const dialog=document.querySelector('#modal');if(dialog.open)dialog.close();dialog.querySelector('h2').textContent=title;dialog.querySelector('#modal-body').innerHTML=html;dialog.showModal();const form=dialog.querySelector('form');if(form&&fn)form.onsubmit=async e=>{e.preventDefault();try{await fn(new FormData(form),form);}catch(error){form.querySelector('.form-error').textContent=error.message;}};},api:async url=>{
            if(url.endsWith('/options'))return {today:'2026-10-26',can_report:true,can_review:true,branches:[],people:[],products:[],categories:{},states:{}};
            if(url.endsWith('/treatments'))return window.testTreatment;
            if(url.includes('/manager-cards'))return {cards:[{id:'manager',manager_name:'TEST MANAGER',day:'2026-10-26',drivers:[row],total:1,completed:0,completion_percent:66,ready:false}]};
            return {rows:[],columns:[],totals:{},definitions:'SYNTHETIC'};
          },mutation:async(path,body)=>{window.testMutations.push({path,body});if(path.endsWith('/treatments'))window.testTreatment.treatments=[{id:'treatment',status:'pending',requested_by:window.testUser.id,requester_name:'Actual preparer',payload:body}];return {};}};
          await KPSAccounting.open(ctx);
        }''',lang)
        page.locator('[data-ac=settlements]').click()
        # Follow the accountant's actual shift-card entry before driver actions.
        card=page.locator('[data-manager-card=manager]')
        card.locator('summary').click()
        expect(card).to_have_js_property('open',True)
        page.locator('[data-variance-confirm]').click()
        page.locator('[data-treatment-propose]').click()
        form=page.locator('[data-treatment-form]')
        expect(form.locator('[name=kind] option')).to_have_count(1)
        expect(form).to_contain_text('-5.50')
        form.locator('[name=evidence_reference]').fill('Actual receipt 001')
        form.locator('[name=note]').fill('Customer balance supported by receipt')
        form.locator('[name=liable_party_name]').fill('Customer reference 001')
        form.locator('[name=followup_user_id]').select_option('followup')
        form.locator('[name=due_on]').fill('2026-10-27')
        form.locator('button[type=submit]').click()
        expect(page.locator('[data-treatment-review]')).to_have_count(0)
        assert page.evaluate('window.testMutations[0].body.cash_variance_halalas')==-550
        assert page.evaluate('window.testMutations[0].body.card_variance_halalas')==0
        page.locator('#modal').evaluate('(el)=>el.close()')
        page.evaluate("()=>{window.testUser.id='independent-owner';window.testUser.role='owner';}")
        page.locator('[data-variance-confirm]').click()
        page.locator('[data-treatment-review][data-decision=approve]').click()
        expect(page.locator('[name=confirmed]')).not_to_be_checked()
        expect(page.locator('[name=allow_period_approval_with_outstanding]')).not_to_be_checked()
        page.locator('[name=evidence_reference]').fill('Independent actual document 002')
        page.locator('[name=note]').fill('Owner reviewed actual debt evidence')
        page.locator('[name=confirmed]').check()
        page.locator('[name=allow_period_approval_with_outstanding]').check()
        page.locator('#modal button[type=submit]').click()
        page.wait_for_function('window.testMutations.length===2')
        assert page.evaluate('window.testMutations[1].path').endswith('/treatments/treatment/approve')
        assert page.evaluate('window.testMutations[1].body.allow_period_approval_with_outstanding') is True
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert not errors
        browser.close()
