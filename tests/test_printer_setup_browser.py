"""Guided branch setup, automatic status and physical acceptance against isolated DB."""
import json
import os
import zipfile
import pytest
from app import db
from test_browser import _capture, _context, _owner_login, _staff, chromium_browser, synthetic_mail

from tests.browser_sms_helpers import sms_browser_app as live_app

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit isolated browser acceptance')]

@pytest.mark.parametrize('language',['ar','en'])
def test_guided_printer_setup_and_live_acceptance(live_app,chromium_browser,tmp_path,language):
    from playwright.sync_api import expect
    manager=_staff('delivery_manager')
    context,page,errors=_context(chromium_browser,live_app)
    headers=_owner_login(context,live_app,manager)
    try:
        page.goto(live_app+'/ops')
        if language=='en': page.get_by_role('button',name='English',exact=True).click()
        page.locator('#navigation [data-page=printers]').click()
        page.locator('[data-action=branch-printers]').click()
        page.locator('#printer-branch').select_option('kps-branch-1')
        page.locator('[data-action=pair-printer]').click()
        expect(page.locator('#modal select[name=branch_id]')).to_have_value('kps-branch-1')
        page.locator('#modal input[name=label]').fill('TEST ONLY branch computer')
        with page.expect_download() as download:
            page.locator('#modal button[type=submit]').click()
        path=tmp_path/'branch.zip';download.value.save_as(path)
        with zipfile.ZipFile(path) as bundle: setup=json.loads(bundle.read('setup.json'))
        assert setup['branch_id']=='kps-branch-1'
        result=context.request.post(live_app+'/api/printing/bridge/pair',headers=headers,data={'pairing_code':setup['pairing_code'],'printer_name':'TEST ONLY thermal printer'})
        assert result.status==200
        paired=result.json();token={'Authorization':'Bearer '+paired['token']}
        test=page.locator('#printer-live [data-action=test-printer]')
        expect(test).to_be_enabled(timeout=12000)
        expect(page.locator('.printer-directory .pill')).to_contain_text('بانتظار الاعتماد' if language=='ar' else 'Awaiting approval')
        for width in (1280, 768, 390):
            page.set_viewport_size({'width':width,'height':900})
            layout=page.locator('#printer-live .printer-directory').evaluate('''root => {
                const name=root.querySelector('td:nth-child(2)');
                const pill=root.querySelector('.pill');
                const notes=root.querySelector('td:nth-child(4)');
                const elements=[name,pill,notes,...root.querySelectorAll('td button')];
                const readable=elements.every(e=>{
                    const style=getComputedStyle(e);
                    return style.whiteSpace==='normal'&&style.textOverflow!=='ellipsis'&&
                        e.scrollWidth<=e.clientWidth+1&&e.scrollHeight<=e.clientHeight+1;
                });
                const buttons=[...root.querySelectorAll('td:last-child button')].map(e=>e.getBoundingClientRect());
                const overlap=buttons.some((r,i)=>buttons.slice(i+1).some(b=>
                    Math.min(r.right,b.right)>Math.max(r.left,b.left)&&Math.min(r.bottom,b.bottom)>Math.max(r.top,b.top)));
                return {readable,overlap,notesWrap:getComputedStyle(notes).overflowWrap,
                    pageOverflow:document.documentElement.scrollWidth>innerWidth};
            }''')
            assert layout=={'readable':True,'overlap':False,'notesWrap':'anywhere','pageOverflow':False}
            _capture(page,f'TEST_ONLY_printer_layout_{language}_{width}')
        page.set_viewport_size({'width':1280,'height':900})
        with page.expect_response(lambda r: r.url.endswith("/test") and r.request.method=="POST"):
            test.click()
        claim=context.request.post(live_app+'/api/printing/bridge/next',headers={**headers,**token},data={})
        job=claim.json()['job']
        assert job and job['payload']['kind']=='test'
        assert context.request.post(live_app+'/api/printing/bridge/jobs/'+job['id']+'/ack',headers={**headers,**token},data={'status':'submitted'}).status==200
        approve=page.locator('#printer-live [data-action=approve-printer]')
        expect(approve).to_be_visible(timeout=12000)
        approve.click()
        page.locator('#modal input[name=paper_confirmed]').check()
        page.locator('#modal button[type=submit]').click()
        expect(page.locator('#printer-live .pill.good')).to_contain_text('معتمد' if language=='ar' else 'Approved',timeout=12000)
        _capture(page,'TEST_ONLY_printer_approved_'+language)
        with db.tx() as conn:
            conn.execute("UPDATE branch_print_devices SET last_seen_at=now()-interval '2 minutes' WHERE id=%s",(paired['device_id'],))
        expect(page.locator('#printer-live')).to_contain_text('لا يوجد اتصال' if language=='ar' else 'No connection',timeout=12000)
        expect(page.locator('#printer-live .pill.good')).to_have_count(0)
        expect(page.locator('#printer-live [data-action=test-printer]')).to_be_disabled()
        assert not errors
    finally: context.close()
