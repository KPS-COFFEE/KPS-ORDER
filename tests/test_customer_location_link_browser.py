"""Customer map-link acceptance against real assets and synthetic APIs only."""
import json
import os
from pathlib import Path
import pytest
from test_experience_entry_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium acceptance')


@pytest.mark.parametrize('lang',['ar','en'])
def test_paste_refresh_otp_checkout_and_invalid_replacement(static_customer,lang):
    from playwright.sync_api import sync_playwright,expect
    point={'lat':28.4,'lng':45.9}
    link='https://maps.app.goo.gl/SYNTHETIC?g_st=iw'
    branch={'id':'demo-branch','brand_id':'kps','branch_no':1,'name':'فرع المروج','city':'حفر الباطن',
            'location':point,'location_verified':True,'available':True,'direct_distance_meters':500,
            'payment_methods':['cash','card'],'services':{'delivery':{'available':True},'pickup':{'available':False},'car':{'available':False}}}
    product={'id':'demo-paid','name':'قهوة','category':'قهوة','price_halalas':1000,'available':True,'source_verified':True,'option_groups':[]}
    session={'user':None,'csrf_token':None,'customer_verified':False}
    user={'id':'synthetic-customer','role':'customer','brand_id':'kps','display_name':'علي','phone':'+966512345678'}
    requests=[];errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
        context.add_init_script("localStorage.setItem('kps_customer_lang',"+json.dumps(lang)+")")
        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
        def api(route):
            request=route.request;path=request.url.split('/api/',1)[1].split('?',1)[0];requests.append(path)
            responses={
                'catalog':{'brand':{'id':'kps','name':'KPS COFFEE'},'products':[product],'source_verified':True},
                'branches':{'branches':[branch],'brand_services':{'delivery':True,'pickup':False,'car_pickup':False}},
                'session':session,'identity/channels':{'customer_phone_required':True,'channels':{'sms':{'available':True}}},
                'experience/public':{'customer_entry_enabled':True,'gift_offer_active':False},
                'customer-map/config':{'enabled':False},'banners':{'banners':[]},'customer/profile':{'addresses':[]},
                'branches/nearest':{'branches':[branch],'excluded':[]},'products/demo-paid/availability':{'available':True},
                'auth/otp/request':{'challenge_id':'synthetic','channel':'sms'},
            }
            if path=='location/parse-map-url':
                if request.post_data_json['url']!=link:
                    route.fulfill(status=422,content_type='application/json',body=json.dumps({'detail':{'code':'MAP_PIN_REQUIRED','message':'حدد دبوسًا صحيحًا'}}));return
                data={'location':point,'source':'customer_selected','location_is_device_verified':False}
            elif path=='auth/otp/verify':
                session.update(user=user,csrf_token='synthetic-only',customer_verified=True);data=session
            elif path in ('quote','gifts/quote'):
                assert request.post_data_json['customer']['location']==point
                data={'quote':{'subtotal_halalas':1000,'delivery_fee_halalas':500,'total_halalas':1500,'items':[]}}
            else:
                assert path in responses,'Unexpected API operation: '+path
                data=responses[path]
            route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
        page.route('**/api/**',api)
        page.goto(static_customer+'/',wait_until='networkidle')
        def open_link():
            page.evaluate('document.fonts.ready')
            control=page.locator('#manual-location-button')
            control.evaluate("el=>el.scrollIntoView({block:'center',behavior:'instant'})")
            output=Path('test-results');output.mkdir(exist_ok=True)
            page.screenshot(path=str(output/f'map-link-{lang}-before-open.png'))
            print(page.evaluate("""()=>({width:innerWidth,client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth,
              visualWidth:visualViewport?.width,scale:visualViewport?.scale,button:document.querySelector('#manual-location-button').getBoundingClientRect().toJSON()})"""))
            assert page.evaluate('document.documentElement.scrollWidth')<=391
            control.click()
        open_link()
        page.locator('#map-url').fill('موقعي: \u200f'+link)
        expect(page.locator('#map-link-status')).to_contain_text('✓')
        expect(page.locator('#nearest-branches .nearest-branch-card')).to_have_count(1)
        assert requests.count('location/parse-map-url')==1
        page.locator('#nearest-branches button').click()
        page.locator('.product-add-button').first.click();page.locator('#product-add').click()
        page.reload(wait_until='networkidle')
        expect(page.locator('#clear-location')).to_be_visible()
        expect(page.locator('#branch-select')).to_have_value('demo-branch')
        expect(page.locator('#nearest-branches .nearest-branch-card')).to_have_count(1)
        assert requests.count('location/parse-map-url')==1,'reload restores the resolved pin without another Google request'
        page.locator('#customer-name').fill('علي');page.locator('#customer-phone').fill('512345678')
        expect(page.locator('#checkout-button')).to_be_enabled();page.locator('#checkout-button').click()
        page.locator('#otp-request-form [type=submit]').click()
        page.locator('#otp-code').fill('1234');page.locator('#verify-otp').click()
        expect(page.locator('#confirm-dialog')).to_be_visible()
        assert 'orders' not in requests
        page.keyboard.press('Escape');page.reload(wait_until='networkidle')
        expect(page.locator('#clear-location')).to_be_visible()
        expect(page.locator('#checkout-button')).to_be_enabled()
        open_link();expect(page.locator('#map-url')).to_have_value(link)
        page.locator('#map-url').fill('https://maps.app.goo.gl/INVALID-SYNTHETIC')
        expect(page.locator('#map-link-status')).to_have_class('notice error')
        expect(page.locator('#checkout-button')).to_be_disabled()
        expect(page.locator('#nearest-panel')).to_be_hidden()
        page.reload(wait_until='networkidle')
        expect(page.locator('#clear-location')).to_be_hidden()
        assert not errors
        context.close();browser.close()
