"""Canonical paid campaign, real PostgreSQL, and preserved owner decisions."""
import pytest
from app import admin, catalog, db, london_campaign

pytestmark = pytest.mark.postgres


def test_combo_is_server_priced_and_campaign_does_not_grant_gifts(database):
    with db.tx() as c:
        product = c.execute('SELECT * FROM products WHERE id=%s', (london_campaign.PRODUCT,)).fetchone()
        assert product['brand_id'] == 'kps'
        assert product['category'] == '✨ جديدنا'
        assert product['price_halalas'] == 1550
        public = catalog.public_product(product)
        assert public['source_verified'] and public['available']
        assert public['name_en'] == 'London Cake with Coffee of the Day'
        unit, _ = catalog._price_units(product, catalog.OrderItem(product_id=product['id'], quantity=1))
        assert unit == 1550
        banner = c.execute('SELECT * FROM banners WHERE id=%s', (london_campaign.BANNER,)).fetchone()
        assert banner['destination'] == {'kind': 'product', 'id': product['id']}
        settings = c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings']
        # Advertising consent gates this campaign independently of gift terms.
        c.execute('UPDATE banners SET active=true WHERE id=%s', (banner['id'],))
        c.execute("UPDATE app_settings SET settings=jsonb_set(settings,'{storefront_advertising}',%s) WHERE brand_id='kps'", (db.json({'version': 1, 'advertising_enabled': False}),))
        assert admin.active_banners(c, 'kps', None) == []
        c.execute("UPDATE app_settings SET settings=jsonb_set(settings,'{storefront_advertising}',%s) WHERE brand_id='kps'", (db.json({'version': 2, 'advertising_enabled': True}),))
        assert [row['id'] for row in admin.active_banners(c, 'kps', None)] == [banner['id']]
        settings = c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings']
        before = settings.copy()
        c.execute('UPDATE products SET price_halalas=1600 WHERE id=%s', (product['id'],))
        c.execute('UPDATE banners SET active=false WHERE id=%s', (banner['id'],))
        london_campaign.apply(c)
        assert c.execute('SELECT price_halalas FROM products WHERE id=%s', (product['id'],)).fetchone()['price_halalas'] == 1600
        assert c.execute('SELECT active FROM banners WHERE id=%s', (banner['id'],)).fetchone()['active'] is False
        assert c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings'] == before
        assert settings['owner_campaign_decisions'][london_campaign.DECISION]['applied'] is True
