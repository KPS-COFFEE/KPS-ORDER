"""Real operations assets; synthetic API responses, no real orders or messages."""
import json
import os
from pathlib import Path
from urllib.parse import urlparse
import pytest
from test_experience_entry_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')


@pytest.mark.parametrize('lang,width',[('ar',1366),('en',1366),('ar',390)])
def test_workspace_actions_scope_chat_and_morning_guard(static_customer,lang,width):
    from playwright.sync_api import sync_playwright, expect
    user={'id':'manager','brand_id':'kps','role':'delivery_manager','display_name':'مدير التوصيل',
          'branch_ids':['branch'],'effective_permissions':['dashboard_view','orders_view','delivery_assign',
          'shifts_view','drivers_view','drivers_availability','chat_view','chat_reply','chat_manage','shifts_open']}
    shift={'id':'manager-shift','user_id':'manager','kind':'manager','status':'open','branch_ids':['branch'],
           'opened_at':'2026-10-20T05:00:00+03:00','delivery_window_snapshot':{'period':'morning'}}
    def order(oid,period,prep,delivery='pending'):
        return {'id':oid,'order_no':oid,'branch_id':'branch','service':'delivery','version':1,
                'preparation_status':prep,'delivery_status':delivery,'payment_status':'unpaid',
                'created_at':'2026-10-20T08:00:00+03:00','total_halalas':2500,
                'customer':{'name':'عميل تجريبي','neighborhood':'المروج'},
                'stage_timing':{'color':'red' if oid=='late' else 'normal','total_seconds':100,'reasons':[]},
                'snapshot':{'order_reference':oid,'branch_name':'المروج','delivery_period':period,
                    'delivery_workflow':'branch_fulfillment' if period=='morning' else 'manager_dispatch','items':[]}}
    orders=[order('morning','morning','new'),order('new-evening','evening','new'),order('late','evening','accepted')]
    conversation={'id':'chat1','order_reference':'late','customer_name':'عميل تجريبي','route':'delivery','status':'open','unread_count':1}
    writes=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':width,'height':1000})
        context.add_init_script(f"localStorage.setItem('kps_ops_lang',{json.dumps(lang)})")
        page=context.new_page();errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        def route_api(route):
            req=route.request;path=urlparse(req.url).path
            if req.method!='GET': writes.append((path,req.post_data_json))
            payload={}
            if path=='/api/session':payload={'user':user,'csrf_token':'synthetic-token'}
            elif path=='/api/catalog':payload={'brand':{'id':'kps','name':'KPS COFFEE','theme':{}}}
            elif path=='/api/branches':payload={'branches':[{'id':'branch','name':'المروج','branch_no':1,'city':'حفر الباطن','brand_id':'kps'}]}
            elif path=='/api/orders':payload={'orders':orders,'next_offset':None}
            elif path=='/api/shifts/current':payload={'shifts':[shift]}
            elif path=='/api/operations/drivers':payload={'drivers':[{'id':'driver','display_name':'سائق تجريبي','branch_ids':['branch'],'availability':'available','readiness_action':'ready','active_tasks':0,'shift_id':'driver-shift','shift_status':'open','shift_available':True}]}
            elif path=='/api/conversations':payload={'conversations':[conversation]}
            elif path=='/api/conversations/chat1/messages':payload={'conversation':conversation,'messages':[{'id':'m1','seq':1,'sender_id':'customer','text':'وين طلبي؟','created_at':'2026-10-20T08:02:00+03:00'}]}
            elif path=='/api/orders/new-evening/manager-accept':
                orders[1]['preparation_status']='accepted';payload={'order':orders[1]}
            route.fulfill(status=200,content_type='application/json',body=json.dumps(payload))
        page.route('**/api/**',route_api)
        page.goto(static_customer+'/ops.html')
        expect(page.locator('#dispatch-orders')).to_be_visible()
        expect(page.locator('#dispatch-orders article')).to_have_count(3)
        assert page.locator('[data-action="manager-accept"][data-id="morning"]').count()==0
        assert page.locator('[data-action="assign"][data-id="morning"]').count()==0
        page.locator('[data-action="manager-accept"][data-id="new-evening"]').click()
        expect(page.locator('[data-action="assign"][data-id="new-evening"]')).to_be_visible()
        assert any(path=='/api/orders/new-evening/manager-accept' and body['version']==1 for path,body in writes)
        page.locator('#dispatch-filters [name=late]').check()
        expect(page.locator('#dispatch-orders article')).to_have_count(1)
        page.locator('#dispatch-filters [name=late]').uncheck()
        page.locator('.dispatch-side [data-action=conversation]').click()
        expect(page.locator('#message-form')).to_be_visible()
        page.locator('#message-form textarea').fill('طلبك جارٍ تجهيزه')
        # The 15-second operational poll must keep receiving new orders while
        # a manager composes a reply, without reconstructing the chat form.
        page.evaluate("window.originalComposer=document.querySelector('#message-form textarea'); originalComposer.setSelectionRange(3,3)")
        orders.append(order('arrived-during-reply','evening','new'))
        conversation['last_message_text']='رسالة جديدة أثناء كتابة الرد'
        expect(page.locator('#dispatch-orders article')).to_have_count(4,timeout=25000)
        expect(page.locator('#message-form textarea')).to_have_value('طلبك جارٍ تجهيزه')
        assert page.evaluate("document.querySelector('#message-form textarea')===originalComposer && document.activeElement===originalComposer && originalComposer.selectionStart===3")
        expect(page.locator('.dispatch-side .conversation-preview')).to_have_text('رسالة جديدة أثناء كتابة الرد')
        assert '/ops.html' in page.url
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert not any('/messages' in path for path,_ in writes)
        assert not errors,errors
        Path('test-results').mkdir(exist_ok=True)
        page.screenshot(path=f'test-results/customer-entry-delivery-dashboard-{lang}-{width}.png',full_page=True)
        browser.close()
