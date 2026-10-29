"""Live camera capture and failure recovery with persisted stage evidence."""
import os
import pytest
from app import db
from test_browser import (live_app, chromium_browser, synthetic_mail, _role_fixture,
                          _context, _staff_ui_login, _authenticated_post,
                          _inspection_payload, _attach_task_photo, _submit_modal)

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.getenv('TEST_BROWSER') != '1', reason='Requires real Chromium and isolated PostgreSQL')]


@pytest.mark.parametrize('language', ['ar', 'en'])
@pytest.mark.parametrize('camera_mode', ['stream', 'denied'])
def test_live_camera_and_recovery_save_real_stage_photos(live_app, chromium_browser, language, camera_mode):
    from playwright.sync_api import expect
    users, branch, order = _role_fixture()
    contexts = []
    try:
        manager_context, manager, _ = _context(chromium_browser, live_app)
        contexts.append(manager_context)
        driver_context, driver, errors = _context(chromium_browser, live_app)
        contexts.append(driver_context)
        driver.add_init_script(f"localStorage.setItem('kps_ops_lang', '{language}')")
        driver.add_init_script("""(() => {
          window.__cameraStopped=0;
          navigator.mediaDevices.getUserMedia=async () => {
            CAMERA_FAILURE
            const canvas=document.createElement('canvas');canvas.width=320;canvas.height=240;
            const context=canvas.getContext('2d');context.fillStyle='green';context.fillRect(0,0,320,240);
            const stream=canvas.captureStream(10);
            const timer=setInterval(()=>context.fillRect(0,0,320,240),100);
            for(const track of stream.getTracks()){
              const stop=track.stop.bind(track);
              track.stop=()=>{window.__cameraStopped++;clearInterval(timer);stop();};
            }
            return stream;
          };
        })();""".replace('CAMERA_FAILURE', "throw new DOMException('Denied for test','NotAllowedError');" if camera_mode=='denied' else ''))
        _staff_ui_login(manager, users['manager'])
        _staff_ui_login(driver, users['driver'])
        # The synthetic stream below must not conceal a real browser policy
        # denial: the document itself must permit same-origin camera access.
        response = driver_context.request.get(live_app + '/ops')
        assert response.headers['permissions-policy'] == 'camera=(self), microphone=(), geolocation=(self)'
        assert driver.evaluate("document.featurePolicy.allowsFeature('camera')")
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
            driver.locator(action).click()
            expect(driver.locator('#task-photo-gallery')).to_have_count(0)
            if camera_mode == 'stream':
                expect(driver.locator('#task-camera-live')).to_be_visible()
                expect(driver.locator('#task-photo-shutter')).to_be_enabled()
                expect(driver.locator('#task-native-camera')).to_be_hidden()
                driver.locator('#task-photo-shutter').click()
            else:
                expect(driver.locator('#task-native-camera')).to_be_visible()
                expect(driver.locator('#task-photo-shutter')).to_be_disabled()
                with db.tx() as conn:
                    assert not conn.execute('SELECT 1 FROM delivery_photos WHERE order_id=%s AND stage=%s',(order,stage)).fetchone()
                with driver.expect_file_chooser() as camera:
                    driver.locator('#task-native-camera').click()
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
        if camera_mode == 'stream':
            assert driver.evaluate('window.__cameraStopped') >= 2
        assert errors == []
    finally:
        for context in contexts:
            context.close()
