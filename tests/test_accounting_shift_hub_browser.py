"""A manager period is the work boundary, even when dates and drivers repeat."""
import os
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser acceptance')


@pytest.mark.parametrize('width', [390, 1440])
def test_manager_period_cards_bind_bags_and_actions_to_exact_shift(width):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_EXECUTABLE') or None, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_content('<html lang="ar" dir="rtl"><body><h1></h1><main id="content"></main><dialog><div id="modal-body"></div></dialog></body></html>')
        page.add_style_tag(content='*{box-sizing:border-box}body{margin:0;padding:20px;background:#faf8f5;font-family:Arial}main{max-width:1280px;margin:auto}button,input,textarea,select{font:inherit;padding:10px;border:1px solid #ddd;border-radius:8px}button{background:#a64116;color:#fff;cursor:pointer}dialog{width:min(600px,calc(100vw - 24px))}.ac-case-form{display:grid;gap:12px}.ac-case-form label{display:grid;gap:6px}')
        page.add_style_tag(path=str(ROOT/'public/assets/accounting.css'))
        page.add_script_tag(path=str(ROOT/'public/assets/accounting.js'))
        page.evaluate('''async()=>{
          window.writes=[];window.allocations=[];window.counted=false;
          const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
          const card=(id,period,shift)=>({id,manager_name:'مدير اختبار',day:'2026-11-01',period,shift_no:id,opened_at:'2026-11-01T16:00:00+03:00',closed_at:'2026-11-02T04:00:00+03:00',branch_names:['فرع اختبار'],financial_ledger_closed:false,drivers:[],pending_drivers:[{id:shift,driver_name:'نفس السائق',responsible:'accounting',reason:'driver_accounting_pending'}],total:0,expected_driver_count:1,review_completed:0,completion_percent:0,balance_completed:0,cash_completed:0,review_needed_count:0,remaining_count:1,ready:false});
          await KPSAccounting.open({lang:'ar',user:{id:'accountant',role:'admin'},tr:(ar,en)=>ar,esc,money:n=>(n/100).toFixed(2),cents:n=>Math.round(Number(n)*100),heading:t=>document.querySelector('h1').textContent=t,openSharedDriverClosure:async id=>window.allocations.push(id),closeModal:()=>document.querySelector('dialog').close(),modal:(title,html,submit)=>{const d=document.querySelector('dialog');d.querySelector('#modal-body').innerHTML=html;d.showModal();d.querySelector('form').onsubmit=async e=>{e.preventDefault();await submit(new FormData(e.target));};},mutation:async(path,body)=>{window.writes.push({path,body});window.counted=true;},api:async path=>{
            if(path.endsWith('/options'))return {today:'2026-11-01',can_report:true,can_review:true,branches:[],people:[],products:[],categories:{},states:{},responsibilities:{}};
            if(path==='/api/cash-bags')return {bags:[{id:'evening-bag',anchor_shift_id:'evening-driver',shift_ids:['evening-driver'],manager_shift_ids:['evening-manager'],driver_name:'نفس السائق',reference:'EVENING-ONLY',destination:'manager_vault',declared_cash_halalas:15000,ready_to_count:true,count_id:window.counted?'actual-count':null,received_cash_halalas:window.counted?15000:null,accounting_custody_difference_halalas:0,accounting_custody_card_difference_halalas:window.counted?100:0}]};
            if(path.includes('/manager-cards'))return {cards:[card('morning-manager','morning','morning-driver'),card('evening-manager','evening','evening-driver')]};
            throw Error(path);
          }});
        }''')
        morning = page.locator('[data-manager-card="morning-manager"]')
        evening = page.locator('[data-manager-card="evening-manager"]')
        assert not morning.get_attribute('open')
        assert page.locator('.ac-workspace-help').get_attribute('open') is None
        page.locator('[data-manager-search]').fill('evening-manager')
        expect(morning).not_to_be_visible()
        expect(evening).to_be_visible()
        page.locator('[data-manager-search]').fill('')
        page.locator('[data-manager-status]').select_option('approved')
        expect(morning).not_to_be_visible()
        expect(evening).not_to_be_visible()
        page.locator('[data-manager-status]').select_option('pending')
        expect(morning).to_be_visible()
        morning.locator('> summary').click()
        expect(morning.locator('[data-pending-driver="morning-driver"]')).to_be_visible()
        assert morning.locator('[data-inline-bag]').count() == 0, 'Matching driver name and date must never join another manager period'
        expect(morning.locator('[data-day-approve]')).to_be_disabled()
        evening.locator('> summary').click()
        expect(evening.locator('[data-inline-bag="evening-bag"]')).to_be_visible()
        expect(evening.locator('[data-manager-bag-count]')).to_be_visible()
        assert page.evaluate('window.writes') == [], 'Opening shift cards must not receive bags or approve shifts'
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        output=ROOT/'test-artifacts';output.mkdir(exist_ok=True)
        page.screenshot(path=str(output/f'accounting-shift-hub-{width}.png'),full_page=True)
        evening.locator('[data-manager-bag-count]').click()
        assert page.locator('dialog [name=cash]').input_value() == ''
        page.locator('dialog [name=cash]').fill('150')
        page.locator('dialog [name=card]').fill('0')
        page.locator('dialog [name=reference]').fill('Witnessed count 001')
        page.locator('dialog [name=confirmed]').check()
        page.locator('dialog [type=submit]').click()
        expect(page.locator('dialog')).not_to_be_visible()
        expect(page.locator('[data-manager-card="evening-manager"]')).to_have_attribute('open','')
        expect(page.locator('[data-manager-card="evening-manager"] [data-manager-bag-count]')).to_have_count(0)
        expect(page.locator('[data-manager-card="evening-manager"] [data-manager-bag-custody]')).to_be_visible()
        assert page.evaluate('window.writes.length') == 1
        assert page.evaluate('window.writes[0].path') == '/api/cash-bags/evening-bag/count'
        expect(page.locator('[data-manager-card="evening-manager"] [data-day-approve]')).to_be_disabled()
        page.locator('[data-manager-card="evening-manager"] [data-ac-driver-allocate="evening-driver"]').first.click()
        assert page.evaluate('window.allocations') == ['evening-driver']
        assert not errors, errors
        browser.close()
