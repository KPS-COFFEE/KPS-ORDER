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
    product=prepare_rows(rows())['products'][0]
    product['settings'].update(enabled=True,lifecycle='active',source_verified=True)
    product['settings']['owner_approved_digest']=catalog.commercial_digest(product)
    with db.tx() as c:
        c.execute("INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,'enbishan',%s,%s,%s,%s)",
                  (product['id'],product['name'],product['category'],product['price_halalas'],db.json(product['settings'])))
    small=choices(product,'صغير','توت');large=choices(product,'كبير','رمان')
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.getenv('TEST_CHROMIUM_EXECUTABLE'),args=['--no-sandbox'])
        try:
            context=browser.new_context(viewport={'width':width,'height':900})
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/?brand_id=enbishan&lang='+language,wait_until='networkidle')
            expect(page.locator('html')).to_have_attribute('lang',language)
            page.locator('[data-product-id="'+product['id']+'"] .product-content h3 button').click()
            selects=page.locator('#product-options select')
            selects.nth(0).select_option(small['SIZE']);selects.nth(1).select_option(small['FLAVOUR'])
            expect(page.locator('#product-price')).to_have_attribute('data-variant-price','1300')
            expect(page.locator('#product-price del')).to_have_count(1)
            selects.nth(0).select_option(large['SIZE']);selects.nth(1).select_option(large['FLAVOUR'])
            expect(page.locator('#product-price')).to_have_attribute('data-variant-price','1850')
            expect(page.locator('#product-price del')).to_have_count(0)
            selects.nth(0).select_option(small['SIZE'])
            expect(page.locator('#product-add')).to_be_disabled()
            expect(page.locator('#product-error')).to_be_visible()
            assert not errors,errors
        finally:
            browser.close()
