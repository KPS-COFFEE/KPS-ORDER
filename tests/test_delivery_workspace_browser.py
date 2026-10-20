"""Real operations assets; synthetic API responses, no real orders or messages."""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse
import pytest
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

@pytest.fixture(scope='module')
def static_customer():
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(Path(__file__).resolve().parents[1]/'public')))
    worker=Thread(target=server.serve_forever,daemon=True);worker.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown();server.server_close();worker.join(timeout=5)

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')


@pytest.mark.parametrize('lang,width',[('ar',1366),('en',1366),('ar',390)])
def test_workspace_actions_scope_chat_and_morning_guard(static_customer,lang,width):
    from playwright.sync_api import sync_playwright, expect
    user={'id':'manager','brand_id':'kps','role':'delivery_manager','display_name':'مدير التوصيل',
          'branch_ids':['branch'],'effective_permissions':['dashboard_view','orders_view','delivery_assign',
          'shifts_view','drivers_view','drivers_availability','chat_view','chat_reply','chat_manage','shifts_open']}
    shift={'id':'manager-shift','user_id':'manager','kind':'manager','status':'open','branch_ids':['branch'],
           'opened_at':'2026-10-20T05:00:00+03:00','delivery_window_snapshot':{'period':'morning'}}
    def order(oid,period,prep,delivery='pending'):
        return {'id':oid,'order_no':oid,'branch_id':'branch','service':'delivery','version':1,
                'preparation_status':prep,'delivery_status':delivery,'payment_status':'unpaid',
                'created_at':'2026-10-20T08:00:00+03:00','total_halalas':2500,
                'customer':{'name':'عميل تجريبي','neighborhood':'المروج'},
                'stage_timing':{'color':'red' if oid=='late' else 'normal','total_seconds':100,'reasons':[]},
                'snapshot':{'order_reference':oid,'branch_name':'المروج','delivery_period':period,
                    'delivery_workflow':'branch_fulfillment' if period=='morning' else 'manager_dispatch','items':[]}}
    orders=[order('morning','morning','new'),order('new-evening','evening','new'),order('late','evening','accepted')]
    conversation={'id':'chat1','order_reference':'late','customer_name':'عميل تجريبي','route':'delivery','status':'open','unread_count':1}
    writes=[];order_queries=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':width,'height':1000})
        context.add_init_script(f"localStorage.setItem('kps_ops_lang',{json.dumps(lang)})")
        page=context.new_page();errors=[];external_requests=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def isolated_assets(route):
            if urlparse(route.request.url).netloc!=urlparse(static_customer).netloc:
                external_requests.append(route.request.url)
                route.abort()
            else:
                route.continue_()
        page.route('**/*',isolated_assets)
        def route_api(route):
            req=route.request;path=urlparse(req.url).path
            if req.method!='GET': writes.append((path,req.post_data_json))
            payload={}
            if path=='/api/session':payload={'user':user,'csrf_token':'synthetic-token'}
            elif path=='/api/catalog':payload={'brand':{'id':'kps','name':'KPS COFFEE','theme':{}}}
            elif path=='/api/branches':payload={'branches':[{'id':'branch','name':'المروج','branch_no':1,'city':'حفر الباطن','brand_id':'kps'}]}
            elif path=='/api/operations/map-config':payload={'enabled':False,'provider':'mapbox'}
            elif path=='/api/orders/new-evening':payload={'order':orders[1]}
            elif path=='/api/shifts':payload={'shifts':[{'id':'driver-shift','kind':'driver','status':'open','display_name':'سائق تجريبي'}]}
            elif path=='/api/orders/new-evening/assign':
                orders[1]['delivery_status']='assigned';payload={'task':{'id':'task1'}}
            elif path=='/api/orders':
                order_queries.append(req.url)
                payload={'orders':orders,'next_offset':None,'workspace_counts':{'all':{'total':103,'new':102,'preparing':1},'current':{'total':3,'new':2,'preparing':1}}}
            elif path=='/api/shifts/current':payload={'shifts':[shift]}
            elif path=='/api/operations/drivers':payload={'drivers':[{'id':'driver','display_name':'سائق تجريبي','branch_ids':['branch'],'availability':'available','readiness_action':'ready','active_tasks':0,'shift_id':'driver-shift','shift_status':'open','shift_available':True}]}
            elif path=='/api/conversations':payload={'conversations':[conversation]}
            elif path=='/api/conversations/chat1/messages':payload={'conversation':conversation,'messages':[{'id':'m1','seq':1,'sender_id':'customer','text':'وين طلبي؟','created_at':'2026-10-20T08:02:00+03:00'}]}
            elif path=='/api/orders/new-evening/manager-accept':
                orders[1]['preparation_status']='accepted';payload={'order':orders[1]}
            route.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
        page.route('**/api/**',route_api)
        page.goto(static_customer+'/ops.html')
        expect(page.locator('#dispatch-orders')).to_be_visible()
        expect(page.locator('#dispatch-orders article')).to_have_count(3)
        expect(page.locator('[data-count-scope=all] [data-stage=""] b')).to_have_text('١٠٣' if lang=='ar' else '103')
        expect(page.locator('[data-count-scope=current] [data-stage=""]')).to_have_attribute('aria-pressed','true')
        expect(page.locator('[data-count-scope=current] [data-stage=""] b')).to_have_text('٣' if lang=='ar' else '3')
        assert 'dispatch_workspace=true' in order_queries[0] and 'dispatch_scope=current' in order_queries[0]
        page.locator('[data-count-scope=all] [data-stage=""]').click()
        expect(page.locator('[data-count-scope=all] [data-stage=""]')).to_have_attribute('aria-pressed','true')
        assert 'dispatch_scope=all' in order_queries[-1]
        page.locator('[data-count-scope=current] [data-stage=""]').click()
        expect(page.locator('[data-count-scope=current] [data-stage=""]')).to_have_attribute('aria-pressed','true')
        expect(page.locator('#dispatch-map')).to_be_visible()
        expect(page.locator('#dispatch-map .dispatch-map-state')).to_contain_text(
            'الخريطة تحتاج تفعيل Mapbox' if lang=='ar' else 'Mapbox activation required')
        expect(page.locator('#dispatch-panel-drivers')).to_be_visible()
        expect(page.locator('#dispatch-panel-messages')).to_be_visible()
        assert page.locator('[data-action=dispatch-panel]').count()==0
        # Main operating area must be reachable immediately, not pushed below a
        # long vertical menu and stacked status cards on desktop.
        expect(page.locator('body')).to_have_class(re.compile(r'\bdelivery-room\b'))
        # The temporary admin request panel must never push the delivery map down.
        page.locator('#driver-checks').evaluate("host=>{host.innerHTML='<section class=panel><button>Temporary fuel checks</button><p>Temporary owner tool</p></section>';}")
        expect(page.locator('#driver-checks')).not_to_be_visible()
        page.locator('body').evaluate("body=>body.classList.remove('delivery-room')")
        expect(page.locator('#driver-checks')).to_be_visible()
        page.locator('body').evaluate("body=>body.classList.add('delivery-room')")
        if width >= 1000:
            geometry=page.evaluate("""()=>{
              const box=s=>document.querySelector(s).getBoundingClientRect();
              const nav=box('#navigation'),row=box('#dispatch-orders article'),side=box('.dispatch-side');
              const orders=box('.dispatch-orders'),map=box('.dispatch-map-panel');
              const driver=box('#dispatch-drivers article'),chat=box('#dispatch-panel-messages .conversation-list');
              return {navTop:nav.top,navBottom:nav.bottom,navHeight:nav.height,rowTop:row.top,
                      sideTop:side.top,sideBottom:side.bottom,height:innerHeight,
                      ordersLeft:orders.left,ordersTop:orders.top,mapLeft:map.left,mapRight:map.right,
                      mapTop:map.top,sideRight:side.right,driverTop:driver.top,driverBottom:driver.bottom,
                      chatTop:chat.top,chatBottom:chat.bottom};
            }""")
            assert geometry['rowTop'] < min(650,geometry['height']*.72),geometry
            assert 0 <= geometry['navTop'] < geometry['navBottom'] <= geometry['rowTop'],geometry
            assert geometry['navHeight'] <= 110,geometry
            assert geometry['sideTop'] <= geometry['rowTop'] < geometry['sideBottom'],geometry
            # Orders on the right, map in the center, drivers and inbox on the
            # left are present together in both locales, without panel tabs.
            assert geometry['sideRight'] <= geometry['mapLeft'] < geometry['mapRight'] <= geometry['ordersLeft'],geometry
            assert max(geometry['sideTop'],geometry['mapTop'],geometry['ordersTop'])-min(geometry['sideTop'],geometry['mapTop'],geometry['ordersTop'])<=2,geometry
            assert 0 <= geometry['driverTop'] < geometry['driverBottom'] <= geometry['height'],geometry
            assert 0 <= geometry['chatTop'] < geometry['chatBottom'] <= geometry['height'],geometry
            expect(page.locator('.dispatch-table-head')).to_be_visible()
        else:
            toggle=page.locator('.dispatch-nav-toggle')
            expect(toggle).to_be_visible()
            expect(toggle).to_have_attribute('aria-controls','navigation')
            expect(page.locator('#navigation')).not_to_be_visible()
            toggle.click()
            expect(toggle).to_have_attribute('aria-expanded','true')
            expect(page.locator('#navigation')).to_be_visible()
            expect(page.locator('#navigation [data-page=dashboard]')).to_be_visible()
            toggle.click()
            expect(toggle).to_have_attribute('aria-expanded','false')
            expect(page.locator('#navigation')).not_to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        Path('test-results').mkdir(exist_ok=True)
        page.screenshot(path=f'test-results/customer-entry-control-room-{lang}-{width}.png',full_page=True)
        assert page.locator('[data-action="manager-accept"][data-id="morning"]').count()==0
        assert page.locator('[data-action="assign"][data-id="morning"]').count()==0
        page.locator('[data-action="manager-accept"][data-id="new-evening"]').click()
        expect(page.locator('[data-action="assign"][data-id="new-evening"]')).to_be_visible()
        page.locator('[data-action="assign"][data-id="new-evening"]').click()
        page.locator('#modal select[name=driver_shift_id]').select_option('driver-shift')
        page.locator('#modal button[type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible()
        assignment=[body for path,body in writes if path=='/api/orders/new-evening/assign']
        assert assignment==[{'driver_shift_id':'driver-shift','accept_order':False}]

        assert any(path=='/api/orders/new-evening/manager-accept' and body['version']==1 for path,body in writes)
        page.locator('#dispatch-filters [name=late]').check()
        expect(page.locator('#dispatch-orders article')).to_have_count(1)
        page.locator('#dispatch-filters [name=late]').uncheck()
        page.locator('.dispatch-side [data-action=conversation]').click()
        expect(page.locator('#message-form')).to_be_visible()
        page.locator('#message-form textarea').fill('طلبك جارٍ تجهيزه')
        # The 15-second operational poll must keep receiving new orders while
        # a manager composes a reply, without reconstructing the chat form.
        page.evaluate("window.originalComposer=document.querySelector('#message-form textarea'); originalComposer.setSelectionRange(3,3)")
        orders.append(order('arrived-during-reply','evening','new'))
        conversation['last_message_text']='رسالة جديدة أثناء كتابة الرد'
        expect(page.locator('#dispatch-orders article')).to_have_count(4,timeout=25000)
        expect(page.locator('#message-form textarea')).to_have_value('طلبك جارٍ تجهيزه')
        assert page.evaluate("document.querySelector('#message-form textarea')===originalComposer && document.activeElement===originalComposer && originalComposer.selectionStart===3")
        expect(page.locator('.dispatch-side .conversation-preview')).to_have_text('رسالة جديدة أثناء كتابة الرد')
        assert '/ops.html' in page.url
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert not any('/messages' in path for path,_ in writes)
        assert not external_requests,external_requests
        assert not errors,errors
        Path('test-results').mkdir(exist_ok=True)
        page.screenshot(path=f'test-results/customer-entry-delivery-dashboard-{lang}-{width}.png',full_page=True)
        browser.close()
