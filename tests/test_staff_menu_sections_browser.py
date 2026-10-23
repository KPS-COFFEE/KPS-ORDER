"""KPS category filters use actual catalog names; Enbishan keeps its existing menu."""
import json
import os
from pathlib import Path

import pytest
from test_delivery_workspace_browser import static_customer

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit browser run')


@pytest.mark.parametrize('engine,lang,width', [
    ('chromium', 'ar', 1366), ('webkit', 'en', 1366),
    ('chromium', 'en', 390), ('webkit', 'ar', 390),
])
@pytest.mark.parametrize('brand', ['kps', 'enbishan'])
def test_category_filters_five_cards_cart_and_brand_scope(static_customer, engine, lang, width, brand):
    from playwright.sync_api import sync_playwright, expect

    catalog = json.loads((Path(__file__).resolve().parents[1] / 'data/catalog.json').read_text())
    products = [dict(id=p['id'], name=p['name'], category=p['category'], available=True,
                     price_halalas=1000, image_url='/assets/kps-receipt-logo.png')
                for p in catalog['products']]
    expected = {
        'sweets': {'kps-03', *(f'kps-{i:02}' for i in range(15, 25))},
        'cold': {'kps-32', *(f'kps-{i:02}' for i in range(9, 15))},
        'coffee': {'kps-01', 'kps-02', *(f'kps-{i:02}' for i in range(4, 9))},
        'boxes': {'kps-33', 'kps-34', *(f'kps-{i:02}' for i in range(25, 30))},
        'thermal': {'kps-30', 'kps-31'}, 'extras': {'kps-35'},
    }
    assert set.union(*expected.values()) == {p['id'] for p in products}
    with sync_playwright() as p:
        browser = getattr(p, engine).launch()
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(static_customer + '/ops.html')
        page.evaluate('''async ({products,brand,lang})=>{
          await KPSCustomerOrder.open({
            user:{id:'manager',brand_id:brand,role:'delivery_manager'},branches:[{id:'branch',name:'Branch'}],
            tr:(ar,en)=>lang==='ar'?ar:en,esc:value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),money:String,
            api:async path=>path==='/api/shifts/current'?{shifts:[{user_id:'manager',kind:'manager',status:'open',branch_ids:['branch']}]}:path.startsWith('/api/catalog')?{products}:{contacts:[]},
            mutation:async()=>{throw Error('No server write is expected');},closeModal:()=>{},onCreated:()=>{},
            modal:(title,html)=>{document.querySelector('#modal-body').innerHTML=html;document.querySelector('#modal').showModal();}
          });
        }''', {'products': products, 'brand': brand, 'lang': lang})
        cards = page.locator('#staff-menu .staff-menu-card')
        if brand == 'enbishan':
            expect(page.locator('#staff-menu-categories')).to_be_hidden()
            expect(cards).to_have_count(len(products))
            assert not page.locator('#staff-order-form').get_attribute('class').endswith('staff-order-kps-menu')
            assert page.locator('#modal').bounding_box()['width'] <= 720
        else:
            buttons = page.locator('#staff-menu-categories [data-menu-category]')
            expect(buttons).to_have_count(6)
            positions = buttons.evaluate_all('(buttons)=>buttons.map(b=>b.getBoundingClientRect().top)')
            assert max(positions) - min(positions) <= 1, 'Category buttons must share a single row'
            for key, ids in expected.items():
                button = page.locator(f'[data-menu-category={key}]')
                button.click()
                expect(page.locator(f'[data-menu-category={key}]')).to_have_attribute('aria-pressed', 'true')
                seen = set(page.locator('#staff-menu .staff-menu-photo').evaluate_all('(buttons)=>buttons.map(b=>b.dataset.menuAdd)'))
                assert seen == ids
                columns = page.locator('#staff-menu').evaluate('(grid)=>getComputedStyle(grid).gridTemplateColumns.split(" ").length')
                assert columns == (5 if width >= 640 else 2)
            page.locator('[data-menu-category=coffee]').click()
            page.locator('.staff-menu-photo[data-menu-add=kps-02]').click(click_count=2)
            expect(page.locator('[data-product-count=kps-02]')).to_have_text('2')
            page.locator('[data-menu-category=sweets]').click()
            page.locator('.staff-menu-photo[data-menu-add=kps-03]').click()
            expect(page.locator('#staff-cart > div')).to_have_count(2)
            page.locator('[data-menu-category=coffee]').click()
            expect(page.locator('[data-product-count=kps-02]')).to_have_text('2')
            # The existing item selector also follows the correct category.
            page.locator('#staff-product').select_option('kps-30')
            expect(page.locator('[data-menu-category=thermal]')).to_have_attribute('aria-pressed', 'true')
            expect(page.locator('.staff-menu-photo[data-menu-add=kps-30]')).to_be_visible()
            assert page.locator('#modal').evaluate('(dialog)=>dialog.scrollWidth<=dialog.clientWidth+1')
            page.locator('[data-menu-category=sweets]').click()
            Path('test-artifacts').mkdir(exist_ok=True)
            page.screenshot(path=f'test-artifacts/staff-menu-{engine}-{lang}-{width}.png')
        assert not errors
        browser.close()
