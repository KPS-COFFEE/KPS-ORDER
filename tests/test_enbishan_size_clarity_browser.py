import json, os
from pathlib import Path
import pytest
from test_experience_entry_browser import static_customer
pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser acceptance')
ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('lang,width',[('ar',390),('en',390),('ar',1366)])
def test_size_contents_price_and_photo_are_visible_before_flavour(static_customer,lang,width):
    from playwright.sync_api import sync_playwright,expect
    product=json.loads((ROOT/'tests/fixtures/enbishan-mixed-before-family.json').read_text())
    family='family-fixture'
    product['option_groups'][0]['values'].append({'code':family,'name':'عائلي · ٣٦ حبة (١٨ ملفوف + ١٨ ورق عنب)','name_en':'Family · 36 pieces (18 cabbage + 18 grape leaves)'})
    family_image='/assets/enbishan-menu/owner-20261025/grape-cabbage-family.webp'
    for v in product['option_groups'][1]['values']:
        options={'SIZE':family,'FLAVOUR':v['code']}
        product['variant_images'].append({'options':options,'image_url':family_image})
        product['variant_price_matrix'].append({'options':options,'price_halalas':4550,'compare_at_halalas':6200})
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':width,'height':900})
        context.add_init_script("localStorage.setItem('kps_customer_lang',"+json.dumps(lang)+")")
        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        def route_api(route):
            key=route.request.url.split('/api/',1)[1].split('?',1)[0]
            responses={'catalog':{'brand':{'id':'enbishan','name':'عنبيشن'},'source_verified':True,'products':[product]},'branches':{'branches':[],'brand_services':{'delivery':True,'pickup':False,'car_pickup':False}},'session':{'user':None,'csrf_token':None,'customer_verified':False},'identity/channels':{'customer_phone_required':False,'channels':{'guest':{'available':True}}},'experience/public':{'customer_entry_enabled':True},'customer-map/config':{'enabled':False},'banners':{'banners':[]}}
            route.fulfill(content_type='application/json',body=json.dumps(responses.get(key,{})))
        page.route('**/api/**',route_api)
        page.goto(static_customer+'/?brand_id=enbishan',wait_until='networkidle')
        page.add_style_tag(path=str(ROOT/'public/assets/enbishan.css'))
        expect(page.locator('.enbishan-size-summary')).to_contain_text('٣٦' if lang=='ar' else '36')
        page.locator('.product-image-button').click()
        choice=page.locator('.customer-option-choices button').filter(has_text='عائلي' if lang=='ar' else 'Family')
        expect(choice).to_have_count(1)
        expect(choice.locator('del')).to_be_visible()
        choice.click()
        expect(page.locator('#product-description')).to_be_visible()
        expect(page.locator('#product-description')).to_contain_text('١٨ ملفوف + ١٨ ورق عنب' if lang=='ar' else '18 cabbage + 18 grape leaves')
        expect(page.locator('#product-image')).to_have_attribute('src',family_image)
        assert page.locator('.enbishan-size-price').count()>=4
        assert page.evaluate('document.documentElement.scrollWidth')<=width+1
        assert not errors
        browser.close()
