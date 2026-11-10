"""Chromium UI fixtures plus dedicated CRM real HTTP/PostgreSQL acceptance.

All records and external SMS responses are synthetic; no physical-device claim.
"""
import json
import os
from pathlib import Path

import pytest
from test_browser import chromium_browser
from tests.browser_sms_helpers import sms_browser_app

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Requires explicit real Chromium')


@pytest.mark.parametrize('language', ['ar', 'en'])
@pytest.mark.parametrize('width', [390, 768, 1280])
def test_integrated_cards_tables_and_customer_lookup(chromium_browser, language, width):
    """Render the production asset with long synthetic customer/staff/branch values."""
    from playwright.sync_api import expect
    page = chromium_browser.new_page(viewport={'width': width, 'height': 1050})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.set_content('<html lang="'+language+'" dir="'+('rtl' if language == 'ar' else 'ltr')+'"><body><main><p>Synthetic visual acceptance only / بيانات صناعية للعرض فقط</p><div id="fixture"></div><dialog id="modal" open></dialog></main></body></html>')
        page.add_style_tag(path=str(ROOT / 'public/ops.css'))
        page.add_style_tag(path=str(ROOT / 'public/assets/driver-hours.css'))
        page.add_script_tag(path=str(ROOT / 'public/assets/customer-order.js'))
        page.evaluate('''async language => {
          const tr=(ar,en)=>language==='ar'?ar:en;
          const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
          const long=tr('اسم عميل تجريبي طويل لاختبار ظهور جميع البيانات دون قص أو إخفاء النص','Long synthetic customer and branch name to verify complete readable wrapping');
          document.querySelector('#fixture').innerHTML=`<section class="hours-kpis">${Array.from({length:4},(_,i)=>`<article class="panel"><h2>${tr('ساعات العمل','Work hours')} ${i+1}</h2><strong>123.45</strong></article>`).join('')}</section><section class="panel staff-directory"><div class="table-wrap"><table><thead><tr><th>${tr('العميل','Customer')}</th><th>${tr('الفرع','Branch')}</th><th>${tr('ملاحظات','Notes')}</th><th>${tr('المبلغ','Amount')}</th></tr></thead><tbody><tr><td>${long}</td><td>${long}</td><td>${long.repeat(5)}</td><td class="money">12,345.67</td></tr></tbody></table></div></section>`;
          window.fixtureCalls=[];
          const api=async path=>{fixtureCalls.push(path);if(path==='/api/shifts/current')return {shifts:[{user_id:'TEST_MANAGER',kind:'manager',status:'open',branch_ids:['TEST_BRANCH']}]};if(path.startsWith('/api/catalog'))return {products:[]};if(path.startsWith('/api/orders/customer-lookup'))return {contacts:[{name:long,phone:'+966500000001',addresses:[{address:long,neighborhood:long,location:{lat:28.4,lng:45.9}}]}],ambiguous:false,verified:false};throw Error('Unexpected fixture path: '+path);};
          await KPSCustomerOrder.open({api,mutation:async()=>{throw Error('This visual fixture cannot write')},tr,esc,money:String,user:{id:'TEST_MANAGER',brand_id:'kps'},branches:[{id:'TEST_BRANCH',name:long}],modal:(title,html)=>{document.querySelector('#modal').innerHTML='<h2>'+esc(title)+'</h2>'+html},closeModal:()=>{},onCreated:()=>{}});
        }''', language)
        phone = page.locator('#staff-order-form [name=phone]')
        phone.fill('0500000001')
        choice = page.locator('.customer-address-choice')
        expect(choice).to_be_visible()
        # Stored contact never replaces entered values until explicit staff selection.
        expect(page.locator('[name=address]')).to_have_value('')
        choice.click()
        expect(page.locator('[name=lat]')).to_have_value('28.4')
        expect(page.locator('[name=lng]')).to_have_value('45.9')
        assert '+966500000001' in page.evaluate('fixtureCalls.find(x=>x.includes("customer-lookup"))') or '%2B966500000001' in page.evaluate('fixtureCalls.find(x=>x.includes("customer-lookup"))')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert page.locator('dialog').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1')
        screenshots=ROOT/'test-artifacts'
        screenshots.mkdir(exist_ok=True)
        page.screenshot(path=str(screenshots/f'parallel-customer-{language}-{width}.png'), full_page=True)
        page.locator('#modal').evaluate('(e)=>e.close()')
        assert page.locator('td').first.evaluate('(e)=>e.clientWidth>=220')
        assert page.locator('th').first.evaluate('(e)=>e.clientWidth>=220')
        assert page.locator('td').first.evaluate('(e)=>getComputedStyle(e).whiteSpace') == 'normal'
        assert page.locator('td').first.evaluate('(e)=>getComputedStyle(e).textOverflow') == 'clip'
        assert page.locator('td').first.evaluate('(e)=>e.scrollHeight<=e.clientHeight+1')
        if width == 1280:
            boxes=page.locator('.hours-kpis article').all()
            assert len({round(e.bounding_box()['y']) for e in boxes})==1
        page.screenshot(path=str(screenshots/f'parallel-cards-tables-{language}-{width}.png'), full_page=True)
        assert not errors
    finally:
        page.close()


@pytest.mark.postgres
@pytest.mark.parametrize('language', ['ar', 'en'])
@pytest.mark.parametrize('width', [390, 768, 1280])
def test_authorized_customer_records_real_http_postgres(sms_browser_app, chromium_browser, language, width):
    """Dedicated CRM page uses real scoped HTTP/DB reads and synthetic SMS sign-in.

    No request interception, fabricated sessions, external provider claim, or
    production data. Two forbidden records deliberately share the searched phone.
    """
    from playwright.sync_api import expect
    from app import db
    from test_browser import _context, _owner_login, _staff
    from tests.test_core import _make_user

    staff = _staff('branch_manager')
    contact = _make_user()
    phone = '+966500111222'
    name = ('اسم عميل صناعي طويل للتأكد من عرض سجل العميل كاملًا دون قص الحروف'
            if language == 'ar' else
            'Long synthetic customer name for complete readable customer records')
    address = ('عنوان صناعي طويل مع وصف المبنى والمدخل لاختبار عرض البيانات كاملة'
               if language == 'ar' else
               'Synthetic long delivery address with the building and entrance description')
    customer = {'name': name, 'phone': phone, 'address': address,
                'neighborhood': 'TEST ONLY district', 'location': {'lat': 28.4, 'lng': 45.9}}
    allowed_order = db.new_id('crm_visual_order_')
    with db.tx() as conn:
        conn.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city) VALUES('CRM_FORBIDDEN_BRANCH',9001,'CRM_FORBIDDEN_BRANCH','kps','TEST ONLY forbidden branch','TEST ONLY city')")
        conn.execute("INSERT INTO brands(id,name) VALUES('CRM_FORBIDDEN_BRAND','TEST ONLY other brand')")
        conn.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city) VALUES('CRM_OTHER_BRAND_BRANCH',1,'CRM_OTHER_BRAND_BRANCH','CRM_FORBIDDEN_BRAND','TEST ONLY other brand branch','TEST ONLY city')")
        conn.execute("INSERT INTO users(id,role,brand_id,display_name) VALUES('CRM_OTHER_BRAND_CONTACT','customer','CRM_FORBIDDEN_BRAND','TEST ONLY hidden brand contact')")
        records = [
            (allowed_order, 'kps', 'kps-branch-1', contact['id'], customer),
            (db.new_id('crm_hidden_branch_'), 'kps', 'CRM_FORBIDDEN_BRANCH', contact['id'],
             {**customer, 'name': 'SECRET_FORBIDDEN_BRANCH_CONTACT'}),
            (db.new_id('crm_hidden_brand_'), 'CRM_FORBIDDEN_BRAND', 'CRM_OTHER_BRAND_BRANCH',
             'CRM_OTHER_BRAND_CONTACT', {**customer, 'name': 'SECRET_FORBIDDEN_BRAND_CONTACT'}),
        ]
        for oid, brand, branch, cid, details in records:
            conn.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,%s,%s,%s,'delivery','new','pending','cash',1000,0,1000,%s,%s)",
                         (oid, brand, branch, cid, db.json({'items': [], 'order_source': 'delivery_center', 'order_reference': 'TEST-CRM-VISUAL-' + oid}), db.json(details)))
    context, page, errors = _context(chromium_browser, sms_browser_app)
    try:
        headers = _owner_login(context, sms_browser_app, staff)
        page.add_init_script(f"localStorage.setItem('kps_ops_lang','{language}')")
        page.set_viewport_size({'width': width, 'height': 1050})
        page.goto(sms_browser_app + '/ops', wait_until='networkidle')
        page.locator('#navigation [data-page=customers]').click()
        form = page.locator('#customer-records-search')
        expect(form).to_be_visible()
        assert form.locator('[name=branch_id] option[value="CRM_FORBIDDEN_BRANCH"]').count() == 0
        assert form.locator('[name=branch_id] option[value="CRM_OTHER_BRAND_BRANCH"]').count() == 0
        form.locator('[name=branch_id]').select_option('kps-branch-1')
        form.locator('[name=phone]').fill('0500111222')
        with page.expect_response(lambda response: '/api/orders/customer-search?' in response.url and response.request.method == 'GET') as looked_up:
            form.locator('button[type=submit]').click()
        response = looked_up.value
        assert response.status == 200
        payload = response.json()
        assert len(payload['contacts']) == 1 and payload['contacts'][0]['phone'] == phone
        assert payload['contacts'][0]['name'] == name
        assert payload['verified'] is False
        cards = page.locator('#customer-records-results article')
        expect(cards).to_have_count(1)
        expect(cards.locator('h2')).to_have_text(name)
        expect(cards).to_contain_text(address)
        expect(cards.locator('[data-customer-orders]')).to_be_visible()
        expect(page.locator('#customer-records-results')).not_to_contain_text('SECRET_')
        for branch in ('CRM_FORBIDDEN_BRANCH', 'CRM_OTHER_BRAND_BRANCH'):
            denied = context.request.get(sms_browser_app + '/api/orders/customer-lookup',
                                         headers=headers, params={'phone': phone, 'branch_id': branch})
            assert denied.status == 404
            assert 'SECRET_' not in denied.text()
        partial = context.request.get(sms_browser_app + '/api/orders/customer-lookup',
                                      headers=headers, params={'phone': '05001112', 'branch_id': 'kps-branch-1'})
        assert partial.status == 422
        geometry = cards.evaluate('''card=>{
            const title=card.querySelector('h2'),content=[title,...card.querySelectorAll('dd,button')];
            return {titleWidth:title.clientWidth,readable:content.every(e=>{
                const s=getComputedStyle(e);
                return s.textOverflow!=='ellipsis'&&e.scrollWidth<=e.clientWidth+1&&e.scrollHeight<=e.clientHeight+1;
            }),pageOverflow:document.documentElement.scrollWidth>innerWidth};
        }''')
        screenshots = ROOT / 'test-artifacts'
        screenshots.mkdir(exist_ok=True)
        page.screenshot(path=str(screenshots / f'parallel-crm-real-{language}-{width}.png'), full_page=True)
        assert geometry['titleWidth'] >= 140, geometry
        assert geometry['readable'] and not geometry['pageOverflow'], geometry
        # Follow the real card action into the authorized operational order screen.
        cards.locator('[data-customer-orders]').click()
        expect(page.locator('#filters-form [name=phone]')).to_have_value(phone)
        expect(page.locator(f'[data-order-id="{allowed_order}"]')).to_be_visible()
        expect(page.locator('#content')).not_to_contain_text('SECRET_')
        assert not errors, errors
    finally:
        context.close()
