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


def test_mahashi_choices_do_not_repeat_and_have_no_flavour_instruction(static_customer):
    from playwright.sync_api import sync_playwright,expect
    lang='ar';width=390
    product=json.loads((ROOT/'tests/fixtures/enbishan-mahashi-small.json').read_text())
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
        page.locator('.product-image-button').click()
        expect(page.locator('#product-option-hint')).not_to_contain_text('نكهة')
        page.get_by_role('button',name='صغير ٢٠٫٠٠ ر.س',exact=True).click()
        choices=page.locator('.customer-option-choices').filter(has_not=page.get_by_role('button',name='صغير ٢٠٫٠٠ ر.س',exact=True))
        choices.nth(0).get_by_role('button',name='كوسا · حبتان',exact=True).click()
        expect(choices.nth(1).get_by_role('button',name='كوسا · حبتان',exact=True)).to_have_count(0)
        choices.nth(1).get_by_role('button',name='بصل · حبتان',exact=True).click()
        expect(choices.nth(2).get_by_role('button',name='كوسا · حبتان',exact=True)).to_have_count(0)
        expect(choices.nth(2).get_by_role('button',name='بصل · حبتان',exact=True)).to_have_count(0)
        choices.nth(2).get_by_role('button',name='باذنجان · حبتان',exact=True).click()
        expect(page.locator('#product-add')).to_be_enabled()
        assert not errors
        browser.close()


@pytest.mark.parametrize('product_index',[0,1])
def test_grape_flavours_are_two_individual_choices_or_mixed(static_customer,product_index):
    from playwright.sync_api import sync_playwright,expect
    product=json.loads((ROOT/'tests/fixtures/enbishan-grape-flavours-public.json').read_text())[product_index]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':390,'height':844})
        context.add_init_script("localStorage.setItem('kps_customer_lang','ar')")
        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        def route_api(route):
            key=route.request.url.split('/api/',1)[1].split('?',1)[0]
            responses={'catalog':{'brand':{'id':'enbishan','name':'عنبيشن'},'source_verified':True,'products':[product]},'branches':{'branches':[],'brand_services':{'delivery':True,'pickup':False,'car_pickup':False}},'session':{'user':None,'csrf_token':None,'customer_verified':False},'identity/channels':{'customer_phone_required':False,'channels':{'guest':{'available':True}}},'experience/public':{'customer_entry_enabled':True},'customer-map/config':{'enabled':False},'banners':{'banners':[]}}
            route.fulfill(content_type='application/json',body=json.dumps(responses.get(key,{})))
        page.route('**/api/**',route_api)
        page.goto(static_customer+'/?brand_id=enbishan',wait_until='networkidle')
        page.add_style_tag(path=str(ROOT/'public/assets/enbishan.css'))
        page.locator('.product-image-button').click()
        page.locator('.customer-option-choices').first.get_by_role('button').filter(has_text='عائلي').click()
        choices=page.locator('.grape-flavour-choices')
        expect(choices.get_by_role('button')).to_have_count(4)
        expect(choices).not_to_contain_text('+')
        choices.get_by_role('button',name='دبس رمان',exact=True).click()
        choices.get_by_role('button',name='سبايسي',exact=True).click()
        expect(choices.get_by_role('button',name='ليمون',exact=True)).to_be_disabled()
        choices.get_by_role('button',name='مشكل',exact=True).click()
        expect(choices.locator('[aria-pressed=true]')).to_have_count(1)
        expect(choices.locator('[aria-pressed=true]')).to_have_text('مشكل')
        choices.get_by_role('button',name='مشكل',exact=True).click()
        expect(choices.locator('[aria-pressed=true]')).to_have_count(0)
        choices.get_by_role('button',name='مشكل',exact=True).click()
        choices.get_by_role('button',name='ليمون',exact=True).click()
        expect(choices.get_by_role('button',name='مشكل',exact=True)).to_have_attribute('aria-pressed','false')
        page.locator('#product-add').click()
        expect(page.locator('#product-error')).to_be_visible()
        choices.get_by_role('button',name='دبس رمان',exact=True).click()
        page.locator('#product-add').click()
        expect(page.locator('#product-dialog')).not_to_be_visible()
        family=next(v for g in product['option_groups'] if g['code']=='SIZE' for v in g['values'] if 'عائلي' in v['name'])
        expected_image=next(v['image_url'] for v in product['variant_images'] if v['options']['SIZE']==family['code'])
        expect(page.locator('#cart-lines .enbishan-cart-image')).to_have_attribute('src',expected_image)
        assert not errors
        browser.close()


def test_catalog_box_cover_switches_to_the_selected_small_size(static_customer):
    from playwright.sync_api import sync_playwright,expect
    products=json.loads((ROOT/'tests/fixtures/enbishan-catalog-presentation.json').read_text())
    product=next(p for p in products if p['name']=='ورق عنب')
    product.update(price_halalas=1200,variant_price_matrix=[])
    family='v-4d099242d361cb106794a77c';small='v-969127f17958a830d1bcc973'
    for v in product['variant_images']:
        product['variant_price_matrix'].append({'options':v['options'],'price_halalas':1200})
    family_image=next(v['image_url'] for v in product['variant_images'] if v['options']['SIZE']==family)
    small_image=next(v['image_url'] for v in product['variant_images'] if v['options']['SIZE']==small)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True);page=browser.new_page(viewport={'width':390,'height':844})
        page.add_init_script("localStorage.setItem('kps_customer_lang','ar')")
        def route_api(route):
            key=route.request.url.split('/api/',1)[1].split('?',1)[0]
            responses={'catalog':{'brand':{'id':'enbishan','name':'عنبيشن'},'source_verified':True,'products':[product]},'branches':{'branches':[],'brand_services':{'delivery':True,'pickup':False,'car_pickup':False}},'session':{'user':None,'csrf_token':None,'customer_verified':False},'identity/channels':{'customer_phone_required':False,'channels':{'guest':{'available':True}}},'experience/public':{'customer_entry_enabled':True},'customer-map/config':{'enabled':False},'banners':{'banners':[]}}
            route.fulfill(content_type='application/json',body=json.dumps(responses.get(key,{})))
        page.route('**/api/**',route_api)
        page.goto(static_customer+'/?brand_id=enbishan',wait_until='networkidle')
        page.add_style_tag(path=str(ROOT/'public/assets/enbishan.css'))
        expect(page.locator('.product-image-button img')).to_have_attribute('src',family_image)
        page.locator('.product-image-button').click()
        expect(page.locator('#product-image')).to_have_attribute('src',family_image)
        page.locator('.customer-option-choices button').filter(has_text='صغير').click()
        expect(page.locator('#product-image')).to_have_attribute('src',small_image)
        browser.close()


@pytest.mark.parametrize('lang,width',[('ar',320),('ar',390),('en',390),('ar',550),('en',1366)])
def test_menu_groups_copy_two_mobile_columns_and_compact_counter(static_customer,lang,width):
    from playwright.sync_api import sync_playwright,expect
    products=json.loads((ROOT/'tests/fixtures/enbishan-catalog-presentation.json').read_text())
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':width,'height':900})
        context.add_init_script("localStorage.setItem('kps_customer_lang',"+json.dumps(lang)+")")
        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        def route_api(route):
            key=route.request.url.split('/api/',1)[1].split('?',1)[0]
            responses={'catalog':{'brand':{'id':'enbishan','name':'عنبيشن'},'source_verified':True,'products':products},'branches':{'branches':[],'brand_services':{'delivery':True,'pickup':False,'car_pickup':False}},'session':{'user':None,'csrf_token':None,'customer_verified':False},'identity/channels':{'customer_phone_required':False,'channels':{'guest':{'available':True}}},'experience/public':{'customer_entry_enabled':True},'customer-map/config':{'enabled':False},'banners':{'banners':[]}}
            route.fulfill(content_type='application/json',body=json.dumps(responses.get(key,{})))
        page.route('**/api/**',route_api)
        page.goto(static_customer+'/?brand_id=enbishan',wait_until='networkidle')
        page.add_style_tag(path=str(ROOT/'public/assets/enbishan.css'))
        # Match the brand marker inserted by storefront_scope on the real domain.
        page.locator('html').evaluate("el=>el.dataset.storefrontBrand='enbishan'")
        cards=page.locator('.product-card')
        expect(cards).to_have_count(len(products))
        expect(page.locator('.enbishan-product-description')).to_have_count(len(products))
        expect(page.locator('.menu-section-title').first).to_have_text('ورق العنب والملفوف' if lang=='ar' else 'Grape leaves & cabbage')
        if width<=550:
            a,b=cards.nth(0).bounding_box(),cards.nth(1).bounding_box()
            assert abs(a['y']-b['y'])<2
            assert abs(a['x']-b['x'])>a['width']
        controls=cards.first.locator('.product-card-controls')
        assert controls.bounding_box()['width']<=120
        for button in controls.locator('button').all():
            box=button.bounding_box();assert box['width']>=44 and box['height']>=44
        assert controls.locator('.product-minus-button').bounding_box()['x']<controls.locator('output').bounding_box()['x']<controls.locator('.product-add-button').bounding_box()['x']
        if os.getenv('MENU_QA_SCREENSHOT') and lang=='ar' and width==390:
            page.locator('.menu-section-title').first.scroll_into_view_if_needed()
            page.screenshot(path='/tmp/enbishan-menu-mobile.png')
        mixed=page.locator('[data-product-id="enbishan-menu-eea4f81998c7705a69c69532"]')
        mixed.locator('h3 button').click()
        size=page.locator('.customer-option-choices button').filter(has_text='21')
        size.click()
        expect(page.locator('#product-description')).to_contain_text('٦ ورق عنب + ١ بطاطس + ١ فليفلة + ٤ بصل + ٤ كوسا + ٥ باذنجان' if lang=='ar' else '6 grape leaves + 1 potato + 1 pepper + 4 onions + 4 zucchini + 5 eggplants')
        assert page.evaluate('document.documentElement.scrollWidth')<=width+1
        assert not errors
        browser.close()
