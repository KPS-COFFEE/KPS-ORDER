"""Real mobile Chromium and WebKit against isolated PostgreSQL, bilingual."""
import os
import pytest
from app import auth,db
from test_browser import live_app,synthetic_mail,_customer_session
pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit isolated browser acceptance')]

@pytest.mark.parametrize('engine',['chromium','webkit'])
@pytest.mark.parametrize('language',['ar','en'])
def test_mobile_policy_verified_return_and_contact_draft(live_app,engine,language,tmp_path):
    from playwright.sync_api import sync_playwright,expect
    uid,token,csrf=_customer_session()
    with db.tx() as c:
        c.execute("UPDATE app_settings SET settings=settings||'{\"customer_phone_required\":true}'::jsonb WHERE brand_id='kps'")
        c.execute("UPDATE users SET identity_kind='phone_sms_verified',phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s",(uid,))
        c.execute("UPDATE sessions SET customer_verified_at=now()-interval '7 days',expires_at=now()+interval '23 days' WHERE user_id=%s",(uid,))
    with sync_playwright() as pw:
        browser=getattr(pw,engine).launch(headless=True)
        context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,locale='ar-SA',timezone_id='Asia/Riyadh')
        context.add_cookies([{'name':auth.COOKIE,'value':token,'url':live_app,'httpOnly':True,'sameSite':'Strict'},{'name':auth.CSRF_COOKIE,'value':csrf,'url':live_app,'sameSite':'Strict'}])
        page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.add_init_script("localStorage.setItem('kps_customer_lang',"+repr(language)+")")
        page.goto(live_app)
        expect(page.locator('#account-button')).to_be_enabled()
        expect(page.locator('#privacy-link')).to_have_attribute('href',f'/privacy?brand_id=kps&lang={language}')
        assert page.locator('#contact-category option').count()==8
        page.locator('#account-button').click()
        expect(page.locator('#customer-profile-form')).to_be_visible()
        expect(page.locator('#otp-request-form')).to_be_hidden()
        page.locator('#account-dialog [data-close]').click()
        page.locator('#contacts-button').click()
        page.locator('#contact-category').select_option('privacy')
        page.locator('#contact-details').fill('TEST ONLY private data request')
        page.locator('#contacts-dialog [data-close]').click()
        page.reload()
        expect(page.locator('#account-button')).to_be_enabled()
        page.locator('#contacts-button').click()
        expect(page.locator('#contact-details')).to_have_value('TEST ONLY private data request')
        page.locator('#contact-category').select_option('privacy')
        page.locator('#contact-start').click()
        expect(page.locator('#chat-dialog')).to_be_visible()
        expect(page.locator('#chat-title')).to_contain_text('CS-')
        page.locator('#chat-text').fill('TEST ONLY unsent message')
        page.locator('#chat-dialog [data-close]').click()
        page.locator('#contacts-button').click()
        page.locator('[data-conversation-id]').first.click()
        expect(page.locator('#chat-text')).to_have_value('TEST ONLY unsent message')
        composer=page.locator('#chat-text');composer.focus()
        page.evaluate("window.testComposer=document.querySelector('#chat-form')")
        page.wait_for_timeout(5500)
        assert page.evaluate("window.testComposer===document.querySelector('#chat-form')")
        expect(composer).to_have_value('TEST ONLY unsent message')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(tmp_path/f'mobile-{engine}-{language}.png'),full_page=True)
        assert not errors
        page.locator('#chat-dialog [data-close]').click()
        page.goto(live_app+'/privacy?lang='+language)
        expect(page.locator('h1')).to_have_text('Privacy policy' if language=='en' else 'سياسة الخصوصية')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        context.close();browser.close()
