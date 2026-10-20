"""Actual AR/EN mobile/desktop variant controls against isolated PostgreSQL."""
import os
import pytest
from playwright.sync_api import sync_playwright, expect
from app import catalog, db
from test_enbishan_mobile_browser import dual_http
from test_enbishan_dual_brand import dual
from test_enbishan_menu_readiness import rows, choices
from app.enbishan_catalog_import import prepare_rows


@pytest.mark.postgres
@pytest.mark.parametrize('language',['ar','en'])
@pytest.mark.parametrize('width',[390,1280])
def test_enbishan_size_flavour_before_after_prices_and_missing_combination(dual_http,language,width):
    base,_=dual_http
    source=rows()
    for row in source:
        size=row['size']
        row['size']=size+(' · 6 قطعة' if size=='صغير' else ' · 12 قطعة')
        row['size_en']='Small · 6 pieces' if size=='صغير' else 'Large · 12 pieces'
        row['flavour']='دبس رمان' if row['flavour']=='توت' else 'ليمون'
        row['flavour_en']='Pomegranate molasses' if row['flavour']=='دبس رمان' else 'Lemon'
    product=prepare_rows(source)['products'][0]
    product['settings'].update(enabled=True,lifecycle='active',source_verified=True)
    product['settings']['owner_approved_digest']=catalog.commercial_digest(product)
    with db.tx() as c:
        c.execute("INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,'enbishan',%s,%s,%s,%s)",
                  (product['id'],product['name'],product['category'],product['price_halalas'],db.json(product['settings'])))
    small=choices(product,'صغير · 6 قطعة','دبس رمان');large=choices(product,'كبير · 12 قطعة','ليمون')
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.getenv('TEST_CHROMIUM_EXECUTABLE'),args=['--no-sandbox'])
        try:
            context=browser.new_context(viewport={'width':width,'height':900})
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/?brand_id=enbishan&lang='+language,wait_until='networkidle')
            if language=='en':
                page.locator('#customer-language').click()
            expect(page.locator('html')).to_have_attribute('lang',language)
            page.locator('[data-product-id="'+product['id']+'"] .product-content h3 button').click()
            selects=page.locator('#product-options select')
            selects.nth(0).select_option(small['SIZE']);selects.nth(1).select_option(small['FLAVOUR'])
            expect(selects.nth(0).locator('option:checked')).to_have_text('Small · 6 pieces' if language=='en' else 'صغير · 6 قطعة')
            expect(selects.nth(1).locator('option:checked')).to_have_text('Pomegranate molasses' if language=='en' else 'دبس رمان')
            expect(page.locator('#product-price')).to_have_attribute('data-variant-price','1300')
            expect(page.locator('#product-price del')).to_have_count(1)
            selects.nth(0).select_option(large['SIZE']);selects.nth(1).select_option(large['FLAVOUR'])
            expect(selects.nth(0).locator('option:checked')).to_have_text('Large · 12 pieces' if language=='en' else 'كبير · 12 قطعة')
            expect(selects.nth(1).locator('option:checked')).to_have_text('Lemon' if language=='en' else 'ليمون')
            expect(page.locator('#product-price')).to_have_attribute('data-variant-price','1850')
            expect(page.locator('#product-price del')).to_have_count(0)
            selects.nth(0).select_option(small['SIZE'])
            expect(page.locator('#product-add')).to_be_disabled()
            expect(page.locator('#product-error')).to_be_visible()
            assert not errors,errors
        finally:
            browser.close()


@pytest.mark.postgres
@pytest.mark.parametrize('language', ['ar', 'en'])
def test_owner_approved_mahashi_distinct_choices_and_identity(dual_http, language):
    from test_enbishan_owner_menu_decisions import reviewed
    from tools.enbishan_workbook import reference_menu
    base, _ = dual_http
    product = next(p for p in prepare_rows(reference_menu(reviewed(), owner_approved=True)['rows'])['products']
                   if p['settings']['source_id'].endswith('-choose3'))
    product['settings'].update(enabled=True, lifecycle='active', source_verified=True)
    product['settings']['owner_approved_digest'] = catalog.commercial_digest(product)
    with db.tx() as c:
        c.execute("INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,'enbishan',%s,%s,%s,%s)",
                  (product['id'],product['name'],product['category'],product['price_halalas'],db.json(product['settings'])))
    options = product['settings']['variant_price_matrix'][0]['options']
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv('TEST_CHROMIUM_EXECUTABLE'), args=['--no-sandbox'])
        try:
            page = browser.new_page(viewport={'width':390,'height':900})
            page.goto(base+'/?brand_id=enbishan', wait_until='networkidle')
            if language == 'en':
                page.locator('#customer-language').click()
            expect(page.locator('html')).to_have_attribute('lang', language)
            expect(page.locator('html')).to_have_attribute('data-storefront-brand', 'enbishan')
            expect(page.locator('#brand-logo')).to_be_visible()
            assert page.locator('a[href="tel:0509895855"]').count() > 0
            page.locator('[data-product-id="'+product['id']+'"] .product-content h3 button').click()
            selects = page.locator('#product-options select')
            for i, group in enumerate(product['settings']['option_groups']):
                selects.nth(i).select_option(options[group['code']])
            expect(page.locator('#product-price')).to_have_attribute('data-variant-price', '2000')
            expect(page.locator('#product-add')).to_be_enabled()
            choice2 = next(i for i,g in enumerate(product['settings']['option_groups']) if g['code']=='CHOICE_2')
            selects.nth(choice2).select_option(options['CHOICE_1'])
            expect(page.locator('#product-add')).to_be_disabled()
            expect(page.locator('#product-error')).to_be_visible()
        finally:
            browser.close()
