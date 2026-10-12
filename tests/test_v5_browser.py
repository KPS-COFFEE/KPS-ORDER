"""V5 browser-operated guest → staff → driver → final-report journey.

Real HTTP and isolated PostgreSQL only, no browser API interception. The invite
link is read from the isolated encrypted test outbox with the test key; no real
external mail is sent and this does not certify a production email channel.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from app import auth, db, email_delivery
from test_browser import (ARTIFACTS, WIDTHS, PASSWORD, _context, _capture, _staff,
                          _staff_ui_login, _authenticated_post, _submit_modal,
                          _inspection_payload, _fill_closure_inspection, _attach_task_photo,
                          chromium_browser, ROOT)

pytestmark = [pytest.mark.postgres,
              pytest.mark.skipif(__import__('os').getenv('TEST_BROWSER') != '1',
                                 reason='V5 browser acceptance requires TEST_BROWSER=1; emulation, not a physical phone.')]


@pytest.fixture
def v5_live_app(database, tmp_path, monkeypatch):
    """Real restartable HTTP process; its DB and encrypted outbox are isolated."""
    monkeypatch.setenv('EMAIL_PROVIDER', 'smtp_ssl')
    monkeypatch.setenv('EMAIL_FROM', 'synthetic-browser@example.test')
    monkeypatch.setenv('SMTP_HOST', 'test-mail.invalid')
    monkeypatch.setenv('SMTP_USERNAME', 'synthetic-outbox-only')
    monkeypatch.setenv('SMTP_PASSWORD', secrets.token_urlsafe(24))
    monkeypatch.setenv('MAIL_OUTBOX_KEY', base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    monkeypatch.setenv('EMAIL_WORKER_ENABLED', 'false')
    monkeypatch.setenv('REPORT_WORKER_ENABLED', 'false')
    with db.tx() as conn:
        conn.execute("UPDATE identity_channels SET state='testing',enabled=false WHERE brand_id='kps' AND channel='email'")
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env = {**os.environ, 'DATABASE_URL': database, 'APP_ENV': 'testing',
           'PUBLIC_BASE_URL': base, 'APP_ALLOWED_ORIGINS': base,
           'APP_ALLOWED_HOSTS': '127.0.0.1,localhost',
           'SESSION_COOKIE_SECURE': 'false', 'OTP_PROVIDER': 'disabled',
           'INITIAL_CATALOG_IMPORT': 'true'}
    log_path = tmp_path / 'v5-browser-test-app.log'

    class IsolatedApp(str):
        process = None
        restart_count = 0

        def stop(self):
            if self.process is None:
                return
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

        def start(self):
            self.process = subprocess.Popen(
                [sys.executable, '-m', 'uvicorn', 'app.main:app', '--host',
                 '127.0.0.1', '--port', str(port)],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    pytest.fail('Isolated V5 HTTP server did not start: ' + log_path.read_text()[-3000:])
                try:
                    if httpx.get(base + '/ready', timeout=1).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            pytest.fail('Isolated V5 HTTP server readiness timed out.')

        def restart(self):
            self.stop()
            self.start()
            self.restart_count += 1

    with log_path.open('w') as log:
        server = IsolatedApp(base)
        server.start()
        try:
            yield server
        finally:
            server.stop()


def _font_evidence(page):
    result = page.evaluate("""async () => {
        const loaded = await Promise.all([400,600,700].map(weight => document.fonts.load(weight+' 18px "Noto Kufi Arabic"')));
        await document.fonts.ready;
        const faces=Array.from(document.fonts).filter(face=>face.family.replaceAll('"','')==='Noto Kufi Arabic');
        return {counts:loaded.map(list=>list.length),faces:faces.map(face=>({weight:face.weight,status:face.status})),
                family:getComputedStyle(document.body).fontFamily,synthesis:getComputedStyle(document.body).fontSynthesis};
    }""")
    assert result['counts'] == [1, 1, 1]
    assert all(face['status'] == 'loaded' for face in result['faces'])
    assert 'Noto Kufi Arabic' in result['family']
    assert result['synthesis'] == 'none'
    return result


def _read_synthetic_invitation(email):
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM email_outbox WHERE recipient=%s AND kind='identity' ORDER BY created_at DESC LIMIT 1", (email,)).fetchone()
        assert row and row['status'] == 'pending'
        payload = email_delivery._payload(row)
    # The secret stays in memory and is never added to screenshots or artifacts.
    return next(line for line in payload['text'].splitlines() if '/#identity_challenge=' in line)


def _close_detail(page):
    if page.locator('#modal').is_visible():
        page.locator('#modal-close').click()


def test_v5_guest_email_invited_driver_one_collection_final_owner_report(v5_live_app, chromium_browser):
    from playwright.sync_api import expect
    base = v5_live_app
    users = {role: _staff(role=role) for role in ('owner', 'delivery_manager', 'branch_manager')}
    with db.tx() as conn:
        conn.execute("UPDATE products SET settings=settings||'{\"source_verified\":true}'::jsonb")
        for index, branch_id in enumerate(('kps-branch-1', 'kps-branch-9')):
            point = {'lat': 28.4001 + index * .01, 'lng': 45.9001 + index * .01}
            settings = {'active': True, 'ordering_verified': True, 'location_verified': True,
                        'pickup_enabled': True, 'car_enabled': True, 'delivery_enabled': True,
                        'opening_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
                        'delivery_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
                        'payment_methods': ['cash', 'card'], 'location': point,
                        'coverage': {'type': 'radius', 'center': point, 'radius_meters': 5000},
                        'delivery_fee_halalas': 0, 'minimum_order_halalas': 0}
            conn.execute('UPDATE branches SET settings=settings||%s WHERE id=%s', (db.json(settings), branch_id))
        conn.execute("UPDATE users SET branch_ids=%s WHERE id=%s", (db.json(['kps-branch-1', 'kps-branch-9']), users['delivery_manager']['id']))
        conn.execute("UPDATE app_settings SET settings=settings||'{\"operational_day_start\":\"00:00\"}'::jsonb WHERE brand_id='kps'")
        product = conn.execute("SELECT id,name,price_halalas FROM products WHERE brand_id='kps' AND coalesce(jsonb_array_length(settings->'option_groups'),0)=0 AND coalesce(jsonb_array_length(settings->'available_cities'),0)=0 ORDER BY id LIMIT 1").fetchone()
    contexts, pages, errors, fonts = {}, {}, [], {}
    try:
        for role, user in users.items():
            context, page, page_errors = _context(chromium_browser, base)
            contexts[role], pages[role] = context, page
            errors.append(page_errors)
            _staff_ui_login(page, user)
            fonts[role] = _font_evidence(page)
        owner, manager, branch = [pages[role] for role in ('owner', 'delivery_manager', 'branch_manager')]
        # Manager registers one driver for two authorized branches through the UI.
        manager.locator('[data-page=drivers]').click()
        manager.locator('[data-action=invite-staff]').click()
        email = 'synthetic-driver-' + secrets.token_hex(5) + '@example.test'
        for name, value in {'display_name': 'سائق اختبار V5 فقط', 'email': email, 'phone': '0500000022'}.items():
            manager.locator(f'#modal-body [name={name}]').fill(value)
        manager.locator('#modal-body [name="scope:kps-branch-1"]').check()
        manager.locator('#modal-body [name="scope:kps-branch-9"]').check()
        _capture(manager, 'v5-manager-two-branch-driver-invitation')
        _submit_modal(manager)
        expect(manager.locator('#content')).to_contain_text('سائق اختبار V5 فقط')
        driver_context, driver, driver_errors = _context(chromium_browser, base)
        contexts['driver'], pages['driver'] = driver_context, driver
        errors.append(driver_errors)
        driver.goto(_read_synthetic_invitation(email))
        expect(driver.locator('#app-shell')).to_be_visible()
        assert '#identity_token' not in driver.url
        driver_user = driver_context.request.get(base + '/api/session').json()['user']
        assert driver_user['role'] == 'driver' and driver_user['email_verified_at']
        assert driver_user['phone_sms_verified_at'] is None and driver_user['phone_whatsapp_verified_at'] is None
        assert set(driver_user['branch_ids']) == {'kps-branch-1', 'kps-branch-9'}
        fonts['driver'] = _font_evidence(driver)
        manager_shift = _authenticated_post(contexts['delivery_manager'], base, '/api/shifts', {'kind': 'manager', 'branch_ids': ['kps-branch-1','kps-branch-9']})['shift']['id']
        driver_shift = _authenticated_post(driver_context, base, '/api/shifts', {'kind': 'driver', 'branch_ids': ['kps-branch-1','kps-branch-9'], 'manager_shift_id': manager_shift, 'vehicle_inspection': _inspection_payload()})['shift']['id']
        customer_context, customer, customer_errors = _context(chromium_browser, base)
        contexts['guest'], pages['guest'] = customer_context, customer
        errors.append(customer_errors)
        customer.goto('/')
        expect(customer.locator('.product-card')).to_have_count(34)
        fonts['guest'] = _font_evidence(customer)
        customer.locator('#branch-select').select_option('kps-branch-1')
        expect(customer.locator('#branch-directions')).to_have_attribute('href', __import__('re').compile(r'destination=28\.4001%2C45\.9001'))
        customer.locator('input[name=service][value=delivery]').check()
        customer.locator('#neighborhood').fill('حي اختبار V5 فقط')
        customer.locator('#address').fill('عنوان اختبار معزول — لا تجهيز ولا إرسال فعلي')
        customer.locator('#manual-location-button').click()
        customer.locator('#latitude').fill('28.4002')
        customer.locator('#longitude').fill('45.9002')
        customer.locator('#save-location').click()
        customer.get_by_role('button', name='تفاصيل ' + product['name'], exact=True).first.click()
        cart_before = customer.locator('#header-count').inner_text()
        if customer.locator('#product-image-zoom').is_visible():
            customer.locator('#product-image-zoom').click()
            expect(customer.locator('#image-dialog')).to_be_visible()
            _capture(customer, 'v5-customer-lightbox-no-cart-change')
            customer.locator('#image-dialog [data-close]').click()
            assert customer.locator('#header-count').inner_text() == cart_before
        customer.locator('#product-add').click()
        customer.locator('#customer-name').fill('ضيف اختبار V5 فقط')
        customer.locator('#customer-phone').fill('0500000033')
        expect(customer.locator('#checkout-button')).to_be_enabled()
        customer.locator('#checkout-button').click()
        expect(customer.locator('#guest-form')).to_be_visible()
        expect(customer.locator('#email-login-button')).to_be_hidden()
        expect(customer.locator('#otp-request-form')).to_be_hidden()
        customer.locator('#guest-submit').click()
        expect(customer.locator('#confirm-dialog')).to_be_visible()
        _capture(customer, 'v5-guest-delivery-order-review')
        customer.locator('#confirm-submit').click()
        expect(customer.locator('#orders-dialog')).to_be_visible()
        with db.tx() as conn:
            order = conn.execute("SELECT * FROM orders WHERE customer->>'name'='ضيف اختبار V5 فقط'").fetchone()
            assert order and order['customer']['identity_kind'] == 'guest'
            assert not order['customer']['phone_verified']
        order_id, total = order['id'], order['total_halalas']
        manager.locator('[data-page=orders]').click()
        expect(manager.locator(f'[data-order-id="{order_id}"]')).to_be_visible()
        _capture(manager, 'v5-manager-sequential-orders')
        # The exact acceptance sequence prepares at the executing branch before
        # dispatch; neither view creates a second order or a second sale.
        branch.locator('[data-page=orders]').click()
        expect(branch.locator(f'[data-order-id="{order_id}"]')).to_be_visible()
        for status in ('accepted','preparing','ready'):
            branch.locator(f'[data-action=order-detail][data-id="{order_id}"]').click()
            branch.locator(f'[data-action=prepare][data-id="{order_id}"][data-status={status}]').click()
            expect(branch.locator(f'#modal-body [data-action=prepare][data-status={status}]')).not_to_be_visible()
            _close_detail(branch)
        manager.locator(f'[data-action=order-detail][data-id="{order_id}"]').click()
        expect(manager.locator('#modal-body')).to_contain_text('ضيف — الجوال غير موثق')
        manager.locator(f'[data-action=assign][data-id="{order_id}"]').click()
        _submit_modal(manager, {'driver_shift_id': driver_shift})
        driver.locator('[data-page=tasks]').click()
        driver.locator('[data-action=task-transition][data-status=accepted]').click()
        branch.locator('[data-page=orders]').click()
        for status in ('handed_over',):
            branch.locator(f'[data-action=order-detail][data-id="{order_id}"]').click()
            branch.locator(f'[data-action=prepare][data-id="{order_id}"][data-status={status}]').click()
            expect(branch.locator(f'#modal-body [data-action=prepare][data-status={status}]')).not_to_be_visible()
            _close_detail(branch)
        driver.locator('[data-page=tasks]').click()
        driver.locator('[data-action=task-transition][data-status=picked_up]').click()
        _attach_task_photo(driver)
        _submit_modal(driver)
        driver.locator('[data-action=collect]').click()
        driver.locator('#modal-body [name=tendered]').fill(f'{total/100:.2f}')
        _submit_modal(driver, check=['confirmed'])
        driver.locator('[data-action=delivered]').click()
        _attach_task_photo(driver)
        _submit_modal(driver, {'proof':'دليل تسليم اختبار اصطناعي فقط'})
        driver.locator('#navigation [data-page=shifts]').click()
        driver.locator(f'[data-action=start-closing][data-id="{driver_shift}"]').click()
        driver.locator('#closure-form [name=counted]').fill(f'{total/100:.2f}')
        driver.locator('#closure-form [name=handover]').fill(f'{total/100:.2f}')
        driver.locator('#closure-form [name=reference]').fill('V5-SYNTHETIC-DRIVER-BAG')
        driver.locator('#closure-form [name=recipient_user_id]').select_option(users['delivery_manager']['id'])
        driver.locator('#save-closure-draft').click()
        expect(driver.locator('#closure-draft-state')).to_contain_text('محفوظة')
        _capture(driver, 'v5-driver-compact-saved-draft')
        _close_detail(driver)
        driver.locator(f'[data-action=shift-detail][data-id="{driver_shift}"]').click()
        expect(driver.locator('#closure-form [name=counted]')).to_have_value(f'{total/100:.2f}')
        _fill_closure_inspection(driver)
        _submit_modal(driver, check=['confirm_closure'])
        manager.locator('[data-page=custody]').click()
        manager.locator('[data-action=confirm-custody]').click()
        _submit_modal(manager, check=['received'])
        manager.locator('#navigation [data-page=shifts]').click()
        manager.locator(f'[data-action=shift-detail][data-id="{driver_shift}"]').click()
        manager.locator(f'[data-action=approve-shift][data-id="{driver_shift}"]').click()
        _submit_modal(manager, check=['confirmed'])
        manager.locator(f'[data-action=start-closing][data-id="{manager_shift}"]').click()
        manager.locator('#closure-form [name=counted]').fill(f'{total/100:.2f}')
        manager.locator('#closure-form [name=handover]').fill(f'{total/100:.2f}')
        manager.locator('#closure-form [name=reference]').fill('V5-SYNTHETIC-MANAGER-BAG')
        manager.locator('#closure-form [name=recipient_user_id]').select_option(users['owner']['id'])
        _capture(manager, 'v5-manager-compact-closure')
        _submit_modal(manager, check=['confirm_closure'])
        owner.locator('[data-page=custody]').click()
        owner.locator('[data-action=confirm-custody]').click()
        _submit_modal(owner, check=['received'])
        owner.locator('#navigation [data-page=shifts]').click()
        owner.locator(f'[data-action=shift-detail][data-id="{manager_shift}"]').click()
        owner.locator(f'[data-action=approve-shift][data-id="{manager_shift}"]').click()
        _submit_modal(owner, check=['confirmed'])
        owner.locator('[data-page=reports]').click()
        expect(owner.locator('#navigation [data-page=reports]')).to_have_attribute('aria-current','page')
        expect(owner.locator('.owner-notifications')).to_be_visible()
        expect(owner.locator('.owner-notifications')).to_contain_text('معتمد نهائيًا')
        expect(owner.locator('.owner-notifications')).to_contain_text('بانتظار قناة البريد')
        _capture(owner, 'v5-owner-final-notification-no-fake-mail')
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) n FROM collections WHERE order_id=%s', (order_id,)).fetchone()['n'] == 1
            assert conn.execute("SELECT count(*) n FROM closure_reports WHERE shift_id IN (%s,%s)", (driver_shift,manager_shift)).fetchone()['n'] == 2
            assert conn.execute("SELECT count(*) n FROM orders WHERE customer_id=%s", (order['customer_id'],)).fetchone()['n'] == 1
            assert conn.execute('SELECT status FROM shifts WHERE id=%s', (manager_shift,)).fetchone()['status'] == 'closed'
            final_reports=conn.execute('SELECT id,kind,snapshot FROM closure_reports WHERE shift_id IN (%s,%s)',(driver_shift,manager_shift)).fetchall()
        saved_report_files = {}
        for report in final_reports:
            reviewer=driver_context if report['kind']=='driver' else contexts['delivery_manager']
            path=base+'/api/reports/'+report['id']+'/renderings/v5/'
            compact=reviewer.request.get(path+'pdf')
            assert compact.status==200 and compact.body().startswith(b'%PDF')
            saved_report_files[(report['id'],report['kind'],'pdf')] = compact.body()
            (ARTIFACTS / ('v5_'+report['kind']+'_final.pdf')).write_bytes(compact.body())
            if report['kind']=='manager':
                for fmt in ('pdf','csv'):
                    detailed=contexts['owner'].request.get(path+fmt)
                    assert detailed.status==200
                    saved_report_files[(report['id'],'owner',fmt)] = detailed.body()
                    (ARTIFACTS / ('v5_owner_final.'+fmt)).write_bytes(detailed.body())
                (ARTIFACTS / 'v5-final-financial-snapshot.json').write_text(json.dumps({'test_only':True,'snapshot':report['snapshot']},ensure_ascii=False,default=str,indent=2)+'\n')
        # Restart the actual HTTP process against the SAME isolated PostgreSQL
        # schema. Startup executes all safe migrations/catalog import again;
        # it must not reset the guest identity, ledger, closures or saved bytes.
        def persistence_state():
            with db.tx() as conn:
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                return {
                    'order': conn.execute('SELECT id,order_no,customer_id,customer,snapshot,total_halalas,payment_status,delivery_status FROM orders WHERE id=%s',(order_id,)).fetchone(),
                    'shifts': conn.execute('SELECT id,status,submission FROM shifts WHERE id IN (%s,%s) ORDER BY id',(driver_shift,manager_shift)).fetchall(),
                    'collections': conn.execute('SELECT * FROM collections WHERE order_id=%s ORDER BY id',(order_id,)).fetchall(),
                    'reports': conn.execute('SELECT id,snapshot,pdf_data,csv_data FROM closure_reports WHERE shift_id IN (%s,%s) ORDER BY id',(driver_shift,manager_shift)).fetchall(),
                    'renderings': conn.execute('SELECT report_id,template_version,view_kind,status,source_sha256,html_data,pdf_data,csv_data FROM report_renderings ORDER BY report_id,view_kind').fetchall(),
                    'notifications': conn.execute('SELECT * FROM report_notifications ORDER BY id').fetchall(),
                    'migrations': conn.execute('SELECT version,sha256 FROM schema_migrations ORDER BY version').fetchall(),
                }
        before_restart = persistence_state()
        base.restart()
        assert base.restart_count == 1
        assert persistence_state() == before_restart
        for role, context in contexts.items():
            session = context.request.get(base+'/api/session')
            assert session.status == 200 and session.json()['user'] and session.json()['csrf_token'], role
        tracked = customer_context.request.get(base+'/api/orders/'+order_id)
        assert tracked.status == 200 and tracked.json()['order']['snapshot'] == order['snapshot']
        assert tracked.json()['order']['delivery_status'] == 'delivered'
        for (rid,role,fmt),contents in saved_report_files.items():
            context = driver_context if role == 'driver' else contexts['delivery_manager'] if role == 'manager' else contexts['owner']
            response = context.request.get(base+'/api/reports/'+rid+'/renderings/v5/'+fmt)
            assert response.status == 200 and response.body() == contents
        customer.reload()
        expect(customer.locator('.product-card')).to_have_count(34)
        owner.reload()
        expect(owner.locator('#app-shell')).to_be_visible()
        owner.locator('[data-page=reports]').click()
        expect(owner.locator('#navigation [data-page=reports]')).to_have_attribute('aria-current','page')
        expect(owner.locator('.owner-notifications')).to_contain_text('معتمد نهائيًا')
        _capture(owner, 'v5-owner-reports-after-real-http-restart')
        assert not any(errors), errors
        (ARTIFACTS / 'v5-browser-evidence-metadata.json').write_text(json.dumps({
            'test_only': True, 'real_http': True, 'network_api_interception': False,
            'database': 'UUID-isolated PostgreSQL schema', 'widths_px': WIDTHS,
            'physical_device_test': False, 'external_email_delivery_tested': False,
            'email_link_source': 'isolated encrypted synthetic test outbox, not an external mailbox',
            'sms_configured': False, 'whatsapp_configured': False, 'font_loads': fonts,
            'financial_events': {'orders':1,'collections':1,'driver_closures':1,'manager_closures':1},
            'actual_http_process_restarts': base.restart_count,
            'restart_database_and_saved_report_bytes_identical': True,
            'authenticated_guest_and_staff_sessions_survived_restart': True,
            'saved_report_sha256': {rid+':'+role+':'+fmt:hashlib.sha256(contents).hexdigest()
                                    for (rid,role,fmt),contents in saved_report_files.items()},
        },ensure_ascii=False,indent=2)+'\n')
    finally:
        for context in contexts.values():
            context.close()
