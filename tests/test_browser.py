"""Real HTTP/Chromium acceptance with an isolated PostgreSQL schema.

Run with TEST_BROWSER=1 and a local TEST_DATABASE_URL. Browser tests make no
intercepted or mocked API responses. The customer session used for the second
test is deliberately seeded in the isolated database, so this is not evidence
of a real SMS provider or a physical phone test. Uploaded pictures are preserved
source menu assets; the banner is clearly labelled test-only, without an offer.
"""
from __future__ import annotations

import json
import base64
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from app import auth, db

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'test-artifacts'
WIDTHS = [360, 390, 768, 1280, 1440]
PASSWORD = 'TestOnly!BrowserOwner-2026'
pytestmark = [pytest.mark.postgres,
              pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Browser acceptance requires explicit TEST_BROWSER=1; no physical-device claim.')]


def _staff(role='owner'):
    user_id = db.new_id('browser_test_')
    username = 'browser.' + secrets.token_hex(5)
    with db.tx() as conn:
        conn.execute('INSERT INTO users(id,username,display_name,password_hash,role,brand_id,branch_ids) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                     (user_id, username, 'اختبار متصفح فقط', auth.hash_password(PASSWORD), role, 'kps', db.json(['kps-branch-1'])))
    return {'id': user_id, 'username': username}


def _customer_session():
    user_id = db.new_id('browser_customer_')
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with db.tx() as conn:
        conn.execute("INSERT INTO users(id,display_name,role,brand_id,phone) VALUES(%s,%s,'customer','kps',%s)",
                     (user_id, 'عميل اختبار متصفح فقط', '+966500000001'))
        conn.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES(%s,%s,%s,%s,now()+interval '1 hour')",
                     (db.new_id('browser_session_'), auth.token_hash(token), auth.token_hash(csrf), user_id))
    return user_id, token, csrf


@pytest.fixture
def synthetic_mail(monkeypatch):
    # Encrypted local synthetic messages only; workers never contact a provider.
    monkeypatch.setenv('EMAIL_PROVIDER','resend')
    monkeypatch.setenv('EMAIL_FROM','sender@example.invalid')
    monkeypatch.setenv('RESEND_API_KEY','synthetic-never-dispatched')
    monkeypatch.setenv('MAIL_OUTBOX_KEY',base64.urlsafe_b64encode(b'W'*32).decode())
    monkeypatch.setenv('EMAIL_WORKER_ENABLED','false')


@pytest.fixture
def live_app(database, tmp_path, synthetic_mail):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env = {**os.environ, 'DATABASE_URL': database, 'APP_ENV': 'testing',
           'PUBLIC_BASE_URL': base, 'APP_ALLOWED_ORIGINS': base,
           'APP_ALLOWED_HOSTS': '127.0.0.1,localhost', 'SESSION_COOKIE_SECURE': 'false',
           'OTP_PROVIDER': 'disabled', 'INITIAL_CATALOG_IMPORT': 'true'}
    for name in ('TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_VERIFY_SERVICE_SID'):
        env.pop(name, None)
    log_path = tmp_path / 'browser-test-app.log'
    with log_path.open('w') as log:
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)],
                                   cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    pytest.fail('Isolated browser HTTP server did not start: ' + log_path.read_text()[-3000:])
                try:
                    if httpx.get(base + '/ready', timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                pytest.fail('Isolated browser HTTP server readiness timed out.')
            yield base
        finally:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture
def chromium_browser():
    # Missing Chromium is an explicit failure when this suite is requested.
    from playwright.sync_api import sync_playwright
    ARTIFACTS.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
        try:
            yield browser
        finally:
            browser.close()


def _context(browser, base, token=None, csrf=None):
    context = browser.new_context(base_url=base, locale='ar-SA', timezone_id='Asia/Riyadh',
                                  viewport={'width': 1280, 'height': 900}, device_scale_factor=1)
    if token:
        context.add_cookies([{'name': auth.COOKIE, 'value': token, 'url': base, 'httpOnly': True, 'sameSite': 'Strict'},
                             {'name': auth.CSRF_COOKIE, 'value': csrf, 'url': base, 'httpOnly': False, 'sameSite': 'Strict'}])
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    return context, page, errors


def _save_overflow_diagnostic(page,prefix,width,dimensions,dialogs):
    """Read-only geometry and viewport evidence before an overflow assertion."""
    outside=page.evaluate("""() => Array.from(document.querySelectorAll('*')).filter(element => {
        const style=getComputedStyle(element);
        return element.getClientRects().length && style.visibility !== 'hidden' && style.display !== 'none';
    }).map(element => {
        const rect=element.getBoundingClientRect();
        const classes=Array.from(element.classList);
        return {
            selector:element.tagName.toLowerCase()+(element.id?'#'+CSS.escape(element.id):'')+
                classes.slice(0,4).map(value=>'.'+CSS.escape(value)).join(''),
            id:element.id,class:element.getAttribute('class')||'',
            left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom,
            clientWidth:element.clientWidth,scrollWidth:element.scrollWidth,
            overflowX:getComputedStyle(element).overflowX,
            insideTableWrap:Boolean(element.closest('.table-wrap')),
            excess:Math.max(0,-rect.left,rect.right-innerWidth)
        };
    }).filter(element => element.excess>0).sort((a,b)=>b.excess-a.excess).slice(0,30)""")
    diagnostic={'test_only':True,'capture':prefix,'viewport':page.viewport_size,
                'document':dimensions,'visible_dialogs':dialogs,'outside_viewport_elements':outside,
                'environment':'Real Chromium browser emulation with isolated synthetic PostgreSQL fixtures',
                'physical_device_test':False,'auth_credentials_included':False}
    stem=f'TEST_ONLY_overflow_{prefix}-{width}'
    page.screenshot(path=str(ARTIFACTS/f'{stem}.png'),full_page=False)
    (ARTIFACTS/f'{stem}.json').write_text(json.dumps(diagnostic,ensure_ascii=False,indent=2)+'\n')


def _capture(page, prefix):
    for width in WIDTHS:
        page.set_viewport_size({'width': width, 'height': 900})
        page.wait_for_timeout(60)
        dimensions = page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth,background:getComputedStyle(document.body).backgroundColor})')
        # A table may scroll inside its own .table-wrap, but the modal itself
        # must fit its content without clipping or whole-dialog horizontal scroll.
        dialogs=page.evaluate("""() => Array.from(document.querySelectorAll('dialog')).filter(dialog =>
            dialog.getClientRects().length && getComputedStyle(dialog).visibility !== 'hidden').map(dialog => ({
                id:dialog.id,width:dialog.clientWidth,scroll:dialog.scrollWidth,
                left:dialog.getBoundingClientRect().left,right:dialog.getBoundingClientRect().right,
                overflow:getComputedStyle(dialog).overflowX
            }))""")
        if dimensions['scroll']>dimensions['width'] or any(dialog['scroll']>dialog['width'] for dialog in dialogs):
            _save_overflow_diagnostic(page,prefix,width,dimensions,dialogs)
        assert dimensions['scroll'] <= dimensions['width'], (prefix, width, dimensions)
        for dialog in dialogs:
            assert dialog['scroll'] <= dialog['width'], (prefix, width, 'dialog horizontal overflow', dialog)
        background = dimensions['background']
        channels=[int(value) for value in re.findall(r'\d+',background)[:3]]
        assert len(channels)==3 and min(channels)>=220, (prefix, background)
        page.screenshot(path=str(ARTIFACTS / f'{prefix}-{width}.png'), full_page=False)


def _owner_login(context, base, owner):
    login = context.request.post(base + '/api/auth/login', headers={'Origin': base},
                                 data={'username': owner['username'], 'password': PASSWORD})
    assert login.ok, login.text()
    return {'Origin': base, 'X-CSRF-Token': login.json()['csrf_token']}


def _test_banner(context, base, headers):
    catalog = json.loads((ROOT / 'data/catalog.json').read_text())
    product = next(product for product in catalog['products'] if product['id'] == 'kps-01')
    source = ROOT / 'public' / product['image_url'].lstrip('/')
    uploaded = context.request.post(base + '/api/admin/assets?purpose=banner',
                                    headers={**headers, 'Idempotency-Key': 'browser-banner-asset-0001'},
                                    multipart={'file': {'name': 'preserved-source-test.jpg', 'mimeType': 'image/jpeg', 'buffer': source.read_bytes()}})
    assert uploaded.ok, uploaded.text()
    response = context.request.post(base + '/api/admin/banners',
                                    headers={**headers, 'Idempotency-Key': 'browser-banner-test-only-0001'},
                                    data={'title': 'اختبار واجهة — بيانات تجريبية', 'body': 'لا يمثل عرضًا أو خصمًا للعملاء.',
                                          'button_label': 'تفاصيل الصنف', 'destination': {'kind': 'product', 'id': 'kps-33'},
                                          'desktop_asset_id': uploaded.json()['id'], 'active': True, 'branch_ids': [], 'sort_order': 1})
    assert response.ok, response.text()
    return response.json()['id']


def test_real_preview_responsive_colors_restriction_banner_and_missing_otp(live_app, chromium_browser):
    from playwright.sync_api import expect
    owner = _staff()
    owner_context, _, owner_errors = _context(chromium_browser, live_app)
    headers = _owner_login(owner_context, live_app, owner)
    banner_id = _test_banner(owner_context, live_app, headers)
    context, page, errors = _context(chromium_browser, live_app)
    try:
        page.goto('/')
        expect(page.locator('.product-card')).to_have_count(34)
        expect(page.locator('#branch-select option')).to_have_count(21)
        expect(page.locator('#catalog-notice')).not_to_be_visible()  # Final owner approval, branch operating data still unconfigured.
        expect(page.locator('#banner-content')).to_contain_text('اختبار واجهة — بيانات تجريبية')
        _capture(page, 'customer-preserved-source-preview-test-banner')

        # Required independent colors are tested on each original source mug.
        for mug_index in (0, 1):
            page.get_by_role('button', name='تفاصيل كوب حراري', exact=True).nth(mug_index).click()
            page.locator('#product-qty').fill('3')
            page.locator('#product-qty').dispatch_event('change')
            expect(page.locator('#product-option-hint')).to_contain_text('اللون لكل كوب')
            expect(page.locator('#apply-first-option')).to_contain_text('لون الكوب الأول')
            page.locator('#product-options select').nth(0).select_option('BLK')
            page.locator('#product-options select').nth(1).select_option('BLU')
            page.locator('#product-options select').nth(2).select_option('BLK')
            if mug_index == 0:
                _capture(page, 'customer-mug-independent-color-dialog')
            page.locator('#product-add').click()
            expect(page.locator('#product-dialog')).not_to_be_visible()
        expect(page.locator('.cart-line')).to_have_count(4)
        expect(page.locator('#checkout-button')).to_be_disabled()
        _capture(page, 'customer-source-approved-cart-branch-unconfigured')

        # Drinks retain flavour language and the original six choices.
        mojito = page.get_by_role('button', name='تفاصيل موهيتو', exact=True)
        mojito.click()
        expect(page.locator('#product-option-hint')).to_contain_text('النكهة')
        expect(page.locator('#product-options select option')).to_have_count(7)
        page.keyboard.press('Escape')
        expect(mojito).to_be_focused()

        expect(page.get_by_role('button', name='إضافة الخلية إلى السلة', exact=True)).to_be_disabled()
        page.locator('#branch-select').select_option('kps-branch-2')
        for variant in ('pecan', 'classic'):
            page.get_by_role('button', name='تفاصيل الخلية', exact=True).click()
            expect(page.locator('#product-add')).to_be_enabled()
            expect(page.locator('#product-options select')).to_have_value('')
            page.locator('#product-add').click()
            expect(page.locator('#product-error')).to_contain_text('حدد النوع')
            page.locator('#product-options select').select_option(variant)
            page.locator('#product-add').click()
            expect(page.locator('#product-dialog')).not_to_be_visible()
        branches = context.request.get(live_app + '/api/branches').json()['branches']
        outside = next(branch for branch in branches if branch['city'] == 'الخفجي')
        page.locator('#branch-select').select_option(outside['id'])
        expect(page.locator('#confirm-dialog')).to_be_visible()
        page.keyboard.press('Escape')
        expect(page.locator('#branch-select')).to_have_value('kps-branch-2')
        expect(page.locator('.cart-line-title').filter(has_text='خلية')).to_have_count(2)
        page.locator('#branch-select').select_option(outside['id'])
        page.locator('#confirm-submit').click()
        expect(page.locator('.cart-line-title').filter(has_text='خلية')).to_have_count(0)
        page.get_by_role('button', name='تفاصيل الصنف', exact=True).click()
        expect(page.locator('#product-restriction')).to_contain_text('حفر الباطن فقط')
        expect(page.locator('#product-add')).to_be_disabled()  # Banner cannot widen item availability.
        page.keyboard.press('Escape')

        page.locator('#account-button').click()
        # V5 hides unconfigured channels and offers the authorized guest path.
        expect(page.locator('#guest-form')).to_be_visible()
        expect(page.locator('#otp-request-form')).not_to_be_visible()
        expect(page.locator('#email-login-button')).not_to_be_visible()
        expect(page.locator('#otp-verify-form')).not_to_be_visible()
        rejected=context.request.post(live_app+'/api/auth/otp/request',headers={'Origin':live_app},data={'phone':'0500000000','brand_id':'kps'})
        assert rejected.status==503
        page.keyboard.press('Escape')
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) AS n FROM orders').fetchone()['n'] == 0
            assert conn.execute("SELECT count(*) AS n FROM users WHERE role='customer'").fetchone()['n'] == 0

        disabled = owner_context.request.patch(live_app + '/api/admin/banners/' + banner_id,
                                              headers={**headers, 'Idempotency-Key': 'browser-banner-disable-0001'}, data={'active': False})
        assert disabled.ok, disabled.text()
        page.reload()
        expect(page.locator('#banner-content')).to_contain_text('اطلب مباشرة من KPS COFFEE')
        expect(page.locator('#banner-content')).not_to_contain_text('اختبار واجهة')
        assert not errors and not owner_errors, (errors, owner_errors)
    finally:
        context.close()
        owner_context.close()


def test_real_customer_car_order_chat_images_and_owner_operations(live_app, chromium_browser):
    from playwright.sync_api import expect
    # These verification flags and hours are explicit synthetic test setup,
    # applied only to this fixture's disposable PostgreSQL schema.
    with db.tx() as conn:
        conn.execute("UPDATE products SET settings=settings||'{\"source_verified\":true}'::jsonb")
        conn.execute('UPDATE branches SET settings=settings||%s WHERE id=%s',
                     (db.json({'pickup_enabled': True, 'car_enabled': True, 'ordering_verified': True,
                               'opening_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
                               'payment_methods': ['cash', 'card']}), 'kps-branch-1'))
    customer_id, token, csrf = _customer_session()
    context, page, errors = _context(chromium_browser, live_app, token, csrf)
    owner_context = None
    try:
        page.goto('/')
        expect(page.locator('.product-card')).to_have_count(34)
        # Persisted general contact uses real HTTP even while this branch is closed
        # for orders. This seeded test session proves UI scope, not SMS delivery.
        page.locator('#legal-contact-button').click()
        page.locator('#public-contact-support').click()
        expect(page.locator('#contacts-dialog')).to_be_visible()
        page.locator('#contact-service').select_option('delivery')
        page.locator('#contact-branch').select_option('kps-branch-5')
        page.locator('#contact-start').click()
        expect(page.locator('#chat-dialog')).to_be_visible()
        page.locator('#chat-text').fill('تواصل اختبار محفوظ دون طلب مع فريق التوصيل')
        page.locator('#send-message').click()
        expect(page.locator('.chat-bubble')).to_contain_text('تواصل اختبار محفوظ دون طلب')
        with db.tx() as conn:
            general=conn.execute('SELECT * FROM conversations WHERE customer_id=%s AND order_id IS NULL',(customer_id,)).fetchone()
            assert general['branch_id']=='kps-branch-5' and general['context_service']=='delivery' and general['route']=='delivery'
        page.reload()
        expect(page.locator('#account-button')).to_have_text('حسابي')
        page.locator('#legal-contact-button').click()
        page.locator('#public-contact-support').click()
        expect(page.locator('#contacts-list [data-conversation-id]')).to_have_count(1)
        _capture(page,'customer-persisted-no-order-inbox')
        page.locator(f'#contacts-list [data-conversation-id="{general["id"]}"]').click()
        expect(page.locator('.chat-bubble')).to_contain_text('تواصل اختبار محفوظ دون طلب')
        _capture(page,'customer-own-no-order-delivery-chat')
        page.keyboard.press('Escape')
        page.locator('#branch-select').select_option('kps-branch-1')
        page.get_by_role('button', name='تفاصيل كوب حراري', exact=True).first.click()
        page.locator('#product-qty').fill('2')
        page.locator('#product-qty').dispatch_event('change')
        page.locator('#product-options select').nth(0).select_option('BLK')
        page.locator('#product-options select').nth(1).select_option('BLU')
        page.locator('#product-add').click()
        expect(page.locator('#product-dialog')).not_to_be_visible()
        page.locator('input[name=service][value=car]').check()
        page.locator('#car-model').fill('سيارة اختبار')
        page.locator('#car-color').fill('أبيض')
        page.locator('#car-plate').fill('TEST-ONLY')
        page.locator('#customer-name').fill('عميل اختبار متصفح فقط')
        page.locator('#order-notes').fill('طلب تجريبي معزول؛ لا يُجهز أو يُسلّم فعليًا.')
        expect(page.locator('#checkout-button')).to_be_enabled()
        page.locator('#checkout-button').click()
        expect(page.locator('#confirm-dialog')).to_be_visible()
        _capture(page, 'customer-review-synthetic-verified-car-order')
        page.locator('#confirm-submit').click()
        expect(page.locator('#orders-dialog')).to_be_visible()
        expect(page.locator('#order-detail')).to_contain_text('أسود')
        expect(page.locator('#order-detail')).to_contain_text('أزرق')
        with db.tx() as conn:
            orders = conn.execute('SELECT * FROM orders WHERE customer_id=%s', (customer_id,)).fetchall()
            assert len(orders) == 1
            saved = orders[0]
            assert [item['units'][0]['options']['COLOR'] for item in saved['snapshot']['items']] == ['BLK', 'BLU']
            assert saved['service'] == 'car' and saved['delivery_fee_halalas'] == 0
        page.get_by_role('button', name='وصلت إلى الفرع', exact=True).click()
        expect(page.get_by_role('button', name='وصلت إلى الفرع', exact=True)).to_have_count(0)
        with db.tx() as conn:
            assert conn.execute('SELECT arrival_at FROM orders WHERE id=%s', (saved['id'],)).fetchone()['arrival_at']
            assert conn.execute('SELECT count(*) AS n FROM driver_tasks WHERE order_id=%s', (saved['id'],)).fetchone()['n'] == 0

        page.get_by_role('button', name='محادثة الطلب', exact=True).click()
        expect(page.locator('#chat-dialog')).to_be_visible()
        page.locator('#chat-text').fill('رسالة اختبار محفوظة للفرع')
        page.locator('#send-message').click()
        expect(page.locator('.chat-bubble')).to_contain_text('رسالة اختبار محفوظة للفرع')
        source_image = next((ROOT / 'public/assets').glob('kps-01-*.jpg'))
        page.locator('#chat-image').set_input_files(str(source_image))
        page.locator('#chat-text').fill('صورة اختبار من المصدر المعتمد')
        page.locator('#send-message').click()
        expect(page.locator('.chat-bubble img')).to_have_count(1)
        page.locator('.chat-bubble img').wait_for(state='visible')
        page.wait_for_function("() => { const img=document.querySelector('.chat-bubble img'); return img&&img.complete&&img.naturalWidth>0; }")
        _capture(page, 'customer-own-persisted-chat-source-image')
        with db.tx() as conn:
            conversation = conn.execute('SELECT * FROM conversations WHERE order_id=%s', (saved['id'],)).fetchone()
            assert conversation['route'] == 'branch'
            assert conn.execute('SELECT count(*) AS n FROM chat_messages WHERE conversation_id=%s', (conversation['id'],)).fetchone()['n'] == 2
            assert conn.execute('SELECT count(*) AS n FROM chat_attachments WHERE conversation_id=%s', (conversation['id'],)).fetchone()['n'] == 1
        page.keyboard.press('Escape')
        page.reload()
        # Click immediately after reload: restored identity must be ready before
        # this control can send a signed-in customer into the guest dialog.
        page.locator('#orders-button').click()
        expect(page.locator('#orders-dialog')).to_be_visible()
        expect(page.locator('#account-dialog')).not_to_be_visible()
        page.get_by_role('button', name='عرض الطلب ومحادثته', exact=True).click()
        page.get_by_role('button', name='محادثة الطلب', exact=True).click()
        expect(page.locator('.chat-bubble')).to_have_count(2)
        expect(page.locator('.chat-bubble img')).to_have_count(1)

        owner = _staff()
        owner_context, ops, owner_errors = _context(chromium_browser, live_app)
        ops.goto('/ops.html')
        ops.locator('#login-form input[name=username]').fill(owner['username'])
        ops.locator('#login-form input[name=password]').fill(PASSWORD)
        ops.locator('#login-submit').click()
        expect(ops.locator('#app-shell')).to_be_visible()
        expect(ops.locator('#content .metrics')).to_be_visible()
        _capture(ops, 'operations-dashboard-synthetic-order')
        ops.locator('#navigation [data-page=admin]').click()
        expect(ops.locator('[data-action=admin-tab][data-tab=banners]')).to_be_visible()
        ops.locator('[data-action=admin-tab][data-tab=banners]').click()
        expect(ops.locator('[data-action=create-banner]')).to_be_visible()
        _capture(ops, 'operations-banner-administration')
        ops.locator('[data-action=create-banner]').click()
        expect(ops.locator('#modal')).to_be_visible()
        banner_title='اختبار إدارة البنر — دون عرض تجاري'
        ops.locator('#banner-form input[name=title]').fill(banner_title)
        ops.locator('#banner-form textarea[name=body]').fill('بيانات اختبار معزولة؛ لا خصم ولا سعر جديد.')
        ops.locator('#banner-form input[name=button_label]').fill('تفاصيل الصنف')
        ops.locator('#banner-form select[name=destination]').select_option('product:kps-33')
        ops.locator('#banner-form input[name=desktop_image]').set_input_files(str(source_image))
        ops.locator('#banner-form input[name=active]').check()
        ops.locator('#banner-form input[name="banner:kps-branch-1"]').check()
        now=datetime.now(ZoneInfo('Asia/Riyadh'))
        ops.locator('#banner-form input[name=starts]').fill((now-timedelta(minutes=30)).strftime('%Y-%m-%dT%H:%M'))
        ops.locator('#banner-form input[name=ends]').fill((now+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'))
        expect(ops.locator('#desktop-preview')).to_contain_text(banner_title)
        expect(ops.locator('#mobile-preview')).to_contain_text(banner_title)
        _capture(ops, 'operations-banner-editor')
        ops.locator('#banner-form button[type=submit]').click()
        expect(ops.locator('#modal')).not_to_be_visible()
        expect(ops.locator('#content')).to_contain_text(banner_title)
        assert len(context.request.get(live_app+'/api/banners?brand_id=kps&branch_id=kps-branch-1').json()['banners'])==1
        assert context.request.get(live_app+'/api/banners?brand_id=kps&branch_id=kps-branch-5').json()['banners']==[]
        ops.locator('[data-action=edit-banner]').first.click()
        expect(ops.locator('#modal')).to_be_visible()
        ops.keyboard.press('Escape')
        expect(ops.locator('#modal')).not_to_be_visible()
        ops.locator('[data-action=edit-banner]').first.click()
        ops.locator('#banner-form input[name=active]').uncheck()
        ops.locator('#banner-form button[type=submit]').click()
        expect(ops.locator('#modal')).not_to_be_visible()
        assert context.request.get(live_app+'/api/banners?brand_id=kps&branch_id=kps-branch-1').json()['banners']==[]
        assert not errors and not owner_errors, (errors, owner_errors)
        (ARTIFACTS / 'browser-evidence-metadata.json').write_text(json.dumps({
            'environment': 'Headless Chromium browser emulation, real local FastAPI HTTP and isolated PostgreSQL',
            'physical_device_test': False, 'external_sms_provider_tested': False,
            'network_api_interception': False, 'screenshots_widths_px': WIDTHS,
            'data': 'Preserved KPS source catalog; explicitly synthetic test users, verification flags, car order, chat and test-only banner',
        }, ensure_ascii=False, indent=2))
    finally:
        context.close()
        if owner_context:
            owner_context.close()


def _role_fixture():
    """Explicitly synthetic second brand: no current KPS prices or scopes changed."""
    brand='browser-ops-test'
    branch_id='browser-ops-branch-test'
    users={}
    with db.tx() as conn:
        conn.execute('INSERT INTO brands(id,name) VALUES(%s,%s)',(brand,'براند اختبار متصفح معزول'))
        conn.execute('INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES(%s,1,%s,%s,%s,%s,%s)',
                     (branch_id,'browser-synthetic-source',brand,'فرع اختبار معزول','حفر الباطن',db.json({})))
        conn.execute('INSERT INTO app_settings(brand_id,settings) VALUES(%s,%s)',
                     (brand,db.json({'operational_day_start':'05:30','late_after_minutes':60})))
        for name,role in [('owner','owner'),('manager','delivery_manager'),('driver','driver'),('branch','branch_manager'),('accountant','admin'),('customer','customer')]:
            identifier='browser-ops-'+name
            username='browser.ops.'+name if name!='customer' else None
            conn.execute('INSERT INTO users(id,username,display_name,password_hash,role,brand_id,branch_ids) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                         (identifier,username,'اختبار '+name+' فقط',auth.hash_password(PASSWORD) if username else None,role,brand,db.json([branch_id])))
            users[name]={'id':identifier,'username':username}
            if name=='accountant':
                conn.execute('UPDATE users SET permissions=%s WHERE id=%s',
                             (db.json(['accounting_reports','accounting_review']),identifier))
        order_id='browser-ops-test-order'
        snapshot={'items':[{'product_id':'browser-synthetic-coffee','name':'قهوة اختبار معزول','quantity':1,
                            'unit_price_halalas':1000,'line_total_halalas':1000,'units':[],'note':''}],
                  'branch_name':'فرع اختبار معزول','notes':'اختبار فقط؛ لا يُجهز أو يُرسل سائق فعلي.'}
        conn.execute('INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,'
                     'subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,%s,%s,%s,\'delivery\',\'ready\',\'pending\',\'cash\',1000,0,1000,%s,%s)',
                     (order_id,brand,branch_id,users['customer']['id'],db.json(snapshot),db.json({'name':'عميل اختبار فقط','address':'عنوان اختبار معزول','location':{'lat':28.4,'lng':45.9}})))
    return users,branch_id,order_id


def _staff_ui_login(page,user):
    from playwright.sync_api import expect
    page.goto('/ops.html')
    page.locator('#login-form input[name=username]').fill(user['username'])
    page.locator('#login-form input[name=password]').fill(PASSWORD)
    page.locator('#login-submit').click()
    expect(page.locator('#app-shell')).to_be_visible()
    expect(page.locator('#content .loading')).to_have_count(0)


def _authenticated_post(context,base,path,body=None):
    session=context.request.get(base+'/api/session').json()
    response=context.request.post(base+path,headers={'Origin':base,'X-CSRF-Token':session['csrf_token'],'Idempotency-Key':secrets.token_hex(16)},data=body or {})
    assert response.ok,response.text()
    return response.json()


def _acknowledge_driver_end(page, language='ar'):
    from playwright.sync_api import expect
    title = 'انتهى دوامك بنجاح' if language=='ar' else 'Your shift has ended'
    expect(page.locator('#modal').get_by_role('heading', name=title, exact=True)).to_be_visible()
    expect(page.locator('#closure-form')).to_have_count(0)
    done = page.locator('#modal [data-action=driver-end-done]')
    expect(done).to_have_count(1)
    done.click()
    expect(page.locator('#modal')).not_to_be_visible()
    expect(page.locator('#navigation [data-page=tasks]')).to_have_attribute('aria-current', 'page')


def _submit_modal(page,values=None,check=None,*,driver_end=False,manager_operational=False):
    from playwright.sync_api import expect
    for name,value in (values or {}).items():
        field=page.locator(f'#modal-body [name="{name}"]')
        if field.evaluate('(el)=>el.tagName')=='SELECT':field.select_option(value)
        else:field.fill(value)
    for name in check or []:page.locator(f'#modal-body input[name="{name}"]').check()
    page.locator('#modal-body button[type=submit]').click()
    if driver_end:
        _acknowledge_driver_end(page)
    if manager_operational:
        # Manager submission opens a distinct operational-review form. The
        # recipient selector is intentionally hidden; it creates no owner
        # custody receipt or financial approval.
        expect(page.locator('#closure-form')).to_have_count(0)
        expect(page.locator('#modal-body')).to_contain_text('المطابقة المالية لا تغيّر تقفيلتك الأصلية')
        page.locator('#modal-body input[name=confirmed]').check()
        page.locator('#modal-body button[type=submit]').click()
    expect(page.locator('#modal')).not_to_be_visible()


def _match_manager_finances(page,base,context,report_id,bank,cash):
    """Verify independent bank/cash review using real accounting controls."""
    from playwright.sync_api import expect
    with db.tx() as conn:
        before=conn.execute('SELECT snapshot,pdf_data,csv_data FROM closure_reports WHERE id=%s',(report_id,)).fetchone()
        assert before['snapshot']['shift']['submission']['operational_approved'] is True
        assert not conn.execute('SELECT 1 FROM manager_financial_matches WHERE report_id=%s',(report_id,)).fetchone()
    page.locator('#navigation [data-page=accounting]').click()
    page.locator('[data-ac=matching]').click()
    action=page.locator(f'[data-match-id="{report_id}"]')
    expect(action).to_be_visible()
    expect(action.locator('xpath=ancestor::tr')).to_contain_text('بانتظار المطابقة المالية')
    action.click()
    for name in ('bank','cash'):
        expect(page.locator(f'#modal-body [name={name}]')).to_have_value('')
    matching_url=base+'/api/accounting/manager-closures/'+report_id+'/match'
    with page.expect_response(lambda response:response.request.method=='POST' and response.url==matching_url) as matched:
        _submit_modal(page,{'bank':f'{bank/100:.2f}','cash':f'{cash/100:.2f}',
                           'bank_reference':'TEST ONLY BANK STATEMENT',
                           'cash_reference':'TEST ONLY ACCOUNTANT CASH RECEIPT'})
    assert matched.value.status==200
    request=matched.value.request
    # Request.headers is Playwright's reduced header map; use the actual wire
    # headers so APIRequestContext replays the browser's Origin and CSRF proof.
    headers={key:value for key,value in request.all_headers().items()
             if key in ('origin','x-csrf-token','idempotency-key','content-type')}
    headers['origin']=base
    assert headers.get('x-csrf-token') and headers.get('idempotency-key')
    repeated=context.request.post(matching_url,headers=headers,data=request.post_data_json)
    assert repeated.status==200,repeated.text()
    assert repeated.json()==matched.value.json()
    duplicated=context.request.post(matching_url,headers={**headers,'idempotency-key':secrets.token_hex(16)},data=request.post_data_json)
    assert duplicated.status==409,duplicated.text()
    assert duplicated.json()['detail']['code']=='ALREADY_MATCHED'
    expect(page.locator('#ac-results')).to_contain_text('معتمدة ماليًا — مطابقة')
    expect(page.locator(f'[data-match-id="{report_id}"]')).to_have_count(0)
    rows=context.request.get(base+'/api/accounting/manager-closures').json()['closures']
    saved=next(row for row in rows if row['id']==report_id)
    assert saved['financial_status']=='matched'
    assert saved['bank_halalas']==bank and saved['cash_halalas']==cash
    assert saved['expected_bank_halalas']==bank and saved['expected_cash_halalas']==cash
    with db.tx() as conn:
        matches=conn.execute('SELECT * FROM manager_financial_matches WHERE report_id=%s',(report_id,)).fetchall()
        assert len(matches)==1 and matches[0]['status']=='matched'
        assert matches[0]['reviewer_id']!=before['snapshot']['shift']['user_id']
        assert conn.execute('SELECT snapshot,pdf_data,csv_data FROM closure_reports WHERE id=%s',(report_id,)).fetchone()==before


def _inspection_payload(km=1000,fuel=75):
    """Explicit synthetic inspection for journeys that open a driver shift."""
    from app.shift_vehicle import CHECKS
    return {'odometer_km':km,'fuel_percent':fuel,'checks':{key:'ok' for key in CHECKS},'notes':''}


def _fill_closure_inspection(page,km=1025,fuel=50):
    """Use the driver's required ending-inspection controls before submission."""
    from app.shift_vehicle import CHECKS
    form=page.locator('#blind-closure-form')
    form.locator('[name=inspection_odometer]').fill(str(km))
    form.locator('[name=inspection_fuel]').fill(str(fuel))
    for key in CHECKS:form.locator('[name=inspection_'+key+']').select_option('ok')


def _attach_task_photo(page):
    """Upload labelled synthetic evidence through the required real photo UI."""
    from io import BytesIO
    from PIL import Image, ImageDraw
    image=Image.new('RGB',(320,160),'white')
    ImageDraw.Draw(image).text((24,64),'TEST ONLY - NO REAL DELIVERY',fill='black')
    encoded=BytesIO();image.save(encoded,format='PNG')
    page.locator('#modal-body [name=photo]').set_input_files(
        {'name':'TEST_ONLY_delivery.png','mimeType':'image/png','buffer':encoded.getvalue()})


def test_real_all_staff_roles_driver_manager_closures_and_saved_report_files(live_app,chromium_browser):
    """Browser-operated assignment, delivery, receipt and final approvals, no API interception."""
    from playwright.sync_api import expect
    users,branch_id,order_id=_role_fixture()
    contexts={}
    pages={}
    errors=[]
    csp_errors=[]
    try:
        for name in ['owner','manager','driver','branch','accountant']:
            context,page,page_errors=_context(chromium_browser,live_app)
            contexts[name]=context;pages[name]=page
            page.on('console',lambda message:csp_errors.append(message.text) if ('Content Security Policy' in message.text or 'Refused to execute inline' in message.text) else None)
            _staff_ui_login(page,users[name])
            errors.append(page_errors)
        owner,manager,driver,branch=[pages[name] for name in ['owner','manager','driver','branch']]
        assert manager.locator('#navigation [data-page=admin]').count()==0
        assert branch.locator('#navigation [data-page=admin]').count()==0
        assert driver.locator('#navigation [data-page=dashboard]').count()==0
        assert driver.locator('#navigation [data-page=orders]').count()==0
        expect(driver.locator('#navigation [data-page=tasks]')).to_be_visible()

        manager_shift=_authenticated_post(contexts['manager'],live_app,'/api/shifts',{'kind':'manager','branch_ids':[branch_id]})['shift']['id']
        driver_shift=_authenticated_post(contexts['driver'],live_app,'/api/shifts',{'kind':'driver','branch_ids':[branch_id],'manager_shift_id':manager_shift,'vehicle_inspection':_inspection_payload()})['shift']['id']
        manager.locator('#navigation [data-page=orders]').click()
        manager.locator(f'[data-action=order-detail][data-id="{order_id}"]').click()
        manager.locator(f'[data-action=assign][data-id="{order_id}"]').click()
        _submit_modal(manager,{'driver_shift_id':driver_shift})
        _capture(manager,'delivery-manager-live-synthetic-orders')
        driver.locator('#navigation [data-page=tasks]').click()
        accept=driver.locator('[data-action=task-transition][data-status=accepted]')
        expect(accept).to_be_visible()
        # Let two real background polls complete. Re-rendered headings must
        # retain each action once in the simplified driver tools.
        for _ in range(2):
            with driver.expect_response(lambda response:response.url==live_app+'/api/tasks' and response.status==200,timeout=20000):
                expect(driver.locator('.driver-tools [data-action=driver-report]')).to_have_count(1)
                expect(driver.locator('.driver-tools [data-action=driver-vehicle]')).to_have_count(1)
            expect(driver.locator('#content')).to_have_attribute('aria-busy','false')
            expect(driver.locator('.driver-tools [data-action=driver-report]')).to_have_count(1)
            expect(driver.locator('.driver-tools [data-action=driver-vehicle]')).to_have_count(1)
        _capture(driver,'driver-assigned-synthetic-task')
        with driver.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/transition')) as acceptance:
            accept.click()
        assert acceptance.value.status==200,acceptance.value.text()
        assert acceptance.value.json()['task']['status']=='accepted'
        with db.tx() as conn:
            assert conn.execute('SELECT status FROM driver_tasks WHERE order_id=%s',(order_id,)).fetchone()['status']=='accepted'
            assert conn.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='task.accepted'",(order_id,)).fetchone()['n']==1
        expect(driver.locator('[data-action=task-transition][data-status=picked_up]')).to_be_visible()
        branch.locator('#navigation [data-page=orders]').click()
        branch.locator(f'[data-action=order-detail][data-id="{order_id}"]').click()
        expect(branch.locator(f'[data-action=prepare][data-id="{order_id}"][data-status=handed_over]')).to_be_visible()
        _capture(branch,'branch-ready-synthetic-order')
        branch.locator(f'[data-action=prepare][data-id="{order_id}"][data-status=handed_over]').click()
        expect(branch.locator(f'[data-action=prepare][data-id="{order_id}"][data-status=handed_over]')).not_to_be_visible()
        driver.locator('[data-action=task-transition][data-status=picked_up]').click()
        _attach_task_photo(driver)
        _submit_modal(driver)
        expect(driver.locator('[data-action=collect]')).to_be_visible()
        driver.locator('[data-action=collect]').click()
        driver.locator('#modal-body input[name=tendered]').fill('20.00')
        expect(driver.locator('#change-preview')).to_contain_text('١٠')
        _capture(driver,'driver-actual-cash-collection-and-change')
        _submit_modal(driver,check=['confirmed'])
        driver.locator('[data-action=delivered]').click()
        _attach_task_photo(driver)
        _submit_modal(driver,{'proof':'إثبات تسليم اختبار معزول؛ لا يوجد تسليم فعلي.'})
        with db.tx() as conn:
            collection=conn.execute('SELECT * FROM collections WHERE order_id=%s',(order_id,)).fetchone()
            assert collection['amount_halalas']==1000 and collection['tendered_halalas']==2000 and collection['change_halalas']==1000
            assert conn.execute('SELECT delivery_status FROM orders WHERE id=%s',(order_id,)).fetchone()['delivery_status']=='delivered'
            photos=conn.execute('SELECT stage,used_at FROM delivery_photos WHERE order_id=%s ORDER BY stage',(order_id,)).fetchall()
            assert len(photos)==2 and {row['stage'] for row in photos}=={'picked_up','delivered'} and all(row['used_at'] for row in photos)

        driver.locator('#navigation [data-page=shifts]').click()
        driver.locator(f'.shift-card [data-action=start-closing][data-id="{driver_shift}"]').click()
        driver.locator('[data-confirm-driver-close]').click()
        expect(driver.locator('#blind-closure-form')).to_be_visible()
        expect(driver.locator('#modal-body')).not_to_contain_text('الرصيد المتوقع')
        expect(driver.locator('#modal-body')).to_contain_text('إجمالي الكاش شامل العهدة')
        driver.locator('#blind-closure-form input[name=counted]').fill('10.00')
        driver.locator('#blind-closure-form input[name=terminal_total]').fill('0')
        expect(driver.locator('#blind-closure-form [name=recipient_user_id]')).to_have_count(0)
        _fill_closure_inspection(driver)
        expect(driver.locator('#closure-difference')).to_have_count(0)
        _capture(driver,'driver-counted-cash-closure-review')
        _submit_modal(driver,check=['confirm_closure'],driver_end=True)

        # This is the receiving manager's own authenticated browser, not a sender override.
        manager.locator('#navigation [data-page=custody]').click()
        expect(manager.locator('[data-action=confirm-custody]')).to_have_count(1)
        _capture(manager,'delivery-manager-pending-driver-receipt')
        manager.locator('[data-action=confirm-custody]').click()
        _submit_modal(manager,check=['received'])
        manager.locator('#navigation [data-page=shifts]').click()
        manager.locator(f'[data-action=shift-detail][data-id="{driver_shift}"]').click()
        expect(manager.locator('#modal-body')).to_contain_text('التسليم النهائي المستلم المؤكد')
        manager.locator(f'[data-action=approve-shift][data-id="{driver_shift}"]').click()
        expect(manager.locator('#modal-body')).to_contain_text('النقد المستلم فعليًا من السائق')
        expect(manager.locator('#modal-body')).to_contain_text('النقد الذي عده السائق')
        _capture(manager,'delivery-manager-driver-closure-approval')
        _submit_modal(manager,check=['confirmed'])
        with db.tx() as conn:
            assert conn.execute('SELECT status FROM shifts WHERE id=%s',(driver_shift,)).fetchone()['status']=='closed'

        manager.locator(f'[data-action=start-closing][data-id="{manager_shift}"]').click()
        expect(manager.locator('#closure-form')).to_be_visible()
        manager.locator('#closure-form input[name=counted]').fill('10.00')
        manager.locator('#closure-form input[name=handover]').fill('10.00')
        manager.locator('#closure-form input[name=reference]').fill('BROWSER-TEST-MANAGER-BAG')
        expect(manager.locator('#closure-form select[name=recipient_user_id]')).not_to_be_visible()
        _capture(manager,'delivery-manager-counted-final-closure')
        _submit_modal(manager,check=['confirm_closure'],manager_operational=True)
        with db.tx() as conn:
            closed=conn.execute('SELECT status,submission FROM shifts WHERE id=%s',(manager_shift,)).fetchone()
            assert closed['status']=='closed' and closed['submission']['operational_approved'] is True
            assert not conn.execute('SELECT 1 FROM custody_transfers WHERE from_shift_id=%s',(manager_shift,)).fetchone()
            manager_report=conn.execute('SELECT id FROM closure_reports WHERE shift_id=%s',(manager_shift,)).fetchone()['id']
        owner.locator('#navigation [data-page=shifts]').click()
        owner.locator('[data-action=owner-shift-view][data-view=history]').click()
        owner.locator(f'[data-action=shift-detail][data-id="{manager_shift}"]').click()
        expect(owner.locator('#modal-body')).to_contain_text('معتمدة تشغيليًا')
        expect(owner.locator('#modal-body [data-action=approve-shift]')).to_have_count(0)
        _capture(owner,'owner-operationally-approved-manager-closure')
        owner.keyboard.press('Escape')
        _match_manager_finances(pages['accountant'],live_app,contexts['accountant'],manager_report,0,1000)

        # Both reports are really generated after committed, approved closures.
        saved={}
        expect(driver.locator('#navigation [data-page=reports]')).to_have_count(0)
        assert contexts['driver'].request.get(live_app+'/api/reports').json()['reports']==[]
        for kind,who,shift in [('driver','manager',driver_shift),('manager','manager',manager_shift)]:
            actor=pages[who]
            actor.locator('#navigation [data-page=reports]').click()
            report_rows=contexts[who].request.get(live_app+'/api/reports').json()['reports']
            report_id=next(report['id'] for report in report_rows if report['shift_id']==shift)
            actor.locator(f'[data-action=report-view][data-id="{report_id}"]').click()
            expect(actor.locator('#modal-body')).to_contain_text('المتوقع')
            expect(actor.locator('#modal-body')).to_contain_text('المعدود')
            expect(actor.locator('#modal-body')).to_contain_text('التسليم المؤكد')
            _capture(actor,f'{kind}-saved-financial-report-synthetic')
            detail=contexts[who].request.get(live_app+f'/api/reports/{report_id}').json()['report']
            assert detail['pdf_status']=='ready'
            # This exact authenticated API snapshot matches the PDF and CSV
            # fetched below; all actors/orders are isolated synthetic fixtures.
            (ARTIFACTS/f'TEST_ONLY_{kind}_report_snapshot.json').write_text(
                json.dumps(detail['snapshot'],ensure_ascii=False,indent=2)+'\n')
            summary=detail['snapshot']['summary']
            assert summary['unique_orders']==1 and summary['cash_collected_halalas']==1000
            assert summary['submission']['expected_halalas']==1000 and summary['submission']['counted_halalas']==1000
            assert summary['submission']['difference_halalas']==0
            for suffix in ['pdf','csv']:
                response=contexts[who].request.get(live_app+f'/api/reports/{report_id}/{suffix}')
                assert response.ok,response.text()
                contents=response.body()
                if suffix=='pdf':assert contents.startswith(b'%PDF')
                else:assert contents.startswith(b'\xef\xbb\xbf')
                (ARTIFACTS/f'TEST_ONLY_{kind}_closure_example.{suffix}').write_bytes(contents)
            with actor.expect_popup() as opened:
                actor.locator('#modal-body a[href$="/print"]').click()
            print_page=opened.value
            print_errors=[]
            print_page.on('pageerror',lambda error:print_errors.append(str(error)))
            print_page.on('console',lambda message:csp_errors.append(message.text) if ('Content Security Policy' in message.text or 'Refused to execute inline' in message.text) else None)
            print_page.wait_for_load_state('networkidle')
            expect(print_page.locator('[data-print-report]')).to_be_visible()
            _capture(print_page,f'{kind}-real-report-print-view-synthetic')
            print_page.pdf(path=str(ARTIFACTS/f'TEST_ONLY_{kind}_browser_print_example.pdf'),format='A4',print_background=True)
            assert not print_errors,print_errors
            print_page.close()
            actor.keyboard.press('Escape')
            saved[kind]={'unique_orders':summary['unique_orders'],'cash_collected_halalas':summary['cash_collected_halalas'],
                         'card_collected_halalas':summary['card_collected_halalas'],'expected_halalas':summary['submission']['expected_halalas'],
                         'counted_halalas':summary['submission']['counted_halalas'],'received_halalas':summary['submission']['handover_halalas'],
                         'difference_halalas':summary['submission']['difference_halalas']}
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) AS n FROM collections WHERE order_id=%s',(order_id,)).fetchone()['n']==1
            assert conn.execute('SELECT count(*) AS n FROM closure_reports WHERE shift_id=ANY(%s)',([driver_shift,manager_shift],)).fetchone()['n']==2
        assert saved['driver']['cash_collected_halalas']==saved['manager']['cash_collected_halalas']==1000
        (ARTIFACTS/'TEST_ONLY_financial_example_summary.json').write_text(json.dumps({
            'test_only':True,'currency':'SAR','environment':'Chromium browser emulation, real isolated PostgreSQL and HTTP',
            'physical_delivery_or_cash_receipt':False,'physical_device_test':False,'customer_details_included':False,
            'description_ar':'طلب اختبار واحد بقيمة 10 ريالات. استلم السائق ورقة 20 وأعاد 10. التحصيل والتسليم للمدير 10. المدير يعتمد تشغيليًا، والمحاسب المستقل يثبت استلام 10 نقدًا وصفر في البنك، دون إنشاء مبيعات أو عهدة إضافية.',
            'reports':saved},ensure_ascii=False,indent=2))
        assert all(not page_errors for page_errors in errors),errors
        assert not csp_errors,csp_errors
    finally:
        for context in contexts.values():context.close()


def test_real_created_brand_storefront_identity_empty_catalog_and_draft_isolation(live_app,chromium_browser):
    """A real owner-created empty brand never inherits the KPS menu or identity."""
    from playwright.sync_api import expect
    owner=_staff()
    owner_context,_,owner_errors=_context(chromium_browser,live_app)
    _owner_login(owner_context,live_app,owner)
    brand_id='browser-new-brand-test'
    brand_name='براند اختبار جديد فقط'
    _authenticated_post(owner_context,live_app,'/api/identity/step-up',{'password':PASSWORD})
    created=_authenticated_post(owner_context,live_app,'/api/admin/brands',{
        'id':brand_id,'name':brand_name,'owner_email':'brand-owner@example.invalid',
        'owner_display_name':'مالك براند اختبار فقط'})
    assert created['invitation_status']=='pending_email'
    with db.tx() as conn:
        invited=conn.execute('SELECT active,email_verified_at FROM users WHERE id=%s',(created['owner_user_id'],)).fetchone()
        assert invited['active'] is False and invited['email_verified_at'] is None
    assert created['customer_path']=='/?brand_id='+brand_id
    context,page,errors=_context(chromium_browser,live_app)
    try:
        # Save an actual source-product draft on the default KPS storefront.
        page.goto('/')
        expect(page.locator('.product-card')).to_have_count(34)
        page.locator('.product-image-button').first.click()
        page.locator('#product-add').click()
        expect(page.locator('#product-dialog')).not_to_be_visible()
        expect(page.locator('.cart-line')).to_have_count(1)
        kps_draft=page.evaluate("localStorage.getItem('kps-unconfirmed-basket-v2:kps')")
        assert json.loads(kps_draft)['brandId']=='kps'

        page.wait_for_load_state('networkidle')
        requests=[]
        page.on('request',lambda request:requests.append(request.url))
        page.goto(created['customer_path'])
        expect(page.locator('#brand-name')).to_contain_text(brand_name)
        expect(page.locator('#footer-brand')).to_have_text(brand_name)
        expect(page).to_have_title(brand_name+' | الطلبات')
        expect(page.locator('#brand-home')).to_have_attribute('href',created['customer_path'])
        expect(page.locator('.product-card')).to_have_count(0)
        expect(page.locator('.cart-line')).to_have_count(0)
        expect(page.locator('#products')).to_contain_text('لم تُضف أصناف لهذا البراند بعد.')
        expect(page.locator('#branch-select option')).to_have_count(1)
        expect(page.locator('#brand-logo')).not_to_be_visible()
        assert page.locator('#brand-logo').get_attribute('src') is None
        expect(page.locator('#banner-content')).to_contain_text('اطلب مباشرة من '+brand_name)
        expect(page.locator('body')).not_to_contain_text('KPS')
        # Contact fragments must be absent from the response and DOM in both languages.
        for contact in ['0550472662', '966550472662', 'kps.coffe', 'Kps.cofee']:
            assert contact not in page.content()
        page.locator('#customer-language').click()
        expect(page.locator('html')).to_have_attribute('lang', 'en')
        expect(page.locator('body')).not_to_contain_text('KPS')
        page.locator('#legal-contact-button').click()
        expect(page.locator('#public-contact-dialog')).to_be_visible()
        expect(page.locator('#public-contact-support')).to_be_visible()
        assert page.locator('[data-kps-contact]').count() == 0
        page.keyboard.press('Escape')
        page.locator('#customer-language').click()
        expect(page.locator('html')).to_have_attribute('lang', 'ar')
        assert any('/api/catalog?brand_id='+brand_id in url for url in requests)
        assert any('/api/branches?brand_id='+brand_id in url for url in requests)
        assert not any('/assets/kps-' in url or 'brand_id=kps' in url for url in requests)
        _capture(page,'owner-created-empty-brand-customer-storefront')

        # Unconfigured OTP stays hidden. The real disabled endpoint still
        # rejects requests while the new brand has its own isolated guest policy.
        page.locator('#account-button').click()
        expect(page.locator('#guest-form')).to_be_visible()
        expect(page.locator('#otp-request-form')).not_to_be_visible()
        rejected=context.request.post(live_app+'/api/auth/otp/request',headers={'Origin':live_app},data={'phone':'0500000001','brand_id':brand_id})
        assert rejected.status==503
        page.keyboard.press('Escape')
        assert page.evaluate("localStorage.getItem('kps-unconfirmed-basket-v2:kps')")==kps_draft
        page.goto('/')
        expect(page.locator('.product-card')).to_have_count(34)
        expect(page.locator('.cart-line')).to_have_count(1)
        expect(page.locator('#brand-name')).to_contain_text('KPS')
        expect(page.locator('#brand-logo')).to_be_visible()

        # Invalid identity fails closed, with no request for the default menu.
        requests.clear()
        page.goto('/?brand_id=INVALID%2Fbrand')
        expect(page.locator('#site-notice')).to_contain_text('رابط البراند غير صالح')
        expect(page.locator('.product-card')).to_have_count(0)
        expect(page.locator('#account-button')).to_be_disabled()
        assert not any('/api/catalog' in url or '/api/branches' in url for url in requests)
        assert not errors and not owner_errors,errors+owner_errors
        (ARTIFACTS/'owner-created-brand-test-metadata.json').write_text(json.dumps({
            'test_only':True,'brand_created_via_real_authenticated_api':True,'brand_has_no_products_or_branches':True,
            'catalog_or_api_interception':False,'source_kps_menu_unchanged':True,'draft_storage_scoped_by_brand':True,
            'environment':'Chromium browser emulation with real isolated PostgreSQL and HTTP',
            'physical_device_test':False,'real_external_sms_verification':False},indent=2))
    finally:
        context.close();owner_context.close()
