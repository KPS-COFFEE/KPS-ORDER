"""Real operations assets with synthetic trial API and SMS; no production writes."""
import json
import os
from datetime import datetime,timedelta,timezone
from urllib.parse import urlparse
import pytest
from test_delivery_workspace_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')


@pytest.mark.parametrize('lang,width',[('ar',390),('en',390),('ar',1366),('en',1366)])
def test_owner_sms_start_zero_permission_members_and_complete_rehearsal(static_customer,lang,width):
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
                           'simulated':True,'financial_effect':False,
                           'order':{'id':'TRIAL-SYNTHETIC','status':'new','item_label':'طلب تجريبي','print_job_id':None},'events':[]}
                    payload=envelope()
                elif path=='/api/morning-trial/trial-synthetic':payload=envelope()
                elif path=='/api/morning-trial/trial-synthetic/action':
                    action=request.post_data_json['action'];state=trial['order']['status']
                    permitted={'accept':({'manager','preparer'},'new','accepted'),
                               'ready':({'preparer'},'accepted','ready'),'assign':({'owner'},'ready','assigned'),
                               'driver_accept':({'driver'},'assigned','driver_accepted'),
                               'delivered':({'driver'},'driver_accepted','delivered'),
                               'close':({'owner'},'delivered','delivered')}
                    roles,previous,next_status=permitted[action]
                    assert role in roles and state==previous
                    trial['order']['status']=next_status
                    if action=='accept':printed+=1;trial['order']['print_job_id']='test-print'
                    if action=='close':trial['status']='closed'
                    trial['events'].append({'action':action,'actor_name':role,'at':datetime.now(timezone.utc).isoformat()})
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
        preparer=actor('preparer');preparer.locator('[data-trial-action=ready]').click()
        owner.locator('#morning-trial-refresh').click();owner.locator('[data-trial-action=assign]').click()
        driver=actor('driver');driver.locator('[data-trial-action=driver_accept]').click()
        driver.locator('[data-trial-action=delivered]').click()
        owner.locator('#morning-trial-refresh').click();owner.locator('[data-trial-action=close]').click()
        expect(owner.locator('#morning-trial-new')).to_be_visible()
        expect(owner.locator('#content')).to_contain_text('الأثر المالي: لا يوجد' if lang=='ar' else 'Financial effect: none')
        expect(owner.locator('#content')).to_contain_text('ليست فاتورة بيع' if lang=='ar' else 'not a sales invoice')
        assert [event['action'] for event in trial['events']]==['accept','ready','assign','driver_accept','delivered','close']
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
