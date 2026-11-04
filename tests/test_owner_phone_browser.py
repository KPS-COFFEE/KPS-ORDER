import os
import socket
import threading
import time
import pytest
import uvicorn
import httpx
from fastapi.testclient import TestClient
from app import auth,db
from tests.test_core import _authenticate,_make_user
from tests.test_tawked_otp import setup_provider
from test_browser import _context

pytestmark=[pytest.mark.postgres,pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Requires isolated PostgreSQL and actual Chromium')]


@pytest.fixture
def chromium_browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,args=['--no-sandbox'],executable_path=os.getenv('TEST_CHROMIUM_EXECUTABLE') or None)
        try:yield browser
        finally:browser.close()


@pytest.fixture
def phone_app(database,monkeypatch,request):
    setup_provider(monkeypatch)
    brand = getattr(request, 'param', 'legacy-owner-phone')
    if brand != 'kps':
        # Preserve the supported non-KPS legacy binding flow without disabling KPS policy.
        monkeypatch.setenv('EMPLOYEE_SMS_LOGIN_ENABLED', 'false')
        with db.tx() as conn:
            conn.execute('INSERT INTO brands(id,name) VALUES(%s,%s)', (brand, 'Synthetic legacy owner phone'))
            conn.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled,delivery_proven_at,sms_provider) VALUES(%s,'sms','active',true,now(),'tawked')", (brand,))
    owner=_make_user('owner',brand=brand,username='synthetic_phone_owner',password='Synthetic-Phone-Only-42')
    from app.main import app
    with TestClient(app) as client:token,csrf=_authenticate(client,owner)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    monkeypatch.setenv('PUBLIC_BASE_URL',base)
    monkeypatch.setenv('APP_ALLOWED_ORIGINS',base)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            try:
                if httpx.get(base+'/ready',timeout=1,trust_env=False).status_code==200:break
            except httpx.HTTPError:pass
            time.sleep(.1)
        else:pytest.fail('Local app not ready')
        yield base,owner,token,csrf
    finally:
        server.should_exit=True;thread.join(timeout=10)
        assert not thread.is_alive()


@pytest.mark.parametrize('lang',['ar','en'])
def test_legacy_owner_links_mobile_without_email_and_stays_signed_in(phone_app,chromium_browser,lang):
    from playwright.sync_api import expect
    base,owner,token,csrf=phone_app
    context,page,errors=_context(chromium_browser,base)
    try:
        context.add_cookies([{'name':auth.COOKIE,'value':token,'url':base},{'name':auth.CSRF_COOKIE,'value':csrf,'url':base}])
        page.goto(base+'/owner-phone?lang='+lang,wait_until='networkidle')
        expect(page.locator('html')).to_have_attribute('lang',lang)
        page.locator('input[name=phone]').fill('0507778899')
        page.locator('input[name=password]').fill('Synthetic-Phone-Only-42')
        page.locator('#send').click()
        expect(page.locator('#verify')).to_be_visible()
        with db.tx() as conn:assert conn.execute('SELECT phone FROM users WHERE id=%s',(owner['id'],)).fetchone()['phone'] is None
        page.locator('input[name=code]').fill('٠١٢٣')
        page.locator('#confirm').click()
        expect(page.locator('#status')).to_contain_text('*******899')
        with db.tx() as conn:
            row=conn.execute('SELECT * FROM users WHERE id=%s',(owner['id'],)).fetchone()
            assert row['phone']=='+966507778899' and row['phone_sms_verified_at']
            assert row['email_verified_at'] is None and row['role']=='owner' and row['active']
        session=context.request.get(base+'/api/session',headers={'X-KPS-Context':'work'}).json()
        assert session['user']['id']==owner['id'] and session['user']['role']=='owner'
        assert not errors,errors
    finally:context.close()


@pytest.mark.parametrize('phone_app', ['kps'], indirect=True)
def test_kps_owner_password_binding_requires_sms_and_preserves_identity(phone_app,chromium_browser):
    from playwright.sync_api import expect
    base,owner,token,csrf=phone_app
    context,page,errors=_context(chromium_browser,base)
    try:
        context.add_cookies([{'name':auth.COOKIE,'value':token,'url':base},{'name':auth.CSRF_COOKIE,'value':csrf,'url':base}])
        page.goto(base+'/owner-phone',wait_until='networkidle')
        page.locator('input[name=phone]').fill('0507778899')
        page.locator('input[name=password]').fill('Synthetic-Phone-Only-42')
        with page.expect_response(lambda response: response.url.endswith('/api/identity/step-up')) as proof:
            page.locator('#send').click()
        assert proof.value.status == 403
        assert proof.value.json()['detail']['code'] == 'WORK_SMS_REQUIRED'
        expect(page.locator('#verify')).not_to_be_visible()
        with db.tx() as conn:
            row=conn.execute('SELECT * FROM users WHERE id=%s',(owner['id'],)).fetchone()
            assert row['phone'] is None and row['phone_sms_verified_at'] is None
            assert row['role']=='owner' and row['active']
            assert conn.execute('SELECT count(*) n FROM owner_phone_challenges WHERE user_id=%s',(owner['id'],)).fetchone()['n']==0
        session=context.request.get(base+'/api/session',headers={'X-KPS-Context':'work'}).json()
        assert session['user']['id']==owner['id'] and session['user']['role']=='owner'
        assert not errors,errors
    finally:context.close()
