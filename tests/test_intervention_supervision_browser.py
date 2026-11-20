"""Arabic dialog layout and real browser form behavior, without a database."""
from copy import deepcopy
import os
from pathlib import Path

import pytest

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance')
ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def supervision_browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        options={'headless':True}
        if os.getenv('PLAYWRIGHT_CHROMIUM_EXECUTABLE'):
            options['executable_path']=os.environ['PLAYWRIGHT_CHROMIUM_EXECUTABLE']
        browser=p.chromium.launch(**options)
        yield browser
        browser.close()


def payload(stage):
    row={'id':'TEST-request','created_at':'2026-10-29T12:00:00Z','requested_by_name':'مدير التوصيل — حساب اختبار',
         'details':{'kind':'cancel','reason_code':'late_complaint','pending_role':'owner_or_cancel_delegate'},'decision':None,
         'workflow':{'revision':2,'status':'resubmitted','original_reason_code':'late_complaint','reason_code':'no_answer',
             'current_responsible_role':'owner_or_cancel_delegate','current_responsible_name':'المراجع المخول — حساب اختبار',
             'next_action_label':'بانتظار مراجعتك: تحقق من السبب والأدلة ثم اعتمد أو أعد المعاملة للاستكمال',
             'return_note':'صحح سبب الإحالة ووضح نتيجة الاتصال الفعلية. لا تغير السبب الأصلي أو تفترض موافقة العميل.',
             'preferred_return_target_id':'TEST-manager','available_actions':['approve','reject','return'],
             'history':[{'id':'TEST-return','event_type':'intervention.returned','created_at':'2026-10-30T12:00:00Z',
                 'actor_name':'المراجع المخول','target_manager_name':'مدير التوصيل — حساب اختبار','reason_code':'no_answer','note':'طلب استكمال نتيجة الاتصال وإثباتها في السجل.'}]}}
    if stage=='returned':
        row['workflow'].update(status='returned',reason_code='customer_cancel',current_responsible_role='delivery_manager',
            current_responsible_name='مدير التوصيل — حساب اختبار',next_action_label='استكمل طلب العميل الموثق ثم أعد المعاملة للاعتماد',available_actions=['resubmit'])
    if stage=='accounting':
        row['decision']={'status':'approved'}
        row['workflow'].update(status='approved',current_responsible_role='accounting',current_responsible_name=None,
            financial_pending=True,next_action_label='المحاسبة: مطابقة التحصيل والاسترداد الفعلي وإقفال الملف المالي',available_actions=[])
        row['workflow']['history'].append({'id':'TEST-accounting','event_type':'intervention.accounting_pending',
            'created_at':'2026-10-31T12:00:00Z','actor_name':'المراجع المخول','note':'اعتماد الإلغاء التشغيلي؛ المعالجة المالية لم تقفل.'})
    return {'enabled':True,'printed':True,'high_authority':True,'version':7,'requests':[row],
        'reasons':{'late_complaint':'شكوى تأخير','no_answer':'العميل لا يرد','customer_cancel':'طلب العميل الإلغاء'},
        'contact_results':{'not_contacted':'لم يتم التواصل','agreed':'تم التواصل ووافق','no_answer':'لا يرد'},
        'pending_roles':{'accounting':'المحاسبة','delivery_manager':'مدير التوصيل','owner_or_cancel_delegate':'المراجع المخول'},
        'statuses':{},'available_create_kinds':[],'return_targets':[{'id':'TEST-manager','display_name':'مدير التوصيل — حساب اختبار'}],
        'customer_requests':[{'id':'TEST-proof','created_at':'2026-10-30T12:00:00Z','details':{'note':'طلب إلغاء صريح من العميل في المحادثة'}}],
        'attachments':[{'id':'TEST-image','created_at':'2026-10-30T12:00:00Z'}]}


@pytest.mark.parametrize('width',[390,1280])
@pytest.mark.parametrize('stage',['review','returned','accounting'])
def test_arabic_workflow_fits_and_keeps_reason_responsibility_and_actions(supervision_browser,tmp_path,width,stage):
    from playwright.sync_api import expect
    page=supervision_browser.new_page(viewport={'width':width,'height':900},locale='ar-SA')
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    try:
        styles='\n'.join((ROOT/path).read_text() for path in ('public/ops.css','public/assets/order-amendments.css','public/assets/order-interventions.css'))
        page.set_content('<!doctype html><html lang="ar" dir="rtl"><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+styles+'</style></head><body></body></html>')
        page.add_script_tag(content=(ROOT/'public/assets/order-interventions.js').read_text())
        page.evaluate("""async ({data,role})=>{window.TEST_WRITES=[];await KPSInterventions.open({oid:'TEST-order',user:{id:'TEST-manager',role},api:async()=>structuredClone(data),mutation:async(path,body)=>{window.TEST_WRITES.push({path,body});return {status:'saved'};}});} """,{'data':payload(stage),'role':'delivery_manager' if stage in ('returned','accounting') else 'admin'})
        dialog=page.locator('.intervention-dialog')
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text('سبب الإحالة الأصلي')
        expect(dialog).to_contain_text('السبب بعد التصحيح')
        expect(dialog.locator('.intervention-next')).to_contain_text('المسؤول الحالي:')
        if stage=='review':
            dialog.locator('.intervention-return summary').click()
            dialog.locator('.intervention-return [name=note]').fill('راجع الملاحظة واستكمل الدليل ثم أعد المعاملة للاعتماد.')
            expect(dialog.locator('[name=target_manager_id]')).to_have_value('TEST-manager')
            expect(dialog.get_by_role('button',name='إرجاع المعاملة للاستكمال')).to_be_visible()
        elif stage=='returned':
            expect(dialog.get_by_role('button',name='إعادة الرفع للمراجعة')).to_be_visible()
            expect(dialog.locator('[name=customer_request_id]')).to_be_visible()
            assert dialog.get_by_role('button',name='اعتماد وتنفيذ الإلغاء').count()==0
        else:
            expect(dialog.locator('.intervention-next')).to_contain_text('المسؤول الحالي: المحاسبة')
            expect(dialog.locator('.intervention-next')).to_contain_text('مطابقة التحصيل والاسترداد الفعلي')
            assert dialog.locator('form').count()==0
        dialog.locator('.intervention-history summary').click()
        assert page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth')
        assert dialog.evaluate('(el)=>el.scrollWidth <= el.clientWidth+1')
        assert dialog.locator('textarea,select').evaluate_all('(els)=>els.every(el=>el.getBoundingClientRect().width<=el.parentElement.getBoundingClientRect().width+1)')
        assert page.evaluate('window.TEST_WRITES.length')==0
        dialog.evaluate('(el)=>el.scrollTop=0')
        screenshots=Path(os.getenv('KPS_SUPERVISION_SCREENSHOTS',str(tmp_path)))
        screenshots.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(screenshots/f'processing-{stage}-ar-{width}.png'),full_page=True)
        if stage!='accounting':
            action=dialog.locator('form button').last
            action.scroll_into_view_if_needed()
            expect(action).to_be_in_viewport()
            page.screenshot(path=str(screenshots/f'processing-{stage}-actions-ar-{width}.png'),full_page=True)
        assert not errors
    finally:page.close()
