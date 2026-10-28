"""Actual IndexedDB purchase acquisition across isolated same-origin browser tabs."""
import os
from pathlib import Path

import pytest
from test_delivery_workspace_browser import static_customer

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser run')
SOURCE = (Path(__file__).resolve().parents[1] / 'public/customer.js').read_text()
POLICY_HELPERS = SOURCE[SOURCE.index('  function requiresPhoneVerification()'):SOURCE.index('  // Presentation only:')]
HELPERS = SOURCE[SOURCE.index('  // One durable unresolved purchase'):SOURCE.index('  const node = (tag, cls, content) =>')]


def bootstrap(brand):
    return '''const requestedBrandId=BRAND;
const state={session:{user:{id:'synthetic-customer',role:'customer',brand_id:requestedBrandId},csrf_token:'synthetic-csrf',customer_verified:true},cart:[{id:'coffee'}],quote:{}};
const key=()=>crypto.randomUUID();const entryText=(ar,en)=>en;const t=x=>x;
const basketStorageKey='synthetic-basket';
const persistDraft=()=>{},renderCart=()=>{},queueNearestBranches=()=>{},queueQuote=()=>{},closeDialog=()=>{},toast=()=>{};
const openOrders=async id=>{window.opened=id;};
let fail=true;window.writes=[];
const api=async(path,options)=>{window.writes.push(options);if(fail){fail=false;throw Object.assign(new Error('lost response'),{code:'NETWORK'});}return {order:{id:'synthetic-saved'}};};
'''.replace('BRAND', repr(brand)) + POLICY_HELPERS + HELPERS + '''
basketGeneration='synthetic-basket-1';
window.acquire=checkoutAttempt;window.recover=recoverCheckout;window.submit=submitCheckout;
'''


@pytest.mark.parametrize('brand', ['kps', 'enbishan'])
def test_real_indexeddb_tabs_reload_and_frozen_gift(static_customer, brand):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv('TEST_CHROMIUM_EXECUTABLE') or None)
        context = browser.new_context()
        context.route('**/checkout-test', lambda route: route.fulfill(content_type='text/html', body='<title>Isolated checkout</title>'))
        pages = [context.new_page(), context.new_page()]
        for page in pages:
            page.goto(static_customer + '/checkout-test')
            page.add_script_tag(content=bootstrap(brand))
        # Launch both pages without awaiting acquisition: their actual IDB
        # read/write transactions must commit one durable attempt.
        pages[0].evaluate("() => {window.result=undefined;acquire({items:[{product_id:'coffee',quantity:2}],expected_gift:true,expected_gift_version:1}).then(x=>window.result=x);}")
        pages[1].evaluate("() => {window.result=undefined;acquire({items:[{product_id:'coffee',quantity:2}],expected_gift:false,expected_gift_version:2}).then(x=>window.result=x);}")
        for page in pages:
            page.wait_for_function('window.result')
        a, b = [page.evaluate('window.result') for page in pages]
        assert a['key'] == b['key']
        assert a['payload'] == b['payload']
        pages[0].evaluate('() => submit().catch(error=>window.failure=error.code)')
        assert pages[0].evaluate('window.failure') == 'NETWORK'
        pages[0].reload()
        pages[0].add_script_tag(content=bootstrap(brand))
        pages[0].evaluate('fail=false')
        pages[0].evaluate('() => recover()')
        recovered = pages[0].evaluate('window.writes[0]')
        assert recovered['idempotency'] == a['key']
        assert recovered['data'] == a['payload']
        assert pages[0].evaluate('window.opened') == 'synthetic-saved'
        # The other tab still holds the original basket. A saved tombstone
        # opens the existing order instead of another server request.
        pages[1].evaluate('() => submit({items:[],expected_gift:false})')
        assert pages[1].evaluate('window.writes.length') == 0
        assert pages[1].evaluate('window.opened') == 'synthetic-saved'
        context.close()
        browser.close()
