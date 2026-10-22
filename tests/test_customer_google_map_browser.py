"""Google picker DOM acceptance with synthetic SDK only; no billed map calls."""
import json
import os
from pathlib import Path
import pytest
from test_experience_entry_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance')
KEY='AIza'+'a'*35
SYNTHETIC_SDK=r'''
window.constructed=0;window.committed=[];window.saved=null;window.allowed=true;
Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success,error,options){window.gps={success,error,options};}}});
class GoogleMap {
  constructor(container,options){window.constructed++;window.map=this;this.options=options;this.center=options.center;this.zoom=options.zoom;this.events={};
    const attribution=document.createElement('span');attribution.dataset.testid='synthetic-google-attribution';attribution.textContent='Map attribution (synthetic)';container.append(attribution);}
  addListener(name,cb){this.events[name]=cb;return {remove:()=>delete this.events[name]};}
  setCenter(value){this.center=value;this.events.center_changed?.();}
  getCenter(){return {toJSON:()=>this.center};}
  setZoom(value){this.zoom=value;}
  getZoom(){return this.zoom;}
}
window.google={maps:{Map:GoogleMap,RenderingType:{RASTER:'RASTER'},event:{trigger(){},clearInstanceListeners(map){map.events={};}}}};
'''


@pytest.mark.parametrize('engine',['chromium','webkit'])
@pytest.mark.parametrize('lang',['ar','en'])
def test_google_raster_pin_gps_cancel_reuse_and_attribution(static_customer,engine,lang):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=getattr(p,engine).launch(headless=True)
        page=browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
        external=[];errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.route('https://maps.googleapis.com/**',lambda route:(external.append(route.request.url),route.abort()))
        page.goto(static_customer+'/assets/customer-google-map.js')
        page.set_content('<html><head></head><body><div id="host"></div></body></html>')
        page.add_style_tag(url=static_customer+'/style.css')
        page.add_style_tag(url=static_customer+'/customer.css')
        page.add_style_tag(url=static_customer+'/assets/customer-map.css')
        page.add_script_tag(url=static_customer+'/assets/customer-google-map.js')
        page.add_script_tag(url=static_customer+'/assets/customer-map.js')
        page.evaluate(SYNTHETIC_SDK)
        page.evaluate("lang=>{document.documentElement.lang=lang;document.documentElement.dir=lang==='ar'?'rtl':'ltr';}",lang)
        page.evaluate("""async key=>KPSCustomerMap.mount({api:async()=>({enabled:true,provider:'google',browser_key:key}),brandId:'kps',host:document.querySelector('#host'),getLocation:()=>saved,onConfirm:(lat,lng)=>{saved={lat,lng};committed.push(saved);},tr:(ar,en)=>document.documentElement.lang==='ar'?ar:en,canOpen:()=>allowed})""",KEY)
        page.locator('#host button').click()
        expect(page.locator('.map-actions button').last).to_be_disabled()
        page.evaluate('map.events.tilesloaded()')
        expect(page.get_by_test_id('synthetic-google-attribution')).to_be_visible()
        assert page.evaluate('map.options.renderingType')=='RASTER'
        assert page.evaluate('map.options.mapId') is None
        page.evaluate('map.events.click({latLng:{toJSON:()=>({lat:28.45,lng:45.97})}})')
        expect(page.locator('.map-actions button').last).to_be_enabled()
        page.locator('.map-toolbar button').click()
        assert page.evaluate('committed')==[]
        page.locator('#host button').click()
        expect(page.locator('.map-actions button').last).to_be_disabled()
        page.evaluate('map.events.dragstart();map.center={lat:28.46,lng:45.98};map.events.center_changed();map.events.dragend()')
        page.locator('.map-actions button').last.click()
        assert page.evaluate('committed')==[{'lat':28.46,'lng':45.98}]
        assert page.evaluate('constructed')==1
        page.locator('#host button').click()
        page.locator('.map-actions button').first.click()
        assert page.evaluate('gps.options')=={'enableHighAccuracy':True,'timeout':15000,'maximumAge':0}
        page.evaluate('gps.success({coords:{latitude:28.47,longitude:45.99,accuracy:300}})')
        expect(page.locator('.map-status')).to_contain_text('300')
        expect(page.locator('.map-status')).to_contain_text('الدقة منخفضة' if lang=='ar' else 'Low accuracy')
        assert len(page.evaluate('committed'))==1
        page.locator('.map-actions button').first.click()
        page.evaluate('map.events.dragstart();map.center={lat:28.48,lng:46.01};map.events.center_changed();map.events.dragend();gps.success({coords:{latitude:28.1,longitude:45.1,accuracy:1}})')
        page.locator('.map-actions button').last.click()
        assert page.evaluate('committed')[-1]=={'lat':28.48,'lng':46.01}
        page.locator('#host button').click()
        expect(page.get_by_test_id('synthetic-google-attribution')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        output=Path('test-results');output.mkdir(exist_ok=True)
        page.screenshot(path=str(output/f'customer-google-map-{lang}-{engine}.png'))
        page.evaluate('window.gm_authFailure()')
        expect(page.locator('.map-actions button').last).to_be_disabled()
        expect(page.locator('.map-status')).to_contain_text('Google')
        assert len(page.evaluate('committed'))==2
        page.locator('.map-toolbar button').click()
        assert not errors,errors
        assert not external,'Synthetic preloaded SDK must never request Google'
        browser.close()


@pytest.mark.parametrize('failure',['network','auth'])
def test_google_load_failure_keeps_location_uncommitted_and_explains_fallback(static_customer,failure):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':390,'height':844})
        attempts=[]
        def script(route):
            attempts.append(route.request.url)
            if failure=='network':route.abort()
            else:route.fulfill(content_type='application/javascript',body='window.gm_authFailure();')
        page.route('https://maps.googleapis.com/**',script)
        page.goto(static_customer+'/assets/customer-google-map.js')
        page.set_content('<html lang="en"><body><div id="host"></div></body></html>')
        page.add_script_tag(url=static_customer+'/assets/customer-google-map.js')
        page.evaluate("""async key=>{window.committed=[];await KPSCustomerGoogleMap.mount({config:{enabled:true,provider:'google',browser_key:key},brandId:'kps',host:document.querySelector('#host'),getLocation:()=>null,onConfirm:(lat,lng)=>committed.push({lat,lng}),tr:(ar,en)=>en});}""",KEY)
        page.locator('#host button').click()
        expect(page.locator('.map-status')).to_contain_text('Google Maps could not load')
        expect(page.locator('.map-status')).to_contain_text('paste a Google Maps link')
        expect(page.locator('.map-actions button').last).to_be_disabled()
        assert page.evaluate('committed')==[]
        assert len(attempts)==1
        browser.close()
