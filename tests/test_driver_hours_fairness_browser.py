"""Synthetic real Chromium acceptance for acceptance-to-confirmed-return hours."""
import os
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser run')


@pytest.fixture
def hours_page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        options = {'headless': True}
        if os.getenv('TEST_CHROMIUM_EXECUTABLE'):
            options['executable_path'] = os.environ['TEST_CHROMIUM_EXECUTABLE']
        browser = p.chromium.launch(**options)
        contexts = []
        def load(lang='en', width=390):
            context = browser.new_context(viewport={'width':width,'height':900})
            contexts.append(context)
            page = context.new_page()
            page.route('https://hours.test/**', lambda route: route.fulfill(body='',content_type='application/javascript') if '/appearance.js' in route.request.url else route.fulfill(path=str(ROOT/'public'/route.request.url.split('hours.test/')[1].split('?')[0])))
            page.add_init_script("localStorage.setItem('kps-hours-language', '"+lang+"');")
            page.add_init_script('''(() => {
              window.ticks=[];window.setInterval=fn=>{ticks.push(fn);return ticks.length;};window.clearInterval=()=>{};
              window.calls=[];window.hold=false;window.pending=[];window.active=0;window.maxActive=0;window.offline=false;window.hidden=false;
              Object.defineProperty(navigator,'onLine',{get:()=>!offline});Object.defineProperty(document,'visibilityState',{get:()=>hidden?'hidden':'visible'});
              const driver={driver_id:'d',name:'Synthetic driver',shift_status:'open',actual_seconds:27000,duty_seconds:43200,non_delivery_seconds:12600,work_utilization_percent:62.5,active_work_seconds:3600,work_clock_status:'in_progress',work_review_required:false,work_review_reasons:[],work_trip_evidence:[{accepted_at:'2026-10-28T13:00:00Z',returned_at:'2026-10-28T20:30:00Z',return_branch_id:'b2',duration_seconds:27000,status:'returned'}],order_count:4};
              window.payload={start:'2026-10-28',end:'2026-10-28',operational_day_start:'16:00',calculated_at:'2026-10-28T21:00:00Z',totals:{actual_seconds:27000,duty_seconds:43200,non_delivery_seconds:12600,work_utilization_percent:62.5,active_work_seconds:3600,drivers_needing_review:0},drivers:[driver],branches:[{id:'b1',name:'Origin branch'},{id:'b2',name:'Different return branch'}],options:{drivers:[{id:'d',name:'Synthetic driver'},{id:'other',name:'Other driver'}]}};
              window.fetch=async(url,opts={})=>{
                calls.push({url,method:opts.method||'GET'});
                if(url==='/api/session')return {ok:true,status:200,json:async()=>({user:{id:'synthetic-hours-owner',role:'owner',brand_id:'kps',branch_ids:[],effective_permissions:['driver_hours_reports']},csrf_token:'synthetic-csrf'})};
                active++;maxActive=Math.max(active,maxActive);
                const result=structuredClone(payload);
                try{if(hold)await new Promise((resolve,reject)=>{pending.push(resolve);opts.signal?.addEventListener('abort',()=>reject(new DOMException('Cancelled','AbortError')),{once:true});});if(opts.signal?.aborted)throw new DOMException('Cancelled','AbortError');return {ok:true,json:async()=>result};}finally{active--;}
              };
              window.tick=async()=>{ticks.forEach(fn=>fn());await new Promise(r=>setTimeout(r,20));};
            })();''')
            page.goto('https://hours.test/driver-hours.html')
            page.wait_for_selector('.hours-driver')
            return page
        yield load
        for context in contexts:
            context.close()
        browser.close()


@pytest.mark.parametrize('lang,width',[('ar',390),('en',390),('ar',1280),('en',1280)])
def test_accepted_to_return_hours_are_fair_separate_and_mobile_readable(hours_page,lang,width):
    page=hours_page(lang,width)
    assert page.locator('#hours-kpis article').count()==4
    values=page.locator('#hours-kpis strong').all_text_contents()
    assert values == (['٧ ساعة ٣٠ دقيقة','١٢ ساعة','٣ ساعة ٣٠ دقيقة','٦٢٫٥%'] if lang=='ar' else ['7 h 30 min','12 h','3 h 30 min','62.5%'])
    driver=page.locator('.hours-driver')
    assert ('عمل جارٍ من قبول الطلب حتى العودة' if lang=='ar' else 'Active work from acceptance until return') in driver.inner_text()
    assert ('نقص التوقيت' if lang=='ar' else 'Missing timing') in page.locator('[data-i18n=policy]').inner_text()
    driver.locator('details').first.locator('summary').click()
    assert 'Different return branch' in driver.inner_text()
    assert ('قبول أول طلب' if lang=='ar' else 'First task acceptance') in driver.inner_text()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    calls=page.evaluate('calls')
    assert all(r['method']=='GET' for r in calls)
    assert all(r['url']=='/api/session' or '/api/driver-work-hours/report?' in r['url'] for r in calls)
    assert any('/api/driver-work-hours/report?' in r['url'] for r in calls)


def test_missing_timing_is_review_not_confirmed_zero_or_absence(hours_page):
    page=hours_page()
    page.evaluate("payload.drivers[0].work_review_required=true;payload.drivers[0].work_clock_status='needs_review';payload.drivers[0].work_review_reasons=['missing_acceptance_timing'];payload.drivers[0].actual_seconds=0;payload.drivers[0].work_utilization_percent=null;payload.totals.actual_seconds=0;payload.totals.work_utilization_percent=null;payload.totals.drivers_needing_review=1;tick()")
    assert page.locator('#hours-kpis strong').first.inner_text()=='Unavailable'
    text=page.locator('.hours-driver').inner_text()
    assert 'needs review, not absence' in text and 'no start invented' in text
    assert page.locator('#hours-kpis strong').last.inner_text()=='Unavailable'


def test_auto_refresh_single_request_hidden_offline_and_cleanup(hours_page):
    page=hours_page()
    initial=page.evaluate("calls.filter(c=>c.url.includes('/api/driver-work-hours/report?')).length")
    page.evaluate('hold=true;tick()')
    page.evaluate('tick()');page.evaluate('tick()')
    assert page.evaluate("calls.filter(c=>c.url.includes('/api/driver-work-hours/report?')).length")==initial+1
    assert page.evaluate('maxActive')==1
    page.evaluate('hold=false;pending.splice(0).forEach(fn=>fn())')
    page.wait_for_function('active===0')
    page.evaluate('hidden=true;tick()');page.evaluate('hidden=false;offline=true;tick()')
    assert page.evaluate("calls.filter(c=>c.url.includes('/api/driver-work-hours/report?')).length")==initial+1
    page.evaluate("offline=false;window.dispatchEvent(new Event('pagehide'));tick()")
    assert page.evaluate("calls.filter(c=>c.url.includes('/api/driver-work-hours/report?')).length")==initial+1


def test_manual_filter_and_language_replace_old_response_without_race(hours_page):
    page=hours_page()
    page.evaluate('hold=true;tick()')
    page.evaluate("payload.drivers[0].name='Other driver result';document.querySelector('[name=driver_id]').value='other';document.querySelector('[name=driver_id]').dispatchEvent(new Event('change',{bubbles:true}));")
    page.evaluate('hold=false;pending.splice(0).forEach(fn=>fn())')
    page.wait_for_function('active===0')
    assert page.locator('.hours-driver h2').inner_text()=='Other driver result'
    assert 'driver_id=other' in page.evaluate("calls.filter(c=>c.url.includes('/api/driver-work-hours/report?')).at(-1).url")
    page.get_by_role('button',name='العربية',exact=True).click()
    page.wait_for_function("document.documentElement.lang==='ar'")
    assert '٧ ساعة ٣٠ دقيقة' in page.locator('#hours-kpis strong').first.inner_text()


def test_historical_period_does_not_poll_but_manual_refresh_still_works(hours_page):
    page=hours_page()
    # Select a historical day explicitly so initial server-day reconciliation
    # cannot replace the user's date choice. The server supplies its current day.
    page.evaluate("payload.operational_date='2026-10-29';document.querySelector('[name=date]').value='2026-09-01';document.querySelector('[name=date]').dispatchEvent(new Event('change',{bubbles:true}));")
    page.wait_for_function("document.querySelector('#hours-status').textContent===''")
    initial=page.evaluate("calls.filter(c=>c.url.includes('/api/driver-work-hours/report?')).length")
    page.evaluate('tick()');page.evaluate('tick()')
    assert page.evaluate("calls.filter(c=>c.url.includes('/api/driver-work-hours/report?')).length")==initial
    page.get_by_role('button',name='Show report',exact=True).click()
    page.wait_for_function(f"calls.filter(c=>c.url.includes('/api/driver-work-hours/report?')).length==={initial+1}")
    assert 'start=2026-09-01' in page.evaluate("calls.filter(c=>c.url.includes('/api/driver-work-hours/report?')).at(-1).url")
    # A historical month stays quiet; a month containing the current day polls.
    page.evaluate("document.querySelector('[name=period]').value='month';document.querySelector('[name=period]').dispatchEvent(new Event('change',{bubbles:true}));")
    page.wait_for_function("document.querySelector('#hours-status').textContent===''")
    count=page.evaluate("calls.filter(c=>c.url.includes('/api/driver-work-hours/report?')).length");page.evaluate('tick()')
    assert page.evaluate("calls.filter(c=>c.url.includes('/api/driver-work-hours/report?')).length")==count
    page.evaluate("document.querySelector('[name=date]').value='2026-10-01';document.querySelector('[name=date]').dispatchEvent(new Event('change',{bubbles:true}));")
    page.wait_for_function("document.querySelector('#hours-status').textContent===''")
    count=page.evaluate("calls.filter(c=>c.url.includes('/api/driver-work-hours/report?')).length");page.evaluate('tick()')
    assert page.evaluate("calls.filter(c=>c.url.includes('/api/driver-work-hours/report?')).length")==count+1


@pytest.mark.parametrize('lang', ['ar','en'])
def test_recorded_duty_fallback_and_authorized_physical_return_branch_name(hours_page,lang):
    page=hours_page(lang)
    page.evaluate("payload.drivers[0].duty_basis='recorded_shift';payload.work_branches=[{id:'other-brand-return',name:'Authorized shared return branch'}];payload.drivers[0].work_trip_evidence[0].return_branch_id='other-brand-return';tick()")
    text=page.locator('.hours-driver').inner_text()
    assert ('مدة الدوام المحددة غير متوفرة؛ المقارنة تستخدم مدة الشفت المسجلة' if lang=='ar' else 'Configured duty duration is unavailable; comparison uses recorded shift duration') in text
    page.locator('.hours-driver details').first.locator('summary').click()
    assert 'Authorized shared return branch' in page.locator('.hours-driver').inner_text()
    assert page.locator('[name=branch_id] option[value="other-brand-return"]').count()==0
    assert page.locator('[name=branch_id] option[value="b1"]').count()==1
