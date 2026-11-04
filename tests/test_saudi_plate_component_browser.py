"""Real local browser component tests; no API, live vehicles or provider calls."""
from pathlib import Path
import os

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Requires explicit local browser acceptance')


@pytest.mark.parametrize('language,width', [('ar', 360), ('en', 390), ('ar', 1280), ('en', 1280)])
def test_plate_layout_and_live_conversion(language, width):
    from playwright.sync_api import sync_playwright, expect

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': width, 'height': 900})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.set_content(f'<html lang="{language}" dir="{"rtl" if language == "ar" else "ltr"}"><body><form id="fixture"></form><div id="legacy"></div></body></html>')
            page.add_style_tag(path=str(ROOT / 'public/ops.css'))
            page.add_style_tag(path=str(ROOT / 'public/assets/saudi-plate.css'))
            page.add_script_tag(path=str(ROOT / 'public/assets/saudi-plate.js'))
            page.evaluate('''language => {
                fixture.innerHTML = KPSSaudiPlate.field('vehicle_plate','ح م د ١٢٣٤',language);
                KPSSaudiPlate.bindInputs(fixture,language);
            }''', language)
            preview = page.locator('[data-saudi-plate-preview]')
            expect(preview.locator('.saudi-plate')).to_be_visible()
            expect(preview).to_contain_text('خصوصي')
            expect(preview.locator('.saudi-plate__numbers[lang=en]')).to_have_text('1234')
            expect(preview.locator('.saudi-plate__letters[lang=en]')).to_have_text('DZJ')
            geometry = preview.locator('.saudi-plate').evaluate('''plate => {
                const positions = lang => [...plate.querySelectorAll('.saudi-plate__letters[lang='+lang+']>span')].map(cell => cell.getBoundingClientRect().x);
                return {ar:positions('ar'),en:positions('en'),country:getComputedStyle(plate.querySelector('.saudi-plate__country')).backgroundColor};
            }''')
            assert geometry['ar'] == geometry['en']
            assert geometry['ar'] == sorted(geometry['ar'])
            assert geometry['country'] == 'rgb(255, 255, 255)'
            field = page.locator('[name=vehicle_plate]')
            field.fill('1234 DZJ')
            expect(preview.locator('.saudi-plate__letters[lang=ar]')).to_have_text('دمح')
            field.fill('7 BUV')
            expect(preview.locator('.saudi-plate__numbers[lang=ar]')).to_have_text('٧')
            expect(preview.locator('.saudi-plate__letters[lang=ar]')).to_have_text('بوى')
            field.fill('<img src=x onerror=alert(1)>')
            expect(preview.locator('img')).to_have_count(0)
            expect(preview.locator('.saudi-plate')).to_have_count(0)
            expect(preview).to_contain_text('<img')
            field.fill('')
            expect(preview).to_be_empty()
            field.fill('ح م د ١٢٣٤')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert page.evaluate("new FormData(fixture).get('vehicle_plate')") == 'ح م د ١٢٣٤'
            assert not errors, errors
        finally:
            context.close()
            browser.close()
