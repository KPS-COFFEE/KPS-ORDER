import os
import pytest
from test_experience_entry_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')


@pytest.mark.parametrize('lang',['ar','en'])
def test_pin_draft_confirm_cancel_map_reuse_and_no_external_calls(static_customer,lang):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':390,'height':850})
        external=[]
        page.route('https://api.mapbox.com/**',lambda route:(external.append(route.request.url),route.abort()))
        page.goto(static_customer+'/assets/customer-map.js')
        page.set_content('<html><body><div id="host"></div></body></html>')
        page.add_script_tag(url=static_customer+'/assets/customer-map.js')
        page.evaluate("""async lang=>{
          window.committed=[];window.constructed=0;
          class Map {constructor(){constructed++;window.map=this;this.events={};}on(name,cb){this.events[name]=cb;}addControl(){}resize(){}jumpTo(){}remove(){}}
          class Marker {constructor(){window.marker=this;this.events={};}setLngLat(point){this.point=point;return this;}addTo(){return this;}on(name,cb){this.events[name]=cb;}getLngLat(){return {lng:this.point[0],lat:this.point[1]};}}
          window.mapboxgl={Map,Marker,NavigationControl:class {}};
          await KPSCustomerMap.mount({api:async()=>({enabled:true,provider:'mapbox',public_token:'pk.synthetic-test-only',style:'mapbox://styles/mapbox/streets-v12'}),brandId:'kps',host:document.querySelector('#host'),getLocation:()=>null,onConfirm:(lat,lng)=>committed.push({lat,lng}),tr:(ar,en)=>lang==='ar'?ar:en,canOpen:()=>true});
        }""",lang)
        page.locator('#host button').click()
        page.evaluate("map.events.load();map.events.click({lngLat:{lat:28.45,lng:45.96}})")
        assert page.evaluate('committed')==[]
        page.locator('.map-toolbar button').click()
        page.locator('#host button').click()
        expect(page.locator('.map-actions button').last).to_be_disabled()
        page.evaluate("marker.setLngLat([45.97,28.46]);marker.events.dragend()")
        page.locator('.map-actions button').last.click()
        assert page.evaluate('committed')==[{'lat':28.46,'lng':45.97}]
        assert page.evaluate('constructed')==1
        assert not external
        browser.close()


@pytest.mark.parametrize('lang',['ar','en'])
def test_gps_accuracy_explicit_confirmation_late_callback_and_failures(static_customer,lang):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':390,'height':850})
        external=[]
        page.route('https://api.mapbox.com/**',lambda route:(external.append(route.request.url),route.abort()))
        page.goto(static_customer+'/assets/customer-map.js')
        page.set_content('<html><body><div id="host"></div></body></html>')
        page.add_script_tag(url=static_customer+'/assets/customer-map.js')
        page.evaluate("""async lang=>{
          window.committed=[];
          Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success,error,options){window.gps={success,error,options};}}});
          class Map {constructor(){window.map=this;this.events={};}on(name,cb){this.events[name]=cb;}addControl(){}resize(){}jumpTo(){}remove(){}}
          class Marker {constructor(){window.marker=this;this.events={};}setLngLat(point){this.point=point;return this;}addTo(){return this;}on(name,cb){this.events[name]=cb;}getLngLat(){return {lng:this.point[0],lat:this.point[1]};}}
          window.mapboxgl={Map,Marker,NavigationControl:class {}};
          await KPSCustomerMap.mount({api:async()=>({enabled:true,provider:'mapbox',public_token:'pk.synthetic-test-only',style:'mapbox://styles/mapbox/streets-v12'}),brandId:'kps',host:document.querySelector('#host'),getLocation:()=>null,onConfirm:(lat,lng)=>committed.push({lat,lng}),tr:(ar,en)=>lang==='ar'?ar:en,canOpen:()=>true});
        }""",lang)
        page.locator('#host button').click()
        page.evaluate('map.events.load()')
        locate=page.locator('.map-actions button').first
        confirm=page.locator('.map-actions button').last
        locate.click()
        assert page.evaluate('gps.options')=={'enableHighAccuracy':True,'timeout':15000,'maximumAge':0}
        page.evaluate('gps.success({coords:{latitude:28.4500123,longitude:45.9600456,accuracy:7.2}})')
        expect(page.locator('.map-status')).to_contain_text('8')
        assert page.evaluate('committed')==[]
        confirm.click()
        assert page.evaluate('committed')==[{'lat':28.4500123,'lng':45.9600456}]

        page.locator('#host button').click()
        locate.click()
        page.evaluate('gps.success({coords:{latitude:28.45,longitude:45.96,accuracy:300}})')
        expect(page.locator('.map-status')).to_contain_text('300')
        expect(page.locator('.map-status')).to_contain_text('الدبوس' if lang=='ar' else 'pin')
        # A late GPS fix must not replace a deliberate customer pin selection.
        locate.click()
        page.evaluate("map.events.click({lngLat:{lat:28.46,lng:45.97}});gps.success({coords:{latitude:28.40,longitude:45.90,accuracy:2}})")
        confirm.click()
        assert page.evaluate('committed')[-1]=={'lat':28.46,'lng':45.97}

        page.locator('#host button').click()
        locate.click()
        page.evaluate('gps.error({code:1})')
        expect(locate).to_be_enabled()
        expect(confirm).to_be_disabled()
        # Permission refusal leaves manual selection available.
        page.evaluate("map.events.click({lngLat:{lat:28.47,lng:45.98}})")
        expect(confirm).to_be_enabled()
        # A failed map must never confirm an unrendered location.
        page.evaluate("map.events.error({error:new Error('synthetic SDK failure')})")
        expect(confirm).to_be_disabled()
        assert len(page.evaluate('committed'))==2
        assert not external
        browser.close()
