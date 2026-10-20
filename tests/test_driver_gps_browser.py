"""Actual GPS/map browser code with synthetic positions and an isolated API boundary."""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit Chromium run')


@pytest.fixture
def gps_page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        options = {'headless': True}
        if os.getenv('TEST_CHROMIUM_EXECUTABLE'):
            options['executable_path'] = os.environ['TEST_CHROMIUM_EXECUTABLE']
        browser = p.chromium.launch(**options)
        page = browser.new_page()
        page.route('https://gps.test/**', lambda route: route.fulfill(content_type='text/html', body='<div id="gps"></div><div id="map"></div>'))
        page.goto('https://gps.test/')
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        yield page
        assert not errors
        browser.close()


def test_real_browser_driver_consent_throttle_visibility_and_stop(gps_page):
    page = gps_page
    page.evaluate('''() => {
      window.writes=[];window.user={id:'driver',role:'driver'};window.openShift=true;
      window.clock=Date.now();Date.now=()=>window.clock;
      window.hiddenPage=false;Object.defineProperty(document,'hidden',{get:()=>window.hiddenPage});
      window.cleared=0;window.watches=0;
      Object.defineProperty(navigator,'geolocation',{value:{
        watchPosition(ok,fail){window.position=ok;window.locationError=fail;return ++window.watches;},
        clearWatch(){window.cleared++;}
      }});
      window.api=async(path,options={})=>{
        if(path==='/api/shifts/current')return {shifts:window.openShift?[{id:'shift',user_id:'driver',kind:'driver',status:'open'}]:[]};
        window.writes.push({path,...options});
        return {shift_id:'shift',updated_at:new Date(window.clock).toISOString()};
      };
      window.emit=()=>window.position({coords:{latitude:28.4,longitude:45.9,accuracy:12},timestamp:window.clock});
    }''')
    page.add_script_tag(path=str(ROOT / 'public/assets/driver-location.js'))
    page.evaluate("window.controller=KPSDriverLocation.init({api:window.api,getUser:()=>window.user,tr:(ar,en)=>en,host:document.querySelector('#gps')});window.controller.refresh()")
    button = page.locator('#gps button')
    assert page.evaluate('window.watches') == 0
    assert page.evaluate('window.writes.length') == 0
    button.click()
    page.evaluate('window.emit()')
    page.wait_for_function('window.writes.length===1')
    assert page.evaluate('window.writes[0].body') == {'lat': 28.4, 'lng': 45.9, 'accuracy_m': 12}
    assert page.evaluate('window.writes[0].method') == 'POST'
    page.evaluate('window.emit()')
    assert page.evaluate('window.writes.length') == 1
    page.evaluate('window.hiddenPage=true;document.dispatchEvent(new Event("visibilitychange"));window.clock+=21000;window.emit()')
    assert page.evaluate('window.writes.length') == 1
    assert page.evaluate('window.cleared') >= 1
    page.evaluate('window.hiddenPage=false;document.dispatchEvent(new Event("visibilitychange"))')
    page.wait_for_function('window.watches===2')
    page.evaluate('window.emit()')
    page.wait_for_function('window.writes.length===2')
    button.click()
    page.wait_for_function('window.writes.length===3')
    assert page.evaluate('window.writes[2].method') == 'DELETE'
    page.evaluate('window.clock+=21000;window.emit()')
    assert page.evaluate('window.writes.length') == 3
    # Permission denial remains opt-in and clearly explains the blocked state.
    button.click()
    page.evaluate('window.locationError({code:1})')
    assert 'Allow location' in page.locator('#gps').inner_text()
    page.evaluate('window.openShift=false;window.controller.refresh()')
    page.wait_for_function('document.querySelector("#gps button").disabled')
    page.evaluate("window.user={id:'manager',role:'delivery_manager'};window.controller.refresh()")
    assert page.locator('#gps').is_hidden()


def test_real_browser_manager_marker_moves_and_expires(gps_page):
    page = gps_page
    page.evaluate('''() => {
      window.markers=[];
      class Marker {
        constructor({element}){this.element=element;window.markers.push(this);}
        setLngLat(value){this.position=value;return this;}
        setPopup(value){this.popup=value;return this;}
        addTo(map){
          // Real Mapbox adds these classes; its stylesheet positions markers.
          this.element.classList.add('mapboxgl-marker','mapboxgl-marker-anchor-center');
          map.container.append(this.element);return this;
        }
        remove(){this.element.remove();this.removed=true;}
      }
      class Popup {setDOMContent(root){this.root=root;return this;}}
      class Map {
        constructor(options){this.container=options.container;}
        addControl(){} on(name,fn){if(name==='load')setTimeout(fn,0);}resize(){}fitBounds(){}remove(){}
      }
      window.mapboxgl={Map,Marker,Popup,NavigationControl:class {},LngLatBounds:class {extend(){}},supported:()=>true};
      window.clock=Date.now();Date.now=()=>window.clock;
      window.driver=(lat,age)=>({id:'d1',display_name:'Synthetic Driver',gps:{location:{lat,lng:45.9},updated_at:new Date(window.clock-age).toISOString(),accuracy_m:12}});
    }''')
    page.add_style_tag(content='.mapboxgl-marker{position:absolute;top:0;left:0}.dispatch-map-canvas{position:relative;height:300px}')
    page.add_script_tag(path=str(ROOT / 'public/assets/dispatch-map.js'))
    page.evaluate("async()=>{window.mapController=await KPSDispatchMap.mount(document.querySelector('#map'),{api:async()=>({enabled:true,provider:'mapbox',public_token:'pk.synthetic-for-browser-only',style:'synthetic'}),drivers:[window.driver(28.4,1000)],lang:'en'});}")
    page.wait_for_function('window.markers.length===1')
    assert page.evaluate('window.markers[0].position') == [45.9, 28.4]
    assert page.locator('.dispatch-map-pin.driver').count() == 1
    marker = page.locator('.dispatch-map-pin.driver')
    assert marker.evaluate("el=>el.classList.contains('mapboxgl-marker-anchor-center')")
    assert marker.evaluate("el=>getComputedStyle(el).position") == 'absolute'
    assert marker.is_visible()
    page.evaluate('window.mapController.update({drivers:[window.driver(28.5,1000)]})')
    assert page.evaluate('window.markers.length') == 1
    assert page.evaluate('window.markers[0].position') == [45.9, 28.5]
    page.evaluate('window.mapController.update({drivers:[window.driver(28.5,180000)]})')
    assert 'stale' in page.locator('.dispatch-map-pin.driver').get_attribute('class')
    assert marker.evaluate("el=>getComputedStyle(el).position") == 'absolute'
    assert marker.evaluate("el=>el.classList.contains('mapboxgl-marker-anchor-center')")
    page.get_by_role('button', name='Show all locations').click()
    assert marker.is_visible()
    page.evaluate('window.mapController.update({drivers:[window.driver(28.6,1000)]})')
    assert 'stale' not in marker.get_attribute('class')
    assert marker.evaluate("el=>getComputedStyle(el).position") == 'absolute'
    page.evaluate('window.mapController.update({drivers:[window.driver(28.5,1000000)]})')
    assert page.locator('.dispatch-map-pin.driver').count() == 0
    assert page.evaluate('window.markers[0].removed')
    page.evaluate('window.mapController.destroy()')


def test_order_pin_popup_does_not_scroll_or_reload_page(gps_page):
    """Mapbox 3.33 Popup focuses its first button by default, without preventScroll."""
    page = gps_page
    page.evaluate('''() => {
      document.body.style.margin='0';document.body.style.paddingBottom='1000px';
      const host=document.querySelector('#map');
      host.style.cssText='margin-top:900px;height:600px;position:relative';
      window.openedOrders=[];window.mapsCreated=0;window.popupFocusCalls=0;window.useDefaultPopupFocus=true;
      class Popup {
        constructor(options){this.options=window.useDefaultPopupFocus?{focusAfterOpen:true}:{focusAfterOpen:true,...options};}
        setDOMContent(root){this.root=root;return this;}
        addTo(map){
          this.root.style.cssText='position:absolute;top:0;background:white';
          map.container.append(this.root);
          // Exact relevant behavior of Mapbox Popup._focusFirstElement:
          // firstFocusable.focus(), with no preventScroll option.
          if(this.options.focusAfterOpen){window.popupFocusCalls++;this.root.querySelector('button')?.focus();}
          return this;
        }
      }
      class Marker {
        constructor({element}){this.element=element;}
        setLngLat(value){return this;}
        setPopup(value){this.popup=value;return this;}
        addTo(map){
          this.element.style.cssText='position:absolute;top:450px';
          map.container.append(this.element);
          this.element.addEventListener('click',()=>this.popup.addTo(map));return this;
        }
        remove(){this.element.remove();}
      }
      class Map {
        constructor(options){this.container=options.container;window.mapsCreated++;}
        addControl(){}on(name,fn){if(name==='load')setTimeout(fn,0);}resize(){}fitBounds(){}remove(){}
      }
      window.mapboxgl={Map,Marker,Popup,NavigationControl:class {},LngLatBounds:class {extend(){}},supported:()=>true};
      window.order={id:'delivered-order',order_reference:'HFR-123',delivery_status:'delivered',customer_location:{lat:28.4,lng:45.9}};
    }''')
    page.add_script_tag(path=str(ROOT / 'public/assets/dispatch-map.js'))
    mount="""async()=>{window.controller=await KPSDispatchMap.mount(document.querySelector('#map'),{
      api:async()=>({enabled:true,provider:'mapbox',public_token:'pk.synthetic',style:'synthetic'}),
      orders:[window.order],lang:'en',onSelectOrder:id=>window.openedOrders.push(id)});} """
    # Control: reproduce the default SDK focus behavior and native document jump.
    page.evaluate(mount)
    page.wait_for_selector('.dispatch-map-pin.delivered')
    page.evaluate('window.scrollTo(0,1100);window.scrollBefore=window.scrollY')
    page.locator('.dispatch-map-pin.delivered').click()
    assert page.evaluate('window.popupFocusCalls') == 1
    assert page.evaluate('Math.abs(window.scrollY-window.scrollBefore)') > 20
    # Actual production options disable that default; the same click stays put.
    page.evaluate('window.controller.destroy();window.useDefaultPopupFocus=false;window.mapsCreated=0;window.popupFocusCalls=0')
    page.evaluate(mount)
    page.wait_for_selector('.dispatch-map-pin.delivered')
    page.evaluate('window.scrollTo(0,1100);window.scrollBefore=window.scrollY;window.documentBefore=document;window.markerBefore=document.querySelector(".dispatch-map-pin")')
    page.locator('.dispatch-map-pin.delivered').click()
    assert page.evaluate('Math.abs(window.scrollY-window.scrollBefore)') <= 1
    assert page.evaluate('window.popupFocusCalls') == 0
    assert page.evaluate('window.openedOrders') == []
    assert page.evaluate('window.documentBefore===document && window.mapsCreated===1')
    assert page.locator('.dispatch-map-popup').is_visible()
    # Ordinary location polling keeps the map instance and viewport.
    page.evaluate('window.controller.update({orders:[window.order]})')
    assert page.evaluate('window.markerBefore===document.querySelector(".dispatch-map-pin")')
    assert page.evaluate('Math.abs(window.scrollY-window.scrollBefore)') <= 1
    # Explicitly opening an order is a callback, never a navigation or form submit.
    page.locator('.dispatch-map-popup button').evaluate('button=>button.click()')
    assert page.evaluate('window.openedOrders') == ['delivered-order']
    assert page.evaluate('window.documentBefore===document && window.mapsCreated===1')
    page.evaluate('window.controller.destroy()')


def test_routes_require_explicit_fresh_driver_destination_and_budget_permit(gps_page):
    page = gps_page
    page.evaluate('''() => {
      window.calls=[];window.sources={};window.layers={};window.deny=false;
      window.driver={id:'driver',display_name:'Test driver',gps:{location:{lat:28.4,lng:45.9},updated_at:new Date().toISOString()}};
      class Map {
        addControl(){}on(name,fn){if(name==='load')setTimeout(fn,0);}resize(){}fitBounds(){}remove(){}
        addSource(id,source){window.sources[id]=source;}getSource(id){return window.sources[id];}removeSource(id){delete window.sources[id];}
        addLayer(layer){window.layers[layer.id]=layer;}getLayer(id){return window.layers[id];}removeLayer(id){delete window.layers[id];}
      }
      class Marker {constructor({element}){}setLngLat(){return this;}setPopup(){return this;}addTo(){return this;}remove(){}}
      class Popup {setDOMContent(){return this;}}
      window.mapboxgl={Map,Marker,Popup,NavigationControl:class {},LngLatBounds:class {extend(){}},supported:()=>true};
      window.api=async(path,options={})=>{
        window.calls.push({path,...options});
        if(path.includes('map-config'))return {enabled:true,provider:'mapbox',public_token:'pk.synthetic',style:'synthetic'};
        if(options.method==='POST'){
          if(window.deny)throw Object.assign(new Error('limit'),{status:429});
          return {origin:{lat:28.4,lng:45.9},destination:{lat:28.5,lng:46},kind:options.body.task_id?'customer':'return_branch'};
        }
        return {tasks:[{id:'task',order_id:'order',order_no:'123'}],return_branches:[{id:'branch',name:'Main branch'}]};
      };
      window.fetch=async(url,options)=>{window.calls.push({directions:url,credentials:options.credentials});return {ok:true,json:async()=>({code:'Ok',routes:[{duration:610,geometry:{type:'LineString',coordinates:[[45.9,28.4],[46,28.5]]}}]})};};
    }''')
    page.add_script_tag(path=str(ROOT / 'public/assets/dispatch-map.js'))
    page.evaluate("async()=>{window.controller=await KPSDispatchMap.mount(document.querySelector('#map'),{api:window.api,drivers:[window.driver],lang:'en'});}")
    driver = page.get_by_label('Choose driver for route')
    destination = page.get_by_label('Choose route destination')
    calculate = page.get_by_role('button', name='Calculate route and ETA')
    driver.select_option('driver')
    page.wait_for_function('document.querySelectorAll(".dispatch-map-route select")[1].options.length===3')
    assert calculate.is_disabled()
    assert page.evaluate('window.calls.filter(c=>c.directions).length') == 0
    destination.select_option('task:task')
    calculate.click()
    page.wait_for_function('!!window.sources["dispatch-route"]')
    assert 'Estimated customer arrival: 11 minutes' in page.locator('.dispatch-map-route [role=status]').inner_text()
    assert page.evaluate('window.calls.filter(c=>c.method==="POST")[0].body') == {'driver_id': 'driver', 'task_id': 'task'}
    assert page.evaluate('window.calls.filter(c=>c.directions).length') == 1
    assert page.evaluate('window.calls.findIndex(c=>c.method==="POST")<window.calls.findIndex(c=>c.directions)')
    assert page.evaluate('window.calls.find(c=>c.directions).credentials') == 'omit'
    # Polling the map never recalculates a billable route.
    page.evaluate('window.controller.update({drivers:[window.driver]})')
    assert page.evaluate('window.calls.filter(c=>c.directions).length') == 1
    destination.select_option('branch:branch')
    assert page.evaluate('!!window.sources["dispatch-route"]') is False
    calculate.click()
    page.wait_for_function('window.calls.filter(c=>c.directions).length===2')
    assert 'Estimated direct return to branch' in page.locator('.dispatch-map-route [role=status]').inner_text()
    assert 'Excludes pending deliveries' in page.locator('.dispatch-map-route [role=status]').inner_text()
    # A denied permit must never reach Mapbox or retry automatically.
    page.evaluate('window.deny=true')
    calculate.click()
    page.wait_for_function('document.querySelector(".dispatch-map-route [role=status]").textContent.includes("Wait one minute")')
    assert page.evaluate('window.calls.filter(c=>c.directions).length') == 2
    assert page.evaluate('!!window.sources["dispatch-route"]') is False
    # Aging GPS removes route/selection and prevents a new request.
    page.evaluate('window.driver.gps.updated_at=new Date(Date.now()-180000).toISOString();window.controller.update({drivers:[window.driver]})')
    assert calculate.is_disabled()
    assert driver.input_value() == ''
    assert 'stale or unavailable' in page.locator('.dispatch-map-route [role=status]').inner_text()
    assert page.evaluate('window.calls.filter(c=>c.method==="POST").length') == 3
    page.evaluate('window.controller.destroy()')
