"""Actual morning UI: self-open, manual intervention and guarded driver handover.

Synthetic API responses represent authoritative backend events; this browser
suite never fabricates a driver acceptance through a manager write.
"""
import json
import os
from urllib.parse import urlparse
import pytest
from test_delivery_workspace_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')


@pytest.mark.parametrize('lang,width',[('ar',390),('en',390),('ar',1366),('en',1366)])
def test_manager_self_open_assign_busy_driver_prepare_with_reason_and_request_handover(static_customer,lang,width):
    from playwright.sync_api import sync_playwright,expect
    from app.morning_manager_context import CAPABILITIES,BOOTSTRAP_CAPABILITIES
    opened=False;task=None;writes=[];reads=[];errors=[]
    order={'id':'order','order_no':'TEST-MORNING','brand_id':'kps','branch_id':'branch2','service':'delivery',
           'preparation_status':'accepted','delivery_status':'pending','payment_status':'unpaid','payment_method':'cash',
           'morning_driver_accepted':False,'morning_task_status':None,'morning_driver_name':None,
           'version':2,'total_halalas':1000,'customer':{'name':'عميل تجريبي'},
           'snapshot':{'delivery_period':'morning','delivery_workflow':'branch_fulfillment','branch_no':2,'items':[]}}
    manager={'id':'duty','kind':'manager','user_id':'manager','status':'open','branch_ids':['branch2'],'delivery_window_snapshot':{'period':'morning'}}
    busy={'id':'busy-shift','kind':'driver','user_id':'busy-driver','display_name':'عمرو','status':'open','manager_shift_id':'duty',
          'branch_ids':['branch2'],'active_tasks':1,'assignment_eligible':True}
    full={**busy,'id':'full-shift','user_id':'full-driver','display_name':'مكتمل الطاقة','active_tasks':3,'assignment_eligible':False}
    replacement={**busy,'id':'replacement-shift','user_id':'replacement','display_name':'سائق بديل','active_tasks':0}
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);context=browser.new_context(viewport={'width':width,'height':1000})
        context.add_init_script(f"localStorage.setItem('kps_ops_lang',{json.dumps(lang)})")
        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
        def api(route):
            nonlocal opened,task
            request=route.request;path=urlparse(request.url).path;payload={}
            assert path not in {'/api/identity/staff','/api/admin/export'}
            assert request.headers.get('x-delivery-period')=='morning'
            if request.method=='GET':reads.append(path)
            else:writes.append((path,request.post_data_json))
            if path=='/api/session':
                context_data={'period':'morning','shift_id':'duty' if opened else None,'branch_ids':['branch2']}
                if not opened:context_data['bootstrap']=True
                payload={'user':{'id':'manager','role':'delivery_manager','brand_id':'kps','display_name':'أحمد',
                         'branch_ids':['branch2'],'effective_permissions':sorted(CAPABILITIES if opened else BOOTSTRAP_CAPABILITIES),
                         'delivery_context':context_data},'csrf_token':'synthetic'}
            elif path=='/api/catalog':payload={'brand':{'id':'kps','name':'KPS COFFEE','theme':{}}}
            elif path=='/api/branches':payload={'branches':[{'id':'branch2','brand_id':'kps','branch_no':2,'name':'الخالدية','city':'حفر الباطن'}]}
            elif path=='/api/shifts/current':payload={'shifts':[manager] if opened else []}
            elif path=='/api/shifts' and request.method=='POST':
                assert not opened
                assert request.post_data_json=={'kind':'manager','delivery_period':'morning','branch_ids':['branch2'],'opening_halalas':0}
                opened=True;payload={'shift':manager}
            elif path=='/api/shifts':
                assert opened and ('order_id=order' in request.url or 'task_id=task' in request.url)
                payload={'shifts':[replacement] if 'task_id=' in request.url else [busy,full]}
            elif path=='/api/orders':assert opened;payload={'orders':[order],'next_offset':None}
            elif path=='/api/orders/order':assert opened;payload={'order':order,'events':[]}
            elif path=='/api/tasks':
                assert opened and 'order_id=order' in request.url
                payload={'tasks':[task] if task else []}
            elif path=='/api/orders/order/assign':
                assert request.post_data_json=={'driver_shift_id':'busy-shift','accept_order':False}
                task={'id':'task','order_id':'order','shift_id':'busy-shift','driver_id':'busy-driver','status':'assigned'}
                order.update(delivery_status='assigned',morning_task_status='assigned',morning_driver_name='عمرو',driver_name='عمرو')
                payload={'task':task}
            elif path=='/api/orders/order/preparation':
                body=request.post_data_json
                assert body['reason']=='تأكيد الفرع وسبب التدخل' and body['version']==order['version']
                assert order['morning_driver_accepted'] and task['status']=='accepted'
                assert (order['preparation_status'],body['status']) in [('accepted','preparing'),('preparing','ready')]
                order.update(preparation_status=body['status'],version=order['version']+1);payload={'order':order}
            elif path=='/api/tasks/task/handover':
                assert request.post_data_json=={'driver_shift_id':'replacement-shift','reason':'سبب نقل المهمة','reassign_before_pickup':True}
                task['transfer_shift_id']='replacement-shift';payload={'task_id':'task','acceptance_required':True}
            elif request.method!='GET':raise AssertionError('Unexpected mutation '+path)
            route.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
        page.route('**/api/**',api);page.goto(static_customer+'/ops.html?delivery_period=morning')
        expect(page.locator('[data-action=morning-open-duty]')).to_be_visible()
        assert '/api/orders' not in reads and writes==[]
        assert page.locator('[data-action=open-shift]:visible').count()==0
        page.locator('[data-action=morning-open-duty]').click()
        page.locator('[data-action=order-detail][data-id=order]').click()
        expect(page.locator('#morning-dispatch-status')).to_contain_text('لم يُسند بعد' if lang=='ar' else 'still unassigned')
        assert page.locator('#modal [data-action=prepare]').count()==0
        assert len(writes)==1  # Reading a pending order never triggers dispatch.
        page.locator('#modal [data-action=assign]').click()
        options=page.locator('#modal [name=driver_shift_id] option')
        expect(options).to_have_count(2)  # placeholder + busy but eligible driver.
        expect(options.last).to_contain_text('طلبات جارية' if lang=='ar' else 'Active tasks')
        page.locator('#modal [name=driver_shift_id]').select_option('busy-shift')
        page.locator('#modal button[type=submit]').click();expect(page.locator('#modal')).not_to_be_visible()
        page.locator('[data-action=order-detail][data-id=order]').click()
        expect(page.locator('#morning-dispatch-status')).to_contain_text('عمرو')
        assert page.locator('#modal [data-action=assign],#modal [data-action=prepare]').count()==0
        # Acceptance arrives from the actual driver's backend state, not manager UI.
        task['status']='accepted';order.update(morning_driver_accepted=True,morning_task_status='accepted')
        page.locator('#modal [data-action=morning-refresh-order]').click()
        for status in ['preparing','ready']:
            page.locator(f'#modal [data-action=prepare][data-status={status}]').click()
            reason=page.locator('#modal textarea[name=reason]');expect(reason).to_be_visible()
            before=len(writes);page.locator('#modal button[type=submit]').click();assert len(writes)==before
            reason.fill('تأكيد الفرع وسبب التدخل');page.locator('#modal button[type=submit]').click()
            expect(page.locator('#modal')).not_to_be_visible()
            page.locator('[data-action=order-detail][data-id=order]').click()
        page.locator('#modal [data-action=handover-task]').click()
        page.locator('#modal [name=driver_shift_id]').select_option('replacement-shift')
        page.locator('#modal [name=reason]').fill('سبب نقل المهمة')
        page.locator('#modal button[type=submit]').click();expect(page.locator('#modal')).not_to_be_visible()
        page.locator('[data-action=order-detail][data-id=order]').click()
        expect(page.locator('#morning-dispatch-status')).to_contain_text('بانتظار قبول' if lang=='ar' else 'awaits the receiving driver')
        expect(page.locator('#morning-dispatch-status')).to_contain_text('عمرو')
        assert task['driver_id']=='busy-driver' and task['shift_id']=='busy-shift'
        assert page.locator('#modal [data-action=assign],#modal [data-action=handover-task]').count()==0
        assert [path for path,_ in writes]==['/api/shifts','/api/orders/order/assign','/api/orders/order/preparation','/api/orders/order/preparation','/api/tasks/task/handover']
        assert not page.locator('#navigation [data-page=admin]').count()
        expect(page.locator('#navigation [data-page=shifts]')).to_be_visible()
        assert not errors
        browser.close()


@pytest.mark.parametrize('lang',['ar','en'])
def test_no_eligible_morning_driver_keeps_pending_and_blocks_empty_assignment(static_customer,lang):
    from playwright.sync_api import sync_playwright,expect
    from app.morning_manager_context import CAPABILITIES
    user={'id':'manager','role':'delivery_manager','brand_id':'kps','display_name':'أحمد','branch_ids':['branch2'],
          'effective_permissions':sorted(CAPABILITIES),'delivery_context':{'period':'morning','shift_id':'duty','branch_ids':['branch2']}}
    order={'id':'pending','brand_id':'kps','branch_id':'branch2','service':'delivery','preparation_status':'accepted',
           'delivery_status':'pending','version':2,'morning_driver_accepted':False,'customer':{},
           'snapshot':{'delivery_period':'morning','delivery_workflow':'branch_fulfillment','items':[]}}
    writes=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);context=browser.new_context()
        context.add_init_script(f"localStorage.setItem('kps_ops_lang',{json.dumps(lang)})")
        page=context.new_page()
        def api(route):
            request=route.request;path=urlparse(request.url).path;payload={}
            if request.method!='GET':writes.append(path);raise AssertionError('No empty-driver or automatic UI mutations')
            if path=='/api/session':payload={'user':user,'csrf_token':'synthetic'}
            elif path=='/api/catalog':payload={'brand':{'id':'kps','theme':{}}}
            elif path=='/api/branches':payload={'branches':[{'id':'branch2','brand_no':2,'city':'حفر الباطن'}]}
            elif path=='/api/shifts/current':payload={'shifts':[{'id':'duty','kind':'manager','user_id':'manager','status':'open'}]}
            elif path=='/api/orders':payload={'orders':[order]}
            elif path=='/api/orders/pending':payload={'order':order}
            elif path=='/api/tasks':payload={'tasks':[]}
            elif path=='/api/shifts':payload={'shifts':[]}
            route.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
        page.route('**/api/**',api);page.goto(static_customer+'/ops.html?delivery_period=morning')
        page.locator('[data-action=order-detail][data-id=pending]').click()
        expect(page.locator('#morning-dispatch-status')).to_contain_text('لم يُسند بعد' if lang=='ar' else 'still unassigned')
        assert page.locator('#modal [data-action=prepare]').count()==0
        page.locator('#modal [data-action=assign]').click()
        expect(page.locator('#modal button[type=submit]')).to_be_disabled()
        assert writes==[] and order['delivery_status']=='pending'
        browser.close()


def test_self_open_lost_response_recovers_existing_own_duty_without_second_write(static_customer):
    from playwright.sync_api import sync_playwright,expect
    from app.morning_manager_context import CAPABILITIES,BOOTSTRAP_CAPABILITIES
    opened=False;writes=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page()
        def api(route):
            nonlocal opened
            request=route.request;path=urlparse(request.url).path;payload={}
            if request.method!='GET':
                writes.append(path);assert path=='/api/shifts' and not opened
                opened=True
                route.fulfill(status=503,content_type='application/json',body=json.dumps({'detail':{'code':'SYNTHETIC_RESPONSE_LOST','message':'Synthetic lost response'}}))
                return
            if path=='/api/session':
                scope={'period':'morning','branch_ids':['branch2'],'shift_id':'own-duty' if opened else None}
                if not opened:scope['bootstrap']=True
                payload={'user':{'id':'manager','role':'delivery_manager','brand_id':'kps','branch_ids':['branch2'],
                         'effective_permissions':sorted(CAPABILITIES if opened else BOOTSTRAP_CAPABILITIES),'delivery_context':scope},'csrf_token':'synthetic'}
            elif path=='/api/catalog':payload={'brand':{'id':'kps','theme':{}}}
            elif path=='/api/shifts/current':payload={'shifts':[{'id':'own-duty','kind':'manager','user_id':'manager','status':'open'}] if opened else []}
            elif path=='/api/branches':payload={'branches':[{'id':'branch2','branch_no':2,'city':'حفر الباطن'}]}
            elif path=='/api/orders':assert opened;payload={'orders':[]}
            route.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
        page.route('**/api/**',api);page.goto(static_customer+'/ops.html?delivery_period=morning')
        page.locator('[data-action=morning-open-duty]').click()
        expect(page.locator('[data-action=morning-open-duty]')).to_have_count(0)
        expect(page.locator('#content')).to_contain_text('لا توجد طلبات ضمن الفلاتر المحددة')
        assert opened and writes==['/api/shifts']
        browser.close()
