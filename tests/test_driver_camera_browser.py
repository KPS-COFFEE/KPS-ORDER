"""Native-picker intent and real persisted evidence; no physical-device camera claim."""
import os
import pytest
from app import db
from test_browser import (live_app, chromium_browser, synthetic_mail, _role_fixture,
                          _context, _staff_ui_login, _authenticated_post,
                          _inspection_payload, _attach_task_photo, _submit_modal)

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.getenv('TEST_BROWSER') != '1', reason='Requires real Chromium and isolated PostgreSQL')]


@pytest.mark.parametrize('language', ['ar', 'en'])
def test_camera_first_gallery_fallback_and_saved_stage_photos(live_app, chromium_browser, language):
    from playwright.sync_api import expect
    users, branch, order = _role_fixture()
    contexts = []
    try:
        manager_context, manager, _ = _context(chromium_browser, live_app)
        contexts.append(manager_context)
        driver_context, driver, errors = _context(chromium_browser, live_app)
        contexts.append(driver_context)
        driver.add_init_script(f"localStorage.setItem('kps_ops_lang', '{language}')")
        _staff_ui_login(manager, users['manager'])
        _staff_ui_login(driver, users['driver'])
        manager_shift = _authenticated_post(manager_context, live_app, '/api/shifts',
                                             {'kind': 'manager', 'branch_ids': [branch]})['shift']['id']
        driver_shift = _authenticated_post(driver_context, live_app, '/api/shifts', {
            'kind': 'driver', 'branch_ids': [branch], 'manager_shift_id': manager_shift,
            'vehicle_inspection': _inspection_payload()})['shift']['id']
        task = _authenticated_post(manager_context, live_app, f'/api/orders/{order}/assign',
                                   {'driver_shift_id': driver_shift})['task']['id']
        _authenticated_post(driver_context, live_app, f'/api/tasks/{task}/transition', {'status': 'accepted'})
        with db.tx() as conn:
            conn.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s", (order,))
        driver.locator('#navigation [data-page=tasks]').click()
        for stage, action in [('picked_up', '[data-action=task-transition][data-status=picked_up]'),
                              ('delivered', '[data-action=delivered]')]:
            # Desktop Chromium exercises the actual native file-chooser fallback.
            with driver.expect_file_chooser() as initial:
                driver.locator(action).click()
            assert initial.value.element.get_attribute('capture') == 'environment'
            expect(driver.locator('#task-photo-gallery')).to_be_visible()
            # Cancelling the first chooser leaves gallery and camera usable.
            with driver.expect_file_chooser() as gallery:
                driver.locator('#task-photo-gallery').click()
            assert gallery.value.element.get_attribute('capture') is None
            if stage == 'delivered':
                with driver.expect_file_chooser() as camera:
                    driver.locator('#task-photo-camera').click()
                assert camera.value.element.get_attribute('capture') == 'environment'
            _attach_task_photo(driver)
            preview = driver.locator('#task-photo-preview')
            expect(preview).to_be_visible()
            expect(preview).to_have_js_property('complete', True)
            assert preview.evaluate('(img) => img.naturalWidth') == 320
            _submit_modal(driver)
            if stage == 'picked_up':
                _authenticated_post(driver_context, live_app, f'/api/orders/{order}/collect', {
                    'method': 'cash', 'amount_halalas': 1000, 'tendered_halalas': 1000})
                driver.locator('#navigation [data-page=tasks]').click()
        with db.tx() as conn:
            photos = conn.execute('SELECT * FROM delivery_photos WHERE order_id=%s', (order,)).fetchall()
            assert len(photos) == 2
            assert {photo['stage'] for photo in photos} == {'picked_up', 'delivered'}
            assert all(photo['uploader_id'] == users['driver']['id'] and photo['task_id'] == task and photo['used_at'] for photo in photos)
        for photo in photos:
            response = manager_context.request.get(live_app + '/api/delivery-photos/' + photo['id'])
            assert response.status == 200 and response.headers['content-type'].startswith('image/')
            assert len(response.body()) > 0
        assert errors == []
    finally:
        for context in contexts:
            context.close()
