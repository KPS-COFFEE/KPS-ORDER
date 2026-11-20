import json
from pathlib import Path
from app import db, enbishan_menu_assets
ROOT=Path(__file__).resolve().parents[1]

def test_small_artwork_replaces_only_reviewed_images_and_preserves_commercial_values(database):
    products=json.loads((ROOT/'tests/fixtures/enbishan-catalog-presentation.json').read_text())
    sql=(ROOT/'migrations/123_enbishan_approved_small_plates.sql').read_text()
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES ('enbishan','Synthetic') ON CONFLICT DO NOTHING")
        for p in products:
            settings={k:p[k] for k in ['image_url','variant_images','option_groups'] if k in p}
            conn.execute("INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES (%s,'enbishan',%s,%s,1200,%s)", (p['id'],p['name'],p['category'],db.json(settings)))
        before=conn.execute("SELECT id,name,category,price_halalas,settings FROM products WHERE brand_id='enbishan' ORDER BY id").fetchall()
        conn.execute(sql)
        after=conn.execute("SELECT id,name,category,price_halalas,settings FROM products WHERE brand_id='enbishan' ORDER BY id").fetchall()
        changed=[]
        for a,b in zip(before,after):
            assert (a['id'],a['name'],a['category'],a['price_halalas'])==(b['id'],b['name'],b['category'],b['price_halalas'])
            assert a['settings']['option_groups']==b['settings']['option_groups']
            assert [v['options'] for v in a['settings']['variant_images']]==[v['options'] for v in b['settings']['variant_images']]
            if a['settings']!=b['settings']:changed.append(b['name'])
        assert set(changed)=={'بطاطس','ملفوف','بصل','ورق عنب','ورق عنب وملفوف'}
        conn.execute(sql)
        assert conn.execute("SELECT id,name,category,price_halalas,settings FROM products WHERE brand_id='enbishan' ORDER BY id").fetchall()==after
    for key in ['potato','cabbage','onion','grape','mixed']:
        assert enbishan_menu_assets.valid_bundled('/assets/enbishan-menu/owner-20261026-plates/'+key+'.webp')
