"""Actual AR/EN Chromium forms against synthetic isolated PostgreSQL data."""
import os

import pytest

from app import db, maintenance
from test_browser import _capture, _context, _owner_login, _staff, chromium_browser

from tests.browser_sms_helpers import sms_browser_app as live_app

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.getenv('TEST_BROWSER') != '1', reason='Requires explicit isolated browser acceptance.')]


@pytest.mark.parametrize('language', ['ar', 'en'])
def test_driver_fault_form_and_restricted_admin_maintenance_journey(live_app, chromium_browser, language):
    from playwright.sync_api import expect

    driver = _staff('driver')
    context, page, errors = _context(chromium_browser, live_app)
    _owner_login(context, live_app, driver)
    try:
        page.goto(live_app + '/ops')
        page.get_by_role('button', name='أعطال المركبات والصيانة', exact=True).click()
        expect(page.get_by_role('button', name='رفع بلاغ عطل', exact=True)).to_be_visible()
        if language == 'en':
            page.get_by_role('button', name='English', exact=True).click()
        submit_label = 'رفع بلاغ عطل' if language == 'ar' else 'Report vehicle fault'
        page.get_by_role('button', name=submit_label, exact=True).click()
        modal = page.locator('#modal')
        expect(modal).to_be_visible()
        labels = modal.locator('select[name=category] option').all_text_contents()
        assert len(labels) == 19
        for _, ar, en in maintenance.CATEGORIES:
            assert f'{ar} / {en}' in labels
        modal.locator('select[name=category]').select_option('brakes')
        modal.locator('input[name=vehicle_description]').fill('TEST ONLY — مركبة اختبار')
        modal.locator('input[name=vehicle_plate]').fill('TEST 456')
        modal.locator('textarea[name=description]').fill('بلاغ فرامل تجريبي <script>throw new Error("unsafe")</script>')
        _capture(page, 'TEST_ONLY_vehicle_fault_form_' + language)
        modal.get_by_role('button', name='إرسال البلاغ' if language == 'ar' else 'Submit report', exact=True).click()
        expect(modal).not_to_be_visible()
        expect(page.locator('[data-vehicle-report]')).to_have_count(1)
        expect(page.get_by_role('heading', name='الفرامل / Brakes', exact=True)).to_be_visible()
        expect(page.locator('[data-action=maintenance-status]')).to_have_count(0)
        expect(page.locator('[data-vehicle-report] script')).to_have_count(0)
        _capture(page, 'TEST_ONLY_vehicle_driver_reports_' + language)
        assert errors == []
    finally:
        context.close()

    admin = _staff('admin')
    with db.tx() as conn:
        conn.execute('UPDATE users SET permissions=%s WHERE id=%s', (db.json([maintenance.PERMISSION]), admin['id']))
    context, page, errors = _context(chromium_browser, live_app)
    _owner_login(context, live_app, admin)
    try:
        page.goto(live_app + '/ops')
        expect(page.get_by_role('heading', name='أعطال المركبات والصيانة', exact=True)).to_be_visible()
        if language == 'en':
            page.get_by_role('button', name='English', exact=True).click()
        expect(page.locator('#navigation button')).to_have_count(3)
        assert set(page.locator('#navigation button').evaluate_all('(buttons)=>buttons.map(b=>b.dataset.page)'))=={'fleet','maintenance','employeeTickets'}
        expect(page.locator('#navigation [data-page=fleet]')).to_be_visible()
        for key in ('orders', 'shifts', 'custody', 'drivers', 'admin', 'reports'):
            expect(page.locator(f'#navigation [data-page="{key}"]')).to_have_count(0)
        expect(page.locator('[data-action=maintenance-create]')).to_have_count(0)
        expect(page.locator('[data-vehicle-report]')).to_have_count(1)
        page.locator('[data-action=maintenance-status][data-status=in_progress]').click()
        modal = page.locator('#modal')
        modal.locator('textarea[name=note]').fill('بدأ فحص الفرامل — اختبار فقط')
        modal.get_by_role('button', name='حفظ الحالة والملاحظة' if language == 'ar' else 'Save status and note', exact=True).click()
        expect(modal).not_to_be_visible()
        expect(page.locator('[data-action=maintenance-status][data-status=in_progress]')).to_have_count(0)
        page.locator('[data-action=maintenance-status][data-status=resolved]').click()
        modal.locator('textarea[name=note]').fill('إصلاح تجريبي موثق فقط')
        modal.get_by_role('button', name='حفظ الحالة والملاحظة' if language == 'ar' else 'Save status and note', exact=True).click()
        expect(modal).not_to_be_visible()
        expect(page.get_by_text('إصلاح تجريبي موثق فقط', exact=True)).to_be_visible()
        _capture(page, 'TEST_ONLY_vehicle_maintenance_admin_' + language)
        page.locator('[data-action=maintenance-detail]').click()
        expect(modal.get_by_text('بدأ فحص الفرامل — اختبار فقط', exact=True)).to_be_visible()
        expect(modal.get_by_text('إصلاح تجريبي موثق فقط', exact=True)).to_be_visible()
        _capture(page, 'TEST_ONLY_vehicle_maintenance_history_' + language)
        assert errors == []
    finally:
        context.close()
