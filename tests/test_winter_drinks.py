import pytest
from fastapi import HTTPException
from app import catalog, db, winter_drinks

pytestmark = pytest.mark.postgres


def test_owner_drinks_price_scope_and_each_unit_nuts(database):
    with db.tx() as conn:
        products = [conn.execute('SELECT * FROM products WHERE id=%s', (p,)).fetchone() for p in winter_drinks.PRODUCTS]
        branches = conn.execute("SELECT * FROM branches WHERE brand_id='kps'").fetchall()
        assert branches
        for p in products:
            assert p['price_halalas'] == 650 and catalog.source_approved(p)
            assert not catalog.product_availability(p, None)[0]
            for b in branches:
                expected = catalog.normalize_city(b['city']) not in {'رفحاء', 'قبه'}
                assert catalog.product_availability(p, b, service='delivery')[0] == expected
            for city in ['رفحا', 'رفحاء', 'قبة', 'قبه']:
                assert not catalog.product_availability(p, dict(branches[0], city=city))[0]
            assert not catalog.product_availability(p, dict(branches[0], brand_id='enbishan'))[0]
        sahlab, chocolate = products
        units = [{'options': {'NUTS': 'with'}}, {'options': {'NUTS': 'without'}}]
        item = catalog.OrderItem(product_id=sahlab['id'], quantity=2, units=units)
        total, snapshots = catalog._price_units(sahlab, item)
        assert total == 1300
        assert [s['option_snapshot']['NUTS']['choices'][0]['name'] for s in snapshots] == ['مع مكسرات', 'بدون مكسرات']
        for units in [None, [{'options': {}}], [{'options': {'NUTS': 'unknown'}}], [{'options': {'NUTS': ['with', 'without']}}]]:
            with pytest.raises(HTTPException):
                catalog._price_units(sahlab, catalog.OrderItem(product_id=sahlab['id'], quantity=1, units=units))
        assert catalog._price_units(chocolate, catalog.OrderItem(product_id=chocolate['id'], quantity=1))[0] == 650
        with pytest.raises(HTTPException):
            catalog._price_units(chocolate, catalog.OrderItem(product_id=chocolate['id'], quantity=1, units=[{'options': {'NUTS': 'with'}}]))
        # Reboots must preserve later owner edits and never duplicate products.
        conn.execute('UPDATE products SET price_halalas=700 WHERE id=%s', (sahlab['id'],))
        winter_drinks.apply(conn)
        assert conn.execute('SELECT price_halalas FROM products WHERE id=%s', (sahlab['id'],)).fetchone()['price_halalas'] == 700
        assert conn.execute('SELECT count(*) n FROM products WHERE id=ANY(%s)', (list(winter_drinks.PRODUCTS),)).fetchone()['n'] == 2
        assert conn.execute("SELECT count(*) n FROM audit_events WHERE action='catalog.owner_winter_drinks_installed'").fetchone()['n'] == 1
