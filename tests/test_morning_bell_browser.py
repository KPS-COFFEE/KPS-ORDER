"""Real operations asset, minimal permissions, scoped mandatory bell lifecycle."""
import json
import os
from urllib.parse import urlparse, parse_qs
import pytest
from test_delivery_workspace_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')


@pytest.mark.parametrize('role',['preparer','manager'])
@pytest.mark.parametrize('lang,width',[('ar',390),('en',1366)])
def test_morning_minimal_account_mandatory_bell_repeats_until_real_action(static_customer,role,lang,width):
    from playwright.sync_api import sync_playwright, expect
    from app.morning_manager_context import CAPABILITIES
    manager=role=='manager'
    user={'id':role,'brand_id':'kps','role':'delivery_manager' if manager else 'branch_manager',
          'display_name':'موظف تجريبي','branch_ids':['branch2','other-city'],
          'effective_permissions':sorted(CAPABILITIES) if manager else ['orders_view','orders_prepare']}
    if manager:user['delivery_context']={'period':'morning','shift_id':'duty','branch_ids':['branch2']}
    order={'id':'morning','order_no':'MORNING-TEST','brand_id':'kps','branch_id':'branch2','service':'delivery',
           'preparation_status':'new','delivery_status':'pending','version':1,'customer':{},
           'morning_driver_accepted':False,'snapshot':{'delivery_period':'morning','branch_no':9,'items':[]}}
    other=[dict(order,id='evening',order_no='EVENING-IGNORE',snapshot={'delivery_period':'evening'}),
           dict(order,id='other-branch',order_no='BRANCH-IGNORE',branch_id='branch9'),
           dict(order,id='trial',order_no='TRIAL-IGNORE',simulated=True),
           dict(order,id='wrong-city',order_no='CITY-IGNORE',branch_id='other-city')]
    writes=[];requests=[];offline=False;policy_reads=[]
    audio="""window.testGesture=false;document.addEventListener('pointerdown',e=>{if(e.isTrusted)window.testGesture=true},true);
window.testBeeps=0;window.testStopped=0;window.AudioContext=class{constructor(){this.state='suspended';this.currentTime=0;this.destination={}}async resume(){if(!window.testGesture)throw Error('Browser gesture required');this.state='running'}createOscillator(){return {connect(){},disconnect(){},frequency:{},start(){window.testBeeps++},stop(at){if(at===undefined){window.testStopped++;this.onended?.()}else queueMicrotask(()=>this.onended?.())}}}createGain(){return {connect(){},disconnect(){},gain:{setValueAtTime(){},exponentialRampToValueAtTime(){}}}}};"""
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':width,'height':1000})
        context.add_init_script(f"localStorage.setItem('kps_ops_lang',{json.dumps(lang)});"+audio)
        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        def api(route):
            request=route.request;path=urlparse(request.url).path;requests.append(path);payload={}
            if request.method!='GET':writes.append(path)
            if path=='/api/session':payload={'user':user,'csrf_token':'test'}
            elif path=='/api/catalog':payload={'brand':{'id':'kps','name':'KPS','theme':{}}}
            elif path=='/api/branches':payload={'branches':[{'id':'branch2','brand_id':'kps','branch_no':2,'name':'B2','city':'حفر الباطن'},
                                                          {'id':'other-city','brand_id':'kps','branch_no':2,'name':'Other','city':'الرياض'}]}
            elif path=='/api/shifts/current':payload={'shifts':[{'id':'duty','kind':'manager','user_id':role,'status':'open','branch_ids':['branch2'],'delivery_window_snapshot':{'period':'morning'}}]}
            elif path=='/api/orders':
                if offline:route.fulfill(status=503,json={'error':{'message':'offline'}});return
                query=parse_qs(urlparse(request.url).query)
                # Two pages prove that the bell is independent of UI/date filters.
                if query.get('limit')==['100'] and query.get('offset')==['0']:payload={'orders':other,'next_offset':100}
                else:payload={'orders':[order],'next_offset':None}
            elif path=='/api/orders/morning':payload={'order':order,'events':[]}
            elif path=='/api/tasks':payload={'tasks':[]}
            elif path=='/api/operations/alert-policy':
                policy_reads.append(path)
                if len(policy_reads)>1:route.fulfill(status=503,json={'error':{'message':'cadence temporarily offline'}});return
                payload={'mandatory':True,'policy':{'first_after_seconds':0,'second_after_seconds':20,
                         'continuous_after_seconds':40,'first_ring_seconds':2,'second_ring_seconds':3}}
            route.fulfill(status=200,json=payload)
        page.route('**/api/**',api);page.clock.install()
        page.goto(static_customer+'/ops.html'+('?delivery_period=morning' if manager else ''))
        alerts=page.locator('#morning-order-alerts')
        expect(alerts).to_contain_text('MORNING-TEST')
        for excluded in ['EVENING-IGNORE','BRANCH-IGNORE','TRIAL-IGNORE','CITY-IGNORE']:expect(alerts).not_to_contain_text(excluded)
        assert page.evaluate('window.testBeeps')==0
        expect(page.locator('#alert-audio-blocked')).to_be_visible()
        page.locator('#page-heading').click()
        page.wait_for_function('window.testBeeps === 2')
        page.locator('#morning-order-alerts [data-action=order-review]').click()
        # Nondefault owner cadence must propagate even with zero normal manager
        # permissions; falling back silently to 30/60 would fail these checks.
        page.clock.run_for(21000);page.wait_for_function('window.testBeeps === 4')
        page.clock.run_for(21000);page.wait_for_function('window.testBeeps === 5')
        assert not page.locator('[data-action=alert-sound]:visible').count()
        assert not page.locator('#morning-order-alerts input').count()
        # A failed refresh/read/open is never an acknowledgment or mute.
        offline=True;page.clock.run_for(16000)
        assert page.evaluate('window.testStopped')==0
        # The 60s cache TTL starts when the asynchronous initial response
        # completes, not when the 15s interval starts. Its t=60 callback may
        # therefore still be cached; cross t=75 and await the actual failure
        # while the order remains actionable, rather than assuming t=74 did it.
        with page.expect_response(lambda response:
                urlparse(response.url).path=='/api/operations/alert-policy' and response.status==503):
            page.clock.run_for(31000)
        assert len(policy_reads)>=2
        assert page.evaluate('window.testStopped')==0
        assert page.evaluate('window.testBeeps')==5  # Continuous oscillator survived.
        offline=False;order.update(preparation_status='accepted',delivery_status='assigned')
        page.clock.run_for(16000)
        expect(alerts).not_to_contain_text('MORNING-TEST')
        assert page.evaluate('window.testStopped')==1
        # Only a real server-confirmed driver acceptance enables preparation.
        order['morning_driver_accepted']=True;page.clock.run_for(16000)
        expect(alerts).to_contain_text('MORNING-TEST')
        page.wait_for_function('window.testBeeps === 7')
        order['preparation_status']='ready';page.clock.run_for(16000)
        expect(alerts).not_to_contain_text('MORNING-TEST')
        assert not writes
        assert '/api/operations/alerts' not in requests
        assert not errors
        browser.close()
