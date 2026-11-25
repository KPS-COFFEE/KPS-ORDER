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


@pytest.mark.parametrize('width',[390,1440])
def test_real_browser_media_policy_allows_driver_capture_without_js_override(live_app,width):
    """Chromium fake hardware exercises native getUserMedia and real headers.

    This is browser emulation, not acceptance of a physical phone or camera app.
    No media API, business API or response policy is overridden in this test.
    """
    from playwright.sync_api import sync_playwright,expect
    users,branch,order=_role_fixture()
    with sync_playwright() as runtime:
        launch={'headless':True,'args':['--no-sandbox','--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']}
        if os.getenv('TEST_CHROMIUM_EXECUTABLE'):launch['executable_path']=os.environ['TEST_CHROMIUM_EXECUTABLE']
        browser=runtime.chromium.launch(**launch)
        contexts=[]
        try:
            manager_context,manager,_=_context(browser,live_app);contexts.append(manager_context)
            driver_context,driver,errors=_context(browser,live_app);contexts.append(driver_context)
            driver.set_viewport_size({'width':width,'height':1000})
            driver_context.grant_permissions(['camera'],origin=live_app)
            _staff_ui_login(manager,users['manager']);_staff_ui_login(driver,users['driver'])
            response=driver_context.request.get(live_app+'/ops')
            assert response.status==200
            assert response.headers['permissions-policy']=='camera=(self), microphone=(), geolocation=(self)'
            assert response.headers['x-frame-options']=='DENY'
            assert "frame-ancestors 'none'" in response.headers['content-security-policy']
            assert driver.evaluate("document.featurePolicy.allowsFeature('camera')") is True
            assert driver.evaluate("document.featurePolicy.allowsFeature('microphone')") is False
            assert driver.evaluate("navigator.mediaDevices.getUserMedia.toString().includes('[native code]')")
            manager_shift=_authenticated_post(manager_context,live_app,'/api/shifts',
                {'kind':'manager','branch_ids':[branch]})['shift']['id']
            driver_shift=_authenticated_post(driver_context,live_app,'/api/shifts',
                {'kind':'driver','branch_ids':[branch],'manager_shift_id':manager_shift,
                 'vehicle_inspection':_inspection_payload()})['shift']['id']
            task=_authenticated_post(manager_context,live_app,f'/api/orders/{order}/assign',
                {'driver_shift_id':driver_shift})['task']['id']
            _authenticated_post(driver_context,live_app,f'/api/tasks/{task}/transition',{'status':'accepted'})
            with db.tx() as conn:conn.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(order,))
            driver.locator('#navigation [data-page=tasks]').click()
            for stage,action in [('picked_up','[data-action=task-transition][data-status=picked_up]'),
                                 ('delivered','[data-action=delivered]')]:
                driver.locator(action).click()
                expect(driver.locator('#task-camera-live')).to_be_visible()
                expect(driver.locator('#task-photo-shutter')).to_be_enabled(timeout=10000)
                expect(driver.locator('#task-native-camera')).to_be_hidden()
                media=driver.locator('#task-camera-live').evaluate("video=>({width:video.videoWidth,height:video.videoHeight,video:video.srcObject.getVideoTracks().map(t=>t.readyState),audio:video.srcObject.getAudioTracks().length})")
                assert media['width']>0 and media['height']>0
                assert media['video']==['live'] and media['audio']==0
                driver.locator('#task-photo-shutter').click()
                expect(driver.locator('#task-photo-preview')).to_be_visible()
                expect(driver.locator('#task-photo-preview')).to_have_js_property('complete',True)
                assert driver.locator('#task-photo-preview').evaluate('img=>img.naturalWidth')>0
                _submit_modal(driver)
                if stage=='picked_up':
                    _authenticated_post(driver_context,live_app,f'/api/orders/{order}/collect',
                        {'method':'cash','amount_halalas':1000,'tendered_halalas':1000})
                    driver.locator('#navigation [data-page=tasks]').click()
            with db.tx() as conn:
                photos=conn.execute('SELECT * FROM delivery_photos WHERE order_id=%s',(order,)).fetchall()
                assert len(photos)==2 and {p['stage'] for p in photos}=={'picked_up','delivered'}
                assert all(p['uploader_id']==users['driver']['id'] and p['task_id']==task and p['used_at'] for p in photos)
            for photo in photos:
                saved=manager_context.request.get(live_app+'/api/delivery-photos/'+photo['id'])
                assert saved.status==200 and saved.headers['content-type'].startswith('image/')
                assert saved.body()
            # The same origin/browser grant must not widen the customer page.
            customer=driver.goto(live_app+'/',wait_until='domcontentloaded')
            assert customer.headers['permissions-policy']=='camera=(), microphone=(), geolocation=(self)'
            assert driver.evaluate("document.featurePolicy.allowsFeature('camera')") is False
            denied=driver.evaluate("""async()=>{try{const s=await navigator.mediaDevices.getUserMedia({video:true,audio:false});s.getTracks().forEach(t=>t.stop());return 'UNEXPECTED_STREAM';}catch(e){return e.name;}}""")
            assert denied=='NotAllowedError'
            assert errors==[]
        finally:
            for context in contexts:context.close()
            browser.close()



@pytest.mark.parametrize('language', ['ar', 'en'])
@pytest.mark.parametrize('width', [390, 1280])
def test_driver_issue_language_reasons_conflict_and_followup(chromium_browser, tmp_path, language, width):
    """Real delivery issue controls retain their codes and permissions in both languages."""
    from pathlib import Path
    from playwright.sync_api import expect
    root = Path(__file__).resolve().parents[1]
    page = chromium_browser.new_page(viewport={'width': width, 'height': 900})
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    reasons = {
        'customer_cancel': 'طلب العميل الإلغاء', 'customer_change': 'طلب العميل التعديل',
        'no_answer': 'العميل لا يرد', 'late_complaint': 'شكوى تأخير', 'wrong_address': 'مشكلة في العنوان',
        'damaged': 'مشكلة في الطلب', 'customer_not_present': 'العميل غير موجود',
        'amount_short': 'المبلغ ناقص', 'payment_failed': 'تعذر الدفع',
        'missing_or_wrong_order': 'نقص أو خطأ بالطلب', 'other': 'مشكلة أخرى',
        'branch_unavailable': 'تعذر تنفيذ الفرع', 'duplicate': 'طلب مكرر',
    }
    try:
        styles = '\n'.join((root / p).read_text() for p in (
            'public/ops.css', 'public/assets/order-amendments.css', 'public/assets/order-interventions.css'))
        page.set_content('<html lang="' + language + '" dir="' + ('ltr' if language == 'en' else 'rtl') + '"><head><style>' + styles + '</style></head><body></body></html>')
        page.add_script_tag(content=(root / 'public/assets/order-interventions.js').read_text())
        page.evaluate("""async reasons=>{
            window.qaData={enabled:true,version:7,requests:[],available_create_kinds:['problem'],reasons,pending_roles:{delivery_manager:'مدير التوصيل'}};
            window.qaWrites=[];window.qaConflict=true;
            window.openIssue=()=>KPSInterventions.open({oid:'TEST-issue',user:{id:'TEST-driver',role:'driver'},
                api:async()=>structuredClone(qaData),mutation:async(path,body)=>{
                    if(qaConflict)throw new Error('تغير الطلب؛ حدّث التفاصيل.');
                    qaWrites.push({path,body});return {status:'saved'};
                }});
            await openIssue();
        }""", reasons)
        dialog = page.locator('.intervention-dialog')
        expect(dialog).to_have_attribute('dir', 'ltr' if language == 'en' else 'rtl')
        expect(dialog.locator('[name=reason_code] option')).to_have_count(13)
        assert dialog.locator('[name=kind] option').evaluate_all('(els)=>els.map(e=>e.value)') == ['problem']
        assert dialog.locator('[name=physical_resolution], [name=escalate], [name=evidence_image]').count() == 0
        if language == 'en':
            assert not any('\u0600' <= c <= '\u06ff' for c in dialog.inner_text())
            expect(dialog.locator('[name=reason_code] option[value=no_answer]')).to_have_text('Customer not answering')
        else:
            expect(dialog.locator('[name=reason_code] option[value=no_answer]')).to_have_text('العميل لا يرد')
        dialog.locator('[name=reason_code]').select_option('no_answer')
        dialog.locator('[name=note]').fill('ملاحظة أصلية من السائق')
        submit = dialog.locator('button').filter(has_text='Submit to responsible employee' if language == 'en' else 'رفع الطلب للمسؤول')
        submit.click()
        expect(dialog.locator('[role=alert]')).to_have_text('The order has changed. Refresh its details.' if language == 'en' else 'تغير الطلب؛ حدّث التفاصيل.')
        expect(dialog.locator('[name=reason_code]')).to_have_value('no_answer')
        expect(dialog.locator('[name=note]')).to_have_value('ملاحظة أصلية من السائق')
        assert page.evaluate('qaWrites.length') == 0
        page.evaluate('qaConflict=false')
        submit.click()
        page.wait_for_function('qaWrites.length===1')
        write = page.evaluate('qaWrites[0]')
        assert write['body'] == {
            'version': 7, 'kind': 'problem', 'reason_code': 'no_answer', 'contact_result': 'no_answer',
            'customer_request_id': None, 'evidence_attachment_id': None, 'note': 'ملاحظة أصلية من السائق',
        }
        assert write['path'] == '/api/order-interventions/orders/TEST-issue'
        dialog.locator('[data-close]').click()
        page.evaluate("""async()=>{
            qaData.available_create_kinds=[];
            qaData.requests=[{id:'TEST-request',requested_by_name:'Driver',created_at:'2026-11-19T12:00:00Z',
                details:{kind:'problem',reason_code:'no_answer',note:'ملاحظة السائق <script>'},
                workflow:{status:'requested',original_reason_code:'no_answer',reason_code:'no_answer',available_actions:[],current_responsible_role:'delivery_manager',next_action_label:'معالجة البلاغ وتوثيق الإجراء المتخذ',history:[]}}];
            await openIssue();
        }""")
        dialog = page.locator('.intervention-dialog')
        expect(dialog.locator('.intervention-next')).to_contain_text('Currently responsible: Delivery manager' if language == 'en' else 'المسؤول الحالي: مدير التوصيل')
        expect(dialog.locator('.intervention-next')).to_contain_text('Resolve the reported problem and document the action taken' if language == 'en' else 'معالجة البلاغ وتوثيق الإجراء المتخذ')
        expect(dialog.locator('.intervention-note')).to_have_text('ملاحظة السائق <script>')
        assert dialog.locator('script, form').count() == 0
        assert dialog.evaluate('(el)=>el.scrollWidth <= el.clientWidth+1')
        assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
        page.screenshot(path=str(tmp_path / ('driver-issue-' + language + '-' + str(width) + '.png')))
        assert not errors
    finally:
        page.close()
