"""Owner-approved source preparation, without database or provider calls."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from fastapi import HTTPException
from app import catalog
from app.enbishan_catalog_import import prepare_rows
from app.menu_variants import selected_price
from tools.enbishan_workbook import reference_menu

MANIFEST = Path(__file__).resolve().parents[1] / 'docs/enbishan/menu-review/reference-menu.json'


def reviewed():
    data = json.loads(MANIFEST.read_text())
    headers = {'B': 'Name Item English', 'C': 'اسم الصنف بالعربي', 'D': 'Category - القسم',
               'G': 'العدد', 'H': 'الاختيارات', 'I': 'سعر المنيو بعد الخصم',
               'J': 'السعر الاساسى', 'K': 'Image Link - رابط الصورة', 'L': 'Item Price'}
    return {'source_sha256': data['source_sha256'],
            'sheets': {'THE MENU': [headers] + [r['source'] for r in data['inventory']]},
            'source_rows': {'THE MENU': [2] + [r['source_row'] for r in data['inventory']]},
            'merged_cells': {'THE MENU': []}}


def test_all_seventeen_decisions_resolved_prices_preserved_reimport_stable():
    inspection = reviewed()
    pending = reference_menu(inspection)
    assert len(pending['unresolved']) == 17
    approved = reference_menu(inspection, owner_approved=True)
    assert approved['unresolved'] == []
    assert len(approved['inventory']) == 56
    assert [(r['source_row'], r['sale_halalas'], r['original_halalas']) for r in approved['inventory']] == [
        (r['source_row'], r['sale_halalas'], r['original_halalas']) for r in pending['inventory']]
    before = prepare_rows(pending['rows'])
    after = prepare_rows(approved['rows'])
    assert len(after['products']) == 31
    assert {p['id'] for p in before['products']} <= {p['id'] for p in after['products']}
    assert all(p['settings']['enabled'] is False and p['settings']['lifecycle'] == 'draft'
               and p['settings']['source_verified'] is False for p in after['products'])
    assert after == prepare_rows(reference_menu(inspection, owner_approved=True)['rows'])
    assert approved['ordering_enabled'] is False
    inspection['source_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='reviewed source'):
        reference_menu(inspection, owner_approved=True)


def test_three_distinct_choices_are_priced_server_side_no_duplicates_or_unknowns():
    products = prepare_rows(reference_menu(reviewed(), owner_approved=True)['rows'])['products']
    product = next(p for p in products if p['settings']['source_id'].endswith('-choose3'))
    matrix = product['settings']['variant_price_matrix']
    assert len(matrix) == 120
    assert {r['price_halalas'] for r in matrix} == {2000}
    options = deepcopy(matrix[0]['options'])
    total, snapshot = catalog._price_units(product, catalog.OrderItem(
        product_id=product['id'], quantity=1, units=[catalog.Unit(options=options)]))
    assert total == 2000 and len(snapshot[0]['option_snapshot']) == 4
    for invalid in [{**options, 'CHOICE_2': options['CHOICE_1']},
                    {**options, 'CHOICE_1': 'unlisted'},
                    {k: v for k, v in options.items() if k != 'CHOICE_3'}]:
        with pytest.raises(HTTPException):
            catalog._price_units(product, catalog.OrderItem(
                product_id=product['id'], quantity=1, units=[catalog.Unit(options=invalid)]))
        with pytest.raises(HTTPException):
            selected_price(product, invalid)


def test_combo_flavours_khaliya_and_half_compositions():
    approved = reference_menu(reviewed(), owner_approved=True)
    by_row = {r['source_row']: r for r in approved['inventory']}
    for row, half in [(10, 3), (11, 6), (12, 12), (13, 18)]:
        assert f'({half} ورق عنب + {half} ملفوف)' in by_row[row]['size_label']
    assert '(5 كريسبي + 5 ملفوف)' in by_row[41]['size_label']
    products = prepare_rows(approved['rows'])['products']
    mojitos = [p for p in products if p['name'].startswith('موهيتو مع')]
    assert len(mojitos) == 3
    for product in mojitos:
        flavours = next(g for g in product['settings']['option_groups'] if g['code'] == 'FLAVOUR')['values']
        assert len(flavours) == 6 and all(v['name_en'] for v in flavours)
        assert len(product['settings']['variant_price_matrix']) == 6
        assert product['price_halalas'] == 1950
    khaliya = next(p for p in products if p['name'] == 'خلية')
    assert len(khaliya['settings']['variant_price_matrix']) == 4
    assert len([p for p in products if p['name'].startswith('بوكس رمضان')]) == 4


def test_browser_variant_matcher_rejects_repeated_choices():
    import subprocess
    product = next(p for p in prepare_rows(reference_menu(reviewed(), owner_approved=True)['rows'])['products']
                   if p['settings']['source_id'].endswith('-choose3'))
    browser_product = {**product, **product['settings']}
    script = """
const fs=require('fs'),assert=require('assert'),v=require('./public/assets/menu-variants.js');
const p=JSON.parse(fs.readFileSync(0,'utf8'));
for(const row of p.variant_price_matrix) assert.strictEqual(v.selected(p,row.options).price_halalas,2000);
const options={...p.variant_price_matrix[0].options};options.CHOICE_2=options.CHOICE_1;
assert.strictEqual(v.selected(p,options),null);
delete options.CHOICE_3;assert.strictEqual(v.selected(p,options),null);
"""
    subprocess.run(['node', '-e', script], input=json.dumps(browser_product), text=True,
                   cwd=MANIFEST.parents[3], check=True, capture_output=True)


def test_reviewed_choice_set_rejects_unknown_or_unbounded_expansion():
    base = {'product_code': 'TEST', 'name': 'TEST', 'category': 'TEST',
            'price': '20', 'selection_set': 'mahashi_choose_three'}
    with pytest.raises(ValueError, match='Unknown reviewed'):
        prepare_rows([{**base, 'selection_set': 'unreviewed'}])
    with pytest.raises(ValueError, match='variant limit'):
        prepare_rows([{**base, 'size': str(i)} for i in range(9)])
