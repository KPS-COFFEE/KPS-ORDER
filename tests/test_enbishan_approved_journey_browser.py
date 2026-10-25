"""Real customer DOM: browse, basket, contact once, location and quoted review."""
import json,os
from pathlib import Path
import pytest
from test_experience_entry_browser import static_customer
pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance')
ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('lang,width',[('ar',390),('en',1366)])
def test_approved_guest_journey_preserves_text_and_real_contracts(static_customer,lang,width):
    from playwright.sync_api import sync_playwright,expect
    point={'lat':28.4,'lng':45.9};requests=[];errors=[]
    product={'id':'synthetic-paid','brand_id':'enbishan','name':'ورق عنب','name_en':'Grape Leaves','category':'المحاشي','category_en':'Stuffed',
             'price_halalas':1200,'enabled':True,'available':True,'source_verified':True,'option_groups':[]}
    branch={'id':'synthetic-branch','brand_id':'enbishan','branch_no':1,'name':'عنبيشن 1','city':'حفر الباطن','location':point,
            'location_verified':True,'available':True,'direct_distance_meters':500,'payment_methods':['cash','card'],
            'services':{'delivery':{'available':True},'pickup':{'available':False,'code':'BRAND_SERVICE_DISABLED'},'car':{'available':False,'code':'BRAND_SERVICE_DISABLED'}}}
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);context=browser.new_context(viewport={'width':width,'height':900})
        context.add_init_script("localStorage.setItem('kps_customer_lang',"+json.dumps(lang)+")")
        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
        def api(route):
            req=route.request;key=req.url.split('/api/',1)[1].split('?',1)[0];requests.append((key,req.method))
            responses={'catalog':{'brand':{'id':'enbishan','name':'عنبيشن'},'source_verified':True,'products':[product]},
                       'branches':{'branches':[branch],'brand_services':{'delivery':True,'pickup':False,'car_pickup':False}},
                       'session':{'user':None,'csrf_token':None,'customer_verified':False},
                       'identity/channels':{'customer_phone_required':False,'channels':{'guest':{'available':True},'sms':{'available':True}}},
                       'experience/public':{'customer_entry_enabled':True},'customer-map/config':{'enabled':False},'banners':{'banners':[]},
                       'branches/nearest':{'branches':[branch],'excluded':[]},'products/synthetic-paid/availability':{'available':True},
                       'customer/marketing-consent':{'brand_id':'enbishan','notice':None,'marketing':{'sms':False,'whatsapp':False},'affects_order_messages':False}}
            if key=='location/parse-map-url':data={'location':point,'source':'customer_selected'}
            elif key=='identity/guest':
                assert req.post_data_json=={'brand_id':'enbishan','display_name':'Synthetic Guest','phone':'0500000000'}
                data={'user':{'id':'synthetic-guest','brand_id':'enbishan','role':'customer','identity_kind':'guest','display_name':'Synthetic Guest','phone':'0500000000'},'csrf_token':'synthetic','customer_verified':False}
            elif key in ('quote','gifts/quote'):
                assert req.post_data_json['customer']['location']==point
                assert req.post_data_json['service']=='delivery'
                data={'quote':{'subtotal_halalas':1200,'delivery_fee_halalas':500,'total_halalas':1700,'items':[]}}
            else:
                assert key in responses,'Unexpected API: '+key
                data=responses[key]
            route.fulfill(content_type='application/json',body=json.dumps(data))
        page.route('**/api/**',api)
        page.goto(static_customer+'/?brand_id=enbishan',wait_until='networkidle')
        page.add_style_tag(path=str(ROOT/'public/assets/enbishan.css'))
        expect(page.locator('#products .product-card')).to_have_count(1)
        expect(page.locator('input[name=service][value=delivery]')).to_be_checked()
        expect(page.locator('input[name=service][value=pickup]')).to_be_hidden()
        expect(page.locator('input[name=service][value=car]')).to_be_hidden()
        assert page.locator('#banner-content h1').inner_text()==('اطلب مباشرة من عنبيشن' if lang=='ar' else 'Order directly from عنبيشن')
        page.locator('.product-add-button').click();page.locator('#product-add').click();page.locator('#header-cart').click()
        expect(page.locator('#enbishan-basket-dialog')).to_be_visible()
        expect(page.locator('#customer-name')).to_be_hidden()
        page.locator('#enbishan-continue').click();page.locator('#customer-name').fill('Synthetic Guest');page.locator('#customer-phone').fill('0500000000')
        page.locator('#enbishan-checkout-location').click();page.locator('#manual-location-button').click();page.locator('#map-url').fill('https://maps.google.com/?q=28.4,45.9')
        expect(page.locator('#branch-select')).to_have_value('synthetic-branch')
        page.locator('#enbishan-location-done').click();page.locator('#neighborhood').fill('Synthetic area')
        expect(page.locator('#checkout-button')).to_be_enabled();page.locator('#checkout-button').click()
        expect(page.locator('#confirm-dialog')).to_be_visible()
        assert requests.count(('identity/guest','POST'))==1
        assert not any(k.startswith('auth/otp') or (k=='orders' and method=='POST') for k,method in requests)
        expect(page.locator('#customer-name')).to_have_value('Synthetic Guest')
        assert not errors
        assert page.evaluate('document.documentElement.scrollWidth')<=width+1
        browser.close()
