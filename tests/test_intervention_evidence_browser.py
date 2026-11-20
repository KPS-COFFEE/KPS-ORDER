"""Real file inputs and ops callbacks keep evidence in the selected order brand."""
import base64
from copy import deepcopy
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import pytest

from test_intervention_supervision_browser import payload, supervision_browser

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser acceptance')
ROOT = Path(__file__).resolve().parents[1]
ORDER = 'TEST-enbishan-order'
PATH = '/api/order-interventions/orders/' + ORDER
PICTURE = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a/BsAAAAASUVORK5CYII=')


@pytest.mark.parametrize('stage,width', [('create', 390), ('returned', 390), ('returned', 1280)])
def test_direct_evidence_keeps_draft_on_failure_and_reuses_upload(supervision_browser, tmp_path, stage, width):
    from playwright.sync_api import expect

    data = payload('returned')
    data.update(high_authority=False, customer_requests=[], attachments=[])
    if stage == 'create':
        data.update(requests=[], available_create_kinds=['cancel', 'problem'])
    current = deepcopy(data)
    requests, errors = [], []
    attempts = {'submit': 0, 'upload': 0, 'conversation': 0}
    page = supervision_browser.new_page(viewport={'width': width, 'height': 900}, locale='ar-SA')
    page.on('pageerror', lambda error: errors.append(str(error)))
    html = re.sub(r'<script[\s\S]*?</script>|<link[^>]*>', '', (ROOT / 'public/ops.html').read_text())

    def route_request(route):
        request = route.request
        path = urlsplit(request.url).path
        if not path.startswith('/api/'):
            return route.fulfill(content_type='text/html', body=html)
        requests.append((request.method, path, request.post_data_json if request.method == 'POST' else None))
        if path == '/api/central-operations/resource-context':
            return route.fulfill(json={'brand_id': 'enbishan'})
        assert request.headers.get('x-operations-brand') == 'enbishan'
        if path == PATH and request.method == 'GET':
            return route.fulfill(json=current)
        if path == '/api/conversations' and request.method == 'POST':
            assert request.post_data_json == {'order_id': ORDER}
            attempts['conversation'] += 1
            return route.fulfill(json={'conversation': {'id': 'TEST-evidence-chat', 'brand_id': 'enbishan', 'order_id': ORDER}})
        if path == '/api/conversations/TEST-evidence-chat/attachments':
            body = request.post_data_json
            assert body['content_type'] == 'image/png'
            assert base64.b64decode(body['data_base64']) == PICTURE
            attempts['upload'] += 1
            return route.fulfill(json={'id': 'TEST-uploaded-image', 'url': '/api/chat-attachments/TEST-uploaded-image?context=work'})
        target = PATH if stage == 'create' else PATH + '/TEST-request/resubmit'
        if path == target and request.method == 'POST':
            body = request.post_data_json
            assert body['evidence_attachment_id'] == 'TEST-uploaded-image'
            assert body['contact_result'] == 'agreed'
            if stage == 'create':
                assert body['reason_code'] == 'customer_cancel'
            else:
                assert body['note'] == 'تم إرفاق صورة المحادثة المطلوبة'
                assert body['workflow_revision'] == 2
            attempts['submit'] += 1
            if attempts['submit'] == 1:
                return route.fulfill(status=409, json={'error': {'code': 'TEST_CONFLICT', 'message': 'تعذر حفظ المعاملة التجريبية؛ أعد المحاولة'}})
            current.update(requests=[], available_create_kinds=[], attachments=[{'id': 'TEST-uploaded-image'}])
            return route.fulfill(json={'id': 'TEST-request'})
        raise AssertionError('Unexpected request: ' + request.method + ' ' + path)

    try:
        page.route('**/*', route_request)
        page.goto('https://qa.invalid/ops')
        styles = '\n'.join((ROOT / path).read_text() for path in (
            'public/ops.css', 'public/assets/order-amendments.css', 'public/assets/order-interventions.css'))
        page.add_style_tag(content=styles)
        page.evaluate("""()=>{
            window.setInterval=()=>0;
            window.AudioContext=undefined;window.webkitAudioContext=undefined;
            window.KPSDriverLocation={init:()=>({refresh:async()=>{},reset(){}})};
            window.KPSDriverChecks={init:()=>({refresh:async()=>{},reset(){}})};
        }""")
        for path in ('public/assets/central-operations.js', 'public/assets/order-interventions.js'):
            page.add_script_tag(content=(ROOT / path).read_text())
        source, count = re.subn(r'  init\(\);\s*\}\)\(\);\s*$',
                               '  window.qa={state,openOrderIntervention};\n})();',
                               (ROOT / 'public/ops.js').read_text())
        assert count == 1
        page.add_script_tag(content=source)
        page.evaluate("""async oid=>{
            qa.state.user={id:'TEST-manager',role:'delivery_manager',brand_id:'kps',branch_ids:['kps-1'],
                effective_permissions:['orders_view','chat_reply'],central_operations:{brands:[
                    {id:'kps',branch_ids:['kps-1']},{id:'enbishan',branch_ids:['enb-1']}]}};
            qa.state.csrf='synthetic';qa.state.lang='ar';
            await qa.openOrderIntervention(oid);
        }""", ORDER)
        dialog = page.locator('.intervention-dialog')
        if stage == 'create':
            dialog.locator('[name=reason_code]').select_option('customer_cancel')
        else:
            dialog.locator('[name=note]').fill('تم إرفاق صورة المحادثة المطلوبة')
        dialog.locator('[name=contact_result]').select_option('agreed')
        picker = dialog.locator('[name=evidence_image]')
        picker.set_input_files({'name': 'synthetic-proof.png', 'mimeType': 'image/png', 'buffer': PICTURE})
        assert attempts == {'submit': 0, 'upload': 0, 'conversation': 0}
        button = dialog.get_by_role('button', name='رفع الطلب للمسؤول' if stage == 'create' else 'إعادة الرفع للمراجعة', exact=True)
        button.click()
        expect(dialog.locator('[role=alert]')).not_to_be_empty()
        expect(button).to_be_enabled()
        expect(dialog.locator('[name=contact_result]')).to_have_value('agreed')
        assert picker.evaluate('(el)=>el.files[0].name') == 'synthetic-proof.png'
        if stage == 'returned':
            expect(dialog.locator('[name=note]')).to_have_value('تم إرفاق صورة المحادثة المطلوبة')
        assert attempts == {'submit': 1, 'upload': 1, 'conversation': 1}
        assert dialog.evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        screenshot_dir = Path(os.getenv('KPS_SUPERVISION_SCREENSHOTS', str(tmp_path)))
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot_dir / f'direct-evidence-{stage}-{width}.png'), full_page=True)
        button.click()
        expect(picker).to_have_count(0)
        assert attempts == {'submit': 2, 'upload': 1, 'conversation': 1}
        assert not any(path.endswith('/messages') for _, path, _ in requests)
        assert not errors, errors
    finally:
        page.close()
