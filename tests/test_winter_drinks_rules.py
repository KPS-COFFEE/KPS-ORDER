"""Exercise real catalog policy and price snapshots without a runtime database."""
import pytest
from fastapi import HTTPException
from app import catalog, winter_drinks


def drinks():
    return list(winter_drinks.approved_products(sorted(catalog.DELIVERY_CITIES - winter_drinks.EXCLUDED_CITIES)))


@pytest.mark.parametrize('city,expected', [('حفر الباطن', True), ('القيصومة', True), ('الذيبية', True), ('الخفجي', True), ('النعيرية', True), ('رفحا', False), ('رفحاء', False), ('قبة', False), ('قبه', False)])
def test_city_availability(city, expected):
    branch = {'id': 'synthetic-branch', 'brand_id': 'kps', 'city': city, 'settings': {}}
    for product in drinks():
        assert catalog.source_approved(product)
        assert catalog.product_availability(product, branch, service='delivery')[0] == expected
        assert not catalog.product_availability(product, None)[0]
        assert not catalog.product_availability(product, branch | {'brand_id': 'enbishan'})[0]
        assert not catalog.product_availability(product, branch | {'settings': {'product_availability': {product['id']: False}}})[0]


def test_sahlab_per_cup_choices_same_price_and_saved_preparation_names():
    p, chocolate = drinks()
    item = catalog.OrderItem(product_id=p['id'], quantity=2, units=[{'options': {'NUTS': 'with'}}, {'options': {'NUTS': 'without'}}])
    amount, snapshots = catalog._price_units(p, item)
    assert amount == 1300
    assert [s['price_halalas'] for s in snapshots] == [650, 650]
    assert [s['option_snapshot']['NUTS']['choices'][0]['name'] for s in snapshots] == ['مع مكسرات', 'بدون مكسرات']
    assert catalog._price_units(chocolate, catalog.OrderItem(product_id=chocolate['id'], quantity=1))[0] == 650
    assert catalog.public_product(p)['name_en'] == 'Sahlab'
    assert catalog.public_product(chocolate)['name_en'] == 'Hot Chocolate'


@pytest.mark.parametrize('units', [None, [{'options': {}}], [{'options': {'NUTS': 'unknown'}}], [{'options': {'NUTS': ['with', 'without']}}]])
def test_cannot_order_ambiguous_or_unapproved_nuts(units):
    p = drinks()[0]
    with pytest.raises(HTTPException):
        catalog._price_units(p, catalog.OrderItem(product_id=p['id'], quantity=1, units=units))
