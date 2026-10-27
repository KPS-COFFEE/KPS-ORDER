"""Real wall-screen DOM and timer acceptance, with deterministic road API evidence."""
import os
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser run')


@pytest.fixture
def wall():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        options = {'headless': True}
        if os.getenv('TEST_CHROMIUM_EXECUTABLE'):
            options['executable_path'] = os.environ['TEST_CHROMIUM_EXECUTABLE']
        browser = p.chromium.launch(**options)
        page = browser.new_page(viewport={'width': 1440, 'height': 900})
        page.set_content('<main id="host"></main>')
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.evaluate('''() => {
          window.clock=Date.now();Date.now=()=>clock;window.timers=[];
          window.setInterval=(fn,ms)=>{timers.push({fn,ms});return timers.length;};window.clearInterval=()=>{};
          window.hidden=false;window.online=true;
          Object.defineProperty(document,'hidden',{get:()=>window.hidden,configurable:true});
          Object.defineProperty(navigator,'onLine',{get:()=>window.online,configurable:true});
          window.requests=[];window.orderChanged=false;window.failOrders=false;window.extraDriver=false;window.secondStale=false;window.etaFailure=0;window.providerUnavailable=false;window.driverStale=false;window.captureOld=false;window.holdEta=false;window.pending=[];window.etaInFlight=0;window.maxEtaInFlight=0;
          window.KPSDispatchMap={mount:async()=>({update(){},destroy(){},fit(){},resize(){}})};
          const iso=ms=>new Date(ms).toISOString();
          window.api=async(path,options={})=>{
            requests.push({path,method:options.method||'GET',body:options.body});
            if(path.includes('/route-eta')){
              etaInFlight++;maxEtaInFlight=Math.max(maxEtaInFlight,etaInFlight);
              if(holdEta)await new Promise(resolve=>pending.push(resolve));
              etaInFlight--;
              if(etaFailure){const error=new Error('Denied');error.status=etaFailure;throw error;}
              if(providerUnavailable)return {status:'unavailable',reason:'routing_provider_unavailable'};
              return {status:'ready',driver_id:'d1',calculated_at:iso(clock),location_updated_at:iso(clock),refresh_after_seconds:60,next_stop:{arrival_at:iso(clock+240000)},delivery_complete_at:iso(clock+900000),return_at:iso(clock+1380000),stops:[{id:'s1',kind:'dropoff',brand_id:'kps',order_id:'o1',order_no:'K-1',customer_name:'Ahmed',arrival_at:iso(clock+240000)}]};
            }
            if(path.includes('driver-locations'))return {drivers:[{id:'d1',display_name:'Driver One',branch_ids:['b1'],movement_state:'delivering',updated_at:iso(clock),captured_at:iso(clock-(driverStale||captureOld?300000:0)),stale:driverStale},...(extraDriver?[{id:'d2',display_name:'Driver Two',branch_ids:['b2'],movement_state:'delivering',updated_at:iso(clock),stale:secondStale}]:[])]};
            if(path.startsWith('/api/orders?')&&failOrders)throw new Error('Order refresh failed');
            if(path.startsWith('/api/orders?')&&orderChanged)return {orders:[{id:'o2',brand_id:'kps',order_no:'K-2',customer_name:'Different customer',driver_id:'d1',created_at:iso(clock),delivery_status:'picked_up'}],next_offset:null,workspace_counts:{current:{total:1,road:1}}};
            if(path.startsWith('/api/orders?'))return {orders:[{id:'o1',brand_id:'kps',order_no:'K-1',customer_name:'Ahmed',driver_id:'d1',created_at:iso(clock-1000),delivery_status:'picked_up'}],next_offset:null,workspace_counts:{current:{total:1,road:1}}};
            throw new Error('Unexpected API: '+path);
          };
          window.tick=async(ms=15000)=>{clock+=ms;for(const t of timers)t.fn();await new Promise(resolve=>setTimeout(resolve,30));};
        }''')
        page.add_script_tag(path=str(ROOT / 'public/assets/order-tracking.js'))
        page.evaluate("async()=>window.controller=await KPSOrderTracking.mount(document.querySelector('#host'),{api,branches:[{id:'b1',city:'Hafar'},{id:'b2',city:'Khafji'}],tr:(ar,en)=>en,lang:'en'})")
        yield page
        assert errors == []
        # A road estimate is permitted; publishing a trip and changing orders is not.
        for request in page.evaluate('requests'):
            assert '/publish' not in request['path']
            assert request['method'] == 'GET' or request['path'] == '/api/operations/route-eta'
            if request['path'] == '/api/operations/route-eta':
                assert request['method'] == 'POST'
                assert request['body'] == {'driver_id': request['body']['driver_id'], 'automatic_preview': True}
        browser.close()


def eta_count(page):
    return page.evaluate("requests.filter(r=>r.path.includes('/route-eta')).length")


def test_wall_automatic_customer_completion_return_countdowns_and_minute_budget(wall):
    wall.wait_for_function("requests.some(r=>r.path.includes('/route-eta'))")
    wall.wait_for_function("document.querySelector('.tracking-driver').textContent.includes('Ahmed')")
    text = wall.locator('.tracking-driver').inner_text()
    assert 'Ahmed' in text and 'K-1' in text
    assert '4 min' in text and '15 min' in text and '23 min' in text
    assert 'Deliveries complete' in text and ('Branch return' in text or 'Return' in text)
    assert wall.locator('.tracking-driver-destination').is_hidden()
    initial = eta_count(wall)
    wall.evaluate('tick(15000)');wall.evaluate('tick(15000)');wall.evaluate('tick(15000)')
    assert eta_count(wall) == initial
    wall.evaluate('tick(16000)')
    wall.wait_for_function(f"requests.filter(r=>r.path.includes('/route-eta')).length==={initial + 1}")
    assert wall.evaluate('maxEtaInFlight') == 1


def test_wall_stale_hidden_offline_and_cleanup_suppress_route_requests(wall):
    wall.wait_for_function("requests.some(r=>r.path.includes('/route-eta'))")
    initial = eta_count(wall)
    wall.evaluate('window.hidden=true;tick(61000)')
    wall.evaluate('window.hidden=false;window.online=false;tick(61000)')
    assert eta_count(wall) == initial
    wall.evaluate('window.online=true;window.driverStale=true;tick(61000)')
    assert eta_count(wall) == initial
    assert 'Stale' in wall.locator('.tracking-driver').inner_text()
    assert '4 min' not in wall.locator('.tracking-driver').inner_text()
    wall.evaluate('window.controller.destroy();tick(61000)')
    assert eta_count(wall) == initial


def test_wall_deferred_provider_does_not_overlap_minute_requests(wall):
    wall.wait_for_function("requests.some(r=>r.path.includes('/route-eta'))")
    wall.evaluate('window.holdEta=true;tick(61000)')
    wall.wait_for_function('pending.length===1')
    initial = eta_count(wall)
    wall.evaluate('tick(61000)');wall.evaluate('tick(61000)')
    assert eta_count(wall) == initial
    assert wall.evaluate('maxEtaInFlight') == 1
    wall.evaluate('window.holdEta=false;pending.splice(0).forEach(fn=>fn())')
    wall.wait_for_function('etaInFlight===0')


def test_wall_old_capture_never_looks_live_with_fresh_receipt(wall):
    wall.wait_for_function("requests.some(r=>r.path.includes('/route-eta'))")
    initial = eta_count(wall)
    wall.evaluate('window.captureOld=true;tick(61000)')
    assert eta_count(wall) == initial
    assert 'Stale' in wall.locator('.tracking-driver').inner_text()
    assert '4 min' not in wall.locator('.tracking-driver').inner_text()


def test_wall_two_driver_provider_calls_are_serial(wall):
    wall.wait_for_function("requests.some(r=>r.path.includes('/route-eta'))")
    wall.evaluate('window.extraDriver=true;window.holdEta=true;tick(61000)')
    wall.wait_for_function('pending.length===1')
    initial = eta_count(wall)
    wall.evaluate('tick(15000)')
    assert eta_count(wall) == initial
    wall.evaluate('pending.splice(0).forEach(fn=>fn())')
    wall.wait_for_function('pending.length===1')
    assert eta_count(wall) == initial + 1
    wall.evaluate('window.holdEta=false;pending.splice(0).forEach(fn=>fn())')
    wall.wait_for_function('etaInFlight===0')
    assert wall.evaluate('maxEtaInFlight') == 1
    assert wall.locator('.tracking-driver').count() == 2


@pytest.mark.parametrize('status', [401, 403, 404, 409])
def test_wall_denied_or_changed_itinerary_clears_previous_customer_estimate(wall, status):
    wall.wait_for_function("document.querySelector('.tracking-driver').textContent.includes('Ahmed')")
    wall.evaluate(f'window.etaFailure={status};tick(61000)')
    wall.wait_for_function("!document.querySelector('.tracking-driver-eta').textContent.includes('Ahmed')")
    assert '4 min' not in wall.locator('.tracking-driver-eta').inner_text()


def test_wall_estimate_expires_without_manufacturing_new_calculation(wall):
    wall.wait_for_function("document.querySelector('.tracking-driver').textContent.includes('Ahmed')")
    wall.evaluate('window.providerUnavailable=true;tick(61000)')
    assert 'Ahmed' in wall.locator('.tracking-driver-eta').inner_text()
    assert 'Last saved estimate' in wall.locator('.tracking-driver-eta').inner_text()
    wall.evaluate('tick(61000)')
    assert 'Ahmed' not in wall.locator('.tracking-driver-eta').inner_text()
    assert '4 min' not in wall.locator('.tracking-driver-eta').inner_text()


def test_wall_filter_change_discards_inflight_provider_result(wall):
    wall.wait_for_function("document.querySelector('.tracking-driver').textContent.includes('Ahmed')")
    wall.evaluate('window.extraDriver=true;window.holdEta=true;tick(61000)')
    wall.wait_for_function('pending.length===1')
    wall.get_by_label('City', exact=True).select_option('Khafji')
    wall.wait_for_function("document.querySelector('.tracking-driver strong').textContent==='Driver Two'")
    wall.evaluate('window.holdEta=false;pending.splice(0).forEach(fn=>fn())')
    wall.wait_for_function('etaInFlight===0')
    assert 'Ahmed' not in wall.locator('.tracking-driver-eta').inner_text()
    # Subsequent refresh starts only the newly visible driver's preview.
    wall.evaluate('tick(15000)')
    wall.wait_for_function("requests.filter(r=>r.path.includes('/route-eta')).at(-1).body.driver_id==='d2'")
    assert wall.locator('.tracking-driver strong').all_text_contents() == ['Driver Two']


def test_wall_completed_toggle_discards_inflight_estimate(wall):
    wall.wait_for_function("document.querySelector('.tracking-driver').textContent.includes('Ahmed')")
    wall.evaluate('window.holdEta=true;tick(61000)')
    wall.wait_for_function('pending.length===1')
    wall.get_by_role('button', name='Completed: 0', exact=True).click()
    wall.wait_for_selector('text=Completed — newest first')
    wall.evaluate('window.holdEta=false;pending.splice(0).forEach(fn=>fn())')
    wall.wait_for_function('etaInFlight===0')
    assert 'Ahmed' not in wall.locator('.tracking-driver-eta').inner_text()
    count = eta_count(wall)
    wall.evaluate('tick(15000)')
    assert eta_count(wall) == count
    wall.evaluate('tick(46000)')
    wall.wait_for_function("document.querySelector('.tracking-driver-eta').textContent.includes('Ahmed')")


def test_wall_serial_queue_rechecks_latest_location_before_next_driver(wall):
    wall.wait_for_function("requests.some(r=>r.path.includes('/route-eta'))")
    wall.evaluate('window.extraDriver=true;window.holdEta=true;tick(61000)')
    wall.wait_for_function('pending.length===1')
    initial = eta_count(wall)
    wall.evaluate('window.driverStale=true;window.secondStale=true;tick(15000)')
    wall.evaluate('window.holdEta=false;pending.splice(0).forEach(fn=>fn())')
    wall.wait_for_function('etaInFlight===0')
    assert eta_count(wall) == initial
    assert 'Ahmed' not in wall.locator('.tracking-driver-eta').first.inner_text()
    assert wall.evaluate('maxEtaInFlight') == 1


@pytest.mark.parametrize('refresh_event', ['orderChanged', 'failOrders'])
def test_wall_old_eta_cannot_restore_after_same_city_refresh_invalidates_itinerary(wall, refresh_event):
    wall.wait_for_function("document.querySelector('.tracking-driver-eta').textContent.includes('Ahmed')")
    wall.evaluate('window.holdEta=true;tick(61000)')
    wall.wait_for_function('pending.length===1')
    wall.evaluate(f'window.{refresh_event}=true;tick(15000)')
    wall.evaluate('window.holdEta=false;pending.splice(0).forEach(fn=>fn())')
    wall.wait_for_function('etaInFlight===0')
    assert 'Ahmed' not in wall.locator('.tracking-driver-eta').inner_text()
    assert 'K-1' not in wall.locator('.tracking-driver-eta').inner_text()
