"""Real owner forms/customer choices on isolated HTTP+PostgreSQL, never live orders."""
from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from test_browser import ARTIFACTS, ROOT, _context, _owner_login, _staff, chromium_browser, live_app, synthetic_mail

pytestmark = [pytest.mark.postgres,
              pytest.mark.skipif(os.getenv('TEST_BROWSER') != '1', reason='Requires isolated PostgreSQL and actual Chromium; no external delivery proof.')]


def _capture(page, name, width):
    page.set_viewport_size({'width': width, 'height': 900})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.evaluate("""() => {
      let badge=document.querySelector('#test-evidence-label');
      if(!badge){badge=document.createElement('div');badge.id='test-evidence-label';document.body.append(badge);}
      badge.textContent='ISOLATED TEST DATA — ACTUAL CHROMIUM UI — NOT LIVE ORDERING';
      Object.assign(badge.style,{position:'fixed',top:'0',left:'0',right:'0',zIndex:'99999',background:'#183b28',color:'white',font:'10px Arial',textAlign:'center',padding:'4px',pointerEvents:'none'});
    }""")
    ARTIFACTS.mkdir(exist_ok=True)
    page.screenshot(path=str(ARTIFACTS / f'{name}-{width}.png'), full_page=False)


def test_owner_original_image_draft_publish_and_pause_only_black_through_actual_forms(live_app, chromium_browser):
    from playwright.sync_api import expect
    context, page, errors = _context(chromium_browser, live_app)
    headers = _owner_login(context, live_app, _staff())
    customer_context, customer, customer_errors = _context(chromium_browser, live_app)
    try:
        for number in (1, 9):
            response = context.request.patch(live_app + f'/api/admin/branches/kps-branch-{number}',
                headers={**headers, 'Idempotency-Key': uuid4().hex}, data={'settings': {
                    'active': True, 'ordering_verified': True, 'pickup_enabled': True,
                    'opening_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
                    'payment_methods': ['cash']}})
            assert response.status == 200, response.text()
        page.goto('/ops.html')
        page.locator('#navigation [data-page=admin]').click()
        page.locator('[data-action=admin-tab][data-tab=products]').click()
        page.locator('[data-action=create-product]').click()
        form = page.locator('#product-editor')
        form.locator('[name=name]').fill('اختبار المالك — بيانات معزولة')
        form.locator('[name=category]').fill('TEST ONLY')
        form.locator('[name=price]').fill('12.50')
        form.locator('[name=description]').fill('اختبار أصلي للصورة والتوفر فقط')
        form.locator('[name=product_image]').set_input_files(str(ROOT / 'public/assets/kps-30-8037793c7cf2.jpg'))
        expect(page.locator('#product-preview-desktop img')).to_be_visible()
        expect(page.locator('#product-preview-mobile img')).to_be_visible()
        for width in (390, 1280):
            page.locator('#product-preview-desktop').scroll_into_view_if_needed()
            _capture(page, 'owner-original-image-draft', width)
        page.locator('[data-action=add-option-group]').click()
        group = form.locator('.option-group')
        group.locator('[name^=group_code_]').fill('COLOR')
        group.locator('[name^=group_name_]').fill('اللون')
        group.locator('[name^=group_type_]').select_option('color')
        for code, label in [('BLACK', 'أسود'), ('BLUE', 'أزرق')]:
            group.locator('[data-action=add-option-value]').click()
            value = group.locator('.option-value').last
            value.locator('[name^=option_code_]').fill(code)
            value.locator('[name^=option_name_]').fill(label)
        with page.expect_response(lambda r: r.url.endswith('/api/admin/products') and r.request.method == 'POST') as saved:
            form.locator('button[type=submit]').click()
        created = saved.value.json()['product']
        identifier = created['id']
        assert created['settings']['lifecycle'] == 'draft' and not created['settings']['enabled']
        assert created['settings']['image_asset_id']
        assert identifier not in {p['id'] for p in customer_context.request.get(live_app + '/api/catalog').json()['products']}
        form = page.locator('#product-editor')
        expect(form.locator('[name=id]')).to_have_value(identifier)
        expect(page.locator('#product-preview-mobile img')).to_be_visible()
        form.locator('[name=lifecycle]').select_option('active')
        form.locator('[name=source_verified]').check()
        with page.expect_response(lambda r: '/api/admin/products/' + identifier in r.url and r.request.method == 'PATCH') as published:
            form.locator('button[type=submit]').click()
        assert published.value.status == 200, published.value.text()
        expect(page.locator('#modal')).not_to_be_visible()

        # Pause only black globally using the existing option form.
        page.locator(f'[data-action=edit-product][data-id="{identifier}"]').click()
        form = page.locator('#product-editor')
        black = form.locator('.option-value').filter(has=page.locator('input[name^=option_code_][value=BLACK]'))
        black.locator('[name^=option_enabled_]').uncheck()
        with page.expect_response(lambda r: '/api/admin/products/' + identifier in r.url and r.request.method == 'PATCH') as stopped:
            form.locator('button[type=submit]').click()
        assert stopped.value.status == 200, stopped.value.text()
        customer.goto('/')
        customer.locator('#branch-select').select_option('kps-branch-1')
        customer.locator(f'.product-card[data-product-id="{identifier}"] .product-image-button').click()
        choices = customer.locator('#product-options select')
        # Inspect the native option property: Playwright's enabled matcher does
        # not apply its disabled semantics to <option>. Real keyboard selection
        # must skip the paused choice rather than programmatically select blue.
        assert choices.locator('option[value=BLACK]').evaluate('(option) => option.disabled') is True
        assert choices.locator('option[value=BLUE]').evaluate('(option) => option.disabled') is False
        choices.press('Home')
        expect(choices).to_have_value('')
        choices.press('ArrowDown')
        expect(choices).to_have_value('BLUE')
        for width in (390, 1280):
            _capture(customer, 'customer-black-paused-blue-available', width)
        customer.locator('#product-dialog [data-close]').first.click()

        # Resume black globally and stop it only at branch 1, leaving branch 9.
        page.locator(f'[data-action=edit-product][data-id="{identifier}"]').click()
        form = page.locator('#product-editor')
        black = form.locator('.option-value').filter(has=page.locator('input[name^=option_code_][value=BLACK]'))
        black.locator('[name^=option_enabled_]').check()
        black.locator('summary').click()
        black.locator('select[name^=option_unavailable_]').select_option(['kps-branch-1'])
        for width in (390, 1280):
            black.scroll_into_view_if_needed()
            _capture(page, 'owner-black-branch-one-only', width)
        with page.expect_response(lambda r: '/api/admin/products/' + identifier in r.url and r.request.method == 'PATCH') as branch_stop:
            form.locator('button[type=submit]').click()
        assert branch_stop.value.status == 200, branch_stop.value.text()
        for branch_no, black_available in [(1, False), (9, True)]:
            customer.reload()
            customer.locator('#branch-select').select_option(f'kps-branch-{branch_no}')
            customer.locator(f'.product-card[data-product-id="{identifier}"] .product-image-button').click()
            choices = customer.locator('#product-options select')
            assert choices.locator('option[value=BLACK]').evaluate('(option) => option.disabled') is (not black_available)
            assert choices.locator('option[value=BLUE]').evaluate('(option) => option.disabled') is False
            choices.press('Home')
            expect(choices).to_have_value('')
            choices.press('ArrowDown')
            expect(choices).to_have_value('BLACK' if black_available else 'BLUE')
            customer.locator('#product-dialog [data-close]').first.click()
        page.locator('#language').click()
        page.locator(f'[data-action=edit-product][data-id="{identifier}"]').click()
        expect(page.locator('#modal-title')).to_have_text('Edit item and availability')
        expect(page.locator('#product-editor')).to_contain_text('Desktop preview')
        for width in (390, 1280):
            _capture(page, 'owner-item-control-english', width)
        assert not errors and not customer_errors
    finally:
        customer_context.close()
        context.close()
