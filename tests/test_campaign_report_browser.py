"""Actual staff report assets with isolated API responses; never live orders."""
import json
import os
from pathlib import Path
from urllib.parse import parse_qs,urlsplit
import pytest
from test_experience_entry_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser run')


@pytest.mark.parametrize('lang,width',[('ar',390),('en',1366)])
def test_scoped_report_filters_masked_beneficiaries_and_pagination(static_customer,lang,width):
    from playwright.sync_api import sync_playwright,expect
    calls=[]
    owner={'id':'owner','role':'owner','brand_id':'kps','effective_permissions':['reports_view'],
           'operations_brands':[{'id':'kps','name':'KPS COFFEE'},{'id':'enbishan','name':'ENBISHAN'}]}
    def respond(route):
        request=route.request;url=urlsplit(request.url);q=parse_qs(url.query);calls.append((url.path,q,dict(request.headers)))
        selected=request.headers.get('x-operations-brand')
        assert request.headers.get('x-kps-context')=='work'
        if url.path=='/api/session':
            user=dict(owner,brand_id='enbishan' if selected=='enbishan' else 'kps')
            return route.fulfill(json={'user':user,'csrf_token':'synthetic-unused'})
        assert selected=='enbishan'
        if url.path.endswith('/daily'):
            summary={'online_orders':3,'unique_customers':2,'first_time_customers':1,'repeat_customers':1,'gift_claims':2,'completed_orders':1,'cancelled_orders':1,'cancelled_gift_orders':1}
            return route.fulfill(json={'brand_id':'enbishan','start':q['start'][0],'end':q['end'][0],
              'summary':summary,'branches':[{'id':'enb-2','name':'المحمدية'}],
              'daily':[dict(summary,report_date='2026-10-22',non_cancelled_orders=2)]})
        offset=int(q['offset'][0]);n=51 if offset==0 else 1
        return route.fulfill(json={'brand_id':'enbishan','beneficiaries':[{'order_id':'test-'+str(i+offset),'order_no':i+offset+1,'branch_id':'enb-2','policy_version':1,'phone_suffix':'••••1449','claimed_at':'2026-10-22T13:00:00+00:00','cancelled':False,'first_online_order':False} for i in range(n)]})
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':width,'height':1000})
        page.add_init_script("localStorage.setItem('kps-ops-language',"+json.dumps(lang)+")")
        page.route('**/api/**',respond)
        page.goto(static_customer+'/campaign-report.html?operations_brand=enbishan')
        expect(page.locator('#report-content')).to_be_visible()
        expect(page.locator('#report-brand')).to_have_text('ENBISHAN')
        assert page.locator('html').get_attribute('dir')==('rtl' if lang=='ar' else 'ltr')
        assert page.locator('.beneficiary-card').count()==50
        assert '••••1449' in page.locator('#report-beneficiaries').inner_text()
        assert '+966' not in page.locator('body').inner_text()
        assert page.evaluate('document.documentElement.scrollWidth')<=width
        page.locator('[name=start]').fill('2026-10-22');page.locator('[name=end]').fill('2026-10-22')
        page.locator('[name=branch_id]').select_option('enb-2');page.locator('[name=policy_version]').fill('1')
        page.locator('#report-filters button[type=submit]').click()
        expect(page.locator('#report-applied')).to_contain_text('2026-10-22')
        page.locator('#report-next').click()
        expect(page.locator('.beneficiary-card')).to_have_count(1)
        expect(page.locator('#report-next')).to_be_disabled()
        last=[c for c in calls if c[0].endswith('/beneficiaries')][-1]
        assert last[1]['offset']==['50'] and last[1]['policy_version']==['1'] and last[1]['branch_id']==['enb-2']
        out=Path('test-results')/f'campaign-report-{lang}-{width}.png';out.parent.mkdir(exist_ok=True)
        page.screenshot(path=str(out),full_page=True)
        page.locator('#report-language').click()
        actual='en' if lang=='ar' else 'ar'
        assert page.locator('html').get_attribute('lang')==actual
        page.screenshot(path=str(out.with_name(f'campaign-report-{actual}-{width}.png')),full_page=True)
        browser.close()


def test_forged_brand_and_withdrawn_report_access_show_no_report(static_customer):
    from playwright.sync_api import sync_playwright,expect
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page()
        calls=[]
        def respond(route):
            calls.append(route.request.url)
            route.fulfill(json={'user':{'role':'delivery_manager','brand_id':'kps','effective_permissions':[],'operations_brands':[{'id':'kps'}]}})
        page.route('**/api/**',respond)
        page.goto(static_customer+'/campaign-report.html?operations_brand=enbishan')
        expect(page.locator('#report-status')).to_have_class('error')
        expect(page.locator('#report-content')).to_be_hidden()
        assert len(calls)==1 and urlsplit(calls[0]).path=='/api/session'
        browser.close()
