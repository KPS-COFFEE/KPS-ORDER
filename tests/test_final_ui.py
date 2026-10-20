"""Final approved presentation and real registered-phone/code screen acceptance.

Only SMS provider I/O is synthetic. Browser verification exercises real HTTP
and session creation; it does not claim delivery to a physical phone.
"""
from copy import deepcopy
import os
import re

import pytest

from app import db, email_delivery, reporting
from test_v5_reports import sample
from test_browser import _context, _capture, chromium_browser, live_app, synthetic_mail


def test_final_reports_human_refs_numbered_branches_and_raw_drill_targets():
    snap=sample('manager',1)
    snap['shift']['shift_no']=42
    snap['orders'][0]['order_no']=1234
    snap['display_branches']['b1']['branch_no']=9
    snap['driver_reports'][0]['shift_no']=41
    before=deepcopy(snap)
    tables={table['key']:table for table in reporting.v5_tables(snap,'owner')}
    assert ['مرجع الشفت / حالة الاعتماد','M-000042 / معتمد نهائيا'] in tables['shift']['rows']
    assert tables['orders']['rows'][0][0]=='1234'
    assert tables['orders']['rows'][0][1]=='9 · المروج 1'
    assert tables['drivers']['rows'][0][-1]=='معتمد / D-000041'
    assert 'refunds' not in tables and 'exceptions' not in tables
    page=reporting.make_v5_print_html(snap,'owner','private-report-id')
    assert 'data-drill-target="order-TEST-ORDER-1"' in page
    assert '<summary>طلب 1234 - تفاصيل الأصناف المحفوظة</summary>' in page
    assert 'تقرير M-000042' in page
    assert snap==before


def test_final_report_separates_order_collection_custody_and_direct_receipts():
    snap=sample('manager',1)
    sid=snap['shift']['id']
    snap['custody_events']=[
        {'id':'custody-in','shift_id':sid,'kind':'transfer_in','amount_halalas':1000,'reference_id':'driver-receipt'},
        {'id':'direct-in','shift_id':sid,'kind':'direct_receipt','amount_halalas':500},
        {'id':'advance-out','shift_id':sid,'kind':'expense','amount_halalas':-200},
    ]
    snap['transfers'].append({'id':'driver-receipt','from_shift_id':snap['driver_reports'][0]['shift_id'],'to_shift_id':sid,'amount_halalas':1000,'status':'confirmed'})
    before=deepcopy(snap)
    rows=next(table['rows'] for table in reporting.v5_tables(snap) if table['key']=='finance')
    flat={row[index]:row[index+1] for row in rows for index in (0,2)}
    assert flat['كاش محصل فعليا بعد الباقي']=='10.00'
    assert flat['نقد مستلم من السائقين']=='10.00'
    assert flat['استلامات نقدية مباشرة مسجلة']=='5.00'
    assert flat['مصروفات / سلف / ردود نقدية مسجلة']=='2.00'
    assert flat['التسليم النهائي المؤكد']=='10.00'
    assert snap==before


@pytest.mark.postgres
@pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires explicitly enabled real Chromium and isolated PostgreSQL.')
def test_final_mobile_code_screen_registered_phone_requires_certified_sms(live_app,chromium_browser):
    from playwright.sync_api import expect
    identifier=db.new_id('final_ui_driver_')
    phone='+966500009999'
    with db.tx() as conn:
        conn.execute("INSERT INTO users(id,role,brand_id,display_name,email,phone,email_verified_at,identity_kind,invitation_status,active,branch_ids) VALUES(%s,'driver','kps',%s,%s,%s,now(),'staff_email_verified','active',true,%s)",
                     (identifier,'سائق اختبار الشاشة فقط','code-ui@example.invalid',phone,db.json(['kps-branch-1','kps-branch-9'])))
        conn.execute("UPDATE identity_channels SET state='unconfigured',enabled=false WHERE brand_id='kps' AND channel='sms'")
    context,page,errors=_context(chromium_browser,live_app)
    try:
        page.goto('/ops.html')
        expect(page.locator('#login-shell')).to_be_visible()
        assert page.locator('#staff-code-request-form input[type=email]').count()==0
        for width in (360,390,1280):
            page.set_viewport_size({'width':width,'height':900})
            expect(page.locator('#login-form input[name=password]')).not_to_be_visible()
            expect(page.locator('#staff-code-request-form input[name=phone]')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            _capture(page,'final-registered-mobile-login-'+str(width))
        page.locator('#staff-code-request-form input[name=phone]').fill(phone)
        page.locator('#staff-code-request-submit').click()
        expect(page.locator('#staff-code-request-error')).to_contain_text('غير معتمدة')
        expect(page.locator('#staff-code-verify-form')).not_to_be_visible()
        assert page.locator('#staff-code-request-state').inner_text()==''
        assert context.request.get(live_app+'/api/session', headers={'X-KPS-Context':'work'}).json()['user'] is None
        with db.tx() as conn:
            conn.execute("UPDATE identity_channels SET state='active',enabled=true WHERE brand_id='kps' AND channel='email'")
        page.locator('#staff-code-request-submit').click()
        expect(page.locator('#staff-code-verify-form')).to_be_visible()
        expect(page.locator('#staff-code-note')).to_contain_text('SMS')
        expect(page.locator('#staff-code-verify-form input[name=code]')).to_have_attribute('maxlength','4')
        expect(page.locator('#staff-code-request-state')).not_to_contain_text('code-ui@example.invalid')
        page.set_viewport_size({'width':390,'height':900})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        _capture(page,'final-numeric-code-screen-390')
        page.set_viewport_size({'width':1280,'height':900})
        code='0123'
        wrong='9999'
        page.locator('#staff-code-verify-form input[name=code]').fill(wrong)
        page.locator('#staff-code-verify-submit').click()
        expect(page.locator('#staff-code-verify-error')).to_contain_text('غير صحيح')
        page.locator('#staff-code-verify-form input[name=code]').fill(code)
        page.locator('#staff-code-verify-submit').click()
        expect(page.locator('#app-shell')).to_be_visible()
        expect(page.locator('#navigation [data-page=tasks]')).to_have_attribute('aria-current','page')
        session=context.request.get(live_app+'/api/session', headers={'X-KPS-Context':'work'}).json()
        assert session['user']['id']==identifier
        assert session['user']['phone_sms_verified_at'] is not None
        with db.tx() as conn:
            proof=conn.execute('SELECT step_up_at FROM sessions WHERE user_id=%s AND revoked_at IS NULL',(identifier,)).fetchone()
            assert proof and proof['step_up_at'] is None
        assert session['user']['branch_ids']==['kps-branch-1','kps-branch-9']
        _capture(page,'final-driver-code-sign-in')
        assert not errors
    finally:
        context.close()
