"""Terminal pending request actions through served assets and real HTTP/PostgreSQL."""
import os
import re
from pathlib import Path
from uuid import uuid4
import pytest
from app import db,auth
from test_operations import ops
from test_terminal_request_review import terminal_case,sale_records,recorded_events
from test_browser import live_app,synthetic_mail,chromium_browser,_context,_owner_login,PASSWORD,ARTIFACTS

pytestmark=[pytest.mark.postgres,pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True),
    pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit real browser acceptance')]


@pytest.mark.parametrize('who,lang,width,action',[
    ('manager','ar',390,'rejected'),('director','en',1280,'refer_financial')])
def test_terminal_pending_request_has_real_review_action_and_preserves_original_sale(ops,live_app,chromium_browser,who,lang,width,action):
    from playwright.sync_api import expect
    actor,_,_,_=ops;oid,rid=terminal_case(ops);before=sale_records(oid);events_before=recorded_events(oid)
    username='terminal-review-'+uuid4().hex
    with db.tx() as c:c.execute('UPDATE users SET username=%s,password_hash=%s WHERE id=%s',(username,auth.hash_password(PASSWORD),'ops-'+who))
    # The application server must use actual sessions, not the API fixture's
    # temporary principal override from creation of the synthetic request.
    actor(who).app.dependency_overrides.clear()
    context,page,errors=_context(chromium_browser,live_app)
    reads=[]
    page.on('response',lambda response:reads.append(response.url))
    try:
        _owner_login(context,live_app,{'id':'ops-'+who,'username':username})
        context.add_init_script("localStorage.setItem('kps_ops_lang',"+repr(lang)+");")
        page.set_viewport_size({'width':width,'height':950})
        page.goto(live_app+'/ops',wait_until='networkidle')
        if who=='director':
            page.locator(f'[data-action=order-detail][data-id="{oid}"]').first.click()
            page.locator(f'#modal-body [data-action=amend-order][data-id="{oid}"]').click()
        else:
            alert=page.locator(f'#operational-alerts [data-action=amend-order][data-id="{oid}"]')
            expect(alert).to_be_visible(timeout=20000)
            alert.click()
        dialog=page.locator('.amend-dialog')
        request=dialog.locator('.service-request')
        expect(request.locator('[name=request_decision]')).to_be_visible()
        button=request.locator('[data-terminal-request-decision='+action+']')
        expect(button).to_be_visible()
        button.scroll_into_view_if_needed()
        page.screenshot(path=str(ARTIFACTS/f'terminal-request-pending-{who}-{lang}-{width}.png'),full_page=True)
        assert request.locator('[data-terminal-request-decision=resolved]').count()==0
        assert dialog.locator('#amend-refund').count()==0
        assert sale_records(oid)==before
        assert recorded_events(oid)==events_before
        with db.tx() as c:assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='customer_request.decided'",(oid,)).fetchone()['n']==0
        button.click()
        expect(dialog.locator('[role=alert]')).not_to_be_empty()
        with db.tx() as c:assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type IN ('customer_request.decided','customer_request.referred')",(oid,)).fetchone()['n']==0
        request.locator('[name=request_decision]').fill('تمت مراجعة طلب العميل المتأخر مع حفظ الفاتورة والتحصيل')
        button.click()
        expected=('مرفوض' if lang=='ar' else 'Rejected') if action=='rejected' else ('محال للمراجعة المحاسبية' if lang=='ar' else 'Referred to accounting review')
        expect(dialog.locator('.service-request h3')).to_contain_text(expected)
        if action=='refer_financial':expect(dialog.locator('.service-request')).to_contain_text('accounting')
        assert dialog.locator('[data-terminal-request-decision]').count()==0
        assert dialog.evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        page.screenshot(path=str(ARTIFACTS/f'terminal-request-{who}-{lang}-{width}.png'),full_page=True)
        assert any('/assets/order-amendments.js?v=20261101-terminal-customer-review1' in url for url in reads)
        shipped_ops = re.search(r'<script src="(/ops\.js\?v=[^"]+)"', (Path(__file__).resolve().parents[1]/'public/ops.html').read_text()).group(1)
        assert any(shipped_ops in url for url in reads)
        assert sale_records(oid)==before
        with db.tx() as c:
            event=c.execute("SELECT actor_id,details FROM order_events WHERE order_id=%s AND event_type=%s",(oid,'customer_request.decided' if action=='rejected' else 'customer_request.referred')).fetchone()
            assert event['actor_id']=='ops-'+who and event['details']['request_id']==rid
        assert not errors
    finally:context.close()
