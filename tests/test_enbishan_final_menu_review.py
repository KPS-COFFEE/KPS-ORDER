"""Final owner review matches exact source, prices, identifiers and original art."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import db, catalog, enbishan_catalog_import as importer, enbishan_menu_review as review
from app.menu_variants import selected_price

ROOT = Path(__file__).resolve().parents[1]


def source():
    return json.loads((ROOT / 'docs/enbishan/menu-review/reference-menu.json').read_text())


def prepared():
    data = source()
    return importer.prepare(importer.MenuInput(source_sha256=data['source_sha256'],
        rows=data['rows'], owner_review=review.DECISION))


def test_all_approved_mojito_combos_are_pickup_only_without_restricting_kps_or_food():
    products = prepared()['products']
    mojitos = [p for p in products if p['name'].startswith('موهيتو')]
    assert len(mojitos) == 3
    for original in mojitos:
        product = deepcopy(original)
        product['settings'].update(enabled=True, lifecycle='active')
        before = deepcopy(product)
        assert catalog.product_availability(product, None, service='pickup')[0]
        for service in (None, 'pickup', 'delivery', 'car'):
            assert catalog.public_product(product, service=service)['pickup_only'] is True
        assert 'pickup_only' not in catalog.public_product({**product, 'brand_id': 'kps'})
        for service in ('delivery', 'car'):
            allowed, reason = catalog.product_availability(product, None, service=service)
            assert allowed is False and 'استلام' in reason
        assert catalog.product_availability({**product, 'brand_id': 'kps'}, None, service='delivery')[0]
        assert product == before
    food = deepcopy(next(p for p in products if p['name'] == 'ورق عنب'))
    food['settings'].update(enabled=True, lifecycle='active')
    assert catalog.product_availability(food, None, service='delivery')[0]


def test_final_review_retains_31_source_records_hides_only_three_and_never_activates():
    data = source()
    baseline = importer.prepare_rows(data['rows'])
    original = deepcopy(baseline)
    final = review.apply(baseline, source_sha256=data['source_sha256'], rows=data['rows'])
    assert baseline == original
    assert len(final['products']) == final['retained_products'] == 31
    visible = [p for p in final['products'] if p['settings'].get('purchasable') is not False]
    assert len(visible) == final['review_visible_products'] == 28
    assert {p['settings']['source_id'] for p in final['products'] if p not in visible} == review.HIDDEN
    assert {p['id'] for p in final['products']} == {p['id'] for p in baseline['products']}
    assert all(p['settings']['enabled'] is False and p['settings']['lifecycle'] == 'draft'
               and p['settings']['source_verified'] is False for p in final['products'])
    assert final['ordering_enabled'] is False
    assert final['catalog_digest'] == db.digest(final['products'])
    assert prepared() == final


def test_approved_mixed_sizes_retain_identifiers_and_exact_source_prices_and_original_36():
    data = source()
    baseline = next(p for p in importer.prepare_rows(data['rows'])['products']
                    if p['settings']['source_id'] == review.MIX_SOURCE)
    product = next(p for p in prepared()['products'] if p['settings']['source_id'] == review.MIX_SOURCE)
    size = next(g for g in product['settings']['option_groups'] if g['code'] == 'SIZE')
    assert [(v['code'], v['name'], v['name_en']) for v in size['values']] == [
        (code, labels[0], labels[1]) for code, labels in review.MIX_SIZES.items()]
    previous = product['settings']['owner_review_original_variants']
    assert previous == {key: baseline['settings'][key] for key in previous}
    assert len(previous['variant_price_matrix']) == 16
    assert len(product['settings']['variant_price_matrix']) == 12
    expected = [row for row in previous['variant_price_matrix'] if row['options']['SIZE'] in review.MIX_SIZES]
    assert product['settings']['variant_price_matrix'] == expected
    retired = [row for row in previous['variant_price_matrix'] if row['options']['SIZE'] not in review.MIX_SIZES]
    assert len(retired) == 4
    assert len({r['options']['SIZE'] for r in retired}) == 1
    with pytest.raises(HTTPException):
        selected_price(product, retired[0]['options'])
    assert product['price_halalas'] == baseline['price_halalas']


def test_five_approved_images_are_byte_identical_and_crispy_changes_small_variant_only():
    data = source()
    originals = {p['settings']['source_id']: p for p in importer.prepare_rows(data['rows'])['products']}
    products = {p['settings']['source_id']: p for p in prepared()['products']}
    evidence = json.loads((ROOT / 'docs/enbishan/menu-review/owner-images-20261022.json').read_text())
    assert len(evidence['assets']) == 5
    for entry in evidence['assets']:
        contents = (ROOT / 'public' / entry['path'].lstrip('/')).read_bytes()
        assert len(contents) == entry['bytes']
        assert hashlib.sha256(contents).hexdigest() == entry['sha256']
    for code, filename in review.PHOTOS.items():
        settings = products[code]['settings']
        assert settings['image_url'] == '/assets/enbishan-menu/owner-review/' + filename
        assert settings['variant_price_matrix'] == originals[code]['settings']['variant_price_matrix']
        if filename == 'crispy-small.jpeg':
            before = originals[code]['settings']['variant_images']
            for original, updated in zip(before, settings['variant_images'], strict=True):
                if original['options']['SIZE'] == review.CRISPY_SMALL_SIZE:
                    assert updated['image_url'] == settings['image_url']
                else:
                    assert updated == original


@pytest.mark.parametrize('tamper', ['workbook', 'rows', 'brand', 'artwork'])
def test_final_review_rejects_unreviewed_changes(tamper, monkeypatch):
    data = source()
    result = importer.prepare_rows(data['rows'])
    if tamper == 'workbook':
        data['source_sha256'] = '0' * 64
    elif tamper == 'rows':
        data['rows'][0]['price'] = '0.01'
    elif tamper == 'brand':
        result['products'][0]['brand_id'] = 'kps'
    else:
        monkeypatch.setattr(review.enbishan_menu_assets, 'valid_bundled', lambda path: False)
    with pytest.raises(ValueError):
        review.apply(result, source_sha256=data['source_sha256'], rows=data['rows'])
