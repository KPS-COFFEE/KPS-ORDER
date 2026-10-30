"""Real browser, full ops.js, synthetic fault IO; separate from server acceptance.

Standalone: python tests/test_ops_screen_faults_browser.py
Set TEST_CHROMIUM_EXECUTABLE for a preinstalled Chromium; no production requests.
"""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def run_acceptance(root=ROOT):
    source=(root/'public/ops.js').read_text()
    source,count=re.subn(r'  init\(\);\s*\}\)\(\);\s*$',
        '  window.qa={state,loadPage,refreshEmployeeAccess,recheckEmployeeView,pollDispatchWorkspace};\n})();',source)
    assert count == 1, 'Full app test hook must replace startup only'
    html=re.sub(r'<script[\s\S]*?</script>|<link[^>]*>', '', (root/'public/ops.html').read_text())
    css=(root/'public/ops.css').read_text()
    user={'id':'qa-owner','role':'owner','brand_id':'kps','branch_ids':['qa-branch'],
          'effective_permissions':[],'display_name':'Synthetic QA'}
    records=[]
    with sync_playwright() as p:
        launch={'headless':True,'args':['--no-sandbox','--disable-gpu']}
        if os.getenv('TEST_CHROMIUM_EXECUTABLE'):launch['executable_path']=os.environ['TEST_CHROMIUM_EXECUTABLE']
        browser=p.chromium.launch(**launch)
        for width in [390,1440]:
            user['role']='owner';user['effective_permissions']=[]
            context=browser.new_context(viewport={'width':width,'height':900})
            page=context.new_page();page.set_default_timeout(5000)
            control={'session':'ok','orders':'ok','held':[],'calls':0}
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            def route_request(route):
                from urllib.parse import urlsplit
                path=urlsplit(route.request.url).path
                if not path.startswith('/api/'):
                    return route.fulfill(content_type='text/html',body=html)
                body={}
                if path=='/api/session':
                    if control['session']=='network':return route.abort()
                    if control['session'] in ['500','revoked']:
                        return route.fulfill(status=403 if control['session']=='revoked' else 500,
                            json={'detail':{'message':'Synthetic fault'}})
                    if control['session']=='delay':
                        # Leave request unresolved until the application's real 5s abort.
                        control['held'].append(route);return
                    body={'user':user,'csrf_token':'synthetic-csrf'}
                elif path=='/api/orders':
                    control['calls']+=1
                    if control['orders']=='500':return route.fulfill(status=500,json={'detail':{'message':'Synthetic fault'}})
                    if control['orders']=='hold':control['held'].append(route);return
                    body={'orders':[],'next_offset':None}
                elif path=='/api/branches':body={'branches':[{'id':'qa-branch','brand_id':'kps','branch_no':1,'name':'Synthetic','city':'Synthetic'}]}
                elif path=='/api/operations/people':body={'users':[]}
                elif path=='/api/shifts/current':body={'shifts':[]}
                elif path=='/api/order-interventions/pending-cancellations':body={'requests':[]}
                return route.fulfill(json=body)
            context.route('https://qa.invalid/**',route_request)
            page.goto('https://qa.invalid/ops');page.add_style_tag(content=css)
            page.evaluate('''()=>{window.setInterval=()=>0;
                window.KPSDriverLocation={init:()=>({refresh:async()=>{},reset:()=>{}})};
                window.KPSDriverChecks={init:()=>({refresh:async()=>{},reset:()=>{}})};}''')
            page.add_script_tag(content=source)
            page.evaluate('''user=>{qa.state.user=user;qa.state.csrf='synthetic';qa.state.page='orders';
                document.querySelector('#app-shell').hidden=false;}''',user)
            assert page.evaluate('()=>qa.loadPage()') is True
            expect(page.locator('#filters-form')).to_be_visible()
            page.locator('input[name=start]').fill('2026-10-01')
            before=page.locator('#content').inner_html()
            control['orders']='500';page.evaluate('()=>qa.loadPage(true)')
            assert page.locator('#content').inner_html()==before
            expect(page.locator('#status')).to_contain_text('البيانات السابقة باقية')
            control['orders']='ok';page.evaluate('()=>qa.loadPage(true)')
            assert page.locator('input[name=start]').input_value()=='2026-10-01'
            for fault in ['network','500','delay']:
                control['session']=fault
                if fault=='delay':
                    page.evaluate('()=>{window.sessionCheck=qa.refreshEmployeeAccess()}')
                    expect(page.locator('#work-access-recovery')).to_be_visible(timeout=7000)
                    page.evaluate('()=>window.sessionCheck')
                    
                    for pending in control['held']:
                        try:pending.abort()
                        except Exception:pass
                    control['held'].clear()
                else:page.evaluate('()=>qa.refreshEmployeeAccess()')
                expect(page.locator('#work-access-recovery')).to_be_visible()
                expect(page.locator('#app-shell')).to_have_css('visibility','hidden')
                assert page.locator('input[name=start]').input_value()=='2026-10-01'
                control['session']='ok';page.evaluate('()=>qa.refreshEmployeeAccess()')
                expect(page.locator('#app-shell')).to_have_css('visibility','visible')
                expect(page.locator('#work-access-recovery')).not_to_be_visible()
            control['orders']='hold';page.evaluate('()=>{window.first=qa.loadPage(true)}')
            page.wait_for_timeout(100);assert control['held']
            calls=control['calls'];page.evaluate('()=>qa.loadPage(true)');assert control['calls']==calls
            page.locator('input[name=start]').fill('2026-10-02')
            control['held'].pop().fulfill(json={'orders':[],'next_offset':None})
            page.evaluate('()=>window.first')
            assert page.locator('input[name=start]').input_value()=='2026-10-02'
            page.evaluate('()=>{window.first=qa.loadPage(true)}');page.wait_for_timeout(100)
            page.evaluate("()=>{qa.state.page='interventionInbox';void qa.loadPage()}")
            control['held'].pop().fulfill(json={'orders':[],'next_offset':None})
            page.evaluate('()=>window.first')
            assert page.evaluate('()=>qa.state.page')=='interventionInbox'
            assert page.locator('#filters-form').count()==0
            # Execute actual driver and manager handlers, beyond extracted function tests.
            control['orders']='ok'
            user['role']='driver';user['effective_permissions']=['tasks_view','shifts_view','shifts_open','tasks_update']
            page.evaluate("user=>{qa.state.user=user;qa.state.page='tasks'}",user)
            assert page.evaluate('()=>qa.loadPage()') is True
            expect(page.locator('.driver-home-summary')).to_be_visible()
            driver_content=page.locator('#content').inner_html()
            control['session']='network';page.evaluate('()=>qa.refreshEmployeeAccess()')
            expect(page.locator('#work-access-recovery')).to_be_visible()
            assert page.locator('#content').inner_html()==driver_content
            control['session']='ok';page.evaluate('()=>qa.refreshEmployeeAccess()')
            expect(page.locator('#app-shell')).to_have_css('visibility','visible')
            user['role']='delivery_manager'
            user['effective_permissions']=['dashboard_view','orders_view','drivers_view','shifts_view','chat_view','delivery_assign']
            page.evaluate("user=>{qa.state.user=user;qa.state.page='dashboard'}",user)
            assert page.evaluate('()=>qa.loadPage()') is True
            expect(page.locator('.dispatch-workspace')).to_be_visible()
            page.locator('#dispatch-filters input[name=search]').fill('synthetic-filter')
            control['orders']='500';page.evaluate('()=>qa.pollDispatchWorkspace()')
            expect(page.locator('.dispatch-workspace')).to_be_visible()
            assert page.locator('#dispatch-filters input[name=search]').input_value()=='synthetic-filter'
            control['orders']='ok';page.evaluate('()=>qa.pollDispatchWorkspace()')
            assert page.locator('#dispatch-filters input[name=search]').input_value()=='synthetic-filter'
            # Actual server denial is terminal; no cached success may keep work visible.
            control['session']='revoked';page.evaluate('()=>qa.refreshEmployeeAccess()')
            assert page.evaluate('()=>qa.state.user') is None
            expect(page.locator('#login-shell')).to_be_visible()
            assert not errors,errors
            records.append({'width':width,'result':'PASS','javascript_errors':errors})
            context.close()
        browser.close()
    return records


if __name__=='__main__':
    import json,sys
    print(json.dumps(run_acceptance(Path(sys.argv[1]) if len(sys.argv)>1 else ROOT),indent=2))
