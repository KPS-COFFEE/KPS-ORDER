import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.getenv("TEST_BROWSER") != "1", reason="Explicit browser acceptance")
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("product_index", [0, 1])
@pytest.mark.parametrize("lang,width", [("ar", 390), ("ar", 1280), ("en", 390)])
def test_manager_uses_individual_flavours_and_emits_only_complete_catalog_choices(product_index, lang, width):
    from playwright.sync_api import sync_playwright, expect

    product = json.loads((ROOT / "tests/fixtures/enbishan-grape-flavours-public.json").read_text())[product_index]
    product["available"] = True
    group = next(g for g in product["option_groups"] if g["code"] == "FLAVOUR")
    pairs = [v for v in group["values"] if v["code"].startswith("pair-")]
    mixed = next(v for v in group["values"] if v["name"] == "مشكل")
    with sync_playwright() as p:
        launch = {"headless": True}
        if os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE"):
            launch["executable_path"] = os.environ["PLAYWRIGHT_CHROMIUM_EXECUTABLE"]
        browser = p.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": width, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content('<html dir="' + ("rtl" if lang == "ar" else "ltr") + '"><body><dialog open style="width:min(1000px,calc(100vw - 24px))"><div id="modal-body"></div></dialog></body></html>')
        page.add_style_tag(path=str(ROOT / "public/ops.css"))
        page.add_style_tag(path=str(ROOT / "public/assets/staff-menu.css"))
        page.add_script_tag(path=str(ROOT / "public/assets/customer-order.js"))
        page.evaluate("""async ({product,lang})=>{
          window.requests=[];
          const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
          await window.KPSCustomerOrder.open({
            user:{id:'manager',role:'delivery_manager',brand_id:'enbishan'},
            branches:[{id:'branch',name:'Test branch'}],
            api:async path=>path.startsWith('/api/catalog')?{products:[product]}:{shifts:[{user_id:'manager',kind:'manager',status:'open',branch_ids:['branch']}]},
            mutation:async (path,body)=>{window.requests.push({path,body});return {quote:{subtotal_halalas:1200,delivery_fee_halalas:600,total_halalas:1800}}},
            tr:(ar,en)=>lang==='ar'?ar:en,esc,money:value=>String(value),
            modal:(title,html,submit)=>{document.querySelector('#modal-body').innerHTML=html;window.submitOrder=submit},
            closeModal:()=>{},onCreated:()=>{}
          });
        }""", {"product": product, "lang": lang})
        field = page.locator(".staff-grape-flavours")
        picks = field.locator("[data-grape-pick]")
        expect(picks).to_have_count(4)
        assert "+" not in field.inner_text()
        expect(field.locator("[data-flavour-resolved]")).not_to_be_visible()
        # Choose the first size normally; FLAVOUR uses the separate, resolved value.
        for size_field in page.locator("#staff-options>fieldset").all():
            if "staff-grape-flavours" not in (size_field.get_attribute("class") or ""):
                size_field.locator("input").first.check()
        for name, value in {"phone": "0500000000", "neighborhood": "Test", "address": "Test", "lat": "28.4", "lng": "45.9"}.items():
            page.locator('[name="' + name + '"]').fill(value)
        page.locator("#staff-add").click()
        expect(page.locator("#staff-cart>div")).to_have_count(0)
        for pair in pairs:
            picks.nth(3).check()
            picks.nth(3).uncheck()
            names = pair["name"].split(" + ")
            all_names = list(dict.fromkeys(name for value in pairs for name in value["name"].split(" + ")))
            indexes = [all_names.index(name) for name in names]
            picks.nth(indexes[0]).check()
            page.locator("#staff-add").click()
            expect(page.locator("#staff-cart>div")).to_have_count(0)
            picks.nth(indexes[1]).check()
            expect(picks.nth(next(i for i in range(3) if i not in indexes))).to_be_disabled()
            page.locator("#staff-add").click()
            expect(page.locator("#staff-cart>div")).to_have_count(1)
            page.evaluate("async()=>{const f=document.querySelector('#staff-order-form');await window.submitOrder(new FormData(f),f)}")
            sent = page.evaluate("window.requests.at(-1).body.items[0].units[0].options.FLAVOUR")
            assert sent == [pair["code"]]
            page.locator("[data-remove]").click()
        picks.nth(3).check()
        assert picks.evaluate_all("(inputs)=>inputs.filter(i=>i.checked).length") == 1
        page.locator("#staff-add").click()
        page.evaluate("async()=>{const f=document.querySelector('#staff-order-form');await window.submitOrder(new FormData(f),f)}")
        assert page.evaluate("window.requests.at(-1).body.items[0].units[0].options.FLAVOUR") == [mixed["code"]]
        page.locator("[data-remove]").click()
        picks.nth(0).check()
        expect(picks.nth(3)).not_to_be_checked()
        page.locator("#staff-add").click()
        expect(page.locator("#staff-cart>div")).to_have_count(0)
        assert field.locator("label").evaluate_all("(labels)=>labels.every(l=>getComputedStyle(l).flexDirection==='row'&&l.scrollWidth<=l.clientWidth+1)")
        assert not errors
        browser.close()
