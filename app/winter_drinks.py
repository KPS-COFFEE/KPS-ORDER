"""One-time owner approval for KPS winter drinks, 18 November 2026."""
from . import catalog, db

DECISION = 'owner-winter-drinks-20261118'
PRODUCTS = ('kps-sahlab', 'kps-hot-chocolate')
EXCLUDED_CITIES = {'رفحاء', 'قبه'}


def approved_products(cities):
    nuts = [{'code': 'NUTS', 'name': 'المكسرات', 'name_en': 'Nuts', 'type': 'select',
             'required': True, 'min_select': 1, 'max_select': 1, 'values': [
                 {'code': 'with', 'name': 'مع مكسرات', 'name_en': 'With nuts', 'price_delta_halalas': 0},
                 {'code': 'without', 'name': 'بدون مكسرات', 'name_en': 'Without nuts', 'price_delta_halalas': 0}]}]
    for product_id, name, name_en, options, image in [
        (PRODUCTS[0], 'السحلب', 'Sahlab', nuts, 'sahlab-owner-20261118.webp'),
        (PRODUCTS[1], 'الهوت شوكلت', 'Hot Chocolate', [], 'hot-chocolate-owner-20261118.webp'),
    ]:
        settings = {'enabled': True, 'source_verified': True, 'lifecycle': 'active',
                    'name_en': name_en, 'category_en': 'Beverages',
                    'image_url': '/assets/' + image, 'option_groups': options,
                    'available_cities': cities, 'owner_decision': DECISION}
        yield {'id': product_id, 'brand_id': 'kps', 'name': name, 'category': '☕ المشروبات',
               'price_halalas': 650, 'settings': settings}


def apply(conn):
    row = conn.execute("SELECT settings FROM app_settings WHERE brand_id='kps' FOR UPDATE").fetchone()
    if not row:
        return
    configuration = row['settings']
    decisions = configuration.get('owner_campaign_decisions') or {}
    if decisions.get(DECISION):
        return
    cities = set(catalog.DELIVERY_CITIES)
    cities.update(r['city'] for r in conn.execute("SELECT DISTINCT city FROM branches WHERE brand_id='kps'").fetchall())
    cities = sorted({catalog.normalize_city(c) for c in cities if c} - EXCLUDED_CITIES)
    for product in approved_products(cities):
        product_id = product['id']
        # Fail closed on an unexpected identity collision instead of replacing a product.
        if conn.execute('SELECT 1 FROM products WHERE id=%s', (product_id,)).fetchone():
            raise RuntimeError('Winter drink identity already exists without this owner decision.')
        conn.execute('INSERT INTO products(id,brand_id,name,category,price_halalas,settings) '
                     "VALUES(%s,'kps',%s,%s,650,%s)",
                     (product_id, product['name'], product['category'], db.json(product['settings'])))
    conn.execute("UPDATE app_settings SET settings=%s,updated_at=now() WHERE brand_id='kps'",
                 (db.json(configuration | {'owner_campaign_decisions': decisions | {
                     DECISION: {'product_ids': list(PRODUCTS), 'applied': True}}}),))
    db.audit(conn, None, 'catalog.owner_winter_drinks_installed', 'brand', 'kps',
             {'decision_id': DECISION, 'product_ids': list(PRODUCTS), 'price_halalas': 650,
              'excluded_cities': sorted(EXCLUDED_CITIES)})
