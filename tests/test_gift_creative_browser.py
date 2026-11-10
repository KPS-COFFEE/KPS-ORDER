"""Approved creative only; no production requests, OTP, or offer activation."""
import hashlib
import os
from pathlib import Path
import pytest
from test_experience_entry_browser import static_customer

pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Explicit Chromium run')
TITLE = 'البدايات الحلوة… لها هدية من كبز 🎁'


@pytest.mark.parametrize('width', [320, 390, 768, 1366])
def test_exact_logo_left_phrase_only_shimmer_and_reduced_motion(static_customer, width):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': width, 'height': 1000})
        page.goto(static_customer + '/assets/kps-gift-creative.js')
        page.set_content('<html><body style="margin:0"><div id="ad"></div></body></html>')
        page.add_style_tag(url=static_customer + '/style.css')
        page.add_style_tag(url=static_customer + '/assets/kps-gift-creative.css')
        page.add_script_tag(url=static_customer + '/assets/kps-gift-creative.js')
        def render(brand, title):
            return page.evaluate('(args)=>KPSGiftCreative.render(document.querySelector("#ad"),{brandId:args[0],banner:{title:args[1]}})', [brand, title])
        assert not render('other', TITLE)
        assert not render('kps', 'Unapproved campaign')
        assert page.locator('#ad').inner_html() == ''
        assert render('kps', TITLE)
        page.evaluate('document.fonts.ready')
        expect(page.locator('.kps-gift-logo')).to_be_visible()
        logo = page.locator('.kps-gift-logo').bounding_box()
        ad = page.locator('.kps-gift-ad').bounding_box()
        assert logo['x'] < ad['x'] + ad['width'] / 2
        assert page.evaluate('document.documentElement.scrollWidth') <= width
        assert page.locator('.kps-gift-phrase > em').inner_text() == 'هدية'
        assert page.locator('.kps-gift-phrase > .kps-gift-glint').get_attribute('aria-hidden') == 'true'
        animated = page.locator('.kps-gift-ad').evaluate('(ad)=>[ad,...ad.querySelectorAll("*")].filter(e=>getComputedStyle(e).animationName!=="none").map(e=>e.className)')
        assert animated == ['kps-gift-glint', 'kps-gift-glint kps-gift-product-glint']
        phrase_glint = page.locator('.kps-gift-phrase > .kps-gift-glint')
        product_glint = page.locator('.kps-gift-product-glint')
        products = page.locator('.kps-gift-products')
        assert products.evaluate('(e)=>e.childNodes[0].textContent+e.childNodes[1].textContent+e.childNodes[2].textContent') == 'قهوة اليوم بارد + كوكيز'
        assert products.evaluate('(e)=>getComputedStyle(e).whiteSpace') == 'nowrap'
        assert products.evaluate('(e)=>parseFloat(getComputedStyle(e).fontSize)') > page.locator('.kps-gift-caption').evaluate('(e)=>parseFloat(getComputedStyle(e).fontSize)')
        assert products.bounding_box()['y'] >= page.locator('.kps-gift-phrase').bounding_box()['y'] + page.locator('.kps-gift-phrase').bounding_box()['height']
        assert products.evaluate('(e)=>e.scrollWidth<=e.clientWidth')
        assert phrase_glint.evaluate('(e)=>getComputedStyle(e).animationDuration') == '2.5s'
        assert product_glint.evaluate('(e)=>getComputedStyle(e).animationDuration') == '2s'
        assert product_glint.get_attribute('aria-hidden') == 'true'
        for ms in (0, 250, 500, 1999):
            product_glint.evaluate('(e,ms)=>{const a=e.getAnimations()[0];a.pause();a.currentTime=ms}', ms)
            assert products.evaluate('(e)=>getComputedStyle(e).opacity') == '1'
            if ms >= 500:
                assert product_glint.evaluate('(e)=>getComputedStyle(e).opacity') == '0'
            elif ms == 250:
                assert float(product_glint.evaluate('(e)=>getComputedStyle(e).opacity')) > 0
        frames = phrase_glint.evaluate('(e)=>e.getAnimations()[0].effect.getKeyframes().map(f=>({offset:f.offset,position:f.maskPosition||f.webkitMaskPosition,opacity:f.opacity}))')
        assert any(frame['offset'] == .2 and frame['opacity'] == '0' for frame in frames)
        assert page.locator('.kps-gift-phrase').evaluate('(e)=>getComputedStyle(e).opacity') == '1'
        page.emulate_media(reduced_motion='reduce')
        for glint in (phrase_glint, product_glint):
            assert glint.evaluate('(e)=>getComputedStyle(e).animationName') == 'none'
            assert glint.evaluate('(e)=>getComputedStyle(e).opacity') == '0'
        assert products.evaluate('(e)=>getComputedStyle(e).opacity') == '1'
        assert page.locator('.kps-gift-phrase').evaluate('(e)=>getComputedStyle(e).opacity') == '1'
        path = Path('test-results') / f'gift-creative-{width}.png'
        path.parent.mkdir(exist_ok=True)
        page.locator('#ad').screenshot(path=str(path))
        browser.close()


def test_owner_logo_bytes_unchanged():
    path = Path(__file__).resolve().parents[1] / 'public/assets/kps-gift-original.png'
    assert hashlib.sha256(path.read_bytes()).hexdigest() == 'b9c02dcf851808c0bc629aecdda63921462e31ae9609fef460bc9f9b6b2675ca'
