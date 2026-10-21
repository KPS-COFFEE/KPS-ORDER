"""Actual operations asset, isolated synthetic API, both languages and sizes."""
import json
import os
from urllib.parse import urlparse
import pytest
from test_delivery_workspace_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')


@pytest.mark.parametrize('lang,width',[('ar',390),('en',390),('ar',1366),('en',1366)])
def test_preparer_waits_for_driver_acceptance_before_preparing_then_ready(static_customer,lang,width):
    from playwright.sync_api import sync_playwright, expect
    user={'id':'preparer','brand_id':'kps','role':'branch_manager','display_name':'محضّر تجريبي',
          'branch_ids':['branch2'],'effective_permissions':['orders_view','orders_prepare']}
    order={'id':'morning-order','order_no':'TEST-1','brand_id':'kps','branch_id':'branch2',
           'service':'delivery','preparation_status':'new','delivery_status':'pending',
           'payment_status':'unpaid','payment_method':'cash','version':1,'total_halalas':1000,
           'created_at':'2026-10-21T08:00:00+03:00','customer':{'name':'عميل تجريبي','neighborhood':'حي تجريبي'},
           # Customer selected branch 9; actual morning execution is branch 2.
           'snapshot':{'branch_no':9,'branch_name':'فرع اختاره العميل','items':[],
                       'delivery_period':'morning','delivery_workflow':'branch_fulfillment'}}
    writes=[];print_jobs=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':width,'height':1000})
        context.add_init_script(f"localStorage.setItem('kps_ops_lang',{json.dumps(lang)})")
        page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def api(route):
            request=route.request;path=urlparse(request.url).path;payload={}
            if path=='/api/session':payload={'user':user,'csrf_token':'synthetic'}
            elif path=='/api/catalog':payload={'brand':{'id':'kps','name':'KPS COFFEE','theme':{}}}
            elif path=='/api/branches':payload={'branches':[{'id':'branch2','brand_id':'kps','branch_no':2,'name':'الخالدية','city':'حفر الباطن'}]}
            elif path=='/api/orders':payload={'orders':[order],'next_offset':None}
            elif path=='/api/orders/morning-order':payload={'order':order,'events':[]}
            elif path=='/api/orders/morning-order/preparation':
                body=request.post_data_json;writes.append((path,body))
                assert body['version']==order['version']
                if body['status']=='accepted':
                    assert order['preparation_status']=='new'
                    print_jobs.append('queued')
                    order.update(delivery_status='assigned',morning_driver_accepted=False,morning_task_status='assigned',morning_driver_name='عمرو')
                elif body['status']=='preparing':assert order['preparation_status']=='accepted' and order['morning_driver_accepted']
                elif body['status']=='ready':assert order['preparation_status']=='preparing' and order['morning_driver_accepted']
                order['preparation_status']=body['status'];order['version']+=1
                payload={'order':order}
                if body['status']=='accepted':payload['printing']={'id':'queued','status':'queued'}
            elif request.method!='GET':
                raise AssertionError('Unexpected mutation: '+path)
            route.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
        page.route('**/api/**',api)
        page.goto(static_customer+'/ops.html')
        details=page.locator('[data-action=order-detail][data-id=morning-order]')
        details.click()
        accept=page.locator('[data-action=prepare][data-status=accepted]')
        expect(accept).to_have_text('قبول الطلب' if lang=='ar' else 'Accept order')
        assert page.locator('[data-action=remote-print]:visible').count()==0
        accept.click()
        details.click()
        assert page.locator('[data-action=prepare]:visible').count()==0
        expect(page.locator('#modal')).to_contain_text('بانتظار قبول السائق' if lang=='ar' else 'Waiting for the current driver')
        # The real driver's server event, never the cashier, supplies acceptance.
        order.update(morning_driver_accepted=True,morning_task_status='accepted')
        page.locator('#modal [data-action=morning-refresh-order]').click()
        prepare=page.locator('[data-action=prepare][data-status=preparing]')
        expect(prepare).to_have_text('تحضير الطلب' if lang=='ar' else 'Prepare order')
        assert page.locator('[data-action=prepare][data-status=ready]:visible').count()==0
        prepare.click();details.click()
        ready=page.locator('[data-action=prepare][data-status=ready]')
        expect(ready).to_have_text('تم التجهيز' if lang=='ar' else 'Mark ready')
        assert page.locator('[data-action=prepare][data-status=preparing]:visible').count()==0
        ready.click()
        details.click()
        assert page.locator('[data-action=prepare]:visible').count()==0
        assert [body['status'] for _,body in writes]==['accepted','preparing','ready']
        assert print_jobs==['queued']
        assert not errors
        browser.close()
