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
        extensions_map = {**SimpleHTTPRequestHandler.extensions_map, '.js': 'application/javascript; charset=utf-8'}
        def log_message(self,*args): pass
    server = ThreadingHTTPServer(('127.0.0.1',0), functools.partial(Handler,directory=str(ROOT/'public')))
    worker = Thread(target=server.serve_forever,daemon=True);worker.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown();server.server_close();worker.join(timeout=5)


@pytest.mark.parametrize('entry_enabled,offer_active',[(True,False),(False,True)])
@pytest.mark.parametrize('auth_stage',['early','checkout'])
@pytest.mark.parametrize('lang,width', [(lang,width) for lang in ('ar','en') for width in (390,768,1366)])
def test_public_menu_optional_early_login_and_verified_checkout(static_customer,lang,width,auth_stage,entry_enabled,offer_active):
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
        product={'id':'demo-paid','name':'صنف مدفوع','category':'قهوة','price_halalas':1000,'available':True,'source_verified':True,'option_groups':[]}
        gift_items=[{'product_id':'demo-coffee','name':'قهوة اليوم','quantity':1,'unit_price_halalas':0,'line_total_halalas':0,'is_gift':True},{'product_id':'demo-cookie','name':'كوكيز','quantity':1,'unit_price_halalas':0,'line_total_halalas':0,'is_gift':True}]
        quote={'subtotal_halalas':1000,'delivery_fee_halalas':500,'total_halalas':1500,'items':[{'product_id':'demo-paid','quantity':1,'line_total_halalas':1000},*gift_items],
               'first_order_gift':{'status':'eligible','version':1,'items':gift_items}}
        def api(route):
            request = route.request;path = request.url.split('/api/',1)[1].split('?',1)[0]
            requests.append(path)
            responses = {
                'catalog':{'brand':{'id':'kps','name':'KPS COFFEE'},'products':[product],'source_verified':True},
                'branches':{'branches':[branch,{**branch,'id':'outside-branch','name':'فرع خارج النطاق'}],
                            'brand_services':{'delivery':True,'pickup':False,'car_pickup':False}},
                'session':{'user':None,'csrf_token':None,'customer_verified':False},
                'identity/channels':{'customer_phone_required':True,'channels':{'sms':{'available':True}}},
                'experience/public':{'customer_entry_enabled':entry_enabled,'gift_offer_active':offer_active},
                    'customer-map/config':{'enabled':True,'provider':'mapbox','public_token':'pk.synthetic-test-only','style':'mapbox://styles/mapbox/streets-v12'},
                'banners':{'banners':[]},
                'auth/otp/request':{'challenge_id':'synthetic-challenge','channel':'sms'},
                'auth/otp/verify':session,
                'customer/profile':{'addresses':[]},
                'branches/nearest':{'branches':[{**branch,'available':True,'direct_distance_meters':500}],'excluded':[]},
                'products/demo-paid/availability':{'available':True},
                'quote':{'quote':{**quote,'items':quote['items'][:1],
                    'first_order_gift':{'status':'verification_required','version':1,'items':[]}}},
                'gifts/quote':{'quote':quote},
            }
            if path not in responses:
                raise AssertionError('Unexpected API operation: '+path)
            route.fulfill(status=200,content_type='application/json',body=json.dumps(responses[path]))
        page.route('**/api/**',api)
        page.route('https://api.mapbox.com/**',lambda route:route.abort())
        context.add_init_script('''
            class Map {constructor(){this.events={};}on(name,cb){this.events[name]=cb;if(name==='load')setTimeout(cb,0);}addControl(){}resize(){}jumpTo(){}remove(){}}
            class Marker {setLngLat(){return this;}addTo(){return this;}on(){}}
            window.mapboxgl={Map,Marker,NavigationControl:class {}};
        ''')
        context.add_init_script(f"localStorage.setItem('kps_customer_lang','{lang}')")
        page.goto(static_customer+'/',wait_until='networkidle')
        # Browsing is public even when the owner enables the enhanced journey.
        expect(page.locator('#menu')).to_be_visible()
        expect(page.locator('.product-add-button').first).to_be_visible()
        expect(page.locator('#account-dialog')).not_to_be_visible()
        expect(page.locator('#entry-form')).to_be_visible()
        expect(page.locator('.service-panel')).to_be_visible()
        assert 'auth/otp/request' not in requests
        # Location selection is also available to unauthenticated visitors.
        page.locator('.branch-location-tools .row > button').first.click()
        expect(page.locator('.customer-map-dialog')).to_be_visible()
        page.locator('.map-toolbar button').click()
        assert 'auth/otp/request' not in requests
        if auth_stage=='early':
            page.locator('#entry-form [name=name]').fill('علي')
            page.locator('#entry-form [name=phone]').fill('512345678')
            page.locator('#entry-form [type=submit]').click()
            expect(page.locator('#otp-verify-form')).to_be_visible()
            page.locator('#otp-code').fill('1234')
            page.locator('#verify-otp').click()
            expect(page.locator('#menu')).to_be_visible()
        page.locator('#manual-location-button').click()
        page.locator('#manual-location details summary').click()
        page.locator('#latitude').fill('28.4');page.locator('#longitude').fill('45.9')
        page.locator('#save-location').click()
        expect(page.locator('#nearest-branches .nearest-branch-card')).to_have_count(1)
        page.locator('#nearest-branches button').last.click()
        expect(page.locator('#menu')).to_be_visible()
        # The nearest list is server-qualified in both modes. The legacy manual
        # dropdown may still list other branches when entry policy is disabled.
        expect(page.locator('#nearest-branches')).not_to_contain_text('فرع خارج النطاق')
        expect(page.locator('#branch-select')).to_have_value('demo-branch')
        if entry_enabled:
            expect(page.locator('#branch-select option[value=outside-branch]')).to_have_count(0)
        page.locator('#neighborhood').fill('حي اختبار');page.locator('#address').fill('عنوان اختبار')
        page.locator('.product-add-button').first.click();page.locator('#product-add').click()
        if auth_stage=='checkout':
            # The basket collects recipient details for quoting; this is not a login.
            page.locator('#customer-name').fill('علي')
            page.locator('#customer-phone').fill('512345678')
            assert 'auth/otp/request' not in requests
        expect(page.locator('#quote-summary .first-order-gift')).to_be_visible()
        if auth_stage=='early':
            expect(page.locator('#quote-summary .first-order-gift')).to_contain_text('قهوة اليوم')
            expect(page.locator('#quote-summary .first-order-gift')).to_contain_text('كوكيز')
        else:
            expect(page.locator('#quote-summary .first-order-gift')).to_contain_text('تحقق' if lang=='ar' else 'Verify')
        if auth_stage=='checkout':
            assert 'auth/otp/request' not in requests
        page.locator('#checkout-button').click()
        if auth_stage=='checkout':
            expect(page.locator('#otp-request-form')).to_be_visible()
            page.locator('#otp-name').fill('علي')
            page.locator('#phone').fill('512345678')
            page.locator('#otp-request-form [type=submit]').click()
            expect(page.locator('#otp-verify-form')).to_be_visible()
            page.locator('#otp-code').fill('1234')
            page.locator('#verify-otp').click()
        expect(page.locator('#confirm-dialog .first-order-gift')).to_be_visible()
        expect(page.locator('#confirm-dialog .first-order-gift')).to_contain_text('قهوة اليوم')
        expect(page.locator('#confirm-dialog .first-order-gift')).to_contain_text('كوكيز')
        page.keyboard.press('Escape')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        assert 'auth/otp/request' in requests and 'auth/otp/verify' in requests
        assert 'gifts/quote' in requests and 'orders' not in requests
        out=ROOT/'test-results';out.mkdir(exist_ok=True)
        page.screenshot(path=str(out/f'customer-entry-{entry_enabled}-{offer_active}-{auth_stage}-{lang}-{width}.png'),full_page=True)
        assert not errors,errors
        context.close();browser.close()


@pytest.mark.parametrize('browser_name',['chromium','webkit'])
@pytest.mark.parametrize('lang',['ar','en'])
def test_guest_browsing_hides_optional_form_preserves_details_and_requires_otp(static_customer,lang,browser_name):
    from playwright.sync_api import sync_playwright,expect
    branch={'id':'demo-branch','brand_id':'kps','branch_no':1,'name':'فرع المروج','city':'حفر الباطن',
            'location':{'lat':28.4,'lng':45.9},'location_verified':True,'available':True,
            'payment_methods':['cash','card'],'services':{'delivery':{'available':True},'pickup':{'available':False},'car':{'available':False}}}
    product={'id':'demo-paid','name':'قهوة','category':'قهوة','price_halalas':1000,'available':True,'source_verified':True,'option_groups':[]}
    requests=[]
    with sync_playwright() as p:
        browser=getattr(p,browser_name).launch(headless=True)
        context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
        context.add_init_script("localStorage.setItem('kps_customer_lang',"+json.dumps(lang)+")")
        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        def api(route):
            path=route.request.url.split('/api/',1)[1].split('?',1)[0];requests.append(path)
            responses={
                'catalog':{'brand':{'id':'kps','name':'KPS COFFEE'},'products':[product],'source_verified':True},
                'branches':{'branches':[branch],'brand_services':{'delivery':True,'pickup':False,'car_pickup':False}},
                'session':{'user':None,'csrf_token':None,'customer_verified':False},
                'identity/channels':{'customer_phone_required':True,'channels':{'sms':{'available':True}}},
                'experience/public':{'customer_entry_enabled':False,'gift_offer_active':True},
                'customer-map/config':{'enabled':False},'banners':{'banners':[]},
                'branches/nearest':{'branches':[branch],'excluded':[]},
                'products/demo-paid/availability':{'available':True},
                'quote':{'quote':{'subtotal_halalas':1000,'delivery_fee_halalas':500,'total_halalas':1500,'items':[]}},
                'auth/otp/request':{'challenge_id':'synthetic','channel':'sms'},
            }
            assert path in responses,'Unexpected API operation: '+path
            route.fulfill(status=200,content_type='application/json',body=json.dumps(responses[path]))
        context.route('**/api/**',api)
        page.goto(static_customer+'/',wait_until='networkidle')
        expect(page.locator('#entry-form')).to_be_visible()
        page.locator('#entry-form [name=name]').fill('علي')
        page.locator('#entry-form [name=phone]').fill('512345678')
        page.locator('#entry-browse').click()
        expect(page.locator('#entry-form')).to_have_count(0)
        expect(page.locator('#customer-name')).to_have_value('علي')
        expect(page.locator('#customer-phone')).to_have_value('512345678')
        expect(page.locator('.entry-steps li')).to_have_count(3)
        for _ in range(2):
            page.locator('#customer-language').click()
            expect(page.locator('#entry-form')).to_have_count(0)
            expect(page.locator('#customer-name')).to_have_value('علي')
            expect(page.locator('#customer-phone')).to_have_value('512345678')
        page.locator('#manual-location-button').click();page.locator('#manual-location details summary').click()
        page.locator('#latitude').fill('28.4');page.locator('#longitude').fill('45.9');page.locator('#save-location').click()
        expect(page.locator('#nearest-branches button')).to_have_count(1);page.locator('#nearest-branches button').click()
        page.locator('.product-add-button').first.click();page.locator('#product-add').click()
        expect(page.locator('#entry-form')).to_have_count(0)
        expect(page.locator('#customer-name')).to_have_value('علي');expect(page.locator('#customer-phone')).to_have_value('512345678')
        expect(page.locator('#neighborhood')).to_have_attribute('required','')
        expect(page.locator('#checkout-button')).to_be_disabled()
        page.locator('#neighborhood').fill('حي اختبار')
        expect(page.locator('#address')).to_have_value('')
        expect(page.locator('#checkout-button')).to_be_enabled()
        assert 'auth/otp/request' not in requests
        page.locator('#checkout-button').click()
        expect(page.locator('#otp-request-form')).to_be_visible()
        expect(page.locator('#otp-name')).to_have_value('علي');expect(page.locator('#phone')).to_have_value('512345678')
        assert 'auth/otp/request' not in requests and 'orders' not in requests
        # Explicit verification remains necessary; the browse flag grants no identity.
        page.locator('#otp-request-form [type=submit]').click()
        expect(page.locator('#otp-verify-form')).to_be_visible()
        assert requests.count('auth/otp/request')==1 and 'orders' not in requests
        page.reload(wait_until='networkidle')
        expect(page.locator('#neighborhood')).to_have_value('حي اختبار')
        expect(page.locator('#entry-form')).to_have_count(0)
        expect(page.locator('#menu')).to_be_visible()
        assert page.evaluate("sessionStorage.getItem('kps-customer-browsing-v1:kps')")=='1'
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        # A separate tab visit still offers the optional form. No name/mobile is stored by the browse flag.
        other=context.new_page();other.goto(static_customer+'/',wait_until='networkidle')
        expect(other.locator('#entry-form')).to_be_visible()
        assert not errors,errors
        context.close();browser.close()
