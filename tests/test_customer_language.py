"""Customer language acceptance with the existing guest/order engines.

Real Chromium and HTTP run only in an explicitly isolated PostgreSQL schema.
Screenshots contain synthetic fixture data, not production identities or mail.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import db
from test_browser import _context, _owner_login, _staff, chromium_browser, live_app, synthetic_mail
from test_launch_browser import _configure_branches, _evidence

ROOT = Path(__file__).resolve().parents[1]


def test_customer_translation_codes_are_complete_and_keep_approved_source_immutable():
    """Execute the shipped translation helpers; no DOM or network is required."""
    executable = shutil.which('node')
    if not executable:
        pytest.skip('Node is required to execute shipped customer language helpers.')
    source = (ROOT / 'public/customer.js').read_text()
    prefix = source[:source.index('  const brandParameters=')]
    definitions = set(re.findall(r'^    "([a-zA-Z0-9_]+)":', prefix, re.M))
    literal_calls = set(re.findall(r'\bt\([\'"]([a-zA-Z0-9_]+)[\'"](?=\s*[,\)])', source))
    markup = (ROOT / 'public/index.html').read_text()
    static_calls = set(re.findall(r'data-i18n(?:-[a-z-]+)?="([a-zA-Z0-9_]+)"', markup))
    assert (literal_calls | static_calls) <= definitions
    bootstrap = prefix + 'globalThis.probe={uiStrings,t,sourceText,groupLabel,serverReason,lang:(next)=>language=next};})();'
    program = r'''
const vm=require('vm'),assert=require('assert');
const context={localStorage:{getItem:()=>null},Intl};vm.createContext(context);
vm.runInContext(JSON.parse(process.argv[1]),context);
const p=context.probe;
const approved={id:'kps-30',name:'اسم معتمد',name_en:'Approved source name',price_halalas:4000,
 option_groups:[{code:'COLOR',name:'اللون',values:[{code:'BLK',name:'أسود',enabled:false}]}]};
const before=JSON.stringify(approved);
for(const lang of ['ar','en']){
 p.lang(lang);
 for(const [code,pair] of Object.entries(p.uiStrings)){
  assert.ok(pair[lang],code+' is missing '+lang);
  assert.ok(!/\{\w+\}/.test(String(p.t(code,{0:'one',1:'two',time:'16:00',service:'Pickup',payment:'Cash'}))),code);
 }
}
p.lang('en');assert.equal(p.sourceText(approved),'Approved source name');
assert.equal(String(p.groupLabel({code:'COLOR',name:'اللون'})),'Color');
assert.equal(String(p.serverReason({code:'BRANCH_CLOSED',reason:'مغلق'})),
 'This branch is currently closed for new orders.');
p.lang('ar');assert.equal(p.sourceText(approved),'اسم معتمد');
assert.equal(JSON.stringify(approved),before);
process.stdout.write(JSON.stringify({pairs:Object.keys(p.uiStrings).length,source_unchanged:true}));
'''
    result = subprocess.run([executable, '-e', program, json.dumps(bootstrap)],
                            capture_output=True, text=True, check=True, timeout=15)
    assert json.loads(result.stdout)['source_unchanged']


@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Requires real Chromium and isolated PostgreSQL.')
@pytest.mark.parametrize('width', [390, 1280])
def test_customer_language_guest_car_order_arrival_chat_and_persistence(live_app, chromium_browser, width):
    from playwright.sync_api import expect

    owner_context, _, _ = _context(chromium_browser, live_app)
    headers = _owner_login(owner_context, live_app, _staff())
    _configure_branches(owner_context, live_app, headers)
    context, page, errors = _context(chromium_browser, live_app)
    page.set_viewport_size({'width': width, 'height': 900})
    try:
        page.goto('/')
        expect(page.locator('html')).to_have_attribute('lang', 'ar')
        expect(page.locator('html')).to_have_attribute('dir', 'rtl')
        expect(page.locator('.product-card')).to_have_count(34)
        expect(page.locator('#announcement-ticker')).not_to_be_visible()
        expect(page.locator('#brand-logo')).to_be_visible()
        expect(page.locator('#banner-area')).to_be_visible()
        source_cards = page.locator('.product-card').evaluate_all("""els=>els.map(el=>({
            id:el.dataset.productId,image:el.querySelector('img')?.getAttribute('src'),
            title:el.querySelector('h3')?.textContent}))""")
        _evidence(page, f'customer-language-ar-{width}', language='ar', customer='synthetic guest')
        page.locator('#branch-select').select_option('kps-branch-1')
        page.locator('input[name=service][value=car]').check()
        page.locator('#car-model').fill('Test car')
        page.locator('#car-color-choice').select_option('أبيض')
        page.locator('#car-plate').fill('TEST-ONLY')
        page.locator('.product-card[data-product-id="kps-30"] .product-image-button').click()
        page.locator('#product-options select').select_option('BLU')
        page.locator('#product-add').click()
        expect(page.locator('#product-dialog')).not_to_be_visible()
        page.locator('#customer-name').fill('Synthetic language guest')
        page.locator('#customer-phone').fill('0500000001')
        page.locator('#order-notes').fill('Isolated test order; no real preparation or delivery.')
        expect(page.locator('#checkout-button')).to_be_enabled()
        draft_before = page.evaluate("localStorage.getItem('kps-unconfirmed-basket-v2:kps')")
        page.locator('#customer-language').click()
        expect(page.locator('html')).to_have_attribute('lang', 'en')
        expect(page.locator('html')).to_have_attribute('dir', 'ltr')
        expect(page.locator('#service-title')).to_have_text('How would you like to receive your order?')
        expect(page.locator('#orders-button')).to_have_text('My orders')
        expect(page.locator('#checkout-button')).to_have_text('Review and confirm order')
        expect(page.locator('#customer-name')).to_have_value('Synthetic language guest')
        expect(page.locator('#car-plate')).to_have_value('TEST-ONLY')
        expect(page.locator('#branch-select')).to_have_value('kps-branch-1')
        assert page.evaluate("localStorage.getItem('kps-unconfirmed-basket-v2:kps')") == draft_before
        translated_cards = page.locator('.product-card').evaluate_all("""els=>els.map(el=>({
            id:el.dataset.productId,image:el.querySelector('img')?.getAttribute('src'),
            title:el.querySelector('h3')?.textContent}))""")
        assert [(c['id'], c['image']) for c in translated_cards] == [(c['id'], c['image']) for c in source_cards]
        assert translated_cards[0]['title'] == 'Cold Brew'
        assert all(not re.search(r'[\u0600-\u06ff]', c['title']) for c in translated_cards)
        assert 'Drinks' in page.locator('#categories').inner_text()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert 'Noto Kufi Arabic' in page.locator('body').evaluate('el=>getComputedStyle(el).fontFamily')
        _evidence(page, f'customer-language-en-{width}', language='en', basket='unchanged; source IDs/options retained')
        page.locator('#checkout-button').click()
        expect(page.locator('#guest-form')).to_be_visible()
        expect(page.locator('#guest-submit')).to_have_text('Order as a guest')
        page.locator('#guest-name').fill('Synthetic language guest')
        page.locator('#guest-phone').fill('0500000001')
        page.locator('#guest-submit').click()
        expect(page.locator('#confirm-dialog')).to_be_visible()
        expect(page.locator('#confirm-content')).to_contain_text('Car pickup')
        expect(page.locator('#confirm-content')).to_contain_text('Cash')
        page.locator('#confirm-submit').click()
        expect(page.locator('#order-detail')).to_be_visible()
        expect(page.locator('#order-detail')).to_contain_text('Saved order amount')
        expect(page.locator('#order-detail')).to_contain_text('Preparation')
        expect(page.locator('#order-detail')).to_contain_text('Payment')
        session = context.request.get(live_app + '/api/session').json()
        assert session['user']['identity_kind'] == 'guest'
        with db.tx() as conn:
            orders = conn.execute('SELECT * FROM orders WHERE customer_id=%s', (session['user']['id'],)).fetchall()
            assert len(orders) == 1
            order = orders[0]
            assert order['service'] == 'car'
            assert order['snapshot']['items'][0]['units'][0]['options']['COLOR'] == 'BLU'
        _evidence(page, f'customer-language-en-order-{width}', language='en', order='synthetic car pickup')
        page.get_by_role('button', name='I have arrived at the branch', exact=True).click()
        expect(page.get_by_role('button', name='I have arrived at the branch', exact=True)).to_have_count(0)
        page.locator('#order-detail').get_by_role('button', name='Order conversation', exact=True).click()
        expect(page.locator('#chat-dialog')).to_be_visible()
        expect(page.locator('#send-message')).to_have_text('Send')
        page.locator('#chat-text').fill('Synthetic bilingual conversation / محادثة تجريبية')
        page.locator('#send-message').click()
        expect(page.locator('#chat-messages')).to_contain_text('Synthetic bilingual conversation')
        _evidence(page, f'customer-language-en-chat-{width}', language='en', conversation='synthetic; real HTTP')
        page.locator('#chat-dialog [data-close]').click()
        page.locator('#customer-language').click()
        expect(page.locator('html')).to_have_attribute('lang', 'ar')
        expect(page.locator('#orders-button')).to_have_text('طلباتي')
        assert page.locator('.product-card').evaluate_all("""els=>els.map(el=>({
            id:el.dataset.productId,image:el.querySelector('img')?.getAttribute('src'),
            title:el.querySelector('h3')?.textContent}))""") == source_cards
        page.locator('#customer-language').click()
        page.reload()
        expect(page.locator('html')).to_have_attribute('lang', 'en')
        expect(page.locator('#orders-button')).to_have_text('My orders')
        assert context.request.get(live_app + '/api/session').json()['user']['id'] == session['user']['id']
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) AS n FROM orders WHERE customer_id=%s', (session['user']['id'],)).fetchone()['n'] == 1
            assert conn.execute('SELECT arrival_at FROM orders WHERE id=%s', (order['id'],)).fetchone()['arrival_at']
        assert not errors
    finally:
        context.close()
        owner_context.close()

