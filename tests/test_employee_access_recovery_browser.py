"""Real Chromium recovery rendering/input/top-layer behavior with controlled session IO."""
import os
import pytest
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser opt-in required')

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'public/ops.js').read_text()
ACCESS = SOURCE[SOURCE.index('  let permissionRefreshBusy=false;'):SOURCE.index('  new MutationObserver(hideUnavailableActions)')]

def _assert_recovery_preserves_open_dialog_and_retries_above_it(width,height,lang):
    with sync_playwright() as p:
        launch={'headless':True}
        if os.getenv('TEST_CHROMIUM_EXECUTABLE'):launch['executable_path']=os.environ['TEST_CHROMIUM_EXECUTABLE']
        browser=p.chromium.launch(**launch)
        page=browser.new_page(viewport={'width':width,'height':height})
        page.set_content('<html><body><main id="app-shell"><input id="draft" value="unsaved"><p>Private employee work</p></main><dialog id="modal"><div id="modal-body"><input id="modal-draft" value="open operation draft"></div></dialog></body></html>')
        page.add_style_tag(content=(ROOT/'public/ops.css').read_text())
        page.evaluate('''lang=>{window.state={user:{id:'employee',effective_permissions:['dispatch']},csrf:'before',requestKeys:new Map(),chatHistory:new Map(),lang};window.trialMode=false;window.$=(s,root=document)=>root.querySelector(s);window.tr=(ar,en)=>lang==='ar'?ar:en;window.requests=[];window.api=(path,options)=>new Promise((resolve,reject)=>{requests.push({resolve,reject});options.signal.addEventListener('abort',()=>reject(new Error('timeout')));});window.showLogin=()=>{state.user=null;document.querySelector('#app-shell').hidden=true;setEmployeeAccessPending(false);};window.setStatus=()=>{};window.closeModal=()=>document.querySelector('#modal').close();window.renderBrandSwitch=()=>{};window.navigation=()=>{};window.loadPage=async()=>{};window.hideUnavailableActions=()=>{};}''',lang)
        page.evaluate('''()=>{window.ownerDashboardCalls=[];window.ownerDashboardController={pause(){ownerDashboardCalls.push('pause');},reset(){ownerDashboardCalls.push('reset');}};}''')
        page.add_script_tag(content=ACCESS)
        page.evaluate("document.querySelector('#modal').showModal();window.check=refreshEmployeeAccess();requests[0].reject(new Error('network'));window.check")
        expect(page.locator('#work-access-recovery')).to_be_visible()
        expect(page.locator('#app-shell')).to_have_css('visibility','hidden')
        expect(page.locator('#modal')).to_have_css('visibility','hidden')
        assert page.evaluate("document.querySelector('#app-shell').inert")
        assert page.evaluate("ownerDashboardCalls.includes('pause') && !ownerDashboardCalls.includes('reset')")
        assert page.locator('#modal-draft').input_value()=='open operation draft'
        # Browser actionability proves the native old dialog does not make retry inert.
        page.locator('#work-access-recovery button').click()
        page.wait_for_function('requests.length>=2')
        page.evaluate("requests.at(-1).resolve({user:{...state.user},csrf_token:'after'})")
        expect(page.locator('#work-access-recovery')).not_to_be_visible()
        expect(page.locator('#modal')).to_be_visible()
        assert page.locator('#modal-draft').input_value()=='open operation draft'
        assert page.locator('#draft').input_value()=='unsaved'
        assert page.evaluate("document.querySelector('#modal').open")
        assert page.evaluate("!document.querySelector('#app-shell').inert")
        assert page.evaluate("!ownerDashboardCalls.includes('reset')"), 'Transient recovery must preserve owner dashboard state'
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        browser.close()

def test_recovery_preserves_open_dialog_and_retries_above_it():
    for args in [(390,844,'ar'), (1440,900,'en')]:
        _assert_recovery_preserves_open_dialog_and_retries_above_it(*args)
        print('PASS',args)

if __name__ == '__main__':
    test_recovery_preserves_open_dialog_and_retries_above_it()
