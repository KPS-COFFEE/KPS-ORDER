"""Accounting actions stay together without turning opening a close into receipt."""
import os
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser acceptance')


@pytest.mark.parametrize('width,lang', [(390,'ar'), (1440,'ar'), (390,'en'), (1440,'en')])
def test_accounting_default_queue_collect_count_and_refresh(width, lang):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as runtime:
        launch = {'headless': True, 'args': ['--no-sandbox']}
        if os.getenv('CHROMIUM_EXECUTABLE'):
            launch['executable_path'] = os.environ['CHROMIUM_EXECUTABLE']
        browser = runtime.chromium.launch(**launch)
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        errors=[]
        page.on('pageerror',lambda error: errors.append(str(error)))
        page.set_content('<html dir="rtl"><body><h1></h1><main id="content"></main><dialog><h2></h2><div id="modal-body"></div></dialog></body></html>')
        page.add_style_tag(content='*{box-sizing:border-box}body{margin:0;padding:16px;font-family:Arial}input,select,button,textarea{font:inherit;max-width:100%;padding:8px}dialog{max-width:calc(100vw - 24px);width:600px}.ac-case-form{display:grid;gap:12px}.ac-case-form label{display:grid;gap:6px}')
        page.add_style_tag(path=str(ROOT/'public/assets/accounting.css'))
        page.add_script_tag(path=str(ROOT/'public/assets/accounting.js'))
        page.evaluate('''async lang=>{
          const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
          window.writes=[];window.reads=[];window.allocations=[];window.counted=false;
          await KPSAccounting.open({lang,user:{id:'accountant',role:'admin'},tr:(ar,en)=>lang==='ar'?ar:en,esc,money:v=>(v/100).toFixed(2),cents:v=>Math.round(Number(v)*100),heading:t=>document.querySelector('h1').textContent=t,
            openSharedAccounting:async()=>window.allocations.push('inbox'),openSharedDriverClosure:async id=>window.allocations.push(id),
            closeModal:()=>document.querySelector('dialog').close(),modal:(title,html,submit)=>{
              const dialog=document.querySelector('dialog');dialog.querySelector('h2').textContent=title;dialog.querySelector('#modal-body').innerHTML=html;dialog.showModal();
              dialog.querySelector('form').onsubmit=async event=>{event.preventDefault();try{await submit(new FormData(event.target));}catch(error){dialog.querySelector('[role=alert]').textContent=error.message;}};
            },
            mutation:async(path,body)=>{window.writes.push({path,body});window.counted=true;return {count_id:'actual-count'};},
            api:async path=>{
              window.reads.push(path);
              if(path.includes('/options'))return {today:'2026-11-02',can_report:true,can_review:true,branches:[],people:[],products:[],categories:{},states:{},responsibilities:{}};
              if(path==='/api/cash-bags')return {bags:[{id:'bag-1',anchor_shift_id:'driver-shift',driver_name:'TEST driver <name>',reference:'BAG-001',branch_name:'TEST branch',destination:'branch',shift_date:'2026-11-01',declared_cash_halalas:700,declared_card_halalas:900,ready_to_count:true,count_id:window.counted?'actual-count':null,received_cash_halalas:window.counted?650:null,counted_by_name:'TEST accountant',accounting_custody_difference_halalas:window.custodyDiff?-50:null,custody_review:window.custodyDiff?{financial_state:'review_approved_outstanding',financially_complete:false}:null}, {id:'waiting',driver_name:'Manager waiting driver',reference:'BAG-002',destination:'manager_vault',declared_cash_halalas:300,ready_to_count:false}]};
              if(path.includes('/manager-cards'))return {cards:[]};
              throw Error('Unexpected read '+path);
            }});
        }''',lang)
        expect(page.locator('[data-ac=settlements]')).to_have_attribute('aria-pressed','true')
        assert page.evaluate('window.writes.length') == 0
        assert page.locator('.ac-workflow ol li').count() == 4
        assert page.locator('[data-ac-bag-count]').count() == 1
        assert page.locator('.ac-bag-row').count() == 2
        page.locator('[data-ac-bag-search]').fill('BAG-001')
        expect(page.locator('.ac-bag-row')).to_have_count(1)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.locator('[data-ac-bag-count]').click()
        assert page.locator('dialog input[name=cash]').input_value() == ''
        assert page.locator('dialog input[name=card]').input_value() == ''
        page.locator('dialog input[name=cash]').fill('6.50')
        page.locator('dialog input[name=card]').fill('9.00')
        page.locator('dialog input[name=reference]').fill('Witnessed collection 001')
        page.locator('dialog button[type=submit]').click()
        assert page.evaluate('window.writes.length') == 0, 'Unchecked physical confirmation must prevent submission'
        page.locator('dialog input[name=confirmed]').check()
        page.locator('dialog button[type=submit]').click()
        expect(page.locator('dialog')).not_to_be_visible()
        assert page.evaluate('window.writes') == [{'path':'/api/cash-bags/bag-1/count','body':{'received_cash_halalas':650,'card_halalas':900,'reference':'Witnessed collection 001','note':'','confirmed':True}}]
        expect(page.locator('[data-ac-bag-count]')).to_have_count(0)
        assert page.locator('[data-ac-bag-search]').input_value() == 'BAG-001', 'Search must survive receipt refresh'
        page.locator('[data-ac-bag-state]').select_option('counted')
        expect(page.locator('.ac-bag-row')).to_have_count(1)
        assert '6.50' in page.locator('.ac-bag-row').inner_text()
        page.locator('[data-ac-bag-allocate]').click()
        expect(page.locator('[data-ac-bag-allocate]')).to_be_visible()
        assert page.evaluate('window.allocations') == ['driver-shift']
        assert page.evaluate('window.writes.length') == 1, 'Count and navigation must not approve or settle money'
        page.locator('[data-ac-bag-state]').select_option('pending')
        page.locator('[data-ac-bag-search]').fill('')
        page.evaluate('window.custodyDiff=true')
        page.locator('[data-ac-refresh-work]').click()
        expect(page.locator('[data-ac-bag-custody]')).to_have_count(1)
        assert page.locator('[data-ac-bag-state]').input_value() == 'pending'
        assert page.locator('.ac-bag-row').count() == 2, 'Counted but outstanding custody variance must stay in the default action queue'
        assert page.evaluate('window.writes.length') == 1
        assert not errors
        browser.close()


@pytest.mark.parametrize('width', [390, 1440])
def test_accounting_busy_controls_wait_for_reads_and_preserve_filter_drafts(width):
    """A slow initial read cannot silently consume a report/tab/filter click."""
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as runtime:
        launch={'headless':True,'args':['--no-sandbox']}
        if os.getenv('CHROMIUM_EXECUTABLE'):
            launch['executable_path']=os.environ['CHROMIUM_EXECUTABLE']
        browser=runtime.chromium.launch(**launch)
        page=browser.new_page(viewport={'width':width,'height':1000})
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.set_content('<html><body><main id="content"></main></body></html>')
        page.add_script_tag(path=str(ROOT/'public/assets/accounting.js'))
        page.evaluate('''()=>{
          window.writes=[];window.reportReads=0;
          const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
          const result={rows:[],columns:[],totals:{},definitions:'Authoritative report loaded'};
          window.opening=KPSAccounting.open({lang:'en',user:{id:'viewer',role:'admin'},tr:(ar,en)=>en,esc,money:String,cents:Number,heading:()=>{},modal:()=>{},closeModal:()=>{},mutation:async(path,body)=>window.writes.push({path,body}),api:async path=>{
            if(path.endsWith('/options'))return {today:'2026-11-02',can_report:true,can_review:false,branches:[],people:[],products:[],categories:{},states:{},responsibilities:{}};
            if(path.includes('/manager-cards'))return new Promise(resolve=>window.releaseInitial=()=>resolve({cards:[]}));
            if(path.includes('/reports?')){window.reportReads++;return new Promise((resolve,reject)=>{window.releaseReport=()=>resolve(result);window.rejectReport=()=>reject(Error('Authoritative report unavailable'));});}
            throw Error('Unexpected read '+path);
          }});
        }''')
        page.wait_for_function('typeof window.releaseInitial === "function"')
        expect(page.locator('.accounting')).to_have_attribute('aria-busy','true')
        expect(page.locator('[data-ac=reports]')).to_be_disabled()
        expect(page.locator('#ac-filters button[type=submit]')).to_be_disabled()
        assert page.evaluate('window.reportReads') == 0
        assert page.evaluate('window.writes') == []
        page.evaluate('window.releaseInitial()')
        expect(page.locator('[data-ac=reports]')).to_be_enabled()
        page.locator('[data-ac=reports]').click()
        page.wait_for_function('window.reportReads === 1')
        # shell() replaced every control during the tab switch; replacements
        # must remain disabled until this new authoritative read finishes.
        expect(page.locator('[data-ac=reports]')).to_have_attribute('aria-pressed','true')
        expect(page.locator('[data-ac=reports]')).to_be_disabled()
        expect(page.locator('#ac-filters button[type=submit]')).to_be_disabled()
        page.evaluate('window.releaseReport()')
        expect(page.locator('[name=start]')).to_be_enabled()
        expect(page.locator('.accounting')).to_have_attribute('aria-busy','false')
        page.locator('[name=start]').fill('2026-11-01')
        page.locator('[name=end]').fill('2026-11-02')
        # Represent a control already disabled by its own availability policy.
        page.locator('[data-ac=pdf]').evaluate('(button)=>button.disabled=true')
        page.locator('#ac-filters button[type=submit]').click()
        page.wait_for_function('window.reportReads === 2')
        expect(page.locator('[name=start]')).to_be_disabled()
        expect(page.locator('[name=start]')).to_have_value('2026-11-01')
        page.evaluate('window.rejectReport()')
        expect(page.locator('#ac-error')).to_contain_text('Authoritative report unavailable')
        expect(page.locator('[name=start]')).to_be_enabled()
        expect(page.locator('[name=start]')).to_have_value('2026-11-01')
        expect(page.locator('[name=end]')).to_have_value('2026-11-02')
        expect(page.locator('#ac-filters button[type=submit]')).to_be_enabled()
        expect(page.locator('[data-ac=reports]')).to_be_enabled()
        expect(page.locator('[data-ac=pdf]')).to_be_disabled()
        assert page.evaluate('window.writes') == [], 'Loading and navigation never queue or replay a financial mutation'
        assert not errors
        browser.close()
