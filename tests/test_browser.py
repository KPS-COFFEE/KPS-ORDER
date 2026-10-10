"""Real HTTP/Chromium acceptance with an isolated PostgreSQL schema.

Run with TEST_BROWSER=1 and a local TEST_DATABASE_URL. Browser tests make no
intercepted or mocked API responses. The customer session used for the second
test is deliberately seeded in the isolated database, so this is not evidence
of a real SMS provider or a physical phone test. Uploaded pictures are preserved
source menu assets; the banner is clearly labelled test-only, without an offer.
"""
from __future__ import annotations

import json
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
def live_app(database, tmp_path):
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


def _capture(page, prefix):
    for width in WIDTHS:
        page.set_viewport_size({'width': width, 'height': 900})
        page.wait_for_timeout(60)
        dimensions = page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth,background:getComputedStyle(document.body).backgroundColor})')
        assert dimensions['scroll'] <= dimensions['width'], (prefix, width, dimensions)
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
        expect(page.locator('.product-card')).to_have_count(35)
        expect(page.locator('#branch-select option')).to_have_count(21)
        expect(page.locator('#catalog-notice')).to_contain_text('قيد التحقق')
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
        _capture(page, 'customer-unconfirmed-cart-source-unverified')

        # Drinks retain flavour language and the original six choices.
        mojito = page.get_by_role('button', name='تفاصيل موهيتو', exact=True)
        mojito.click()
        expect(page.locator('#product-option-hint')).to_contain_text('النكهة')
        expect(page.locator('#product-options select option')).to_have_count(7)
        page.keyboard.press('Escape')
        expect(mojito).to_be_focused()

        expect(page.get_by_role('button', name='إضافة خلية بيكان إلى السلة', exact=True)).to_be_disabled()
        page.locator('#branch-select').select_option('kps-branch-1')
        for name in ('خلية بيكان', 'خلية كلاسيك'):
            page.get_by_role('button', name='تفاصيل ' + name, exact=True).click()
            expect(page.locator('#product-add')).to_be_enabled()
            page.locator('#product-add').click()
            expect(page.locator('#product-dialog')).not_to_be_visible()
        branches = context.request.get(live_app + '/api/branches').json()['branches']
        outside = next(branch for branch in branches if branch['city'] == 'الخفجي')
        page.locator('#branch-select').select_option(outside['id'])
        expect(page.locator('#confirm-dialog')).to_be_visible()
        page.keyboard.press('Escape')
        expect(page.locator('#branch-select')).to_have_value('kps-branch-1')
        expect(page.locator('.cart-line-title').filter(has_text='خلية')).to_have_count(2)
        page.locator('#branch-select').select_option(outside['id'])
        page.locator('#confirm-submit').click()
        expect(page.locator('.cart-line-title').filter(has_text='خلية')).to_have_count(0)
        page.get_by_role('button', name='تفاصيل الصنف', exact=True).click()
        expect(page.locator('#product-restriction')).to_contain_text('حفر الباطن فقط')
        expect(page.locator('#product-add')).to_be_disabled()  # Banner cannot widen item availability.
        page.keyboard.press('Escape')

        page.locator('#account-button').click()
        page.locator('#phone').fill('0500000000')
        page.locator('#send-otp').click()
        expect(page.locator('#otp-unavailable')).to_be_visible()
        expect(page.locator('#otp-verify-form')).not_to_be_visible()
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
        expect(page.locator('.product-card')).to_have_count(35)
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
        page.locator('#orders-button').click()
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
