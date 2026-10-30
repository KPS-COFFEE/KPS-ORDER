"""Full ops and prior-deliveries assets: operation reads own their lifecycle."""
import os
import re
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]

def run_acceptance():
    source=(ROOT/'public/ops.js').read_text()
    source,count=re.subn(r'  init\(\);\s*\}\)\(\);\s*$', '  window.qa={state,loadPage};\n})();', source)
    assert count==1
    html=re.sub(r'<script[\s\S]*?</script>|<link[^>]*>', '', (ROOT/'public/ops.html').read_text())
    with sync_playwright() as p:
        launch={'headless':True,'args':['--no-sandbox']}
        if os.getenv('TEST_CHROMIUM_EXECUTABLE'):launch['executable_path']=os.environ['TEST_CHROMIUM_EXECUTABLE']
        browser=p.chromium.launch(**launch)
        for lang in ['ar','en']:
            context=browser.new_context(viewport={'width':390,'height':844});page=context.new_page();page.set_default_timeout(4000)
            control={'hold_orders':False,'hold_options':False,'held_orders':[],'held_options':[],'option_calls':0};errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            options={'grants':[],'managers':[{'id':'manager-duty','display_name':'Synthetic manager','status':'open'}],'shifts':[{'id':'driver-duty','manager_shift_id':'manager-duty','display_name':'Synthetic driver','status':'open'}]}
            def route_request(route):
                from urllib.parse import urlsplit
                path=urlsplit(route.request.url).path
                if not path.startswith('/api/'):return route.fulfill(content_type='text/html',body=html)
                body={}
                if path=='/api/orders':
                    if control['hold_orders']:control['held_orders'].append(route);return
                    body={'orders':[]}
                elif path=='/api/prior-deliveries/options':
                    control['option_calls']+=1
                    if control['hold_options']:control['held_options'].append(route);return
                    body=options
                elif path=='/api/branches':body={'branches':[{'id':'qa-branch','brand_id':'enbishan','name':'Synthetic branch','branch_no':1}]}
                elif path=='/api/operations/people':body={'users':[]}
                elif path=='/api/order-interventions/pending-cancellations':body={'requests':[]}
                return route.fulfill(json=body)
            context.route('https://qa.invalid/**',route_request);page.goto('https://qa.invalid/ops')
            page.add_style_tag(content=(ROOT/'public/ops.css').read_text())
            page.evaluate('''()=>{window.setInterval=()=>0;window.KPSDriverLocation={init:()=>({refresh:async()=>{},reset:()=>{}})};window.KPSDriverChecks={init:()=>({refresh:async()=>{},reset:()=>{}})};}''')
            for asset in ['saudi-plate.js','prior-deliveries.js']:page.add_script_tag(content=(ROOT/'public/assets'/asset).read_text())
            page.add_script_tag(content=source)
            page.evaluate('''lang=>{qa.state.user={id:'synthetic-owner',role:'owner',brand_id:'enbishan',branch_ids:['qa-branch'],effective_permissions:[]};qa.state.lang=lang;qa.state.csrf='synthetic';qa.state.page='orders';document.querySelector('#app-shell').hidden=false;document.querySelector('#login-shell').hidden=true;}''',lang)
            assert page.evaluate('()=>qa.loadPage()') is True
            # The real action lock used to cancel its own successful options read
            # by accidentally inheriting the unrelated background-load token.
            control['hold_orders']=True;page.evaluate('()=>{window.pending=qa.loadPage(true)}')
            page.wait_for_function("document.querySelector('#content').getAttribute('aria-busy')==='true'")
            page.locator('[data-action=prior-deliveries]').click()
            expect(page.locator('#prior-authorize')).to_be_visible()
            page.locator('#prior-authorize').click();expect(page.locator('#prior-grant-form')).to_be_visible()
            expect(page.locator('#prior-grant-form [name=manager_shift_id]')).to_have_value('manager-duty')
            expect(page.locator('#prior-grant-form [name=driver_shift_ids]')).to_have_count(1)
            control['held_orders'].pop().fulfill(json={'orders':[]});page.evaluate('()=>window.pending')
            expect(page.locator('#prior-grant-form')).to_be_visible();page.locator('#modal-close').click();control['hold_orders']=False
            # A navigation while options are in flight must not open the old modal.
            control['hold_options']=True;page.locator('[data-action=prior-deliveries]').click()
            page.wait_for_function("document.querySelector('#status')!==null")
            page.evaluate("()=>{qa.state.page='interventionInbox';return qa.loadPage()}")
            control['held_options'].pop().fulfill(json=options)
            expect(page.locator('#prior-home')).to_have_count(0);expect(page.locator('#status')).to_contain_text('Operation view changed')
            # A branch-only access change also denies an independent operation read.
            page.evaluate("()=>{qa.state.page='orders';return qa.loadPage()}")
            page.locator('[data-action=prior-deliveries]').click()
            page.evaluate('()=>{qa.state.user.branch_ids=[]}')
            control['held_options'].pop().fulfill(json=options)
            expect(page.locator('#prior-home')).to_have_count(0);expect(page.locator('#status')).to_contain_text('Work access changed')
            assert not errors,errors
            assert control['option_calls']==3
            context.close();print('PASS prior modal poll/navigation/access ownership',lang)
        browser.close()

def test_prior_modal_read_ownership():
    run_acceptance()

if __name__=='__main__':run_acceptance()
