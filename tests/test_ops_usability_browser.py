"""Focused real Chromium/HTTP/SMS-policy acceptance, synthetic data only."""
import os
import pytest
from app import db, sms_verification
from test_browser import ARTIFACTS, _staff, _context, _owner_login, chromium_browser
from tests.browser_sms_helpers import sms_browser_app as live_app, complete_owner_sms_dialog

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit real Chromium opt-in required')]


@pytest.mark.parametrize(('lang','width'),[('ar',390),('ar',768),('ar',1280),('en',390),('en',768),('en',1280)])
def test_problem_status_event_rows_and_conversation_cards(live_app,chromium_browser,lang,width):
    from playwright.sync_api import expect
    owner=_staff();context,page,errors=_context(chromium_browser,live_app)
    try:
        _owner_login(context,live_app,owner)
        context.add_init_script(f"localStorage.setItem('kps_ops_lang','{lang}')")
        row={'id':'synthetic-problem','service':'delivery','order_no':'TEST-PROBLEM-001',
             'branch_id':'kps-branch-1','customer':{'name':'عميل اختبار البلاغ','neighborhood':'حي الاختبار'},
             'preparation_status':'handed_over','delivery_status':'problem','payment_status':'unpaid',
             'created_at':'2026-10-18T12:00:00Z','total_halalas':25000,'snapshot':{'items':[]}}
        events=[{'event_type':'order.created','actor_name':'موظف اختبار','created_at':'2026-10-18T12:00:00Z','details':{}},
                {'event_type':'task.problem','actor_name':'سائق اختبار','created_at':'2026-10-18T12:01:00Z','details':{'previous':'picked_up','status':'problem','reason':'سبب صناعي طويل يظهر كاملًا دون قص أو تداخل مع الوقت والفرع'}}]
        page.route('**/api/orders?*',lambda r:r.fulfill(json={'orders':[row],'next_offset':None}))
        page.route('**/api/orders/synthetic-problem',lambda r:r.fulfill(json={'order':row,'events':events}))
        card={'id':'synthetic-chat','order_reference':'TEST-PROBLEM-001','branch_id':'kps-branch-1',
              'branch_name':'المروج','branch_no':1,'route':'delivery','status':'open','customer_name':'عميل اختبار البطاقات',
              'category':'delivery_problem','unread_count':2,'last_message':'رسالة اختبار <script>not executable</script>',
              'created_at':'2026-10-18T12:00:00Z'}
        page.route('**/api/conversations',lambda r:r.fulfill(json={'conversations':[card]}))
        page.route('**/api/conversations/synthetic-chat/messages?*',lambda r:r.fulfill(json={'conversation':card,'messages':[]}))
        page.set_viewport_size({'width':width,'height':950})
        page.goto(live_app+'/ops',wait_until='networkidle')
        page.locator('#navigation [data-page=orders]').click()
        status=page.locator('.order-table tbody td').last
        expect(status).to_contain_text('يتطلب إجراء مدير التوصيل' if lang=='ar' else 'Delivery manager action required')
        expect(status.locator('[data-action=resolve-order-problem]')).to_be_visible()
        page.locator('[data-action=order-detail][data-id=synthetic-problem]').click()
        page.locator('.order-detail-events summary').click()
        expect(page.locator('.order-detail-events')).not_to_contain_text('order.created')
        expect(page.locator('.order-detail-events')).to_contain_text(events[1]['details']['reason'])
        assert page.locator('#modal').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        page.screenshot(path=str(ARTIFACTS/f'event-rows-{lang}-{width}.png'),full_page=True)
        page.locator('#modal .modal-head button').click()
        page.locator('#navigation [data-page=chat]').click()
        expect(page.locator('.chat-layout')).to_have_class('chat-layout inbox-only')
        expect(page.locator('.conversation-customer')).to_have_text(card['customer_name'])
        expect(page.locator('.conversation-preview')).to_have_text(card['last_message'])
        assert page.locator('.conversation script').count()==0
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert page.locator('.conversation').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        page.screenshot(path=str(ARTIFACTS/f'conversation-cards-{lang}-{width}.png'),full_page=True)
        page.locator('.conversation').click()
        expect(page.locator('.conversation')).to_have_attribute('aria-pressed','true')
        expect(page.locator('#chat-detail')).to_be_visible()
        assert not errors,errors
    finally:context.close()


def test_mandatory_bell_controls_hidden_for_staff(live_app,chromium_browser):
    from playwright.sync_api import expect
    user=_staff('delivery_staff');context,page,errors=_context(chromium_browser,live_app)
    try:
        _owner_login(context,live_app,user)
        page.set_viewport_size({'width':390,'height':950})
        page.goto(live_app+'/ops',wait_until='networkidle')
        expect(page.locator('#operational-alerts')).to_be_visible()
        expect(page.locator('[data-action=alert-policy]')).to_have_count(0)
        expect(page.locator('[data-action=alert-sound]')).to_be_hidden()
        page.locator('#page-heading').click()
        expect(page.locator('#alert-audio-blocked')).to_be_hidden()
        page.screenshot(path=str(ARTIFACTS/'mandatory-bell-staff-ar-390.png'),full_page=True)
        assert not errors,errors
    finally:context.close()


def test_owner_bell_policy_saved_only_after_sms(live_app,chromium_browser):
    from playwright.sync_api import expect
    user=_staff();context,page,errors=_context(chromium_browser,live_app)
    try:
        _owner_login(context,live_app,user)
        page.goto(live_app+'/ops',wait_until='networkidle')
        page.locator('[data-action=alert-policy]').click()
        form=page.locator('#modal form')
        form.locator('[name=first_after_seconds]').fill('5')
        form.locator('[name=first_ring_seconds]').fill('2')
        form.locator('button[type=submit]').click()
        with db.tx() as c:
            assert 'alert_policy' not in c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings']
        complete_owner_sms_dialog(page,user)
        expect(page.locator('#modal')).not_to_be_visible()
        with db.tx() as c:
            policy=c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings']['alert_policy']
            assert policy['first_after_seconds']==5 and policy['first_ring_seconds']==2
        assert not errors,errors
    finally:context.close()


@pytest.mark.parametrize(('lang','width'),[('ar',390),('ar',768),('ar',1280),('en',390),('en',768),('en',1280)])
def test_order_table_full_text_and_responsive_layout(live_app,chromium_browser,lang,width):
    from playwright.sync_api import expect
    owner=_staff();context,page,errors=_context(chromium_browser,live_app)
    try:
        _owner_login(context,live_app,owner)
        context.add_init_script(f"localStorage.setItem('kps_ops_lang','{lang}')")
        row={'id':'synthetic-layout-order','order_no':'LAYOUT-001','branch_id':'kps-branch-1',
             'branch_name':'فرع اختبار باسم طويل لعرض النص كاملًا',
             'customer':{'name':'اسم عميل صناعي طويل لا يُقص من جدول الطلبات','neighborhood':'حي اختبار طويل لا ينبغي أن يتداخل مع المبلغ'},
             'driver_name':'اسم سائق صناعي طويل يبقى ظاهرًا بالكامل','total_halalas':25000,
             'created_at':'2026-10-18T12:00:00Z','preparation_status':'completed',
             'timings':{'preparation':{'seconds':8204,'state':'completed'},'delivery':{'seconds':7204,'state':'running'}}}
        page.route('**/api/orders?*',lambda route:route.fulfill(json={'orders':[row],'next_offset':None}))
        page.set_viewport_size({'width':width,'height':950})
        page.goto(live_app+'/ops',wait_until='networkidle')
        page.locator('#navigation [data-page=orders]').click()
        table=page.locator('.order-table')
        expect(table.locator('tbody tr')).to_have_count(1)
        expect(table).to_contain_text(row['customer']['name'])
        expect(table).to_contain_text(row['driver_name'])
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        for cell in table.locator('tbody td').all():
            assert cell.evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        page.screenshot(path=str(ARTIFACTS/f'order-table-{lang}-{width}.png'),full_page=True)
        assert not errors,errors
    finally:context.close()


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
