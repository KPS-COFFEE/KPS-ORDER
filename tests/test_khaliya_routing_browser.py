"""Real customer browser: disabled local pickup and explicit Branch 2 transfer."""
import os
import pytest
from app import db
from tests.test_browser import live_app, chromium_browser, synthetic_mail
from tests.test_khaliya_routing import prepare

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit browser run required')]

@pytest.mark.parametrize('service',['pickup','car'])
@pytest.mark.parametrize('language',['ar','en'])
def test_explicit_pickup_transfer_preserves_basket(live_app,chromium_browser,service,language):
    from playwright.sync_api import expect
    with db.tx() as conn:prepare(conn)
    context=chromium_browser.new_context(base_url=live_app,viewport={'width':390,'height':844},locale='ar-SA')
    context.add_init_script("localStorage.setItem('kps-unconfirmed-basket-v2:kps',JSON.stringify({brandId:'kps',cart:[{productId:'kps-01',quantity:2,options:{},note:'keep me'}]}));")
    page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    try:
        page.goto('/')
        if language=='en':page.locator('#customer-language').click()
        page.locator(f'input[name=service][value={service}]').check()
        page.locator('#branch-select').select_option('kps-branch-1')
        card=page.locator('.product-card[data-product-id="kps-33"]')
        expect(card.locator('.product-add-button')).to_be_disabled()
        label='الطلب من فرع 2' if language=='ar' else 'Order from Branch 2'
        expect(card.get_by_role('button',name=label,exact=True)).to_be_visible()
        card.get_by_role('button',name=label,exact=True).click()
        expect(page.locator('#branch-select')).to_have_value('kps-branch-2')
        expect(card.locator('.product-add-button')).to_be_enabled()
        saved=page.evaluate("JSON.parse(localStorage.getItem('kps-unconfirmed-basket-v2:kps')).cart")
        assert len(saved)==1 and saved[0]['productId']=='kps-01' and saved[0]['quantity']==2
        assert not errors
    finally:context.close()
