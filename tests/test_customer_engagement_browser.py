"""Real dual-brand HTTP/PG18/mobile acceptance; no intercepted application calls."""
import os
import pytest
from playwright.sync_api import sync_playwright, expect
from app import auth, db
from test_enbishan_mobile_browser import dual_http
from test_enbishan_dual_brand import dual


@pytest.fixture
def engagement_http(request,monkeypatch):
    monkeypatch.setenv('RUNTIME_REVISION','a'*40)
    for feature in ('CUSTOMER_BENEFITS','GIFT_ORDERS'):
        for brand in ('KPS','ENBISHAN'):
            for suffix,value in [('ENABLED','true'),('ACCEPTANCE_SHA','a'*40),('OWNER_APPROVAL_SHA','a'*40)]:
                monkeypatch.setenv('KPS_'+feature+'_'+brand+'_'+suffix,value)
    return request.getfixturevalue('dual_http')


@pytest.mark.postgres
@pytest.mark.parametrize('brand',['kps','enbishan'])
@pytest.mark.parametrize('lang',['ar','en'])
@pytest.mark.parametrize('width',[390,1280])
def test_account_credit_and_gift_controls_are_brand_scoped(engagement_http,brand,lang,width):
    base,(_,actor,_,_,_)=engagement_http
    account=actor(brand+'-customer')
    token=account.cookies.get(auth.COOKIE)
    with db.tx() as c:
        for selected,balance in [('kps',111),('enbishan',222)]:
            c.execute("UPDATE users SET phone='+966500001001',phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s",('dual-'+selected+'-customer',))
            c.execute('INSERT INTO customer_wallets(brand_id,customer_id,balance_halalas) VALUES(%s,%s,%s)',(selected,'dual-'+selected+'-customer',balance))
        c.execute("UPDATE sessions SET customer_verified_at=now() WHERE user_id=%s",('dual-'+brand+'-customer',))
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.getenv('TEST_CHROMIUM_EXECUTABLE'),args=['--no-sandbox'])
        try:
            context=browser.new_context(viewport={'width':width,'height':844})
            context.add_cookies([{'name':auth.COOKIE,'value':token,'url':base}])
            context.add_init_script("localStorage.setItem('kps_customer_lang','"+lang+"')")
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/?brand_id='+brand+'&lang='+lang,wait_until='networkidle')
            expect(page.locator('html')).to_have_attribute('dir','rtl' if lang=='ar' else 'ltr')
            page.locator('#account-button').click()
            expect(page.locator('#customer-extra-account')).to_be_visible()
            amount=('1.11' if brand=='kps' else '2.22') if lang=='en' else ('١٫١١' if brand=='kps' else '٢٫٢٢')
            expect(page.locator('#cashback-balance')).to_contain_text(amount)
            expect(page.locator('#gift-controls')).to_have_js_property('hidden',False)
            expect(page.locator('#marketing-sms')).not_to_be_checked()
            expect(page.locator('#marketing-whatsapp')).not_to_be_checked()
            assert not errors
        finally:browser.close()
