"""Promotional placements share a menu identity without rewriting old sales."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from app import catalog, db


@pytest.mark.postgres
def test_menu_collapses_only_equivalent_placements_and_preserves_sources(client):
    response = client.get('/api/catalog?brand_id=kps&branch_id=kps-branch-2').json()
    products = {p['id']: p for p in response['products']}
    assert len(products) == 31
    for source, target in catalog.KPS_PRODUCT_ALIASES.items():
        assert source not in products and target in products
        assert response['legacy_item_mappings'][source]['product_id'] == target
        assert response['legacy_item_mappings'][source]['options'] == {}
    with db.tx() as conn:
        assert conn.execute("SELECT count(*) n FROM products WHERE brand_id='kps'").fetchone()['n'] == 35
        # A branch stock difference must stay visible and must never be bypassed.
        conn.execute("UPDATE branches SET settings=settings||%s WHERE id='kps-branch-2'",
                     (db.json({'product_availability': {'kps-02': False}}),))
    response = client.get('/api/catalog?brand_id=kps&branch_id=kps-branch-2').json()
    assert 'kps-02' not in response['legacy_item_mappings']
    assert next(p for p in response['products'] if p['id'] == 'kps-02')['available'] is False
    assert next(p for p in response['products'] if p['id'] == 'kps-05')['available'] is True


@pytest.mark.postgres
def test_old_alias_quote_keeps_its_own_server_stock_and_price_checks(database):
    with db.tx() as conn:
        conn.execute("UPDATE branches SET settings=settings||%s WHERE id='kps-branch-2'", (db.json({
            'ordering_verified': True, 'pickup_enabled': True, 'active': True,
            'opening_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
            'payment_methods': ['cash']}),))
        body = catalog.QuoteBody.model_validate({
            'brand_id': 'kps', 'branch_id': 'kps-branch-2', 'service': 'pickup', 'payment_method': 'cash',
            'items': [{'product_id': 'kps-02', 'quantity': 2}],
            'customer': {'name': 'اختبار', 'phone': '0500001234'}})
        quote = catalog.quote_order(conn, body)
        assert quote['items'][0]['product_id'] == 'kps-02'
        assert quote['items'][0]['line_total_halalas'] == 1100
        conn.execute("UPDATE branches SET settings=settings||%s WHERE id='kps-branch-2'",
                     (db.json({'product_availability': {'kps-02': False}}),))
        with pytest.raises(HTTPException) as failure:
            catalog.quote_order(conn, body)
        assert failure.value.detail['code'] == 'PRODUCT_UNAVAILABLE'


def test_aliases_are_explicit_brand_scoped_and_never_merge_changed_options_or_prices():
    assert catalog.canonical_product_id('kps', 'kps-02') == 'kps-05'
    assert catalog.canonical_product_id('enbishan', 'kps-02') == 'kps-02'
    assert catalog.canonical_product_id('kps', 'unknown') == 'unknown'
    original = [dict(id='kps-02', brand_id='kps', name='قهوة اليوم بارد', price_halalas=550,
                     option_groups=[], variants=[], available=True),
                dict(id='kps-05', brand_id='kps', name='قهوة اليوم بارد', price_halalas=550,
                     option_groups=[], variants=[], available=True)]
    for key, value in [('price_halalas', 600), ('available', False),
                       ('source_verified', True), ('option_groups', [{'code': 'SIZE'}])]:
        changed = deepcopy(original)
        changed[0][key] = value
        shown, aliases = catalog.collapse_menu_aliases(changed)
        assert shown == changed and aliases == {}
    before = deepcopy(original)
    shown, aliases = catalog.collapse_menu_aliases(original)
    assert len(shown) == 1 and aliases['kps-02']['product_id'] == 'kps-05'
    assert original == before
