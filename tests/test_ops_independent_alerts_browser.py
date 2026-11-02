"""Full operations source: global alert IO stays independent of page-render IO.

Controlled synthetic API responses, actual Chromium/DOM; no production requests.
"""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]


def run_acceptance(root=ROOT, source=None):
    source=source or (root/'public/ops.js').read_text()
    source,count=re.subn(r'  init\(\);\s*\}\)\(\);\s*$',
        '  window.qa={state,loadPage,refreshOperationalAlerts,refreshDriverAlerts};\n})();',source)
    assert count==1
    html=re.sub(r'<script[\s\S]*?</script>|<link[^>]*>','',(root/'public/ops.html').read_text())
    results=[]
    with sync_playwright() as p:
        options={'headless':True,'args':['--no-sandbox']}
        if os.getenv('TEST_CHROMIUM_EXECUTABLE'):options['executable_path']=os.environ['TEST_CHROMIUM_EXECUTABLE']
        browser=p.chromium.launch(**options)
        for role in ['delivery_manager','driver']:
            for revoked_scope in [False,True]:
                page=browser.new_page(viewport={'width':390,'height':844})
                held={};errors=[];task_calls=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                user={'id':'employee','role':role,'brand_id':'kps','branch_ids':['branch'],
                    'effective_permissions':['dashboard_view','orders_view','drivers_view','shifts_view',
                        'shifts_approve','chat_view','operational_alerts','tasks_view']}
                def route_request(route):
                    from urllib.parse import urlsplit
                    path=urlsplit(route.request.url).path
                    if not path.startswith('/api/'):return route.fulfill(content_type='text/html',body=html)
                    data={}
                    if path=='/api/orders':held['page']=route;return
                    if path=='/api/fuel-exceptions':held['alert']=route;return
                    if path=='/api/tasks':
                        task_calls.append(route)
                        if len(task_calls)<=2:
                            held['page' if len(task_calls)==1 else 'alert']=route;return
                        data={'tasks':[]}
                    if path=='/api/shifts/current':data={'shifts':[]}
                    if path=='/api/operations/drivers':data={'drivers':[]}
                    if path=='/api/conversations':data={'conversations':[]}
                    if path=='/api/branches':data={'branches':[{'id':'branch','brand_id':'kps','name':'Synthetic','city':'Synthetic'}]}
                    if path=='/api/session':data={'user':user,'csrf_token':'synthetic'}
                    route.fulfill(json=data)
                page.route('**/*',route_request)
                page.goto('https://qa.invalid/ops')
                page.evaluate('''()=>{window.setInterval=()=>0;
                    window.KPSDriverLocation={init:()=>({refresh:async()=>{},reset(){}})};
                    window.KPSDriverChecks={init:()=>({refresh:async()=>{},reset(){}})};}''')
                page.add_script_tag(content=source)
                page.evaluate('''u=>{qa.state.user=u;qa.state.csrf='synthetic';
                    qa.state.page=u.role==='driver'?'tasks':'dashboard';document.querySelector('#app-shell').hidden=false;
                    window.initial=qa.loadPage();}''',user)
                page.wait_for_timeout(100);assert 'page' in held
                page.evaluate('()=>{window.alerts=qa.refreshOperationalAlerts()}')
                page.wait_for_timeout(100)
                if role=='driver':
                    # Identical task reads now share one in-flight HTTP request.
                    # Page-render cancellation must still leave the independent
                    # alert consumer alive, with its own live-scope validation.
                    assert len(task_calls)==1
                else:assert 'alert' in held
                page.evaluate('''()=>{qa.state.page=qa.state.user.role==='driver'?'shifts':'drivers';void qa.loadPage();}''')
                if revoked_scope:page.evaluate("()=>{qa.state.user={...qa.state.user,branch_ids:['other-branch']}}")
                held['page'].fulfill(json={'orders':[]} if role!='driver' else {'tasks':[{'id':'private-task','status':'assigned'}]})
                page.evaluate('()=>window.initial')
                if role!='driver':held['alert'].fulfill(json={'requests':[]})
                page.evaluate('()=>window.alerts')
                if role=='delivery_manager':
                    assert page.evaluate('()=>qa.state.page')=='drivers'
                    expect(page.locator('#operational-alerts [data-action=fuel-inbox]')).to_have_count(0 if revoked_scope else 1)
                else:
                    assert page.evaluate('()=>qa.state.page')=='shifts'
                    if revoked_scope:expect(page.locator('#operational-alerts [data-page=tasks]')).to_have_count(0)
                    else:expect(page.locator('#operational-alerts [data-page=tasks]')).to_be_visible()
                if revoked_scope:
                    assert 'private-task' not in page.locator('#operational-alerts').inner_html()
                assert not errors,errors
                results.append({'role':role,'scope_changed':revoked_scope,'result':'PASS'})
                page.close()
        browser.close()
    return results

if __name__=='__main__':
    import json
    print(json.dumps(run_acceptance(),indent=2))
