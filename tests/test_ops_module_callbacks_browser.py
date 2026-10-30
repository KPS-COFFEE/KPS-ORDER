"""Actual fleet module callbacks survive retained views without stale-page paints."""
import os,re
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]


def run_acceptance(root=ROOT):
    source=(root/'public/ops.js').read_text()
    source,count=re.subn(r'  init\(\);\s*\}\)\(\);\s*$','  window.qa={state,loadPage};\n})();',source)
    assert count==1
    html=re.sub(r'<script[\s\S]*?</script>|<link[^>]*>','',(root/'public/ops.html').read_text())
    results=[]
    with sync_playwright() as p:
        options={'headless':True,'args':['--no-sandbox']}
        if os.getenv('TEST_CHROMIUM_EXECUTABLE'):options['executable_path']=os.environ['TEST_CHROMIUM_EXECUTABLE']
        browser=p.chromium.launch(**options)
        for width in [390,1440]:
            page=browser.new_page(viewport={'width':width,'height':900})
            mode={'fail_refresh':False,'hold_history':False};held=[];errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            vehicle={'id':'synthetic-car','plate':'QA123','details':{},'assigned_drivers':[]}
            history={'vehicle':vehicle,'rows':[],'total':0,'offset':0,'limit':20}
            def route_request(route):
                from urllib.parse import urlsplit
                path=urlsplit(route.request.url).path
                if not path.startswith('/api/'):return route.fulfill(content_type='text/html',body=html)
                data={}
                if path=='/api/fleet/vehicles':
                    if mode['fail_refresh']:return route.fulfill(status=500,json={'detail':{'message':'Synthetic refresh failure'}})
                    data={'vehicles':[vehicle]}
                elif path=='/api/fleet/options':data={'columns':[],'drivers':[],'branches':[],'today':'2026-10-30'}
                elif path=='/api/fleet/vehicles/synthetic-car':
                    if mode['hold_history']:held.append(route);return
                    data=history
                elif path=='/api/branches':data={'branches':[]}
                elif path=='/api/orders':data={'orders':[],'next_offset':None}
                elif path=='/api/operations/people':data={'users':[]}
                route.fulfill(json=data)
            page.route('**/*',route_request);page.goto('https://qa.invalid/ops')
            page.evaluate('''()=>{window.setInterval=()=>0;
                window.KPSDriverLocation={init:()=>({refresh:async()=>{},reset(){}})};
                window.KPSDriverChecks={init:()=>({refresh:async()=>{},reset(){}})};
                window.KPSSaudiPlate={render:()=>'<span>QA123</span>',text:()=> 'QA123'};}''')
            page.add_script_tag(content=(root/'public/assets/fleet.js').read_text())
            page.add_script_tag(content=source)
            page.evaluate('''()=>{qa.state.user={id:'owner',role:'owner',brand_id:'kps',branch_ids:[],effective_permissions:[]};
                qa.state.page='fleet';qa.state.lang='en';qa.state.csrf='synthetic';document.querySelector('#app-shell').hidden=false;}''')
            assert page.evaluate('()=>qa.loadPage()') is True
            expect(page.locator('[data-fleet-card]')).to_have_count(1)
            before=page.locator('#content').inner_html()
            mode['fail_refresh']=True
            assert page.evaluate('()=>qa.loadPage(true)') is False
            assert page.locator('#content').inner_html()==before
            # The retained real module owns these existing event handlers/API closure.
            page.evaluate("()=>{const input=document.createElement('input');input.id='retained-draft';document.querySelector('#content').append(input)}")
            page.locator('#retained-draft').fill('unsaved work')
            mode['fail_refresh']=False
            page.locator('[data-fleet-vehicle=synthetic-car]').click()
            expect(page.locator('[data-fleet-home]')).to_be_visible()
            expect(page.locator('#page-heading')).to_contain_text('Daily vehicle operating log')
            page.locator('[data-fleet-home]').click()
            expect(page.locator('[data-fleet-card]')).to_have_count(1)
            mode['hold_history']=True
            page.locator('[data-fleet-vehicle=synthetic-car]').click();page.wait_for_timeout(100)
            assert held
            page.locator('#navigation [data-page=orders]').click()
            expect(page.locator('#filters-form')).to_be_visible()
            held.pop().fulfill(json={**history,'vehicle':{**vehicle,'details':{'type':'PRIVATE OLD VIEW'}}})
            page.wait_for_timeout(100)
            expect(page.locator('#filters-form')).to_be_visible()
            assert 'PRIVATE OLD VIEW' not in page.locator('#content').inner_text()
            assert page.evaluate('()=>qa.state.page')=='orders'
            assert not errors,errors
            results.append({'width':width,'retained_module_after_failed_refresh':'PASS','late_module_read_after_navigation':'REJECTED','result':'PASS'})
            page.close()
        browser.close()
    return results

if __name__=='__main__':
    import json
    print(json.dumps(run_acceptance(),indent=2))
