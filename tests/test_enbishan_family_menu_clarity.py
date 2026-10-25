import json
from pathlib import Path
from app import db, menu_variants

ROOT = Path(__file__).resolve().parents[1]

def test_family_option_preserves_existing_sizes_and_prices(database):
    product = json.loads((ROOT/'tests/fixtures/enbishan-mixed-before-family.json').read_text())
    settings = {key: product[key] for key in ('option_groups', 'variant_price_matrix', 'variant_images', 'image_url')}
    sql = (ROOT/'migrations/109_enbishan_family_menu_clarity.sql').read_text()
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES ('enbishan','Synthetic') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES (%s,'enbishan',%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET settings=EXCLUDED.settings", (product['id'],product['name'],product['category'],product['price_halalas'],db.json(settings)))
        conn.execute(sql)
        updated = conn.execute('SELECT * FROM products WHERE id=%s',(product['id'],)).fetchone()
        values = next(g['values'] for g in updated['settings']['option_groups'] if g['code']=='SIZE')
        assert len(values)==4
        assert values[:3]==settings['option_groups'][0]['values']
        family = values[-1]
        assert '١٨ ملفوف + ١٨ ورق عنب' in family['name']
        matrix = updated['settings']['variant_price_matrix']
        assert matrix[:len(settings['variant_price_matrix'])]==settings['variant_price_matrix']
        added = [r for r in matrix if r['options']['SIZE']==family['code']]
        assert len(added)==4
        assert all(r['price_halalas']==4550 and r['compare_at_halalas']==6200 for r in added)
        assert updated['price_halalas']==1200
        menu_variants.validated(updated)
        conn.execute(sql)
        assert conn.execute('SELECT settings FROM products WHERE id=%s',(product['id'],)).fetchone()['settings']==updated['settings']
