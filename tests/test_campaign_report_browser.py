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


@pytest.mark.parametrize('engine',['chromium','webkit'])
@pytest.mark.parametrize('lang',['ar','en'])
def test_owner_finalizes_full_period_reopens_frozen_pages_and_downloads_all(static_customer,engine,lang,tmp_path):
    from playwright.sync_api import sync_playwright,expect
    calls=[];created=False
    user={'id':'test-owner','role':'owner','brand_id':'enbishan','effective_permissions':['reports_view'],
          'operations_brands':[{'id':'enbishan','name':'ENBISHAN'}]}
    summary={'online_orders':3,'unique_customers':2,'first_time_customers':1,'repeat_customers':1,'gift_claims':56,
             'completed_orders':1,'cancelled_orders':1,'cancelled_gift_orders':1}
    rows=[{'order_id':'test-'+str(i),'order_no':i+1,'branch_id':'enb-2','policy_version':1,'phone_suffix':'••••1449',
           'claimed_at':'2026-10-22T13:00:00+00:00','cancelled':False,'first_online_order':False} for i in range(56)]
    report={'brand_id':'enbishan','start':'2026-10-22','end':'2026-10-22','selected_branch_id':None,'policy_version':None,
            'summary':summary,'branches':[{'id':'enb-2','name':'المحمدية'}],
            'daily':[dict(summary,report_date='2026-10-22',non_cancelled_orders=2)]}
    closure={'id':'test-final-period','brand_id':'enbishan','actor_id':'test-owner','reason':'اختبار إقفال الفترة',
             'created_at':'2026-10-23T04:00:00Z','sha256':'a'*64}
    def respond(route):
        nonlocal created
        request=route.request;url=urlsplit(request.url);q=parse_qs(url.query)
        calls.append((request.method,url.path,q,dict(request.headers)))
        assert request.headers.get('x-kps-context')=='work'
        if url.path=='/api/session':return route.fulfill(json={'user':user,'csrf_token':'synthetic-unused'})
        if url.path.endswith('/daily'):return route.fulfill(json=report)
        if url.path.endswith('/beneficiaries'):
            offset=int(q.get('offset',['0'])[0]);return route.fulfill(json={'brand_id':'enbishan','beneficiaries':rows[offset:offset+51]})
        if url.path.endswith('/closures'):
            if request.method=='POST':
                assert request.headers.get('x-csrf-token')=='synthetic-unused'
                assert len(request.headers.get('idempotency-key',''))>=8
                assert request.post_data_json=={'start':'2026-10-22','end':'2026-10-22','branch_id':None,'policy_version':None,'reason':'اختبار إقفال الفترة'}
                created=True
                return route.fulfill(json={'brand_id':'enbishan','closure':closure,'beneficiary_count':56,'immutable':True,'campaign_state_changed':False})
            return route.fulfill(json={'brand_id':'enbishan','closures':[dict(closure,start_date='2026-10-22',end_date='2026-10-22')] if created else []})
        assert created
        if url.path.endswith('/download'):
            assert q['lang']==[lang]
            return route.fulfill(body='\ufeffAll beneficiaries,56\n'+''.join('test-'+str(i)+',••••1449\n' for i in range(56)),content_type='text/csv')
        assert url.path.endswith('/closures/test-final-period')
        offset=int(q.get('offset',['0'])[0]);limit=int(q.get('limit',['50'])[0])
        return route.fulfill(json={'closure':closure,'report':report,'beneficiaries':rows[offset:offset+limit],
                                  'beneficiary_count':56,'offset':offset,'limit':limit,'immutable':True})
    with sync_playwright() as p:
        browser=getattr(p,engine).launch(headless=True);page=browser.new_page(viewport={'width':390,'height':950},accept_downloads=True)
        page.add_init_script("localStorage.setItem('kps-ops-language',"+json.dumps(lang)+")")
        page.route('**/api/**',respond)
        confirmations=[]
        page.on('dialog',lambda dialog:(confirmations.append(dialog.message),dialog.accept()))
        page.goto(static_customer+'/campaign-report.html')
        expect(page.locator('#report-finalize-form')).to_be_visible()
        page.locator('[name=start]').fill('2026-10-22');page.locator('[name=end]').fill('2026-10-22')
        page.locator('#report-filters button[type=submit]').click()
        expect(page.locator('#report-applied')).to_contain_text('2026-10-22')
        page.locator('#report-finalize-reason').fill('اختبار إقفال الفترة')
        page.locator('#report-finalize').click()
        expect(page.locator('#report-final-evidence')).to_be_visible()
        expect(page.locator('#report-finalize-form')).to_be_hidden()
        assert confirmations and ('بحالتهما' in confirmations[0] if lang=='ar' else 'keep their current state' in confirmations[0])
        assert len([x for x in calls if x[0]=='POST'])==1
        expect(page.locator('.beneficiary-card')).to_have_count(50)
        page.locator('#report-next').click()
        expect(page.locator('.beneficiary-card')).to_have_count(6)
        expect(page.locator('#report-next')).to_be_disabled()
        page.locator('#report-saved-refresh').click()
        expect(page.locator('#report-saved option')).to_have_count(2)
        page.locator('#report-saved').select_option('test-final-period')
        expect(page.locator('.beneficiary-card')).to_have_count(50)
        with page.expect_download() as download:
            page.locator('#report-download').click()
        path=tmp_path/(engine+'-'+lang+'.csv');download.value.save_as(path)
        content=path.read_text();assert 'All beneficiaries,56' in content and 'test-55' in content
        assert '+966' not in content
        assert page.evaluate('document.documentElement.scrollWidth')<=390
        out=Path('test-results')/f'campaign-final-report-{lang}-{engine}-390.png';out.parent.mkdir(exist_ok=True)
        page.screenshot(path=str(out),full_page=True)
        browser.close()
