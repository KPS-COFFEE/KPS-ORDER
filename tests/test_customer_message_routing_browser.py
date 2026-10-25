"""Owner selects recipients, completes synthetic SMS, recipient sees real inbox."""
import os
import json
from pathlib import Path

import pytest
from app import db, customer_message_routing as routing
from test_browser import _context, _owner_login, _staff, chromium_browser
from tests.browser_sms_helpers import sms_browser_app as live_app, complete_owner_sms_dialog
from tests.test_core import _make_user
from tests.test_customer_message_routing import message


@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Isolated Chromium only')
def test_owner_routes_and_admin_replies_without_role_change(live_app, chromium_browser):
    from playwright.sync_api import expect
    owner = _staff()
    admin = _staff('admin')
    customer = _make_user('customer')
    cid = message(customer, category='privacy', route='privacy', branch=None)
    context, page, errors = _context(chromium_browser, live_app)
    _owner_login(context, live_app, owner)
    try:
        page.goto(live_app+'/ops')
        page.locator('#navigation [data-page=admin]').click()
        page.locator('[data-action=customer-message-routing]').click()
        modal = page.locator('#modal')
        for group in ('delivery', 'care'):
            modal.locator(f'input[name="routing:kps:{group}"][value="{admin["id"]}"]').check()
        modal.locator('[name=reason]').fill('Synthetic reviewed response responsibility')
        for width in (390, 1280):
            page.set_viewport_size({'width': width, 'height': 900})
            expect(modal.locator('button[type=submit]')).to_be_visible()
        modal.locator('button[type=submit]').click()
        with db.tx() as c:
            assert routing.config(c, 'kps') is None
        complete_owner_sms_dialog(page, owner)
        expect(modal).not_to_be_visible()
        with db.tx() as c:
            assert routing.config(c, 'kps') == {'delivery': [admin['id']], 'care': [admin['id']]}
            assert c.execute('SELECT role FROM users WHERE id=%s', (admin['id'],)).fetchone()['role'] == 'admin'
        assert not errors
    finally:
        context.close()
    context, page, errors = _context(chromium_browser, live_app)
    _owner_login(context, live_app, admin)
    try:
        page.goto(live_app+'/ops')
        page.locator('#navigation [data-page=chat]').click()
        page.locator(f'.conversation[data-id="{cid}"]').click()
        expect(page.locator('#message-form')).to_be_visible()
        page.locator('#message-form textarea').fill('Synthetic customer care response')
        page.locator('#message-form button[type=submit]').click()
        expect(page.locator('#messages')).to_contain_text('Synthetic customer care response')
        expect(page.locator('#chat-detail [data-action=transfer-chat]')).to_have_count(0)
        Path('test-artifacts').mkdir(exist_ok=True)
        page.screenshot(path='test-artifacts/customer-message-routing-admin.png', full_page=True)
        assert not errors
    finally:
        context.close()


@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Isolated Chromium only')
def test_immediate_admin_reply_while_read_ack_is_delayed_never_submits_native_form(live_app, chromium_browser):
    """Hold only read acknowledgments; all reply and read responses remain real HTTP."""
    from playwright.sync_api import expect
    admin=_staff('admin');customer=_make_user('customer')
    cid=message(customer,category='privacy',route='privacy',branch=None)
    with db.tx() as c:
        c.execute("UPDATE app_settings SET settings=settings||%s WHERE brand_id='kps'",(db.json({'customer_message_routing':{'delivery':[admin['id']],'care':[admin['id']]}}),))
    context,page,errors=_context(chromium_browser,live_app)
    _owner_login(context,live_app,admin)
    context.add_init_script("""(() => {
      const original=window.fetch.bind(window);
      let release;
      const gate=new Promise(resolve=>{release=resolve;});
      window.__chatReadTest={blocked:0,release};
      window.fetch=async function(input,options){
        const path=new URL(typeof input==='string'?input:input.url,location.href).pathname;
        if(path===%s){window.__chatReadTest.blocked++;await gate;}
        return original(input,options);
      };
    })();""" % json.dumps('/api/conversations/'+cid+'/read'))
    try:
        page.goto(live_app+'/ops')
        page.locator('#navigation [data-page=chat]').click()
        page.locator(f'.conversation[data-id="{cid}"]').click()
        page.wait_for_function('window.__chatReadTest.blocked>0')
        expect(page.locator('#message-form')).to_be_visible()
        navigations=[]
        page.on('framenavigated',lambda frame:navigations.append(frame.url) if frame==page.main_frame else None)
        page.locator('#message-form textarea').fill('Actual reply while read acknowledgment is held')
        with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/api/conversations/'+cid+'/messages')) as sent:
            page.locator('#message-form button[type=submit]').click()
        assert sent.value.ok,sent.value.text()
        expect(page.locator('#messages')).to_contain_text('Actual reply while read acknowledgment is held')
        assert page.url==live_app+'/ops'
        assert not navigations
        with db.tx() as c:
            assert c.execute('SELECT count(*) n FROM chat_messages WHERE conversation_id=%s AND sender_id=%s AND text=%s',(cid,admin['id'],'Actual reply while read acknowledgment is held')).fetchone()['n']==1
            assert c.execute('SELECT role FROM users WHERE id=%s',(admin['id'],)).fetchone()['role']=='admin'
        page.evaluate('window.__chatReadTest.release()')
        expect(page.locator('#message-form button[type=submit]')).to_be_enabled()
        assert not errors
    finally:
        context.close()
