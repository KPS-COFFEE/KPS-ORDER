"""Render actual closure UI functions with deterministic API fixtures."""
import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

PUBLIC=Path(__file__).resolve().parents[1]/'public'
OPS=(PUBLIC/'ops.js').read_text()


def function_source(start,end):
    return OPS[OPS.index(start):OPS.index(end,OPS.index(start))]


@pytest.mark.parametrize('width',[320,390,1440])
@pytest.mark.parametrize('lang',['ar','en'])
def test_cash_bags_separate_facts_without_phone_overflow(width,lang):
    with sync_playwright() as p:
        browser=p.chromium.launch(args=['--no-sandbox'],executable_path=os.getenv('CHROMIUM_EXECUTABLE') or None)
        page=browser.new_page(viewport={'width':width,'height':900})
        page.set_content('<html dir="'+('rtl' if lang=='ar' else 'ltr')+'"><meta name="viewport" content="width=device-width,initial-scale=1"><dialog id="modal" class="cash-bags-dialog" open><div id="modal-body"></div></dialog></html>')
        page.add_style_tag(path=str(PUBLIC/'ops.css'))
        page.evaluate('''lang=>{window.state={lang};window.tr=(ar,en)=>state.lang==='ar'?ar:en;window.esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));window.money=x=>String(x/100)+' SAR';window.datetime=x=>x;window.dataItem=(label,value)=>`<div><dt>${label}</dt><dd>${value}</dd></div>`;}''',lang)
        page.add_script_tag(content=function_source('  function cashBagCard','  async function cashBagInbox'))
        bags=[dict(id='1',driver_name='محمد السائق Driver',reference='محمد السائق — 2026-10-25 — ABC123',shift_date='2026-10-25',deposited_at='2026-10-26 02:05',destination='branch',branch_name='فرع القيصومة Qaisumah',declared_cash_halalas=12345,declared_card_halalas=45000,ready_to_count=True),dict(id='2',driver_name='Second Driver',reference='Second — 2026-10-25',shift_date='2026-10-25',deposited_at='2026-10-26 02:05',destination='manager_vault',declared_cash_halalas=10000,declared_card_halalas=5000,count_id='count1',received_cash_halalas=9900,counted_by_name='Accountant',counted_at='2026-10-26 03:05')]
        page.evaluate('bags=>document.querySelector("#modal-body").innerHTML=`<div class="cash-bag-list">${bags.map(cashBagCard).join(\'\')}</div>`',bags)
        assert page.locator('.cash-bag-card').count()==2
        assert page.locator('.cash-bag-card').first.locator('dt').count()==7
        assert '123.45 SAR' in page.locator('.cash-bag-card').first.inner_text()
        assert page.locator('[data-action="cash-bag-count"]').count()==2
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert page.locator('#modal-body').evaluate('(el)=>el.scrollWidth<=el.clientWidth')
        browser.close()


@pytest.mark.parametrize('branch_bag',[True,False])
def test_pending_branch_bag_or_received_legacy_closure_and_manager_final_approval(branch_bag):
    with sync_playwright() as p:
        browser=p.chromium.launch(args=['--no-sandbox'],executable_path=os.getenv('CHROMIUM_EXECUTABLE') or None)
        page=browser.new_page()
        page.set_content('<main></main>')
        page.evaluate('''()=>{window.state={user:{id:'manager',role:'delivery_manager'}};window.tr=(ar,en)=>en;window.esc=String;window.money=String;window.driverOrderReviewControl=()=>'';window.dataItem=(l,v)=>`<div>${l}: ${v}</div>`;window.notice=x=>`<p>${x}</p>`;window.check=(l,n)=>`<input name="${n}">${l}`;window.formEnd=l=>`<button>${l}</button>`;window.modal=(title,html,submit)=>{document.querySelector('main').innerHTML=html;window.submit=submit;};window.closeModal=()=>{};window.loadPage=async()=>{};window.mutation=async(path,body)=>window.saved={path,body};window.api=async()=>({shift:{user_id:'driver',status:'submitted'},declaration:{id:'dec',cash_halalas:100,card_halalas:200},cash_bag:{destination:'branch',reference:'bag',branch_name:'Qaisumah'},manager_approval_required:true,manager_approval:null,can_approve:true});}''')
        if not branch_bag:
            page.evaluate('''()=>{window.api=async()=>({shift:{user_id:'driver',status:'submitted'},declaration:{id:'dec',cash_halalas:100,card_halalas:200},cash_bag:null,receipt:{id:'receipt',received_halalas:100},manager_approval_required:true,manager_approval:null,can_approve:true});}''')
        page.add_script_tag(content=function_source('  async function sharedDriverClosure','  async function managerEndDutyReport'))
        page.evaluate('sharedDriverClosure("shift")')
        assert ('final delivery manager approval is required before duty ends' if branch_bag else 'Actual cash receipt is recorded') in page.locator('main').inner_text()
        assert page.locator('input[name=received]').count()==0
        assert 'physically received' not in page.locator('main').inner_text()
        page.evaluate('submit(new Map([["confirmed",true]]))')
        assert page.evaluate('saved.path').endswith('/physical-approval')
        assert page.evaluate('saved.body')=={'declaration_id':'dec','confirmed':True}
        browser.close()


@pytest.mark.parametrize('width',[390,1440])
@pytest.mark.parametrize('approved',[True,False])
def test_counted_branch_bag_reaches_accounting_allocation_only_after_manager_approval(width,approved):
    """Opening a bag never posts money; eligible accountant gets the real form."""
    with sync_playwright() as p:
        browser=p.chromium.launch(args=['--no-sandbox'],executable_path=os.getenv('CHROMIUM_EXECUTABLE') or None)
        page=browser.new_page(viewport={'width':width,'height':1000})
        page.set_content('<main></main>')
        page.evaluate('''approved=>{
          window.saved=[];window.state={user:{id:'accountant',role:'admin'}};
          window.tr=(ar,en)=>en;window.esc=String;window.money=String;window.driverOrderReviewControl=()=>'';
          window.dataItem=(l,v)=>`<div>${l}: ${v}</div>`;window.notice=x=>`<p>${x}</p>`;
          window.check=(l,n)=>`<input type="checkbox" name="${n}">${l}`;
          window.formEnd=l=>`<button type="submit">${l}</button>`;
          window.amountField=(l,n,v)=>`<label>${l}<input name="${n}" value="${v??''}"></label>`;
          window.field=(l,n)=>`<label>${l}<input name="${n}"></label>`;
          window.textarea=(l,n)=>`<label>${l}<textarea name="${n}"></textarea></label>`;
          window.modal=(title,html,submit)=>{document.querySelector('main').innerHTML=html;window.submit=submit;};
          window.closeModal=()=>{};window.loadPage=async()=>{};window.bindSharedClosureDirty=()=>{};
          window.mutation=async(path,body)=>window.saved.push({path,body});
          window.api=async()=>({shift:{user_id:'driver',status:'submitted'},declaration:{id:'dec',cash_halalas:100,card_halalas:200},receipt:{id:'receipt',received_halalas:100},cash_bag:{destination:'branch',reference:'bag',branch_name:'Qaisumah',count_id:'count'},manager_approval_required:true,manager_approval:approved?{id:'approval'}:null,can_approve:false,can_allocate:true,legs:[{shift_id:'kps-shift',brand_id:'kps'},{shift_id:'enb-shift',brand_id:'enbishan'}]});
        }''',approved)
        page.add_script_tag(content=function_source('  async function sharedDriverClosure','  async function managerEndDutyReport'))
        page.evaluate('sharedDriverClosure("shift")')
        assert page.evaluate('saved')==[], 'Opening the correct workflow must not receive or allocate cash'
        assert page.locator('form#central-closure-form').count()==int(approved)
        if approved:
            assert page.locator('[name="cash:kps-shift"]').input_value()==''
            assert page.locator('[name="cash:enb-shift"]').input_value()==''
            assert 'Save attribution for reconciliation' in page.locator('main').inner_text()
        else:
            assert 'final delivery manager approval is required' in page.locator('main').inner_text()
        browser.close()
