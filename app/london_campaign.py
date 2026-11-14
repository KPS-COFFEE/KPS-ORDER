"""Owner-approved London combo, installed once after brand seeding.

Uses the canonical catalog and banner system. Does not enable advertising,
change gift eligibility, or reset later owner edits on subsequent boots.
"""
from . import db

DECISION = 'owner-london-combo-20261114'
PRODUCT = 'kps-london-cake-combo'
BANNER = 'kps-london-cake-20261114'


def apply(conn):
    if not conn.execute("SELECT 1 FROM brands WHERE id='kps'").fetchone():
        return
    inserted = conn.execute(
        "INSERT INTO owner_campaign_decisions(decision_id,brand_id) VALUES(%s,'kps') "
        'ON CONFLICT DO NOTHING RETURNING decision_id', (DECISION,)).fetchone()
    if not inserted:
        return
    settings = {
        'enabled': True, 'source_verified': True, 'lifecycle': 'active',
        'image_url': '/assets/london-cake-combo.webp',
        'name_en': 'London Cake with Coffee of the Day', 'category_en': 'New Arrivals',
        'description': 'كيكة لندن مع قهوة اليوم الباردة — 15.5 ريال بدل 20 ريال.',
        'description_en': 'London cake with iced coffee of the day — SAR 15.5 instead of SAR 20.',
        'owner_decision': DECISION, 'option_groups': [],
    }
    conn.execute(
        'INSERT INTO products(id,brand_id,name,category,price_halalas,settings) '
        "VALUES(%s,'kps',%s,%s,1550,%s) ON CONFLICT DO NOTHING",
        (PRODUCT, 'كيكة لندن مع قهوة اليوم', '✨ جديدنا', db.json(settings)))
    conn.execute(
        'INSERT INTO banners(id,brand_id,title,body,button_label,destination,active,sort_order) '
        "VALUES(%s,'kps',%s,%s,%s,%s,true,-10) ON CONFLICT DO NOTHING",
        (BANNER, 'كيكة لندن مع قهوة اليوم', '15.5 ريال بدل 20 ريال', 'اطلب الآن',
         db.json({'kind': 'product', 'id': PRODUCT})))
    db.audit(conn, None, 'catalog.owner_campaign_installed', 'product', PRODUCT,
             {'decision_id': DECISION, 'price_halalas': 1550, 'advertising_policy_unchanged': True,
              'gift_policy_unchanged': True})
