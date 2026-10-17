"""Real local HTTP, PostgreSQL and Chromium; no live customers/providers."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest
from playwright.sync_api import sync_playwright, expect

from app import auth
from test_enbishan_dual_brand import dual

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def dual_http(dual,tmp_path):
    with socket.socket() as reserve:
        reserve.bind(('127.0.0.1',0));port=reserve.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    env=dict(os.environ,PUBLIC_BASE_URL=base,APP_ALLOWED_ORIGINS=base,
             APP_ALLOWED_HOSTS='127.0.0.1,localhost',SESSION_COOKIE_SECURE='false')
    with (tmp_path/'http.log').open('w') as log:
        process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],
                                 cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                if process.poll() is not None:
                    pytest.fail('Local isolated HTTP server exited: '+(tmp_path/'http.log').read_text()[-2000:])
                try:
                    if httpx.get(base+'/health',timeout=1,trust_env=False).status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            else:pytest.fail('Local isolated HTTP server did not become healthy.')
            yield base,dual
        finally:
            process.terminate()
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


@pytest.mark.postgres
@pytest.mark.parametrize('brand',['kps','enbishan'])
@pytest.mark.parametrize('lang',['ar','en'])
def test_mobile_storefront_catalog_brand_and_language(dual_http,brand,lang):
    base,_=dual_http
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.getenv('TEST_CHROMIUM_EXECUTABLE'),args=['--no-sandbox'])
        try:
            context=browser.new_context(viewport={'width':390,'height':844})
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/?brand_id='+brand+'&lang='+lang)
            expect(page.locator('.product-card').first).to_be_visible()
            if brand=='enbishan':
                expect(page.locator('.product-card')).to_have_count(1)
                expect(page.locator('#brand-home')).to_contain_text('ENBISHAN TEST ONLY')
                assert '0550472662' not in page.content()
            else:
                expect(page.locator('#brand-home')).to_contain_text('KPS COFFEE')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            assert not errors,errors
            output=ROOT/'test-results'/'enbishan-mobile';output.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(output/(brand+'-'+lang+'.png')),full_page=True)
            context.close()
        finally:browser.close()


@pytest.mark.postgres
def test_same_manager_account_switches_brands_on_mobile(dual_http):
    base,(_,actor,_,_,_)=dual_http
    client=actor('manager')
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.getenv('TEST_CHROMIUM_EXECUTABLE'),args=['--no-sandbox'])
        try:
            context=browser.new_context(viewport={'width':390,'height':844})
            context.add_cookies([{'name':name,'value':client.cookies.get(name),'url':base} for name in [auth.COOKIE,auth.CSRF_COOKIE]])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/ops')
            select=page.locator('#operations-brand-switch select')
            expect(select).to_have_value('kps')
            select.select_option('enbishan')
            expect(page).to_have_url(base+'/ops?operations_brand=enbishan')
            expect(select).to_have_value('enbishan')
            expect(page.locator('.brand>span')).to_contain_text('ENBISHAN TEST ONLY')
            assert not errors,errors
            context.close()
        finally:browser.close()


@pytest.mark.postgres
def test_shared_driver_sees_other_brand_task_and_switches_without_new_login(dual_http):
    base,(_,actor,post,shifts,order)=dual_http
    ks=shifts('kps');es=shifts('enbishan')
    post('manager','/api/orders/'+order('kps')+'/assign',{'driver_shift_id':ks[1]})
    post('manager','/api/orders/'+order('enbishan')+'/assign',{'driver_shift_id':es[1]},'enbishan')
    client=actor('driver')
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.getenv('TEST_CHROMIUM_EXECUTABLE'),args=['--no-sandbox'])
        try:
            context=browser.new_context(viewport={'width':390,'height':844})
            context.add_cookies([{'name':name,'value':client.cookies.get(name),'url':base} for name in [auth.COOKIE,auth.CSRF_COOKIE]])
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/ops')
            other=page.locator('a[href="/ops?operations_brand=enbishan"]')
            expect(other).to_contain_text('ENBISHAN TEST ONLY')
            other.click()
            expect(page.locator('#operations-brand-switch select')).to_have_value('enbishan')
            expect(page.locator('.brand>span')).to_contain_text('ENBISHAN TEST ONLY')
            assert not errors,errors
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            context.close()
        finally:browser.close()
