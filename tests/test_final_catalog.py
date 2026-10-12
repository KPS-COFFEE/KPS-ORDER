"""Final owner menu decision: one card, exact provenance, safe historical identities."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi import HTTPException
from psycopg import sql

from app import catalog, db

ROOT = Path(__file__).resolve().parents[1]
OWNER_IMAGE_SHA256 = '4bffccdcf1a7e9667c92176f2f70675978f38704c4a04c05731383447c6547e7'


def _body(**changes):
    return catalog.QuoteBody.model_validate({
        'brand_id': 'kps', 'branch_id': 'kps-branch-2', 'service': 'pickup', 'payment_method': 'cash',
        'items': [{'product_id': 'kps-33', 'quantity': 3,
                   'units': [{'options': {'TYPE': 'classic'}}, {'options': {'TYPE': 'pecan'}},
                             {'options': {'TYPE': 'classic'}}]}],
        'customer': {'name': 'عميل اختبار معزول', 'phone': '0500001234'}, **changes})


def _prepare_branch(conn):
    conn.execute("UPDATE branches SET settings=settings||%s WHERE id='kps-branch-2'", (db.json({
        'ordering_verified': True, 'pickup_enabled': True, 'active': True,
        'opening_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
        'payment_methods': ['cash']}),))


def test_owner_image_is_exact_original_and_web_copy_is_present():
    original = ROOT / 'public/assets/khaliya-owner-20261010.jpeg'
    assert hashlib.sha256(original.read_bytes()).hexdigest() == OWNER_IMAGE_SHA256
    assert (ROOT / 'public/assets/khaliya-owner-20261010.webp').is_file()
    # Immutable imported source material still names both original source records.
    source = json.loads((ROOT / 'data/catalog.json').read_text())
    assert source['metadata']['source_commit'] == 'b9d4f523c18981032151f5ab5dad57bd23f946c5'
    assert {p['id']: p['price_halalas'] for p in source['products'] if p['id'] in {'kps-33', 'kps-34'}} == {'kps-33': 1950, 'kps-34': 1950}


@pytest.mark.postgres
def test_final_catalog_one_card_exact_types_image_source_approval_and_no_branch_invention(client):
    result = client.get('/api/catalog?branch_id=kps-branch-2').json()
    assert result['source_verified'] is True
    assert len(result['products']) == 34
    assert not any(p['id'] == 'kps-34' for p in result['products'])
    parent = next(p for p in result['products'] if p['id'] == 'kps-33')
    assert parent['name'] == 'الخلية'
    assert parent['image_url'] == '/assets/khaliya-owner-20261010.webp'
    assert parent['pending_owner_image'] is False
    group, = parent['option_groups']
    assert (group['code'], group['name'], group['required']) == ('TYPE', 'اختر النوع', True)
    assert [(v['name'], v['source_product_id'], parent['price_halalas']+v['price_delta_halalas']) for v in group['values']] == [
        ('كلاسيك', 'kps-34', 1950), ('بيكان', 'kps-33', 1950)]
    assert result['legacy_item_mappings']['kps-34']['options'] == {'TYPE': 'classic'}
    assert result['legacy_item_mappings']['kps-33']['options'] == {'TYPE': 'pecan'}
    assert client.get(parent['image_url']).status_code == 200
    with db.tx() as conn:
        decisions = conn.execute('SELECT * FROM catalog_owner_approvals').fetchall()
        assert len(decisions) == 35
        assert all(r['result'] == 'approved' and r['evaluated_at'] for r in decisions)
        assert all(r['source_commit'] == 'b9d4f523c18981032151f5ab5dad57bd23f946c5' for r in decisions)
        old = conn.execute("SELECT * FROM products WHERE id='kps-34'").fetchone()
        assert old['name'] == 'خلية كلاسيك' and old['price_halalas'] == 1950
        assert old['settings']['image_url'] == '/assets/kps-34-8c01e210be90.svg'
        assert old['settings']['purchasable'] is False
        assert not conn.execute("SELECT 1 FROM branches WHERE settings->>'ordering_verified'='true'").fetchone()


@pytest.mark.postgres
def test_final_mixed_types_preserve_independent_quantity_and_historical_source_identity(database):
    with db.tx() as conn:
        _prepare_branch(conn)
        quote = catalog.quote_order(conn, _body())
    assert quote['total_halalas'] == 5850
    assert [(i['name'], i['source_id'], i['quantity'], i['line_total_halalas']) for i in quote['items']] == [
        ('الخلية — كلاسيك', 'kps-34', 2, 3900), ('الخلية — بيكان', 'kps-33', 1, 1950)]
    assert all(i['product_id'] == 'kps-33' for i in quote['items'])
    assert [u['source_id'] for u in quote['items'][0]['units']] == ['kps-34', 'kps-34']


@pytest.mark.parametrize('items', [
    [{'product_id': 'kps-33', 'quantity': 1}],
    [{'product_id': 'kps-33', 'quantity': 1, 'units': [{'options': {'TYPE': 'invalid'}}]}],
    [{'product_id': 'kps-34', 'quantity': 1}],
])
@pytest.mark.postgres
def test_final_stale_api_never_silently_selects_or_changes_a_type(database, items):
    with db.tx() as conn:
        _prepare_branch(conn)
        with pytest.raises(HTTPException) as failure:
            catalog.quote_order(conn, _body(items=items))
    assert failure.value.status_code in {409, 422}


@pytest.mark.postgres
def test_final_server_hafar_guard_even_when_old_admin_override_exists(database):
    with db.tx() as conn:
        _prepare_branch(conn)
        conn.execute("UPDATE branches SET city='رفحاء' WHERE id='kps-branch-2'")
        conn.execute("UPDATE products SET settings=settings||%s WHERE id IN ('kps-33','kps-34')",
                     (db.json({'availability_policy_version': 99, 'available_cities': ['رفحاء']}),))
        with pytest.raises(HTTPException) as failure:
            catalog.quote_order(conn, _body())
        assert failure.value.detail['code'] == 'PRODUCT_UNAVAILABLE'
        assert 'حفر الباطن' in failure.value.detail['message']


@pytest.mark.postgres
def test_final_classic_source_stock_stop_is_still_enforced(database):
    with db.tx() as conn:
        _prepare_branch(conn)
        conn.execute("UPDATE products SET settings=settings||%s WHERE id='kps-34'",
                     (db.json({'unavailable_branch_ids': ['kps-branch-2']}),))
        with pytest.raises(HTTPException) as failure:
            catalog.quote_order(conn, _body())
        assert failure.value.detail['code'] == 'PRODUCT_VARIANT_UNAVAILABLE'
        body = _body(items=[{'product_id': 'kps-33', 'quantity': 1, 'units': [{'options': {'TYPE': 'pecan'}}]}])
        assert catalog.quote_order(conn, body)['total_halalas'] == 1950


@pytest.mark.postgres
def test_final_menu_approval_is_not_a_future_price_or_option_bypass(database):
    with db.tx() as conn:
        product = conn.execute("SELECT * FROM products WHERE id='kps-01'").fetchone()
        assert catalog.source_approved(product)
        conn.execute("UPDATE products SET price_halalas=price_halalas+1 WHERE id='kps-01'")
        db.apply_migrations(conn)
        changed = conn.execute("SELECT * FROM products WHERE id='kps-01'").fetchone()
        assert changed['price_halalas'] == product['price_halalas']+1
        assert not catalog.source_approved(changed)
        parent = conn.execute("SELECT * FROM products WHERE id='kps-33'").fetchone()
        assert parent['settings']['unified_khaliya'] is True
        assert parent['settings']['owner_image_approval']['source_sha256'] == OWNER_IMAGE_SHA256


@pytest.mark.postgres
def test_final_existing_live_difference_is_skipped_once_without_overwrite(database):
    with db.tx() as conn:
        # A disposable nested schema models a pre-existing live DB before first
        # approval, without resetting the normal fixture or any runtime database.
        with conn.transaction(force_rollback=True):
            schema = 'kps_test_approval_' + uuid.uuid4().hex
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            conn.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
            db.apply_migrations(conn, seed=False)
            db.seed_initial(conn)
            conn.execute("UPDATE products SET price_halalas=price_halalas+100 WHERE id='kps-01'")
            catalog.apply_final_catalog_approval(conn)
            decision = conn.execute("SELECT * FROM catalog_owner_approvals WHERE product_id='kps-01'").fetchone()
            assert decision['result'] == 'skipped_mismatch'
            row = conn.execute("SELECT * FROM products WHERE id='kps-01'").fetchone()
            assert row['price_halalas'] == decision['source_snapshot']['price_halalas']+100
            assert row['settings']['source_verified'] is False
            conn.execute("UPDATE products SET price_halalas=%s WHERE id='kps-01'", (decision['source_snapshot']['price_halalas'],))
            catalog.apply_final_catalog_approval(conn)
            assert conn.execute("SELECT settings FROM products WHERE id='kps-01'").fetchone()['settings']['source_verified'] is False
            with pytest.raises(psycopg.Error):
                with conn.transaction():
                    conn.execute("UPDATE catalog_owner_approvals SET source_digest='forged' WHERE product_id='kps-01'")

# Reuse the existing isolated real-HTTP fixtures; responses are never intercepted.
from tests.test_browser import live_app, chromium_browser, synthetic_mail  # noqa: E402,F401


@pytest.mark.postgres
@pytest.mark.skipif(__import__('os').environ.get('TEST_BROWSER') != '1', reason='Requires explicit real Chromium acceptance.')
def test_final_real_browser_stale_cart_mapping_one_image_and_required_type(live_app, chromium_browser):
    from playwright.sync_api import expect
    artifacts = ROOT / 'test-artifacts'
    artifacts.mkdir(exist_ok=True)
    context = chromium_browser.new_context(base_url=live_app, locale='ar-SA', timezone_id='Asia/Riyadh',
                                            viewport={'width': 1280, 'height': 900})
    context.add_init_script("""localStorage.setItem('kps-unconfirmed-basket-v2:kps',JSON.stringify({brandId:'kps',cart:[
      {productId:'kps-34',quantity:2,options:{},note:'كلاسيك محفوظ'},
      {productId:'kps-33',quantity:1,options:{},note:'بيكان محفوظ'}]}));""")
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto('/')
        expect(page.locator('.product-card[data-product-id="kps-33"]')).to_have_count(1)
        expect(page.locator('.product-card[data-product-id="kps-34"]')).to_have_count(0)
        expect(page.locator('.cart-line-title').filter(has_text='الخلية — كلاسيك')).to_have_count(1)
        expect(page.locator('.cart-line-title').filter(has_text='الخلية — بيكان')).to_have_count(1)
        expect(page.locator('.cart-line').filter(has_text='صنف محفوظ سابقًا: خلية كلاسيك')).to_have_count(1)
        expect(page.locator('.cart-line').filter(has_text='صنف محفوظ سابقًا: خلية بيكان')).to_have_count(1)
        page.locator('#search').fill('بيكان')
        expect(page.locator('.product-card')).to_have_count(1)
        expect(page.locator('.product-card img')).to_have_attribute('src', '/media/previews/khaliya-owner-20261010.webp')
        page.wait_for_function("document.querySelector('.product-card img')?.naturalWidth > 0")
        for width in (390, 1280):
            page.set_viewport_size({'width': width, 'height': 900})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(artifacts / f'final-khaliya-single-card-{width}.png'))
        page.locator('#branch-select').select_option('kps-branch-2')
        page.get_by_role('button', name='تفاصيل الخلية', exact=True).click()
        expect(page.locator('#product-options select')).to_have_value('')
        expect(page.locator('#product-options label')).to_contain_text('اختر النوع')
        page.locator('#product-add').click()
        expect(page.locator('#product-error')).to_contain_text('حدد النوع')
        page.locator('#product-qty').fill('2')
        page.locator('#product-qty').dispatch_event('change')
        page.locator('#product-options select').nth(0).select_option('classic')
        page.locator('#product-options select').nth(1).select_option('pecan')
        for width in (390, 1280):
            page.set_viewport_size({'width': width, 'height': 900})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(artifacts / f'final-khaliya-required-types-{width}.png'))
        page.locator('#product-add').click()
        expect(page.locator('#product-dialog')).not_to_be_visible()
        # Different notes retain separate rows; even a mixed add keeps exact types.
        expect(page.locator('.cart-line-title').filter(has_text='الخلية — كلاسيك')).to_have_count(2)
        expect(page.locator('.cart-line-title').filter(has_text='الخلية — بيكان')).to_have_count(2)
        saved = page.evaluate("JSON.parse(localStorage.getItem('kps-unconfirmed-basket-v2:kps')).cart")
        quantities = {'classic': 0, 'pecan': 0}
        for line in saved:
            assert line['productId'] == 'kps-33'
            quantities[line['options']['TYPE']] += line['quantity']
        assert quantities == {'classic': 3, 'pecan': 2}
        page.get_by_role('button', name='تكبير صورة الخلية', exact=True).click()
        expect(page.locator('#image-dialog')).to_be_visible()
        expect(page.locator('#image-full')).to_have_attribute('src', '/assets/khaliya-owner-20261010.webp')
        page.screenshot(path=str(artifacts / 'final-khaliya-approved-image-enlarged-1280.png'))
        assert not errors
    finally:
        context.close()
