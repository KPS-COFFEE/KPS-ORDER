"""Real operations assets with synthetic trial API and SMS; no production writes."""
import json
import os
from datetime import datetime,timedelta,timezone
from urllib.parse import urlparse
import pytest
from test_delivery_workspace_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')


@pytest.mark.parametrize('lang,width',[('ar',390),('en',390),('ar',1366),('en',1366)])
@pytest.mark.parametrize('fallback',[False,True])
def test_owner_sms_start_zero_permission_members_and_complete_rehearsal(static_customer,lang,width,fallback):
    from playwright.sync_api import sync_playwright,expect
    users={role:{'id':role,'brand_id':'kps','role':kind,'display_name':role,
                 'branch_ids':['branch2'],'effective_permissions':[],
                 'employee_sms_email_retired':True} for role,kind in
           [('owner','owner'),('manager','delivery_manager'),('preparer','branch_manager'),('driver','driver')]}
    trial=None;printed=0;authorized=False;writes=[];reads=[];errors=[]
    def envelope():return {'trial':trial,'print_job':{'id':'test-print','status':'queued'} if printed else None}
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        def actor(role):
            context=browser.new_context(viewport={'width':width,'height':1000})
            context.add_init_script(f"localStorage.setItem('kps_ops_lang',{json.dumps(lang)})")
            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            def api(route):
                nonlocal trial,printed,authorized
                request=route.request;path=urlparse(request.url).path;payload={}
                assert request.headers.get('x-delivery-period') is None
                if request.method=='GET':reads.append(path)
                else:
                    writes.append((role,path,request.post_data_json))
                    assert request.headers.get('idempotency-key')
                    assert path.startswith('/api/morning-trial/') or path.startswith('/api/identity/owner/sms/')
                if path=='/api/identity/staff/login-policy':payload={'sms_required':True,'channel':'sms'}
                elif path=='/api/session':payload={'user':users[role],'csrf_token':'synthetic'}
                elif path=='/api/morning-trial/current':payload=envelope()
                elif path=='/api/morning-trial/setup':
                    assert role=='owner'
                    payload={'branch':{'id':'branch2','name':'الخالدية','branch_no':2,'city':'حفر الباطن'},
                             **{plural:[{'id':key,'display_name':key}] for plural,key in [('managers','manager'),('preparers','preparer'),('drivers','driver')]}}
                elif path=='/api/identity/owner/sms/request':
                    assert role=='owner';payload={'challenge_id':'synthetic-sms'}
                elif path=='/api/identity/owner/sms/verify':
                    assert request.post_data_json=={'challenge_id':'synthetic-sms','code':'1234'}
                    authorized=True;payload={'verified':True}
                elif path=='/api/morning-trial/start':
                    assert role=='owner' and authorized
                    body=request.post_data_json
                    assert body['branch_id']=='branch2'
                    assert all(body[key+'_user_id']==key for key in ('manager','preparer','driver'))
                    now=datetime.now(timezone.utc)
                    trial={'id':'trial-synthetic','status':'open','branch_id':'branch2',
                           'created_at':now.isoformat(),'expires_at':(now+timedelta(minutes=20)).isoformat(),
                           'participants':{key:{'id':key,'display_name':key} for key in ('manager','preparer','driver')},
                           'simulated':True,'financial_effect':False,'flow_version':2,
                           'order':{'id':'TRIAL-SYNTHETIC','status':'new','item_label':'طلب تجريبي','print_job_id':None},'events':[]}
                    payload=envelope()
                elif path=='/api/morning-trial/trial-synthetic':payload=envelope()
                elif path=='/api/morning-trial/trial-synthetic/action':
                    action=request.post_data_json['action'];state=trial['order']['status']
                    permitted={'accept':({'manager','preparer'},'new','assigned'),
                               'driver_accept':({'driver'},'assigned','driver_accepted'),
                               'preparing':({'manager','preparer'},'driver_accepted','preparing'),
                               'ready':({'manager','preparer'},'preparing','ready'),
                               'picked_up':({'driver'},'ready','picked_up'),
                               'delivered':({'driver'},'picked_up','delivered'),
                               'close':({'owner','manager'},'delivered','delivered')}
                    roles,previous,next_status=permitted[action]
                    assert role in roles and state==previous
                    if role=='manager' and action in ('preparing','ready'):
                        assert request.post_data_json['reason']=='Preparer delayed'
                    trial['order']['status']=next_status
                    if action=='accept':printed+=1;trial['order']['print_job_id']='test-print'
                    if action=='close':trial['status']='closed'
                    trial['events'].append({'action':action,'actor_name':role,'at':datetime.now(timezone.utc).isoformat()})
                    if action=='accept':trial['events'].append({'action':'auto_assign','actor_name':role,'at':datetime.now(timezone.utc).isoformat()})
                    payload=envelope()
                else:raise AssertionError('Unexpected normal operations API '+path)
                route.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
            page.route('**/api/**',api)
            page.goto(static_customer+'/ops.html?morning_trial=1')
            return page
        owner=actor('owner');manager=actor('manager')
        expect(manager.locator('#content')).to_contain_text('لا توجد تجربة' if lang=='ar' else 'No rehearsal')
        owner.locator('#morning-trial-start [type=submit]').click()
        code=owner.locator('dialog[open] input[name=code]');expect(code).to_be_visible()
        code.fill('1234');owner.locator('dialog[open] button[type=submit]').click()
        expect(owner.locator('#morning-trial-order-status')).to_contain_text('طلب وهمي جديد' if lang=='ar' else 'New fictional order')
        manager.reload();expect(manager.locator('[data-trial-action=accept]')).to_be_visible()
        assert manager.locator('#navigation [data-page]').count()==0
        assert manager.locator('[data-trial-action=assign],[data-trial-action=ready],[data-trial-action=stop]').count()==0
        manager.locator('[data-trial-action=accept]').click()
        expect(manager.locator('#morning-trial-print')).to_contain_text('انتظار' if lang=='ar' else 'Queued')
        preparer=actor('preparer')
        assert preparer.locator('[data-trial-action=preparing],[data-trial-action=ready]').count()==0
        assert owner.locator('[data-trial-action=assign]').count()==0
        driver=actor('driver');driver.locator('[data-trial-action=driver_accept]').click()
        assert driver.locator('[data-trial-action=picked_up],[data-trial-action=delivered]').count()==0
        worker=manager if fallback else preparer
        worker.locator('#morning-trial-refresh').click()
        if fallback:
            worker.locator('[data-trial-action=preparing]').click()
            expect(worker.locator('#morning-trial-error')).to_contain_text('اكتب سبب' if lang=='ar' else 'Enter the reason')
            assert trial['order']['status']=='driver_accepted'
            worker.locator('#morning-trial-fallback-reason').fill('Preparer delayed')
        worker.locator('[data-trial-action=preparing]').click()
        expect(worker.locator('#morning-trial-order-status')).to_have_attribute('data-state','preparing')
        expect(worker.locator('[data-trial-action=ready]')).to_be_enabled()
        if fallback:
            # Busy/adopt/finally renders must preserve the draft; a background
            # refresh must not erase an employee's typed justification either.
            expect(worker.locator('#morning-trial-fallback-reason')).to_have_value('Preparer delayed')
            worker.locator('#morning-trial-fallback-reason').fill('Preparer delayed after refresh')
            with worker.expect_response(lambda response:urlparse(response.url).path=='/api/morning-trial/trial-synthetic' and response.request.method=='GET'):
                worker.locator('#morning-trial-refresh').click()
            expect(worker.locator('#morning-trial-fallback-reason')).to_have_value('Preparer delayed after refresh')
            worker.locator('#morning-trial-fallback-reason').fill('Preparer delayed')
        worker.locator('[data-trial-action=ready]').click()
        expect(worker.locator('#morning-trial-order-status')).to_have_attribute('data-state','ready')
        expect(worker.locator('#morning-trial-error')).to_be_empty()
        assert trial['order']['status']=='ready', {'trial':trial,'errors':errors,'writes':writes}
        assert not errors
        driver.locator('#morning-trial-refresh').click()
        expect(driver.locator('#morning-trial-order-status')).to_have_attribute('data-state','ready')
        driver.locator('[data-trial-action=picked_up]').click()
        driver.locator('[data-trial-action=delivered]').click()
        manager.locator('#morning-trial-refresh').click();manager.locator('[data-trial-action=close]').click()
        owner.locator('#morning-trial-refresh').click()
        expect(owner.locator('#morning-trial-new')).to_be_visible()
        expect(owner.locator('#content')).to_contain_text('الأثر المالي: لا يوجد' if lang=='ar' else 'Financial effect: none')
        expect(owner.locator('#content')).to_contain_text('ليست فاتورة بيع' if lang=='ar' else 'not a sales invoice')
        assert [event['action'] for event in trial['events']]==['accept','auto_assign','driver_accept','preparing','ready','picked_up','delivered','close']
        assert printed==1
        assert not any(path.startswith(('/api/orders','/api/shifts','/api/tasks','/api/operations')) for path in reads)
        assert not errors
        browser.close()


@pytest.mark.parametrize('lang',['ar','en'])
def test_missing_eligible_accounts_stays_blocked_without_automatic_grants(static_customer,lang):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);context=browser.new_context()
        context.add_init_script(f"localStorage.setItem('kps_ops_lang',{json.dumps(lang)})")
        page=context.new_page();writes=[]
        def api(route):
            path=urlparse(route.request.url).path
            if route.request.method!='GET':writes.append(path)
            payload={}
            if path=='/api/session':payload={'user':{'id':'owner','brand_id':'kps','role':'owner','display_name':'Owner','effective_permissions':[]},'csrf_token':'synthetic'}
            elif path=='/api/morning-trial/current':payload={'trial':None}
            elif path=='/api/morning-trial/setup':payload={'branch':{'id':'branch2','name':'الخالدية'},'managers':[],'preparers':[],'drivers':[]}
            route.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
        page.route('**/api/**',api);page.goto(static_customer+'/ops.html?morning_trial=1')
        expect(page.locator('#morning-trial-start [type=submit]')).to_be_disabled()
        expect(page.locator('#morning-trial-start .form-error')).to_contain_text('لا تُضاف صلاحيات تشغيل فعلية تلقائيًا' if lang=='ar' else 'actual operating permissions are never added automatically')
        expect(page.locator('#morning-trial-start .form-error a')).to_have_attribute('href','/ops')
        assert writes==[]
        browser.close()


def test_expired_owner_can_stop_and_cancel_only_queued_rehearsal_print(static_customer):
    from playwright.sync_api import sync_playwright,expect
    trial={'id':'trial-expired','status':'open','branch_id':'branch2',
           'expires_at':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(),
           'participants':{'manager':{'id':'manager','display_name':'Manager'},
                           'preparer':{'id':'preparer','display_name':'Preparer'},
                           'driver':{'id':'driver','display_name':'Driver'}},
           'simulated':True,'financial_effect':False,
           'order':{'id':'TRIAL-EXPIRED','status':'accepted','print_job_id':'test-print'},'events':[]}
    print_job={'id':'test-print','status':'queued'};writes=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page()
        def api(route):
            request=route.request;path=urlparse(request.url).path
            assert request.headers.get('x-delivery-period') is None
            payload={}
            if path=='/api/session':payload={'user':{'id':'owner','brand_id':'kps','role':'owner','display_name':'Owner','effective_permissions':[]},'csrf_token':'synthetic'}
            elif path=='/api/morning-trial/current':payload={'trial':trial,'print_job':print_job}
            elif path=='/api/morning-trial/trial-expired/action':
                assert request.method=='POST' and request.headers.get('idempotency-key')
                writes.append((path,request.post_data_json))
                assert request.post_data_json=={'action':'stop'}
                trial['status']='stopped';print_job['status']='cancelled'
                payload={'trial':trial,'print_job':print_job}
            else:assert request.method=='GET'
            route.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
        page.route('**/api/**',api);page.goto(static_customer+'/ops.html?morning_trial=1')
        expect(page.locator('#content')).to_contain_text('انتهت المدة')
        expect(page.locator('[data-trial-action=stop]')).to_be_visible()
        assert page.locator('[data-trial-action]:not([data-trial-action=stop])').count()==0
        page.locator('[data-trial-action=stop]').click()
        expect(page.locator('#morning-trial-print')).to_have_text('أُلغي أمر الاختبار')
        assert trial['status']=='stopped' and print_job['status']=='cancelled'
        assert writes==[('/api/morning-trial/trial-expired/action',{'action':'stop'})]
        browser.close()
