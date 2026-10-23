"""Isolated layout acceptance; mocked read data never touches operational state."""
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser acceptance')
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('width,lang', [(1440,'ar'),(390,'ar'),(1440,'en'),(390,'en')])
def test_accounting_cards_and_filters_fit_and_work(width, lang):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as runtime:
        launch = {'headless': True, 'args': ['--no-sandbox']}
        if os.getenv('CHROMIUM_EXECUTABLE'): launch['executable_path'] = os.environ['CHROMIUM_EXECUTABLE']
        browser = runtime.chromium.launch(**launch)
        page = browser.new_page(viewport={'width': width, 'height': 1100})
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.set_content(f'<html lang="{lang}" dir="{"rtl" if lang=="ar" else "ltr"}"><body><h1 id="heading"></h1><main id="content"></main></body></html>')
        page.add_style_tag(content='body{margin:0;padding:24px;background:#faf8f5;font-family:Arial}button,input,select,textarea{font:inherit;padding:10px;border:1px solid #ddd;border-radius:8px}button{cursor:pointer;background:#a64116;color:white}main{max-width:1280px;margin:auto}*{box-sizing:border-box}')
        page.add_style_tag(path=str(ROOT/'public/assets/accounting.css'))
        page.add_script_tag(path=str(ROOT/'public/assets/accounting.js'))
        page.evaluate('''async lang => {
          const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
          window.accountingQueries=[];
          const ctx={lang,user:{id:'accountant',role:'owner'},tr:(ar,en)=>lang==='en'?en:ar,esc,money:v=>(v/100).toFixed(2),cents:v=>Math.round(Number(v)*100),heading:t=>document.querySelector('#heading').textContent=t,modal:()=>{},closeModal:()=>{},mutation:async()=>({}),
          api:async url=>url.includes('/options')?{today:'2026-10-21',can_report:true,can_review:true,branches:[{id:'branch',branch_no:1,name:'حفر ١',city:'حفر الباطن'},{id:'branch2',branch_no:2,name:'حفر ٢',city:'حفر الباطن'},{id:'outside',branch_no:3,name:'الدمام',city:'الدمام'}],people:[{id:'driver',role:'driver',display_name:'سائق اختبار'}],products:[{id:'coffee',name:'قهوة'},{id:'unsold',name:'صنف بلا مبيعات'}],categories:{},states:{},responsibilities:{}}:url.includes('driver-settlements')?{settlements:[]}:url.includes('manager-closures')?{closures:[]}:(window.accountingQueries.push(url),{rows:[{label:'صنف بلا مبيعات',quantity:0}],columns:[{key:'label',label:'الصنف'},{key:'quantity',label:'الكمية'}],totals:{quantity:0},definitions:'TEST ONLY'})};
          await window.KPSAccounting.open(ctx);
        }''', lang)
        cards = page.locator('.ac-section-card')
        assert cards.count() == 6
        boxes = cards.evaluate_all('(els)=>els.map(e=>({w:e.getBoundingClientRect().width,h:e.getBoundingClientRect().height}))')
        assert max(b['w'] for b in boxes)-min(b['w'] for b in boxes) < 2
        assert page.locator('[data-ac=reports]').get_attribute('aria-pressed') == 'true'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('[name=start]').fill('2026-10-20')
        page.locator('#ac-filters button[type=submit]').click()
        assert page.locator('[name=start]').input_value() == '2026-10-20'
        page.locator('[name=group]').select_option('items')
        page.locator('[name=product_id]').select_option('unsold')
        page.locator('[data-ac=hafar-branches]').click()
        page.wait_for_function("window.accountingQueries.some(u=>new URL(u,'https://test.invalid').searchParams.get('branch_ids')==='branch,branch2')")
        applied = page.evaluate("new URL(window.accountingQueries.at(-1),'https://test.invalid').searchParams.toString()")
        from urllib.parse import parse_qs
        assert parse_qs(applied)['branch_ids']==['branch,branch2']
        assert parse_qs(applied)['product_id']==['unsold']
        assert parse_qs(applied)['group']==['items']
        assert page.locator('#ac-results').inner_text().find('صنف بلا مبيعات')>=0
        page.locator('[name=branch_ids]').select_option('branch2')
        page.locator('#ac-filters button[type=submit]').click()
        page.wait_for_function("new URL(window.accountingQueries.at(-1),'https://test.invalid').searchParams.get('branch_ids')==='branch2'")
        page.locator('[data-ac=all-branches]').click()
        page.wait_for_function("new URL(window.accountingQueries.at(-1),'https://test.invalid').searchParams.get('branch_ids')===''")
        page.locator('[data-ac=settlements]').click()
        page.wait_for_function("document.querySelector('[data-ac=settlements]').getAttribute('aria-pressed')==='true'")
        assert page.locator('#ac-filters').is_hidden()
        page.locator('[data-ac=reports]').click()
        assert page.locator('#ac-filters').is_visible()
        assert page.locator('[name=start]').input_value() == '2026-10-20'
        assert not errors
        output=ROOT/'test-artifacts';output.mkdir(exist_ok=True)
        page.screenshot(path=str(output/f'accounting-cards-{lang}-{width}.png'),full_page=True)
        browser.close()


@pytest.mark.parametrize('width,profile', [(360,'stress'),(390,'stress'),(768,'stress'),(1280,'stress'),(1440,'stress'),(390,'realistic'),(1440,'realistic')])
@pytest.mark.parametrize('lang', ['ar','en'])
def test_manager_settlement_cards_keep_full_money_and_actions_visible(width,profile,lang):
    """Long real-size data stays readable without scrolling across a settlement."""
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as runtime:
        launch={'headless':True,'args':['--no-sandbox']}
        if os.getenv('CHROMIUM_EXECUTABLE'):launch['executable_path']=os.environ['CHROMIUM_EXECUTABLE']
        browser=runtime.chromium.launch(**launch)
        page=browser.new_page(viewport={'width':width,'height':1100})
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.set_content(f'<html lang="{lang}" dir="{"rtl" if lang=="ar" else "ltr"}"><body><h1 id="heading"></h1><main id="content"></main></body></html>')
        page.add_style_tag(content='*{box-sizing:border-box}body{margin:0;padding:20px;background:#faf8f5;font-family:Arial}button,input,select,textarea{font:inherit;padding:10px;border:1px solid #ddd;border-radius:8px}button{cursor:pointer;background:#a64116;color:white}main{max-width:1280px;margin:auto}')
        page.add_style_tag(path=str(ROOT/'public/assets/accounting.css'))
        page.add_script_tag(path=str(ROOT/'public/assets/accounting.js'))
        page.evaluate('''()=>{
          document.body.insertAdjacentHTML('beforeend','<dialog id="history-modal"><h2></h2><div id="modal-body" class="history-body"></div></dialog>');
        }''')
        page.add_style_tag(content='#history-modal{box-sizing:border-box;width:min(720px,calc(100vw - 24px));max-height:calc(100vh - 32px);overflow:auto;border:1px solid #ddd;border-radius:16px;padding:18px}#history-modal h2{font-size:18px;margin:0 0 14px}#history-modal .history-body{min-width:0;max-width:100%}')
        page.evaluate('''async ({lang,profile})=>{
          const esc=v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
          const money=v=>new Intl.NumberFormat(lang==='ar'?'ar-SA':'en-SA',{style:'currency',currency:'SAR'}).format(Number(v||0)/100);
          const longName=lang==='ar'?'عبدالله عبدالرحمن الحربي — سائق تجريبي باسم طويل لاختبار وضوح بطاقة المحاسبة':'Abdullah Abdulrahman Alharbi — synthetic driver with a long accounting display name';
          const base={day:'2026-10-23',bank_halalas:98765432100,cash_due_halalas:12345678900,cash_received_halalas:null,shortage_halalas:0,surplus_halalas:0,balance_approved:true,cash_recorded:false,reconciliation_complete:false,net_difference_halalas:null,stage:'balance_approved',due_at:'2026-10-24T16:00:00+03:00',can_extend:true,overdue:false,history:[],revision:1};
          const rows=[
            {...base,id:'pending',user_id:'driver-pending',driver_name:longName+' 1'},
            {...base,id:'variance',user_id:'driver-variance',driver_name:longName+' 2',stage:'cash_received',cash_recorded:true,reconciliation_complete:true,cash_received_halalas:12345666350,shortage_halalas:12550,net_difference_halalas:-12550,variance_pending:true,revision:2,can_extend:false,can_correct_cash_receipt:true},
            {...base,id:'confirmed',user_id:'driver-confirmed',driver_name:longName+' 3',stage:'cash_received',cash_recorded:true,reconciliation_complete:true,cash_received_halalas:12345777665,surplus_halalas:98765,net_difference_halalas:98765,variance_pending:false,revision:2,can_extend:false,variance_confirmation:{id:'confirmation',actor_name:lang==='ar'?'محاسب تجريبي باسم طويل لتوثيق المراجعة':'Synthetic accountant with a long reviewer name',created_at:'2026-10-23T12:00:00Z',details:{action_taken:lang==='ar'?'توثيق الفائض للمراجعة والمتابعة دون أي خصم آلي من راتب السائق':'Documented surplus for review and follow-up without any automatic payroll deduction'}}}
          ];
          if(profile==='realistic')rows.forEach((r,index)=>{
            r.driver_name=(lang==='ar'?'عبدالله الحربي':'Abdullah Alharbi')+' '+(index+1);
            r.bank_halalas=23100;r.cash_due_halalas=21500;
            if(r.cash_recorded)r.cash_received_halalas=r.id==='variance'?21400:21600;
            r.shortage_halalas=r.id==='variance'?100:0;r.surplus_halalas=r.id==='confirmed'?100:0;
            r.net_difference_halalas=r.cash_recorded?(r.id==='variance'?-100:100):null;
            if(r.variance_confirmation){r.variance_confirmation.actor_name=lang==='ar'?'محاسب تجريبي':'Test accountant';r.variance_confirmation.details.action_taken=lang==='ar'?'توثيق ومتابعة الفائض':'Document and follow up surplus';}
          });
          const confirmed=rows.find(r=>r.id==='confirmed');
          confirmed.original_bank_halalas=25350;confirmed.original_cash_halalas=19250;
          const eventBase={actor_name:lang==='ar'?'محاسب اختبار — اسم طويل لمراجعة سجل الإجراءات':'Synthetic accountant — long action-history display name',bank_halalas:23100,cash_due_halalas:21500,cash_received_halalas:null,stage:'balance_approved',balance_approved:true,note:lang==='ar'?'اعتماد':'Approved',received_on:null,receipt_reference:null};
          confirmed.history=[
            {...eventBase,id:'h1',action_type:'correction',created_at:'2026-10-23T13:36:57Z'},
            {...eventBase,id:'h2',action_type:'approval',created_at:'2026-10-23T15:32:43Z'},
            {...eventBase,id:'h3',action_type:'cash_receipt',stage:'cash_received',cash_received_halalas:23100,created_at:'2026-10-23T15:53:04Z',received_on:'2026-10-23',receipt_reference:'REFERENCE-'+'1234567890'.repeat(8)},
            {...eventBase,id:'h4',action_type:'approval',stage:'cash_received',cash_received_halalas:23100,created_at:'2026-10-23T15:57:48Z',received_on:'2026-10-23',receipt_reference:'REFERENCE-'+'1234567890'.repeat(8),note:lang==='ar'?'تعديل':'Correction'},
            {...eventBase,id:'h5',action_type:'cash_receipt_correction',actor_name:lang==='ar'?'مالك تجريبي':'Test owner',stage:'cash_received',cash_received_halalas:21600,created_at:'2026-10-23T16:10:00Z',received_on:'2026-10-23',receipt_reference:'REFERENCE-'+'1234567890'.repeat(8),note:lang==='ar'?'تصحيح تسجيل النقد بعد المراجعة':'Correct receipt entry after review'}
          ];
          rows.forEach(r=>{r.system_total_halalas=profile==='realistic'?44600:111111111000;r.actual_total_halalas=r.reconciliation_complete?r.bank_halalas+r.cash_received_halalas:null;});
          window.layoutExpected=rows.map(r=>({id:r.id,name:r.driver_name,total:money(r.bank_halalas+(r.reconciliation_complete?r.cash_received_halalas:r.cash_due_halalas)),money:[r.bank_halalas,r.cash_due_halalas,r.cash_received_halalas,r.shortage_halalas,r.surplus_halalas].map((v,index)=>index>=2&&!r.cash_recorded?'—':money(v))}));
          window.layoutMutations=0;
          await KPSAccounting.open({lang,user:{id:'accountant',role:'owner'},tr:(ar,en)=>lang==='ar'?ar:en,esc,money,cents:v=>Math.round(Number(v)*100),heading:t=>document.querySelector('#heading').textContent=t,modal:(title,html)=>{const dialog=document.querySelector('#history-modal');dialog.querySelector('h2').textContent=title;dialog.querySelector('.history-body').innerHTML=html;dialog.showModal();},closeModal:()=>document.querySelector('#history-modal').close(),mutation:async()=>{window.layoutMutations++;throw Error('Layout acceptance must never post financial data');},api:async url=>{
            if(url.includes('/options'))return {today:'2026-10-23',can_report:true,can_review:true,branches:[{id:'branch',name:'TEST Branch',city:'حفر الباطن'}],people:[],products:[],categories:{},states:{},responsibilities:{}};
            if(url.includes('/manager-cards'))return {cards:[{id:'manager',manager_name:lang==='ar'?'مدير توصيل تجريبي — شفت مسائي حفر الباطن':'Synthetic delivery manager — Hafar evening shift',day:'2026-10-23',drivers:rows,total:3,completed:1,balance_completed:3,cash_completed:2,overdue_count:0,completion_percent:71,ready:false,due_on:'2026-10-24',due_at:'2026-10-24T16:00:00+03:00'}]};
            if(url.includes('/driver-settlements'))return {settlements:rows};
            return {rows:[],columns:[],totals:{},definitions:'SYNTHETIC LAYOUT ONLY'};
          }});
        }''',{'lang':lang,'profile':profile})
        page.locator('[data-ac=settlements]').click()
        rows=page.locator('[data-driver-row]')
        expect(rows).to_have_count(3)
        expected=page.evaluate('window.layoutExpected')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'), 'Settlement page must fit the viewport'
        workspace=page.locator('.ac-manager-card').evaluate('''card=>{
          const area=card.parentElement.getBoundingClientRect(),box=card.getBoundingClientRect();
          const table=card.querySelector('.ac-table').getBoundingClientRect(),row=card.querySelector('[data-driver-row]').getBoundingClientRect();
          return {availableWidth:area.width,cardWidth:box.width,leftGap:box.left-area.left,rightGap:area.right-box.right,tableWidth:table.width,rowWidth:row.width};
        }''')
        assert abs(workspace['cardWidth']-workspace['availableWidth'])<=2, workspace
        assert workspace['leftGap']<=2 and workspace['rightGap']<=2, workspace
        assert abs(workspace['rowWidth']-workspace['tableWidth'])<=2, workspace
        for item in expected:
            row=page.locator(f'[data-driver-row="{item["id"]}"]')
            cells=row.locator('td')
            expect(cells).to_have_count(7)
            expect(cells.nth(0)).to_contain_text(item['name'])
            expect(cells.nth(0).locator('.ac-driver-total')).to_contain_text(item['total'])
            expect(row).not_to_contain_text('NaN')
            for index,value in enumerate(item['money'],start=1):
                expect(cells.nth(index)).to_contain_text(value)
            geometry=row.evaluate('''row=>{
              const bounds=row.getBoundingClientRect(),cells=[...row.querySelectorAll('td')];
              const fits=element=>{const box=element.getBoundingClientRect(),range=document.createRange();range.selectNodeContents(element);return element.scrollWidth<=element.clientWidth+1&&[...range.getClientRects()].every(r=>r.left>=box.left-1&&r.right<=box.right+1);};
              const boxes=cells.map(e=>e.getBoundingClientRect());
              const controls=[...cells[6].querySelectorAll('button,a')];
              const name=cells[0].querySelector('.ac-driver-heading>strong').getBoundingClientRect(),total=cells[0].querySelector('.ac-driver-total').getBoundingClientRect();
              const headerGap=Math.max(0,name.left-total.right,total.left-name.right);
              const headerVerticalGap=Math.max(0,name.top-total.bottom,total.top-name.bottom);
              return {rowHeight:bounds.height,headerGap,headerVerticalGap,headerSameLine:name.top<total.bottom&&total.top<name.bottom,rowFits:row.scrollWidth<=row.clientWidth+1,allCellTextFits:cells.every(fits),allActionTextFits:controls.every(fits),headerWidth:boxes[0].width,actionWidth:boxes[6].width,rowWidth:bounds.width,headerBottom:boxes[0].bottom,metricsTop:Math.min(...boxes.slice(1,6).map(b=>b.top)),metricsBottom:Math.max(...boxes.slice(1,6).map(b=>b.bottom)),actionsTop:boxes[6].top};
            }''')
            assert geometry['rowFits'] and geometry['allCellTextFits'] and geometry['allActionTextFits'], geometry
            assert geometry['headerWidth']>=geometry['rowWidth']*.8 and geometry['actionWidth']>=geometry['rowWidth']*.8, geometry
            assert geometry['headerBottom']<=geometry['metricsTop']+1 and geometry['metricsBottom']<=geometry['actionsTop']+1, geometry
            if profile=='realistic':
                # A short name and its total remain one nearby reading group; large
                # screens must not turn ordinary settlements into mostly blank cards.
                assert geometry['headerVerticalGap']<=12, geometry
                if width>=1280:
                    assert geometry['headerSameLine'] and geometry['headerGap']<=24, geometry
                    assert geometry['rowHeight']<=400, geometry
            expect(row.locator('[data-driver-history]')).to_be_visible()
            expect(row.locator(f'a[href="/api/accounting/driver-settlements/{item["id"]}/print"]')).to_be_visible()
        expect(page.locator('[data-variance-confirm=variance]')).to_be_visible()
        expect(page.locator('[data-driver-balance=variance]')).to_be_visible()
        expect(page.locator('[data-driver-receipt=pending]')).to_be_visible()
        expect(page.locator('[data-driver-balance=confirmed]')).to_have_count(0)
        assert page.evaluate('window.layoutMutations')==0 and not errors
        output=ROOT/'test-artifacts';output.mkdir(exist_ok=True)
        page.screenshot(path=str(output/f'accounting-manager-cards-{lang}-{width}{"-realistic" if profile=="realistic" else ""}.png'),full_page=True)
        if profile=='realistic':
            page.locator('[data-driver-cash-correction=variance]').click()
            correction=page.locator('#history-modal')
            correction.locator('[name=corrected_cash]').fill('215.00')
            expect(correction.locator('#cash-correction-preview')).to_contain_text(page.evaluate("new Intl.NumberFormat(document.documentElement.lang==='ar'?'ar-SA':'en-SA',{style:'currency',currency:'SAR'}).format(446)"))
            expect(correction.locator('[name=confirmed]')).not_to_be_checked()
            assert page.evaluate('window.layoutMutations')==0
            page.screenshot(path=str(output/f'accounting-manager-cards-cash-correction-{lang}-{width}.png'),full_page=True)
            page.keyboard.press('Escape')
            page.locator('[data-driver-history=confirmed]').click()
            history=page.locator('.ac-settlement-history')
            expect(history).to_be_visible()
            events=history.locator('.ac-history-event')
            expect(events).to_have_count(5)
            assert events.evaluate_all('(es)=>es.map(e=>e.dataset.actionKind)')==['correction','approval','receipt','approval','correction']
            expect(events.nth(3).locator('h3')).to_have_text('اعتماد المبالغ دون تعديل' if lang=='ar' else 'Amounts approved unchanged')
            expect(events.nth(3).locator('.ac-history-note p')).to_have_text('تعديل' if lang=='ar' else 'Correction')
            assert events.nth(0).locator('[data-changed=true]').count()==2
            assert events.nth(1).locator('[data-changed=true]').count()==0
            expect(events.nth(0).locator('tbody tr').nth(0).locator('td').nth(0)).to_contain_text(page.evaluate("new Intl.NumberFormat(document.documentElement.lang==='ar'?'ar-SA':'en-SA',{style:'currency',currency:'SAR'}).format(253.5)"))
            expect(events.nth(2).locator('.ac-history-received dd')).to_contain_text(page.evaluate("new Intl.NumberFormat(document.documentElement.lang==='ar'?'ar-SA':'en-SA',{style:'currency',currency:'SAR'}).format(231)"))
            expect(events.nth(4).locator('h3')).to_have_text('تصحيح الكاش المستلم — المالك' if lang=='ar' else 'Cash receipt corrected — owner')
            expect(events.nth(4).locator('tbody tr')).to_have_count(3)
            expect(events.nth(4).locator('.ac-history-received dd')).to_contain_text(page.evaluate("new Intl.NumberFormat(document.documentElement.lang==='ar'?'ar-SA':'en-SA',{style:'currency',currency:'SAR'}).format(216)"))
            expect(events.nth(2).locator('time')).to_contain_text('٢٠٢٦' if lang=='ar' else '2026')
            expect(history).not_to_contain_text('NaN')
            assert history.evaluate('''root=>{
              const fits=e=>e.scrollWidth<=e.clientWidth+1;
              const dialog=root.closest('dialog');
              return fits(dialog)&&fits(root)&&[...root.querySelectorAll('article,table,th,td,dd,h3,time')].every(fits)&&document.documentElement.scrollWidth<=innerWidth+1;
            }'''), 'History and full amounts must fit without horizontal scrolling'
            assert page.evaluate('window.layoutMutations')==0 and not errors
            page.screenshot(path=str(output/f'accounting-manager-cards-history-{lang}-{width}.png'),full_page=True)
        browser.close()
