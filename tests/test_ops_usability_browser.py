"""Focused real Chromium/HTTP/SMS-policy acceptance, synthetic data only."""
import os
import pytest
from app import db, sms_verification
from test_browser import ARTIFACTS, _staff, _context, _owner_login, chromium_browser
from tests.browser_sms_helpers import sms_browser_app as live_app, complete_owner_sms_dialog

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit real Chromium opt-in required')]


@pytest.mark.parametrize(('lang','width'),[('ar',390),('ar',768),('ar',1280),('en',390),('en',768),('en',1280)])
def test_staff_cards_and_live_partial_customer_search(live_app,chromium_browser,lang,width):
    from playwright.sync_api import expect
    owner=_staff();driver=_staff('driver');customer=_staff('customer')
    with db.tx() as c:
        c.execute("UPDATE users SET display_name='اسم موظف اختبار طويل لا ينبغي قطعه في البطاقة' WHERE id=%s",(driver['id'],))
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES('usability-search','kps','kps-branch-1',%s,'delivery','ready','pending','cash',1000,0,1000,'{}',%s)",(customer['id'],db.json({'phone':'+966501234567','name':'عميل اختبار البحث الجزئي','address':'عنوان اختبار','neighborhood':'حي اختبار'})))
    context,page,errors=_context(chromium_browser,live_app)
    try:
        _owner_login(context,live_app,owner)
        page.add_init_script(f"localStorage.setItem('kps_ops_lang','{lang}')")
        page.set_viewport_size({'width':width,'height':950})
        page.goto(live_app+'/ops',wait_until='networkidle')
        page.locator('#navigation [data-page=admin]').click()
        page.locator('[data-action=admin-tab][data-tab=users]').click()
        card=page.locator(f'.staff-card[data-staff-id="{driver["id"]}"]')
        expect(card).to_be_visible();expect(card.locator('h2')).to_have_text('اسم موظف اختبار طويل لا ينبغي قطعه في البطاقة')
        expect(card.locator('[data-action=staff-vehicle]')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert card.evaluate('(el)=>el.scrollWidth<=el.clientWidth')
        page.screenshot(path=str(ARTIFACTS/f'staff-cards-{lang}-{width}.png'),full_page=True)
        page.locator('#navigation [data-page=customers]').click()
        expect(page.locator('#customer-records-search select')).to_have_value('')
        page.locator('#customer-records-search [name=phone]').fill('123')
        expect(page.locator('.customer-contact-card h2')).to_have_text('عميل اختبار البحث الجزئي')
        expect(page.locator('.customer-contact-card')).to_contain_text('حفر الباطن')
        page.screenshot(path=str(ARTIFACTS/f'customer-search-{lang}-{width}.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('[data-customer-orders]').click()
        expect(page.locator('[data-order-id=usability-search]')).to_be_visible()
        assert not errors,errors
    finally:context.close()


def test_add_staff_sms_provider_failure_can_retry_without_losing_fields(live_app,chromium_browser,monkeypatch):
    from playwright.sync_api import expect
    owner=_staff();context,page,errors=_context(chromium_browser,live_app)
    try:
        _owner_login(context,live_app,owner)
        original=sms_verification.start
        def unavailable(*args,**kwargs):raise db.error(503,'OTP_PROVIDER_FAILURE','تعذر الاتصال بمزود التحقق. حاول لاحقًا.')
        monkeypatch.setattr(sms_verification,'start',unavailable)
        page.goto(live_app+'/ops',wait_until='networkidle')
        page.locator('#navigation [data-page=admin]').click()
        page.locator('[data-action=admin-tab][data-tab=users]').click()
        page.locator('[data-action=invite-staff]').click()
        form=page.locator('#modal form')
        form.locator('[name=display_name]').fill('موظف اختبار إعادة المحاولة')
        form.locator('[name=phone]').fill('0507654321')
        form.locator('.staff-branch-dropdown summary').click()
        form.locator('[name="scope:kps-branch-1"]').check()
        form.locator('button[type=submit]').click()
        dialog=page.locator('dialog:not(#modal)').filter(has=page.locator('[name=code]'))
        expect(dialog.locator('.form-error')).to_contain_text('تعذر الاتصال')
        expect(form.locator('[name=display_name]')).to_have_value('موظف اختبار إعادة المحاولة')
        with db.tx() as c:assert c.execute("SELECT count(*) n FROM users WHERE phone='+966507654321'").fetchone()['n']==0
        monkeypatch.setattr(sms_verification,'start',original)
        dialog.get_by_role('button',name='إعادة إرسال رمز التحقق',exact=True).click()
        expect(dialog.locator('button[type=submit]')).to_be_enabled()
        complete_owner_sms_dialog(page,owner)
        expect(page.locator('#modal')).not_to_be_visible()
        with db.tx() as c:
            rows=c.execute("SELECT * FROM users WHERE phone='+966507654321'").fetchall()
            assert len(rows)==1 and rows[0]['display_name']=='موظف اختبار إعادة المحاولة'
        assert not errors,errors
    finally:context.close()


@pytest.mark.parametrize('width',[390,768,1280])
def test_driver_card_vehicle_transfer_ui(live_app,chromium_browser,width):
    from playwright.sync_api import expect
    owner=_staff();previous=_staff('driver');target=_staff('driver')
    context,page,errors=_context(chromium_browser,live_app)
    try:
        headers=_owner_login(context,live_app,owner)
        created=context.request.post(live_app+'/api/fleet/vehicles',headers={**headers,'Idempotency-Key':'ui-transfer-vehicle'},data={'vehicle':{'plate':'TEST UI 123','type':'Synthetic','odometer_km':1000},'branch_ids':['kps-branch-1']})
        assert created.ok,created.text()
        with db.tx() as c:
            c.execute("UPDATE users SET display_name='سائق سابق للاختبار',vehicle=%s WHERE id=%s",(db.json({'plate':'TEST UI 123'}),previous['id']))
            c.execute("UPDATE users SET display_name='سائق جديد للاختبار' WHERE id=%s",(target['id'],))
        page.set_viewport_size({'width':width,'height':950})
        page.goto(live_app+'/ops',wait_until='networkidle')
        page.locator('#navigation [data-page=drivers]').click()
        page.locator(f'.staff-card[data-staff-id="{target["id"]}"] [data-action=staff-vehicle]').click()
        form=page.locator('#modal form')
        form.locator('[name=vehicle_id]').select_option(created.json()['vehicle_id'])
        expect(page.locator('#vehicle-assignment-review')).to_contain_text('سائق سابق للاختبار')
        form.locator('[name=reason]').fill('نقل صناعي مصرح به لاختبار الواجهة')
        form.locator('[name=confirm_transfer]').check()
        page.screenshot(path=str(ARTIFACTS/f'vehicle-transfer-ar-{width}.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        form.locator('button[type=submit]').click()
        expect(page.locator('#modal')).not_to_be_visible()
        expect(page.locator(f'.staff-card[data-staff-id="{previous["id"]}"]')).to_contain_text('يلزم إسناد سيارة')
        expect(page.locator(f'.staff-card[data-staff-id="{target["id"]}"]')).to_contain_text('TEST UI 123')
        with db.tx() as c:
            assert c.execute('SELECT vehicle FROM users WHERE id=%s',(previous['id'],)).fetchone()['vehicle']=={}
            assert c.execute("SELECT vehicle->>'plate' plate FROM users WHERE id=%s",(target['id'],)).fetchone()['plate']=='TEST UI 123'
        assert not errors,errors
    finally:context.close()
