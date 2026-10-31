"""Actual SMS, HTTP, PostgreSQL and Chromium observation acknowledgement UI."""
import os

import pytest

from app import db
from test_browser import ARTIFACTS, _context, _customer_session, _owner_login, _staff, chromium_browser
from tests.browser_sms_helpers import sms_browser_app

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit real Chromium run')]


@pytest.mark.parametrize('lang,width',[(lang,width) for lang in ('ar','en') for width in (390,768,1280)])
def test_manager_reads_photo_observation_without_closing_it(sms_browser_app,chromium_browser,lang,width):
    from playwright.sync_api import expect
    base=sms_browser_app
    manager=_staff('delivery_manager');driver=_staff('driver');customer,_,_=_customer_session()
    oid=db.new_id('observation_browser_');shift=db.new_id('observation_shift_')
    details={'kind':'same_image_across_stages','status':'open','fingerprint':'a'*64,
             'driver_id':driver['id'],'task_id':'TEST-ONLY-task','photo_ids':['TEST-pickup','TEST-delivery'],
             'branch_id':'kps-branch-1','note':'تطابق صورة الاستلام والتسليم؛ ملاحظة إدارية للمراجعة، ولا تثبت مخالفة.'}
    with db.tx() as c:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps','[\"kps-branch-1\"]')",(shift,manager['id']))
        c.execute("""INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,
            delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer)
            VALUES(%s,'kps','kps-branch-1',%s,'delivery','ready','pending','cash',1000,0,1000,%s,%s)""",
            (oid,customer,db.json({'items':[],'branch_name':'فرع اختبار','order_reference':'TEST-PHOTO-001'}),
             db.json({'name':'عميل اختبار الصور'})))
        # Synthetic observation seeding isolates UI behaviour. Backend tests
        # separately execute actual upload, pickup and delivery duplicate detection.
        db.event(c,oid,driver['id'],'driver_evidence.observation',details)
        original=c.execute("SELECT id,details,created_at FROM order_events WHERE order_id=%s AND event_type='driver_evidence.observation'",(oid,)).fetchone()
    context,page,errors=_context(chromium_browser,base)
    try:
        _owner_login(context,base,manager)
        context.add_init_script(f"localStorage.setItem('kps_ops_lang','{lang}')")
        page.set_viewport_size({'width':width,'height':950})

        def open_observations():
            page.goto(base+'/ops',wait_until='networkidle')
            if not page.locator('#navigation [data-page=orders]').is_visible():
                page.locator('.dispatch-nav-toggle').click()
            page.locator('#navigation [data-page=orders]').click()
            page.locator(f'#content [data-action=order-detail][data-id="{oid}"]').click()
            page.locator('#modal-body [data-action=order-support]').click()
            expect(page.locator('#modal-body .card')).to_have_count(1)

        open_observations()
        card=page.locator('#modal-body .card')
        expect(card).to_contain_text('ملاحظة إدارية على صور السائق' if lang=='ar' else 'Administrative driver photo observation')
        expect(card).to_contain_text('لا تثبت مخالفة' if lang=='ar' else 'does not establish misconduct')
        read=page.locator('#modal-body [data-action=evidence-observation-read]')
        expect(read).to_be_visible()
        # Merely opening the note is never acknowledgement or resolution.
        with db.tx() as c:
            assert not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='driver_evidence.observation_read'",(oid,)).fetchone()
        page.screenshot(path=str(ARTIFACTS/f'photo-observation-before-{lang}-{width}.png'),full_page=True)
        with page.expect_response(lambda r:'/evidence-observations/' in r.url and r.url.endswith('/read') and r.request.method=='POST') as response:
            read.click()
        assert response.value.ok,response.value.text()
        expect(read).to_have_count(0)
        expect(card).to_contain_text('تمت القراءة' if lang=='ar' else 'Read')
        expect(card).to_contain_text('الملاحظة مفتوحة' if lang=='ar' else 'Observation remains open')
        assert card.evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        assert page.locator('#modal').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        card.scroll_into_view_if_needed()
        page.screenshot(path=str(ARTIFACTS/f'photo-observation-read-{lang}-{width}.png'),full_page=True)
        card.screenshot(path=str(ARTIFACTS/f'photo-observation-card-{lang}-{width}.png'))
        with db.tx() as c:
            acknowledgements=c.execute("SELECT actor_id,created_at,details FROM order_events WHERE order_id=%s AND event_type='driver_evidence.observation_read'",(oid,)).fetchall()
            assert len(acknowledgements)==1
            acknowledgement=acknowledgements[0]
            assert acknowledgement['actor_id']==manager['id'] and acknowledgement['created_at']
            assert acknowledgement['details']=={'observation_id':original['id'],'status':'open','acknowledgement_only':True}
            assert c.execute('SELECT id,details,created_at FROM order_events WHERE id=%s',(original['id'],)).fetchone()==original
            assert c.execute('SELECT delivery_status FROM orders WHERE id=%s',(oid,)).fetchone()['delivery_status']=='pending'
        open_observations()
        expect(card).to_contain_text('تمت القراءة' if lang=='ar' else 'Read')
        expect(card).to_contain_text('الملاحظة مفتوحة' if lang=='ar' else 'Observation remains open')
        expect(read).to_have_count(0)
        assert not errors,errors
    finally:
        context.close()
