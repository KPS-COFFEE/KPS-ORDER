"""Real report UI, mobile/desktop, localized filters, safe rendering and errors."""
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import pytest
from tests.test_experience_entry_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser run')


@pytest.mark.parametrize('lang,width',[('ar',390),('ar',1366),('en',390),('en',1366)])
def test_report_filters_pagination_mobile_and_safe_names(static_customer,lang,width):
    from playwright.sync_api import sync_playwright,expect
    calls=[]
    def respond(route):
        request=route.request;url=urlsplit(request.url);q=parse_qs(url.query)
        assert request.headers.get('x-kps-context')=='work'
        if url.path=='/api/session':
            projection=request.headers.get('x-operations-brand')
            assert projection in (None,'enbishan')
            return route.fulfill(json={'user':{'id':'synthetic-report-owner','role':'owner','brand_id':'enbishan' if projection else 'kps','active':True,'branch_ids':[]},'csrf_token':'synthetic-report-csrf'})
        assert request.headers.get('x-operations-brand')=='enbishan'
        assert url.path=='/api/reports/customers'
        calls.append(q)
        offset=int(q['offset'][0]);n=50 if offset==0 else 1
        summary=dict(known_customers=51,purchasing_customers=40,new_customers=10,repeat_customers=30,
                     inactive_customers=11,orders=180,value_halalas=710000,late_customers=7,lapsed_customers=4)
        rows=[dict(id=f'test-{i}',display_name='<img src=x onerror=alert(1)> اسم عميل طويل للاختبار' if i==0 else f'عميل تجريبي {i}',phone='+966500000001',
                   rank=i+1,orders=20,value_halalas=80000,first_order_at='2026-01-01T10:00:00Z',
                   latest_order_at='2026-01-20T10:00:00Z',days_absent=9,segment='repeat',activity='late',usual_gap_days=3)
              for i in range(offset,offset+n)]
        return route.fulfill(json=dict(brand_id='enbishan',start=q['start'][0],end=q['end'][0],summary=summary,customers=rows,
                 next_offset=50 if offset==0 else None,operating_period=dict(configured=True,period_valid=True),
                 generated_at='2026-10-29T10:00:00+03:00',daily=[dict(day='2026-10-29',purchasing_customers=5,
                 new_customers=2,repeat_customers=3,orders=8,value_halalas=20000,repeat_orders=6)]))
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':width,'height':1000});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.add_init_script(f"localStorage.setItem('kps_ops_lang','{lang}')")
        page.route('**/api/**',respond)
        page.goto(static_customer+'/customer-report.html?operations_brand=enbishan')
        expect(page.locator('#report-content')).to_be_visible()
        assert page.locator('html').get_attribute('dir')==('rtl' if lang=='ar' else 'ltr')
        assert page.locator('#ranking tbody tr').count()==50
        assert page.locator('#ranking img').count()==0
        assert page.evaluate('document.documentElement.scrollWidth')<=width
        page.locator('[name=start]').fill('2026-01-01');page.locator('[name=end]').fill('2026-01-20')
        page.locator('[name=sort]').select_option('value')
        page.locator('[name=activity]').select_option('late')
        page.locator('#apply').click()
        expect(page.locator('#ranking-title')).to_contain_text('value' if lang=='en' else 'قيمة')
        assert calls[-1]['sort']==['value'] and calls[-1]['activity']==['late']
        page.locator('#next').click()
        expect(page.locator('#ranking tbody tr')).to_have_count(1)
        assert calls[-1]['offset']==['50']
        expect(page.locator('#next')).to_be_disabled()
        page.locator('#previous').click()
        expect(page.locator('#ranking tbody tr')).to_have_count(50)
        page.locator('#daily-details summary').click()
        expect(page.locator('#daily')).to_be_visible()
        out=Path('test-results')/f'customer-report-{lang}-{width}.png';out.parent.mkdir(exist_ok=True)
        page.screenshot(path=str(out),full_page=True)
        page.locator('#language').click()
        assert page.locator('html').get_attribute('lang')!=lang
        assert errors==[]
        browser.close()


def test_forbidden_and_revoked_access_clear_report(static_customer):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page();calls=[]
        def forbidden(route):
            calls.append(route.request.url)
            return route.fulfill(json={'user':{'role':'driver','brand_id':'kps'}})
        page.route('**/api/**',forbidden)
        page.goto(static_customer+'/customer-report.html')
        expect(page.locator('#report-status')).to_have_class('error')
        expect(page.locator('#report-content')).to_be_hidden()
        assert len(calls)==1
        browser.close()


def test_network_failure_shows_error_without_old_results(static_customer):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page()
        def respond(route):
            if urlsplit(route.request.url).path=='/api/session':return route.fulfill(json={'user':{'id':'synthetic-report-owner','role':'owner','brand_id':'kps','active':True,'branch_ids':[]},'csrf_token':'synthetic-report-csrf'})
            return route.fulfill(status=401,json={'detail':{'message':'انتهت الجلسة'}})
        page.route('**/api/**',respond)
        page.goto(static_customer+'/customer-report.html')
        expect(page.locator('#report-status')).to_contain_text('انتهت الجلسة')
        expect(page.locator('#report-content')).to_be_hidden()
        assert page.locator('#ranking').inner_text()==''
        browser.close()


from app import db
from tests.browser_sms_helpers import sms_browser_app
from test_browser import _context, _owner_login, _staff, chromium_browser
from tests.test_core import _make_user
from tests.test_customer_crm import seed_order


@pytest.mark.postgres
@pytest.mark.parametrize('offline',[False,True])
def test_real_owner_revocation_clears_an_already_open_customer_report(sms_browser_app,chromium_browser,offline):
    from playwright.sync_api import expect
    base=sms_browser_app
    owner,reviewer=_staff(),_staff()
    customer=_make_user()
    with db.tx() as c:
        c.execute("UPDATE users SET display_name='PRIVATE CUSTOMER REPORT',phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s",(customer['id'],))
        seed_order(c,customer)
    work,page,errors=_context(chromium_browser,base)
    admin,admin_page,admin_errors=_context(chromium_browser,base)
    try:
        headers=_owner_login(work,base,owner)
        reviewer_headers=_owner_login(admin,base,reviewer)
        page.bring_to_front()
        page.goto(base+'/customer-report?operations_brand=kps',wait_until='networkidle')
        expect(page.locator('#ranking')).to_contain_text('PRIVATE CUSTOMER REPORT')
        expect(page.locator('#report-content')).to_be_visible()
        if offline:
            work.set_offline(True)
            expect(page.locator('html')).to_have_attribute('data-customer-access-pending','')
            expect(page.locator('#report-content')).not_to_be_visible()
        paused=admin.request.post(base+'/api/identity/staff/'+owner['id']+'/manage',headers=reviewer_headers,
            data={'action':'pause','reason':'Owner ended access to customer reports'})
        assert paused.ok,paused.text()
        if offline:work.set_offline(False)
        expect(page.locator('#report-status')).to_have_class('error',timeout=7000)
        expect(page.locator('#report-content')).to_be_hidden()
        expect(page.locator('#report-filters')).to_be_hidden()
        expect(page.locator('#report-signin')).to_be_visible()
        for target in ['#ranking','#daily','#summary','#applied-period','#applied-filters','#generated']:
            expect(page.locator(target)).to_be_empty()
        assert 'PRIVATE CUSTOMER REPORT' not in page.locator('body').inner_text()
        denied=work.request.get(base+'/api/reports/customers',headers=headers)
        assert denied.status==401,denied.text()
        assert not errors and not admin_errors
    finally:
        admin.close();work.close()
