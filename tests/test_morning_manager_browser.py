"""Explicit morning context uses actual operations UI without permanent grants."""
import json
import os
from urllib.parse import urlparse
import pytest
from test_delivery_workspace_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')


@pytest.mark.parametrize('lang,width',[('ar',390),('en',390),('ar',1366),('en',1366)])
def test_scoped_manager_accepts_with_dispatch_and_shared_manager_controls(static_customer,lang,width):
    from playwright.sync_api import sync_playwright, expect
    from app.morning_manager_context import CAPABILITIES
    user={'id':'manager','brand_id':'kps','role':'delivery_manager','display_name':'محمد تجريبي',
          'branch_ids':['branch2'],'effective_permissions':sorted(CAPABILITIES),
          'delivery_context':{'period':'morning','shift_id':'morning-duty','branch_ids':['branch2']}}
    manager={'id':'morning-duty','shift_no':'M-TEST','kind':'manager','user_id':'manager','status':'open',
             'brand_id':'kps','branch_ids':['branch2'],'opened_at':'2026-10-21T08:00:00+03:00',
             'delivery_window_snapshot':{'period':'morning'}}
    order={'id':'morning-order','order_no':'TEST-1','brand_id':'kps','branch_id':'branch2','service':'delivery',
           'preparation_status':'new','delivery_status':'pending','payment_status':'unpaid','version':1,
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
            assert path not in {'/api/identity/staff','/api/admin/export'}
            assert not path.startswith('/api/shifts/morning-duty')
            headers.append(request.headers.get('x-delivery-period'))
            if path=='/api/session':payload={'user':user,'csrf_token':'synthetic'}
            elif path=='/api/catalog':payload={'brand':{'id':'kps','name':'KPS COFFEE','theme':{}}}
            elif path=='/api/branches':payload={'branches':[{'id':'branch2','brand_id':'kps','branch_no':2,'name':'الخالدية','city':'حفر الباطن'}]}
            elif path=='/api/shifts/current':payload={'shifts':[manager]}
            elif path=='/api/operations/people':payload={'users':[],'authorized_receivers':[],'available_manager_shifts':[manager]}
            elif path=='/api/orders':payload={'orders':[order],'next_offset':None}
            elif path=='/api/orders/morning-order':payload={'order':order,'events':[]}
            elif path=='/api/tasks':
                assert 'order_id=morning-order' in request.url
                payload={'tasks':[]}
            elif path=='/api/orders/morning-order/manager-accept':
                writes.append((path,request.post_data_json));order['preparation_status']='accepted';order['version']+=1
                payload={'order':order,'printing':{'status':'queued'}}
            elif request.method!='GET':raise AssertionError('Unexpected mutation '+path)
            route.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
        page.route('**/api/**',api)
        page.goto(static_customer+'/ops.html?delivery_period=morning')
        expect(page.locator('#navigation [data-page=orders]')).to_have_count(1)
        assert {'orders','shifts','custody','chat','reports'}<=set(page.locator('#navigation [data-page]').evaluate_all('(nodes)=>nodes.map(node=>node.dataset.page)'))
        expect(page.locator('#session-label')).to_contain_text('صباحي حفر الباطن' if lang=='ar' else 'Hafar morning')
        page.locator('[data-action=order-detail][data-id=morning-order]').click()
        accept=page.locator('#modal [data-action=manager-accept]')
        expect(accept).to_have_text('قبول بدل الفرع' if lang=='ar' else 'Accept for branch')
        assert page.locator('#modal [data-action=prepare]').count()==0
        accept.click()
        expect(page.locator('#modal')).not_to_be_visible()
        assert writes==[('/api/orders/morning-order/manager-accept',{'version':1})]
        page.locator('[data-action=order-detail][data-id=morning-order]').click()
        expect(page.locator('#modal [data-action=manager-accept],#modal [data-action=prepare]')).to_have_count(0)
        # The branch preparer, not the manager, supplies the ready event.
        order['preparation_status']='ready';order['version']+=1
        page.reload()
        page.locator('[data-action=order-detail][data-id=morning-order]').click()
        expect(page.locator('#modal [data-action=assign]')).to_be_visible()
        assert page.locator('[data-action=prepare]:visible').count()==0
        expect(page.locator('#modal [data-action=approve-shift]')).to_have_count(0)
        assert page.locator('[data-action=fiscal-documents]:visible').count()==0
        assert writes==[('/api/orders/morning-order/manager-accept',{'version':1})]
        expect(page.locator('#navigation [data-page=shifts]')).to_be_visible()
        expect(page.locator('#navigation [data-page=admin]')).to_have_count(0)
        assert headers and all(header=='morning' for header in headers)
        assert not errors
        browser.close()


@pytest.mark.parametrize('lang,width',[('ar',390),('en',390),('ar',1366),('en',1366)])
def test_morning_whatsapp_order_uses_branch_two_without_customer_history(static_customer,lang,width):
    from playwright.sync_api import sync_playwright,expect
    from app.morning_manager_context import CAPABILITIES
    user={'id':'manager','brand_id':'kps','role':'delivery_manager','display_name':'مدير تجريبي',
          'branch_ids':['branch2'],'effective_permissions':sorted(CAPABILITIES|{'orders_create_customer'}),
          'delivery_context':{'period':'morning','shift_id':'morning-duty','branch_ids':['branch2']}}
    manager={'id':'morning-duty','kind':'manager','user_id':'manager','status':'open',
             'brand_id':'kps','branch_ids':['branch2','branch3'],'delivery_window_snapshot':{'period':'morning'}}
    branches=[{'id':f'branch{n}','brand_id':'kps','branch_no':n,'name':f'فرع {n}','city':'حفر الباطن'} for n in (2,3)]
    created={'id':'manual-order','order_no':'TEST-MANUAL','brand_id':'kps','branch_id':'branch2','service':'delivery',
             'preparation_status':'new','delivery_status':'pending','payment_status':'unpaid','payment_method':'cash',
             'version':1,'total_halalas':1000,'customer':{'name':'عميل تجريبي'},
             'snapshot':{'delivery_period':'morning','delivery_workflow':'branch_fulfillment','items':[]}}
    requests=[];writes=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':width,'height':1000})
        context.add_init_script(f"localStorage.setItem('kps_ops_lang',{json.dumps(lang)})")
        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        def api(route):
            request=route.request;path=urlparse(request.url).path;requests.append(path)
            assert '/api/admin/' not in path
            payload={}
            if path=='/api/session':payload={'user':user,'csrf_token':'synthetic'}
            elif path=='/api/catalog':payload={'brand':{'id':'kps','name':'KPS COFFEE','theme':{}},
                    'products':[{'id':'product','name':'قهوة تجريبية','available':True,'price_halalas':1000}]}
            elif path=='/api/branches':payload={'branches':branches}
            elif path=='/api/shifts/current':payload={'shifts':[manager]}
            elif path=='/api/operations/people':payload={'users':[],'authorized_receivers':[],'available_manager_shifts':[manager]}
            elif path=='/api/operations/people':payload={'users':[],'authorized_receivers':[],'available_manager_shifts':[manager]}
            elif path=='/api/orders':payload={'orders':[],'next_offset':None}
            elif path=='/api/quote':
                writes.append((path,request.post_data_json))
                payload={'quote':{'subtotal_halalas':1000,'delivery_fee_halalas':0,'total_halalas':1000}}
            elif path=='/api/orders/delivery-center':
                writes.append((path,request.post_data_json));payload={'order':created}
            elif path=='/api/orders/manual-order':payload={'order':created,'events':[]}
            elif request.method!='GET':raise AssertionError('Unexpected mutation '+path)
            route.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
        page.route('**/api/**',api)
        page.goto(static_customer+'/ops.html?delivery_period=morning')
        page.locator('#page-heading [data-action=customer-order]').click()
        form=page.locator('#staff-order-form')
        expect(form).to_contain_text('كطلب جديد بانتظار القبول' if lang=='ar' else 'new order awaiting acceptance')
        expect(form.locator('[name=branch_id]')).to_be_disabled()
        expect(form.locator('[name=branch_id] option')).to_have_count(1)
        expect(form.locator('[name=branch_id]')).to_have_value('branch2')
        expect(page.locator('#complimentary-order')).to_be_visible()
        form.locator('[name=name]').fill('عميل تجريبي')
        form.locator('[name=phone]').fill('0500000099')
        form.locator('[name=neighborhood]').fill('حي تجريبي')
        form.locator('[name=address]').fill('عنوان تجريبي')
        form.locator('[name=lat]').fill('28.42');form.locator('[name=lng]').fill('45.97')
        form.locator('#staff-add').click()
        form.locator('[type=submit]').click()
        expect(form.locator('[type=submit]')).to_have_text('تأكيد وإنشاء الطلب' if lang=='ar' else 'Confirm and create order')
        form.locator('[type=submit]').click()
        expect(page.locator('#modal [data-action=manager-accept]')).to_be_visible()
        assert [path for path,_ in writes]==['/api/quote','/api/orders/delivery-center']
        assert created['preparation_status']=='new' and created['delivery_status']=='pending'
        assert 'preparation_status' not in writes[-1][1]  # Creation never accepts for the branch.
        assert all(body['branch_id']=='branch2' and body['service']=='delivery' for _,body in writes)
        assert writes[-1][1]['expected_total_halalas']==1000
        assert not any('customer-lookup' in path for path in requests)
        assert not errors
        browser.close()
