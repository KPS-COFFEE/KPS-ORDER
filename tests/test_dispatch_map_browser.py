"""Operational map contract with a synthetic SDK; no provider or billing calls."""
import os
import pytest
from test_experience_entry_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')


@pytest.mark.parametrize('lang',['ar','en'])
def test_actual_pins_update_without_invented_driver_gps_or_map_reload(static_customer,lang):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':600,'height':850})
        external=[]
        page.route('https://api.mapbox.com/**',lambda route:(external.append(route.request.url),route.abort()))
        page.route('**/__dispatch-map-test',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body='<html><head><meta charset="utf-8"></head><body><div id="map"></div></body></html>'))
        page.goto(static_customer+'/__dispatch-map-test')
        page.add_style_tag(url=static_customer+'/assets/dispatch-map.css')
        page.add_script_tag(url=static_customer+'/assets/dispatch-map.js')
        page.evaluate("""async lang=>{
          window.constructed=0;window.fitCount=0;window.selected=[];window.allMarkers=[];window.mapRemovals=0;window.positionWrites=0;
          class FakeMap{constructor(){constructed++;window.operationalMap=this;this.events={};}on(n,cb){this.events[n]=cb;}addControl(){}fitBounds(){fitCount++;}resize(){}remove(){mapRemovals++;}}
          class Marker{constructor({element}){this.element=element;allMarkers.push(this);}setLngLat(p){positionWrites++;this.point=p;return this;}setPopup(p){this.popup=p;return this;}addTo(){return this;}remove(){this.removed=true;}}
          class Popup{setDOMContent(el){this.element=el;return this;}}
          window.mapboxgl={Map:FakeMap,Marker,Popup,NavigationControl:class{},LngLatBounds:class{extend(){return this;}}};
          const rows={orders:[{id:'o1',order_no:'KPS-1',customer_location:{lat:28.4,lng:45.9}},{id:'invalid',customer_location:{lat:999,lng:40}}],branches:[{id:'b1',name:'Branch',branch_no:1,location:{lat:28.5,lng:45.8}}],drivers:[{id:'driver',location:{lat:28.6,lng:45.7}}],lang};
          window.initialRows=rows;
          window.controller=await KPSDispatchMap.mount(document.querySelector('#map'),{...rows,brandId:'kps',api:async()=>({enabled:true,provider:'mapbox',public_token:'pk.synthetic-only',style:'mapbox://styles/mapbox/streets-v12'}),onSelectOrder:id=>selected.push(id)});
          operationalMap.events.load();
        }""",lang)
        expect(page.locator('.dispatch-map-canvas')).to_be_visible()
        assert page.evaluate('allMarkers.length')==2
        assert page.evaluate('fitCount')==1
        # A repeated API response must preserve popup nodes/focus and avoid
        # rescheduling every marker position on the phone's main thread.
        assert page.evaluate("""()=>{const nodes=allMarkers.map(m=>m.popup.element.firstChild),writes=positionWrites;
          for(let i=0;i<20;i++)controller.update(structuredClone(initialRows));
          return positionWrites===writes&&allMarkers.every((m,i)=>m.popup.element.firstChild===nodes[i]);}""")
        # Hidden layout panels retain the latest data without doing map work.
        page.evaluate("""()=>{document.querySelector('#map').style.display='none';
          const rows=structuredClone(initialRows);rows.orders[0].customer_location.lng=46;
          controller.update(rows);}""")
        assert page.evaluate('allMarkers[1].point[0]')==45.9
        page.evaluate("document.querySelector('#map').style.display='';controller.resize()")
        assert page.evaluate('allMarkers[1].point[0]')==46
        page.evaluate("allMarkers.find(m=>m.element.getAttribute('aria-label').includes('KPS-1')).popup.element.querySelector('button').click()")
        assert page.evaluate('selected')==['o1']
        page.evaluate("controller.update({orders:[],drivers:[]})")
        assert page.evaluate('allMarkers.filter(m=>m.removed).length')==1
        assert page.evaluate('constructed')==1
        assert page.evaluate('fitCount')==1
        page.evaluate("document.querySelector('#map').remove()")
        page.wait_for_function('mapRemovals===1')
        page.evaluate('controller.destroy();controller.update(initialRows)')
        assert page.evaluate('mapRemovals')==1
        assert page.evaluate('allMarkers.every(m=>m.removed)')
        assert not external
        browser.close()


def test_missing_token_is_explicit_and_does_not_request_sdk(static_customer):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page()
        external=[]
        page.route('https://api.mapbox.com/**',lambda route:(external.append(route.request.url),route.abort()))
        page.route('**/__dispatch-map-test',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body='<html><head><meta charset="utf-8"></head><body><div id="map"></div></body></html>'))
        page.goto(static_customer+'/__dispatch-map-test')
        page.add_style_tag(url=static_customer+'/assets/dispatch-map.css')
        page.add_script_tag(url=static_customer+'/assets/dispatch-map.js')
        page.evaluate("KPSDispatchMap.mount(document.querySelector('#map'),{api:async()=>({enabled:false}),brandId:'kps',lang:'ar',orders:[],drivers:[],branches:[]})")
        expect(page.locator('.dispatch-map-state')).to_contain_text('الخريطة تحتاج تفعيل Mapbox')
        expect(page.locator('.dispatch-map-canvas')).not_to_be_visible()
        assert not external
        browser.close()
