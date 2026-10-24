"""Actual AR/EN mobile/desktop variant controls against isolated PostgreSQL."""
import os
import pytest
from playwright.sync_api import sync_playwright, expect
from app import catalog, db
from test_enbishan_mobile_browser import dual_http
from test_enbishan_dual_brand import dual
from test_enbishan_menu_readiness import rows, choices
from app.enbishan_catalog_import import prepare_rows


def choose_visible_option(select, value):
    label = select.locator('option[value="'+value+'"]').text_content()
    select.locator('..').get_by_role('button', name=label, exact=True).click()


@pytest.mark.postgres
@pytest.mark.parametrize(('language','width'), [('ar',390),('en',1280)])
def test_owner_saves_dormant_morning_period_without_opening_it(dual_http, language, width):
    from app import auth, service_hours
    from app.enbishan_operations_preparation import prepared_hours
    from datetime import datetime
    base, (_, actor, _, _, _) = dual_http
    client = actor('owner', 'enbishan')
    with db.tx() as conn:
        original = conn.execute("SELECT settings FROM branches WHERE id='kps-branch-1'").fetchone()['settings']
        branch = conn.execute("SELECT settings FROM branches WHERE id='enbishan-test-1'").fetchone()['settings']
        branch.update(opening_hours=prepared_hours(), delivery_hours=prepared_hours(),
                      delivery_enabled=False, pickup_enabled=False, car_enabled=False)
        conn.execute("UPDATE branches SET settings=%s WHERE id='enbishan-test-1'", (db.json(branch),))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.getenv('TEST_CHROMIUM_EXECUTABLE'), args=['--no-sandbox'])
        try:
            context = browser.new_context(viewport={'width':width,'height':900})
            context.add_cookies([{'name':name,'value':client.cookies.get(name),'url':base}
                                for name in [auth.COOKIE,auth.CSRF_COOKIE]])
            page = context.new_page();errors=[]
            page.on('pageerror', lambda e:errors.append(str(e)))
            page.goto(base+'/ops?operations_brand=enbishan')
            if language=='en':
                page.locator('#language').click()
            if width <= 650:
                toggle = page.locator('.dispatch-nav-toggle')
                expect(toggle).to_be_visible()
                if toggle.get_attribute('aria-expanded') != 'true':
                    toggle.click()
                expect(toggle).to_have_attribute('aria-expanded', 'true')
                expect(page.locator('#navigation')).to_be_visible()
            admin_navigation = page.locator('#navigation [data-page=admin]')
            expect(admin_navigation).to_be_visible()
            admin_navigation.click()
            page.locator('[data-action=admin-tab][data-tab=branches]').click()
            page.locator('[data-action=edit-branch][data-id=enbishan-test-1]').click()
            periods = page.locator('#delivery_hours-rows .hours-row')
            expect(periods).to_have_count(2)
            expect(periods.nth(0).locator('input[type=time]').first).to_have_value('15:00')
            expect(periods.nth(1).locator('input[type=checkbox][data-action=toggle-hours]')).not_to_be_checked()
            periods.nth(1).locator('input[type=time]').nth(0).fill('08:00')
            periods.nth(1).locator('input[type=time]').nth(1).fill('12:00')
            page.locator('#modal-body button[type=submit]').click()
            expect(page.locator('#modal')).not_to_have_attribute('open','')
            with db.tx() as conn:
                saved = conn.execute("SELECT settings FROM branches WHERE id='enbishan-test-1'").fetchone()['settings']
                assert saved['delivery_hours'][1] == {'days':list(range(7)),'start':'08:00','end':'12:00','enabled':False}
                assert saved['delivery_enabled'] is False
                assert not service_hours.hours_open(saved['delivery_hours'], datetime(2026,10,23,9))
                assert conn.execute("SELECT settings FROM branches WHERE id='kps-branch-1'").fetchone()['settings'] == original
            assert not errors,errors
            context.close()
        finally:
            browser.close()


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
            choose_visible_option(selects.nth(0), small['SIZE']);choose_visible_option(selects.nth(1), small['FLAVOUR'])
            expect(selects.nth(0).locator('option:checked')).to_have_text('Small · 6 pieces' if language=='en' else 'صغير · 6 قطعة')
            expect(selects.nth(1).locator('option:checked')).to_have_text('Pomegranate molasses' if language=='en' else 'دبس رمان')
            expect(page.locator('#product-price')).to_have_attribute('data-variant-price','1300')
            expect(page.locator('#product-price del')).to_have_count(1)
            choose_visible_option(selects.nth(0), large['SIZE']);choose_visible_option(selects.nth(1), large['FLAVOUR'])
            expect(selects.nth(0).locator('option:checked')).to_have_text('Large · 12 pieces' if language=='en' else 'كبير · 12 قطعة')
            expect(selects.nth(1).locator('option:checked')).to_have_text('Lemon' if language=='en' else 'ليمون')
            expect(page.locator('#product-price')).to_have_attribute('data-variant-price','1850')
            expect(page.locator('#product-price del')).to_have_count(0)
            choose_visible_option(selects.nth(0), small['SIZE'])
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
                choose_visible_option(selects.nth(i), options[group['code']])
            expect(page.locator('#product-price')).to_have_attribute('data-variant-price', '2000')
            expect(page.locator('#product-add')).to_be_enabled()
            choice2 = next(i for i,g in enumerate(product['settings']['option_groups']) if g['code']=='CHOICE_2')
            choose_visible_option(selects.nth(choice2), options['CHOICE_1'])
            expect(page.locator('#product-add')).to_be_disabled()
            expect(page.locator('#product-error')).to_be_visible()
        finally:
            browser.close()
