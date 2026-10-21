"""Explicit morning context uses actual operations UI without permanent grants."""
import json
import os
from urllib.parse import urlparse
import pytest
from test_delivery_workspace_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')


@pytest.mark.parametrize('lang,width',[('ar',390),('en',390),('ar',1366),('en',1366)])
def test_scoped_manager_navigation_and_assignment(static_customer,lang,width):
    from playwright.sync_api import sync_playwright, expect
    from app.morning_manager_context import CAPABILITIES
    user={'id':'manager','brand_id':'kps','role':'delivery_manager','display_name':'محمد تجريبي',
          'branch_ids':['branch2'],'effective_permissions':sorted(CAPABILITIES),
          'delivery_context':{'period':'morning','shift_id':'morning-duty','branch_ids':['branch2']}}
    manager={'id':'morning-duty','shift_no':'M-TEST','kind':'manager','user_id':'manager','status':'open',
             'brand_id':'kps','branch_ids':['branch2'],'opened_at':'2026-10-21T08:00:00+03:00',
             'delivery_window_snapshot':{'period':'morning'}}
    driver={'id':'driver-shift','shift_no':'D-TEST','kind':'driver','user_id':'driver','manager_shift_id':'morning-duty',
            'status':'open','brand_id':'kps','branch_ids':['branch2'],'display_name':'عمرو تجريبي',
            'opened_at':'2026-10-21T08:00:00+03:00','delivery_window_snapshot':{'period':'morning'}}
    order={'id':'morning-order','order_no':'TEST-1','brand_id':'kps','branch_id':'branch2','service':'delivery',
           'preparation_status':'ready','delivery_status':'pending','payment_status':'unpaid','version':2,
           'total_halalas':1000,'created_at':'2026-10-21T08:00:00+03:00','customer':{'name':'عميل تجريبي'},
           'snapshot':{'branch_name':'الخالدية','branch_no':2,'delivery_period':'morning',
                       'delivery_workflow':'branch_fulfillment','items':[]}}
    writes=[];headers=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':width,'height':1000})
        context.add_init_script(f"localStorage.setItem('kps_ops_lang',{json.dumps(lang)})")
        page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def api(route):
            request=route.request;path=urlparse(request.url).path;payload={}
            headers.append(request.headers.get('x-delivery-period'))
            if path=='/api/session':payload={'user':user,'csrf_token':'synthetic'}
            elif path=='/api/catalog':payload={'brand':{'id':'kps','name':'KPS COFFEE','theme':{}}}
            elif path=='/api/branches':payload={'branches':[{'id':'branch2','brand_id':'kps','branch_no':2,'name':'الخالدية','city':'حفر الباطن'}]}
            elif path=='/api/shifts/current':payload={'shifts':[manager,driver],'available_manager_shifts':[manager]}
            elif path=='/api/shifts':payload={'shifts':[driver] if 'order_id=' in request.url else [manager,driver]}
            elif path=='/api/shifts/morning-duty':payload={'shift':manager,'tasks':[],'driver_shifts':[driver],
                     'collections':[],'refunds':[],'custody_events':[],'transfers':[],'expected_halalas':0}
            elif path=='/api/operations/people':payload={'users':[{'id':'driver','role':'driver','branch_ids':['branch2'],'display_name':'عمرو تجريبي'}],
                     'available_manager_shifts':[manager]}
            elif path=='/api/orders':payload={'orders':[order],'next_offset':None}
            elif path=='/api/orders/morning-order':payload={'order':order,'events':[]}
            elif path=='/api/orders/morning-order/assign':
                writes.append((path,request.post_data_json));order['delivery_status']='assigned'
                payload={'task':{'id':'assigned-task'}}
            elif request.method!='GET':raise AssertionError('Unexpected mutation '+path)
            route.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
        page.route('**/api/**',api)
        page.goto(static_customer+'/ops.html?delivery_period=morning')
        expect(page.locator('#navigation [data-page=orders]')).to_have_count(1)
        assert set(page.locator('#navigation [data-page]').evaluate_all('(nodes)=>nodes.map(node=>node.dataset.page)'))=={'orders','tasks','shifts'}
        expect(page.locator('#session-label')).to_contain_text('صباحي حفر الباطن' if lang=='ar' else 'Hafar morning')
        page.locator('[data-action=order-detail][data-id=morning-order]').click()
        page.locator('#modal [data-action=assign]').click()
        page.locator('#modal select[name=driver_shift_id]').select_option('driver-shift')
        page.locator('#modal button[type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible()
        assert writes==[('/api/orders/morning-order/assign',{'driver_shift_id':'driver-shift','accept_order':False})]
        page.locator('#navigation [data-page=shifts]').click()
        expect(page.locator('[data-action=shift-detail][data-id=morning-duty]')).to_be_visible()
        assert not page.locator('#navigation [data-page=admin],#navigation [data-page=custody],#navigation [data-page=reports]').count()
        assert headers and all(header=='morning' for header in headers)
        assert not errors
        browser.close()
