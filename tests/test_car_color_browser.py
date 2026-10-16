"""Actual DOM interaction with the shipped color component, no provider claims."""
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright, expect
from test_car_color import COLORS

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('engine,width', [('chromium',390), ('webkit',390), ('chromium',1280)])
@pytest.mark.parametrize('lang', ['ar','en'])
def test_color_selection_custom_switching_and_history(engine,width,lang):
    with sync_playwright() as p:
        browser = getattr(p, engine).launch()
        page = browser.new_page(viewport={'width':width,'height':844})
        try:
            page.set_content('<html lang="'+lang+'"><body><form><label class="field">Car color<input id="car-color" maxlength="40"></label><input id="car-model" value="Corolla"><input id="car-plate" value="ABC 1234"></form></body></html>')
            page.add_style_tag(path=str(ROOT/'public/customer.css'))
            page.add_script_tag(path=str(ROOT/'public/assets/car-color.js'))
            choice, custom = page.locator('#car-color-choice'), page.locator('#car-color-custom')
            expect(choice.locator('option')).to_have_count(18)
            for color in COLORS:
                choice.select_option(color)
                expect(page.locator('#car-color')).to_have_value(color)
                expect(custom).not_to_be_visible()
            choice.select_option('__custom__')
            expect(custom).to_be_visible()
            assert not custom.evaluate('e=>e.checkValidity()')
            custom.fill('   ')
            assert not custom.evaluate('e=>e.checkValidity()')
            expect(page.locator('#car-color')).to_have_value('')
            custom.fill('  لؤلؤي خاص  ')
            expect(page.locator('#car-color')).to_have_value('لؤلؤي خاص')
            choice.select_option('أبيض')
            expect(custom).to_have_value('')
            expect(page.locator('#car-color')).to_have_value('أبيض')
            choice.select_option('__custom__')
            expect(page.locator('#car-color')).to_have_value('')
            page.locator('#car-color').evaluate("e=>{e.value='Historical Pearl';e.dispatchEvent(new Event('change'));}")
            expect(custom).to_have_value('Historical Pearl')
            page.evaluate("document.documentElement.lang=document.documentElement.lang==='ar'?'en':'ar'")
            expect(custom).to_have_value('Historical Pearl')
            expect(choice.locator('option').last).to_have_text('Other Color' if lang=='ar' else 'لون آخر')
            expect(page.locator('#car-model')).to_have_value('Corolla')
            expect(page.locator('#car-plate')).to_have_value('ABC 1234')
        finally:
            browser.close()
