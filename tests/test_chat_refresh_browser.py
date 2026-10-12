"""Real incoming messages preserve composition, attachments, reading position and alert state."""
import io
import os
from uuid import uuid4
import pytest
from PIL import Image
from app import db
from test_browser import _capture,_context,_owner_login,_staff,_customer_session,chromium_browser,live_app,synthetic_mail
pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit isolated browser acceptance')]

@pytest.mark.parametrize('role',['owner','delivery_manager'])
def test_messages_arrive_without_replacing_composer_or_scrolling(live_app,chromium_browser,role):
    from playwright.sync_api import expect
    staff=_staff(role);customer,_,_=_customer_session();oid='TEST-chat-'+uuid4().hex;cid='TEST-conv-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps','kps-branch-1',%s,'delivery','new','pending','cash',1000,0,1000,%s,%s)",(oid,customer,db.json({'order_reference':'TEST-CHAT-STABLE','items':[]}),db.json({'name':'TEST ONLY'})))
        c.execute("INSERT INTO conversations(id,order_id,brand_id,branch_id,customer_id,route) VALUES(%s,%s,'kps','kps-branch-1',%s,'delivery')",(cid,oid,customer))
        for n in range(35):c.execute('INSERT INTO chat_messages(id,conversation_id,sender_id,text) VALUES(%s,%s,%s,%s)',(str(uuid4()),cid,customer,'TEST ONLY history '+str(n)))
    context,page,errors=_context(chromium_browser,live_app);_owner_login(context,live_app,staff)
    try:
        page.goto(live_app+'/ops');page.locator('#navigation [data-page=chat]').click()
        page.locator('.conversation[data-id="'+cid+'"]').click()
        text=page.locator('#message-form textarea');expect(text).to_be_visible()
        text.fill('TEST ONLY draft must stay')
        image=io.BytesIO();Image.new('RGB',(10,10),'white').save(image,'PNG')
        page.locator('#message-form input[type=file]').set_input_files({'name':'test-only.png','mimeType':'image/png','buffer':image.getvalue()})
        text.focus()
        page.evaluate("""() => {window.testComposer=document.querySelector('#message-form');window.testMessages=document.querySelector('#messages');window.testAlerts=document.querySelector('#operational-alerts');const details=window.testAlerts.querySelector('details');if(details)details.open=false;window.testAlertDetails=details;window.testMessages.scrollTop=12;}""")
        with db.tx() as c:c.execute('INSERT INTO chat_messages(id,conversation_id,sender_id,text) VALUES(%s,%s,%s,%s)',(str(uuid4()),cid,customer,'TEST ONLY live incoming'))
        expect(page.locator('#messages')).to_contain_text('TEST ONLY live incoming',timeout=12000)
        assert page.evaluate("document.querySelector('#message-form')===window.testComposer")
        expect(text).to_have_value('TEST ONLY draft must stay')
        assert page.locator('#message-form input[type=file]').evaluate('(e)=>e.files[0].name')=='test-only.png'
        assert page.evaluate("Math.abs(document.querySelector('#messages').scrollTop-12)<2")
        # Cover the old 15-second page/alert timer with no draft/focus protection.
        text.fill('');page.locator('#message-form input[type=file]').set_input_files([])
        page.locator('#messages').click()
        window_y=page.evaluate('window.scrollY')
        with page.expect_response(lambda r:'/api/conversations/alerts' in r.url,timeout=20000):pass
        assert abs(page.evaluate('window.scrollY')-window_y)<2
        assert page.evaluate("document.querySelector('#message-form')===window.testComposer")
        assert page.evaluate("document.querySelector('#messages')===window.testMessages")
        assert page.evaluate("!window.testAlertDetails || (!window.testAlertDetails.open && window.testAlertDetails.isConnected)")
        text.fill('TEST ONLY saved reply')
        page.locator('#message-form button[type=submit]').click()
        expect(page.locator('#messages')).to_contain_text('TEST ONLY saved reply',timeout=12000)
        expect(text).to_have_value('')
        assert page.evaluate("document.querySelector('#message-form')===window.testComposer")
        _capture(page,'TEST_ONLY_stable_chat_'+role)
        assert not errors
    finally:context.close()
