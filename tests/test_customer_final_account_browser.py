"""Real mobile Chromium/WebKit account delta, synthetic local data only."""
import os
import pytest
from app import auth,db
from test_browser import live_app,synthetic_mail,_customer_session,ARTIFACTS
from tests.test_core import _prepare_catalog,_body
pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires explicit real-browser acceptance')]

@pytest.mark.parametrize('engine',['chromium','webkit'])
@pytest.mark.parametrize('lang',['ar','en'])
def test_account_addresses_filters_reorder_and_mobile_layout(live_app,engine,lang):
    from playwright.sync_api import sync_playwright,expect
    _prepare_catalog();uid,token,csrf=_customer_session()
    with db.tx() as c:
        c.execute("UPDATE app_settings SET settings=settings||'{\"customer_phone_required\":true}'::jsonb WHERE brand_id='kps'")
        c.execute("UPDATE users SET identity_kind='phone_sms_verified',phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s",(uid,))
        c.execute('UPDATE sessions SET customer_verified_at=now() WHERE user_id=%s',(uid,))
    with sync_playwright() as pw:
        browser=getattr(pw,engine).launch()
        context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,locale='ar-SA' if lang=='ar' else 'en-US',timezone_id='Asia/Riyadh')
        context.add_cookies([{'name':auth.COOKIE,'value':token,'url':live_app,'httpOnly':True,'sameSite':'Strict'},{'name':auth.CSRF_COOKIE,'value':csrf,'url':live_app,'sameSite':'Strict'}])
        headers={'Origin':live_app,'X-CSRF-Token':csrf,'Idempotency-Key':'test-ui-saved-order'}
        r=context.request.post(live_app+'/api/orders',data=_body(),headers=headers);assert r.status==200,r.text()
        oid=r.json()['order']['id']
        with db.tx() as c:
            before=c.execute('SELECT snapshot FROM orders WHERE id=%s',(oid,)).fetchone()['snapshot']
            c.execute("UPDATE orders SET preparation_status='completed' WHERE id=%s",(oid,))
            c.execute("UPDATE products SET price_halalas=price_halalas+100 WHERE id='kps-01'")
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.add_init_script("localStorage.setItem('kps_customer_lang',"+repr(lang)+")")
        page.goto(live_app);expect(page.locator('#account-button')).to_be_enabled()
        assert page.locator('.customer-navigation button').count()==3
        page.locator('#account-button').click();expect(page.locator('#profile-phone')).to_contain_text('••••')
        assert '+966500000001' not in page.locator('#profile-phone').inner_text()
        page.locator('#address-add').click();page.locator('#address-label').fill('TEST Home');page.locator('#address-text').fill('TEST Street 12');page.locator('#address-neighborhood').fill('TEST area')
        page.locator('#address-save').click();expect(page.locator('#address-dialog')).to_be_hidden()
        expect(page.locator('#profile-addresses')).to_contain_text('TEST Home')
        page.locator('#profile-addresses button').first.click();page.locator('#address-label').fill('TEST Work');page.locator('#address-save').click()
        expect(page.locator('#profile-addresses')).to_contain_text('TEST Work')
        assert len(context.request.get(live_app+'/api/customer/profile').json()['addresses'])==1
        expect(page.locator('#account-refunds-link')).to_have_attribute('href',f'/refunds?brand_id=kps&lang={lang}')
        assert page.locator('#logout-button').evaluate('e=>getComputedStyle(e).position')=='static'
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        ARTIFACTS.mkdir(exist_ok=True);page.screenshot(path=str(ARTIFACTS/f'final-account-{engine}-{lang}.png'))
        page.locator('#account-orders').click();expect(page.locator('#orders-list .order-card')).to_have_count(0)
        page.locator('[data-order-filter=history]').click();expect(page.locator('#orders-list .order-card')).to_have_count(1)
        page.locator('[data-reorder]').click();expect(page.locator('#confirm-dialog')).to_be_visible()
        page.locator('#confirm-submit').click();expect(page.locator('#orders-dialog')).to_be_hidden()
        expect(page.locator('#checkout-button')).to_be_enabled()
        with db.tx() as c:
            assert c.execute('SELECT count(*) n FROM orders').fetchone()['n']==1
            assert c.execute('SELECT snapshot FROM orders WHERE id=%s',(oid,)).fetchone()['snapshot']==before
        # Re-order adopts current price, but never creates an order until separate checkout.
        current=context.request.get(live_app+'/api/catalog?branch_id=kps-branch-1').json()
        price=next(p['price_halalas'] for p in current['products'] if p['id']=='kps-01')
        assert price==before['items'][0]['unit_price_halalas']+100
        page.locator('#checkout-button').click();expect(page.locator('#confirm-dialog')).to_be_visible()
        page.locator('#confirm-dialog [data-close]').click()
        # A new stock stop blocks checkout on the next current quote.
        with db.tx() as c:c.execute("UPDATE products SET settings=settings||'{\"enabled\":false}'::jsonb WHERE id='kps-01'")
        page.locator('#orders-button').click();page.locator('[data-order-filter=history]').click();page.locator('[data-reorder]').click()
        expect(page.locator('#confirm-content')).to_contain_text('غير متاحة' if lang=='ar' else 'unavailable')
        page.locator('#confirm-submit').click();expect(page.locator('#checkout-button')).to_be_disabled()
        with db.tx() as c:assert c.execute('SELECT count(*) n FROM orders').fetchone()['n']==1
        assert not errors
        context.close();browser.close()
