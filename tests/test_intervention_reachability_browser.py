"""Real authenticated UI report → manager resolution; no business API interception."""
import os

import pytest
from app import db
from test_browser import (live_app, chromium_browser, synthetic_mail, _role_fixture,
                          _context, _staff_ui_login, _authenticated_post, _inspection_payload)

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.getenv('TEST_BROWSER') != '1', reason='Requires real Chromium and isolated PostgreSQL')]


@pytest.mark.parametrize('reason', ['no_answer', 'damaged'])
def test_driver_reports_without_changing_task_then_manager_resolves_without_invented_contact(
        live_app, chromium_browser, reason):
    from playwright.sync_api import expect
    users, branch, oid = _role_fixture(initial_snapshot={
        'journey_controls_version': 1, 'delivery_period': 'evening'})
    contexts = []
    try:
        manager_context, manager, manager_errors = _context(chromium_browser, live_app)
        driver_context, driver, driver_errors = _context(chromium_browser, live_app)
        contexts.extend([manager_context, driver_context])
        _staff_ui_login(manager, users['manager'])
        _staff_ui_login(driver, users['driver'])
        manager_shift = _authenticated_post(manager_context, live_app, '/api/shifts',
            {'kind': 'manager', 'branch_ids': [branch]})['shift']['id']
        driver_shift = _authenticated_post(driver_context, live_app, '/api/shifts',
            {'kind': 'driver', 'branch_ids': [branch], 'manager_shift_id': manager_shift,
             'vehicle_inspection': _inspection_payload()})['shift']['id']
        tid = _authenticated_post(manager_context, live_app, f'/api/orders/{oid}/assign',
            {'driver_shift_id': driver_shift})['task']['id']
        _authenticated_post(driver_context, live_app, f'/api/tasks/{tid}/transition', {'status': 'accepted'})
        with db.tx() as c:
            before = c.execute('SELECT status,accepted_at,picked_up_at,delivered_at FROM driver_tasks WHERE id=%s', (tid,)).fetchone()
            original = c.execute('SELECT version,preparation_status,delivery_status,created_at FROM orders WHERE id=%s', (oid,)).fetchone()
        driver.locator('#navigation [data-page=tasks]').click()
        action = 'no-answer' if reason == 'no_answer' else 'problem'
        driver.locator(f'[data-action="{action}"][data-id="{tid}"]').click()
        dialog = driver.locator('.amend-dialog[open]')
        form = dialog.locator('form')
        expect(form.locator('[name=reason_code]')).to_be_visible()
        if reason == 'no_answer':
            expect(form.locator('[name=reason_code]')).to_have_value('no_answer')
        else:
            form.locator('[name=reason_code]').select_option(reason)
        expect(form.locator('textarea,[name=contact_method],[name=contact_result],[name=physical_resolution],[name=evidence_attachment_id]')).to_have_count(0)
        with driver.expect_response(lambda response: response.url.endswith(f'/api/order-interventions/orders/{oid}') and response.request.method == 'POST') as saved:
            form.locator('button').click()
        assert saved.value.ok, saved.value.text()
        request_id = saved.value.json()['id']
        with db.tx() as c:
            report = c.execute('SELECT details FROM order_events WHERE id=%s', (request_id,)).fetchone()['details']
            assert report['reason_code'] == reason and report['kind'] == 'problem'
            assert report['pending_role'] == 'delivery_manager'
            assert report['contact_result'] == ('no_answer' if reason == 'no_answer' else 'not_contacted')
            assert c.execute('SELECT status,accepted_at,picked_up_at,delivered_at FROM driver_tasks WHERE id=%s', (tid,)).fetchone() == before
            assert c.execute('SELECT version,preparation_status,delivery_status,created_at FROM orders WHERE id=%s', (oid,)).fetchone() == original
        manager.locator('#navigation [data-page=tasks]').click()
        manager.locator(f'[data-action=order-intervention][data-id="{oid}"]').click()
        manager_dialog = manager.locator('.amend-dialog[open]')
        expect(manager_dialog.locator('form [name=reason_code]')).to_have_value(reason)
        manager_dialog.locator('[name=contact_result]').select_option('not_contacted')
        with manager.expect_response(lambda response: response.url.endswith(f'/{request_id}/decision') and response.request.method == 'POST') as resolved:
            manager_dialog.locator('button[value=resolve]').click()
        assert resolved.value.ok, resolved.value.text()
        with db.tx() as c:
            decision = c.execute("SELECT actor_id,details FROM order_events WHERE order_id=%s AND event_type='intervention.resolved' AND details->>'request_id'=%s", (oid, request_id)).fetchone()
            assert decision['actor_id'] == users['manager']['id']
            assert decision['details']['contact_result'] == 'not_contacted'
            assert c.execute('SELECT status,accepted_at,picked_up_at,delivered_at FROM driver_tasks WHERE id=%s', (tid,)).fetchone() == before
            assert c.execute('SELECT version,preparation_status,delivery_status,created_at FROM orders WHERE id=%s', (oid,)).fetchone() == original
            assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='financial_case.opened'", (oid,)).fetchone()['n'] == 0
        assert not driver_errors and not manager_errors
    finally:
        for context in contexts:
            context.close()
