"""Full operations/map/tracking assets with synthetic APIs and SDK; no billing calls."""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]


def run_acceptance(root=ROOT):
    source=(root/'public/ops.js').read_text()
    source,count=re.subn(r'  init\(\);\s*\}\)\(\);\s*$',
        '  window.qa={state,loadPage,pollDispatchWorkspace};\n})();',source)
    assert count==1
    html=re.sub(r'<script[\s\S]*?</script>|<link[^>]*>','',(root/'public/ops.html').read_text())
    results=[]
    with sync_playwright() as p:
        opts={'headless':True,'args':['--no-sandbox','--disable-gpu']}
        if os.getenv('TEST_CHROMIUM_EXECUTABLE'):opts['executable_path']=os.environ['TEST_CHROMIUM_EXECUTABLE']
        browser=p.chromium.launch(**opts)
        for width,lang in [(390,'ar'),(1440,'en')]:
            context=browser.new_context(viewport={'width':width,'height':900})
            page=context.new_page();errors=[];requests=[];external=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            user={'id':'manager','brand_id':'kps','role':'delivery_manager','branch_ids':['branch'],
                'effective_permissions':['dashboard_view','orders_view','drivers_view','shifts_view','chat_view','delivery_assign']}
            def route_request(route):
                from urllib.parse import urlsplit
                url=urlsplit(route.request.url);path=url.path
                if url.netloc!='qa.invalid':external.append(route.request.url);return route.abort()
                if not path.startswith('/api/'):return route.fulfill(content_type='text/html',body=html)
                requests.append(path)
                payload={}
                if path=='/api/session':payload={'user':user,'csrf_token':'synthetic'}
                elif path=='/api/branches':payload={'branches':[{'id':'branch','brand_id':'kps','name':'Synthetic','city':'Synthetic','branch_no':1}]}
                elif path=='/api/orders':payload={'orders':[],'next_offset':None}
                elif path=='/api/shifts/current':payload={'shifts':[]}
                elif path in ['/api/operations/drivers','/api/operations/driver-locations']:payload={'drivers':[]}
                elif path=='/api/conversations':payload={'conversations':[]}
                elif path=='/api/operations/map-config':payload={'enabled':True,'provider':'mapbox','public_token':'pk.synthetic-only','style':'mapbox://styles/mapbox/streets-v12'}
                route.fulfill(json=payload)
            page.route('**/*',route_request)
            page.goto('https://qa.invalid/ops')
            for name in ['ops.css','assets/dispatch-layout.css','assets/dispatch-map.css']:
                page.add_style_tag(content=(root/'public'/name).read_text())
            page.evaluate('''()=>{
                window.setInterval=()=>0;window.mapConstructed=0;window.mapRemoved=0;
                window.KPSDriverLocation={init:()=>({refresh:async()=>{},reset(){}})};
                window.KPSDriverChecks={init:()=>({refresh:async()=>{},reset(){}})};
                class MapView{constructor(){mapConstructed++;this.events={};window.mapView=this;}
                    on(name,cb){this.events[name]=cb;}addControl(){}resize(){}remove(){mapRemoved++;}fitBounds(){}}
                class Marker{setLngLat(){return this;}setPopup(){return this;}addTo(){return this;}remove(){}}
                class Popup{setDOMContent(){return this;}}
                window.mapboxgl={Map:MapView,Marker,Popup,NavigationControl:class{},LngLatBounds:class{extend(){return this;}}};
            }''')
            for name in ['assets/dispatch-map.js','assets/order-tracking.js','assets/dispatch-layout.js']:
                page.add_script_tag(content=(root/'public'/name).read_text())
            page.add_script_tag(content=source)
            page.evaluate('''({user,lang})=>{qa.state.user=user;qa.state.csrf='synthetic';qa.state.lang=lang;
                qa.state.page='dashboard';document.querySelector('#app-shell').hidden=false;}''',{'user':user,'lang':lang})
            assert page.evaluate('()=>qa.loadPage()') is True
            expect(page.locator('.dispatch-workspace')).to_be_visible()
            assert page.locator('#dispatch-map .dispatch-map-canvas').count()==0
            expect(page.locator('#dispatch-map.dispatch-map-launch')).to_be_visible()
            assert requests.count('/api/operations/map-config')==0
            assert page.evaluate('mapConstructed')==0
            expect(page.locator('#dispatch-map [data-page=tracking]')).to_be_visible()
            page.locator('#dispatch-filters input[name=search]').fill('preserved-filter')
            assert page.locator('#dispatch-panel-drivers').is_visible()
            awaitable=page.evaluate('()=>qa.pollDispatchWorkspace()')
            assert page.locator('#dispatch-filters input[name=search]').input_value()=='preserved-filter'
            page.locator('#dispatch-map [data-page=tracking]').click()
            expect(page.locator('.tracking-screen')).to_be_visible()
            page.wait_for_function('mapConstructed===1')
            assert requests.count('/api/operations/map-config')==1
            page.evaluate("mapView.events.load()")
            expect(page.locator('.dispatch-map-canvas')).to_be_visible()
            page.locator('.tracking-toolbar [data-page=dashboard]').click()
            expect(page.locator('.dispatch-workspace')).to_be_visible()
            page.wait_for_function('mapRemoved===1')
            assert page.locator('#dispatch-filters input[name=search]').input_value()=='preserved-filter'
            assert requests.count('/api/operations/map-config')==1
            assert page.evaluate('mapConstructed')==1
            assert page.locator('#dispatch-map .dispatch-map-canvas').count()==0
            expect(page.locator('#dispatch-map.dispatch-map-launch')).to_be_visible()
            assert not external,external
            assert not errors,errors
            results.append({'width':width,'lang':lang,'dashboard_map_initializations':0,'tracking_map_initializations':1,'map_cleanup':1,'result':'PASS'})
            context.close()
        browser.close()
    return results

if __name__=='__main__':
    import json,sys
    print(json.dumps(run_acceptance(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT),indent=2))
