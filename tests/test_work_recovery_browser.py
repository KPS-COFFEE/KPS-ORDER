"""Real isolated financial replay; only the response transport is interrupted.

The first POST reaches the actual server and commits. Its reply is then dropped
by Chromium's network fault injection. No business or session reply is mocked.
"""
import os
import pytest
from app import db
from tests.browser_sms_helpers import sms_browser_app
from test_browser import _context, _owner_login, _staff, chromium_browser

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit isolated browser acceptance')]

@pytest.mark.parametrize('width',[390,1440])
@pytest.mark.parametrize('recovery',['same','edited','reload-unrecorded'])
def test_lost_committed_cash_response_preserves_form_blocks_edits_and_replays_once(sms_browser_app,chromium_browser,width,recovery):
    from playwright.sync_api import expect
    base=sms_browser_app;owner=_staff();sid=db.new_id('TEST-recovery-shift-')
    with db.tx() as c:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status) VALUES(%s,'manager',%s,'kps','[\"kps-branch-1\"]','open')",(sid,owner['id']))
    work,page,errors=_context(chromium_browser,base)
    page.set_viewport_size({'width':width,'height':1000})
    try:
        _owner_login(work,base,owner)
        page.goto(base+'/ops',wait_until='networkidle')
        page.locator('#navigation [data-page=shifts]').click()
        page.locator(f'[data-action=shift-detail][data-id="{sid}"]').click()
        page.locator('#modal details').last.locator('summary').click()
        page.locator(f'[data-action=cash-event][data-id="{sid}"]').click()
        form=page.locator('#modal form')
        form.locator('[name=amount]').fill('123.45')
        form.locator('[name=reference]').fill('TEST-ONLY-NETWORK-REPLAY')
        form.locator('[name=reason]').fill('Synthetic original cash confirmed before lost reply')
        form.locator('[name=confirmed]').check()
        path=f'/api/shifts/{sid}/cash-event';requests=[]
        def lose_reply(route):
            requests.append(route.request.headers.get('idempotency-key'))
            if recovery!='reload-unrecorded':
                result=route.fetch()
                assert result.ok,result.text()
            route.abort('connectionreset')
        page.route('**'+path,lose_reply)
        form.locator('button[type=submit]').click()
        expect(form.locator('.form-error')).not_to_be_empty()
        assert form.locator('[name=amount]').input_value()=='123.45'
        with db.tx() as c:
            events=c.execute("SELECT amount_halalas FROM custody_events WHERE shift_id=%s AND kind='direct_receipt'",(sid,)).fetchall()
            assert [r['amount_halalas'] for r in events]==([] if recovery=='reload-unrecorded' else [12345])
        # Real offline/resume leaves the actual draft and checkboxes intact.
        work.set_offline(True)
        expect(page.locator('html')).to_have_attribute('data-work-access-pending','')
        work.set_offline(False)
        expect(page.locator('html')).not_to_have_attribute('data-work-access-pending','')
        assert form.locator('[name=amount]').input_value()=='123.45'
        assert form.locator('[name=confirmed]').is_checked()
        page.unroute('**'+path,lose_reply)
        if recovery=='reload-unrecorded':
            page.reload(wait_until='networkidle')
            page.locator('#navigation [data-page=shifts]').click()
            page.locator(f'[data-action=shift-detail][data-id="{sid}"]').click()
            page.locator('#modal details').last.locator('summary').click()
            page.locator(f'[data-action=cash-event][data-id="{sid}"]').click()
            form=page.locator('#modal form')
            form.locator('[name=amount]').fill('120.00')
            form.locator('[name=reference]').fill('TEST-ONLY-EDITED-AFTER-RELOAD')
            form.locator('[name=reason]').fill('Edited draft must never replace unknown original')
            form.locator('[name=confirmed]').check()
            recovered=[]
            page.on('request',lambda req:recovered.append(req) if req.url.endswith(path) else None)
            form.locator('button[type=submit]').click()
            expect(form.locator('.form-error')).to_contain_text('استعيدت العملية الأصلية')
            assert len(recovered)==1
            assert recovered[0].headers.get('idempotency-key')==requests[0]
            assert recovered[0].post_data_json['amount_halalas']==12345
            assert recovered[0].post_data_json['reference']=='TEST-ONLY-NETWORK-REPLAY'
        elif recovery=='edited':
            form.locator('[name=amount]').fill('120.00')
            form.locator('button[type=submit]').click()
            expect(form.locator('.form-error')).to_contain_text('العملية السابقة محفوظة')
            assert len(requests)==1
        else:
            replay=[]
            page.on('request',lambda req:replay.append(req.headers.get('idempotency-key')) if req.url.endswith(path) else None)
            form.locator('button[type=submit]').click()
            expect(page.locator('#modal')).not_to_be_visible()
            assert replay==requests
        with db.tx() as c:
            events=c.execute("SELECT amount_halalas FROM custody_events WHERE shift_id=%s AND kind='direct_receipt'",(sid,)).fetchall()
            assert [r['amount_halalas'] for r in events]==[12345]
        assert not errors
    finally:
        work.close()
