"""Real iframe viewports, same storefront CSS, no publishing requests."""
import base64
import io
import os
from pathlib import Path
import pytest
from PIL import Image
from test_experience_entry_browser import static_customer

pytestmark=pytest.mark.skipif(os.getenv('TEST_BROWSER')!='1',reason='Explicit Chromium run')


@pytest.mark.parametrize('lang',['ar','en'])
def test_owner_preview_has_three_real_viewports_and_preserves_the_whole_image(static_customer,lang):
    from playwright.sync_api import sync_playwright,expect
    image=io.BytesIO();Image.new('RGB',(1000,700),'orange').save(image,'PNG')
    data='data:image/png;base64,'+base64.b64encode(image.getvalue()).decode()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1280,'height':950})
        # Match production policy: srcdoc must work without weakening CSP or
        # allowing scripts inside the preview frame.
        def security_headers(route):
            response=route.fetch()
            route.fulfill(response=response,headers={**response.headers,
                'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
                'X-Frame-Options':'DENY','Content-Type':'application/javascript; charset=utf-8'})
        page.route('**/assets/banner-device-preview.js',security_headers)
        requests=[];errors=[]
        page.on('request',lambda request:requests.append(request.url) if '/api/' in request.url else None)
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(static_customer+'/assets/banner-device-preview.js')
        page.set_content(f'<html lang="{lang}" dir="'+('rtl' if lang=='ar' else 'ltr')+'"><head><link rel="stylesheet" href="/assets/banner-device-preview.css"></head><body><div id="owner" class="banner-device-previews">'+''.join(f'<section class="banner-preview"><div id="{screen}-preview"></div></section>' for screen in ['mobile','tablet','desktop'])+'</div></body></html>')
        page.add_script_tag(url=static_customer+'/assets/banner-device-preview.js')
        page.evaluate("""image=>{const esc=v=>String(v).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));window.KPSBannerDevicePreview.render({root:document.querySelector('#owner'),esc,tr:(ar,en)=>document.documentElement.lang==='en'?en:ar,title:'<img src=x onerror=alert(1)>',body:'قهوة اليوم وكوكيز',label:'متابعة',desktop:image,mobile:image});}""",data)
        for screen,width in [('mobile',390),('tablet',768),('desktop',1366)]:
            frame=page.locator('#'+screen+'-preview iframe').element_handle().content_frame()
            expect(frame.locator('.campaign-banner picture img')).to_be_visible()
            assert frame.evaluate('innerWidth')==width
            assert frame.locator('.campaign-banner picture img').evaluate("el=>getComputedStyle(el).objectFit")=='contain'
            assert frame.locator('h2').first.inner_text()=='<img src=x onerror=alert(1)>'
            assert frame.locator('.customer-entry h2').inner_text()==('الاسم والجوال أولًا' if lang=='ar' else 'Your name and mobile first')
            assert frame.locator('[onerror]').count()==0
            assert page.locator('#'+screen+'-preview iframe').evaluate('el=>getComputedStyle(el).pointerEvents')!='none'
            if screen=='desktop':
                frame.evaluate('scrollTo(0,document.documentElement.scrollHeight)')
                assert frame.evaluate('scrollY')>0
                frame.evaluate('scrollTo(0,0)')
        assert not requests and not errors
        Path('test-results').mkdir(exist_ok=True)
        page.screenshot(path=f'test-results/customer-entry-ad-preview-{lang}.png',full_page=True)
        browser.close()
