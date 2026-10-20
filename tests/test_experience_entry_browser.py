"""Actual customer assets in Chromium with explicit synthetic API responses.

No SMS, production requests, payments or real orders are sent by these tests.
PostgreSQL behavior is certified separately in test_experience_policy_postgres.
"""
import functools
import json
import os
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import pytest

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def static_customer():
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self,*args): pass
    server = ThreadingHTTPServer(('127.0.0.1',0), functools.partial(Handler,directory=str(ROOT/'public')))
    worker = Thread(target=server.serve_forever,daemon=True);worker.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown();server.server_close();worker.join(timeout=5)


@pytest.mark.parametrize('lang,width', [(lang,width) for lang in ('ar','en') for width in (390,768,1366)])
def test_name_phone_location_eligible_branch_and_no_overflow(static_customer,lang,width):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width':width,'height':950})
        page = context.new_page();errors=[];requests=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        branch = {'id':'demo-branch','brand_id':'kps','branch_no':1,'name':'فرع المروج','city':'حفر الباطن',
                  'location':{'lat':28.4,'lng':45.9},'location_verified':True,'payment_methods':['cash','card'],
                  'services':{'delivery':{'available':True},'pickup':{'available':False},'car':{'available':False}}}
        user = {'id':'synthetic-customer','role':'customer','brand_id':'kps','display_name':'علي','phone':'+966512345678'}
        session = {'user':user,'csrf_token':'synthetic-only','customer_verified':True}
        def api(route):
            request = route.request;path = request.url.split('/api/',1)[1].split('?',1)[0]
            requests.append(path)
            responses = {
                'catalog':{'brand':{'id':'kps','name':'KPS COFFEE'},'products':[],'source_verified':True},
                'branches':{'branches':[branch,{**branch,'id':'outside-branch','name':'فرع خارج النطاق'}],
                            'brand_services':{'delivery':True,'pickup':False,'car_pickup':False}},
                'session':{'user':None,'csrf_token':None,'customer_verified':False},
                'identity/channels':{'customer_phone_required':True,'channels':{'sms':{'available':True}}},
                'experience/public':{'customer_entry_enabled':True},
                'banners':{'banners':[]},
                'auth/otp/request':{'challenge_id':'synthetic-challenge','channel':'sms'},
                'auth/otp/verify':session,
                'customer/profile':{'addresses':[]},
                'branches/nearest':{'branches':[{**branch,'available':True,'direct_distance_meters':500}],'excluded':[]},
            }
            if path not in responses:
                raise AssertionError('Unexpected API operation: '+path)
            route.fulfill(status=200,content_type='application/json',body=json.dumps(responses[path]))
        page.route('**/api/**',api)
        context.add_init_script(f"localStorage.setItem('kps_customer_lang','{lang}')")
        page.goto(static_customer+'/',wait_until='networkidle')
        expect(page.locator('#entry-form')).to_be_visible()
        expect(page.locator('#menu')).to_be_hidden()
        page.locator('#entry-form [name=name]').fill('علي')
        page.locator('#entry-form [name=phone]').fill('512345678')
        page.locator('#entry-form [type=submit]').click()
        expect(page.locator('#otp-verify-form')).to_be_visible()
        page.locator('#otp-code').fill('1234')
        page.locator('#verify-otp').click()
        expect(page.locator('.service-panel')).to_be_visible()
        expect(page.locator('#menu')).to_be_hidden()
        page.locator('#manual-location-button').click()
        page.locator('#manual-location details summary').click()
        page.locator('#latitude').fill('28.4');page.locator('#longitude').fill('45.9')
        page.locator('#save-location').click()
        expect(page.locator('#nearest-branches .nearest-branch-card')).to_have_count(1)
        page.locator('#nearest-branches button').last.click()
        expect(page.locator('#menu')).to_be_visible()
        expect(page.locator('#branch-select option[value=outside-branch]')).to_have_count(0)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert 'auth/otp/request' in requests and 'auth/otp/verify' in requests
        assert not any(path in {'orders','quote'} for path in requests)
        out=ROOT/'test-results';out.mkdir(exist_ok=True)
        page.screenshot(path=str(out/f'customer-entry-{lang}-{width}.png'),full_page=True)
        assert not errors,errors
        context.close();browser.close()
