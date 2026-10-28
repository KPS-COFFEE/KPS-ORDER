import json
from pathlib import Path
from app import db, menu_variants
from app.catalog import source_approved
ROOT=Path(__file__).resolve().parents[1]

def test_owner_flavour_pairs_preserve_prices_and_approval(database):
    fixtures=json.loads((ROOT/'tests/fixtures/enbishan-flavour-decision.json').read_text())
    sql=(ROOT/'migrations/111_enbishan_flavours_and_citrus.sql').read_text()
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('enbishan','Synthetic') ON CONFLICT DO NOTHING")
        for f in fixtures:
            p=f['before'];settings={k:p[k] for k in ('source_id','option_groups','variants','variant_price_matrix')}
            settings.update(source_verified=True,owner_approved_digest=db.digest(p))
            conn.execute('INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET settings=EXCLUDED.settings',tuple(p[k] for k in ('id','brand_id','name','category','price_halalas'))+(db.json(settings),))
        conn.execute(sql)
        for f in fixtures:
            p=conn.execute('SELECT * FROM products WHERE id=%s',(f['before']['id'],)).fetchone()
            assert source_approved(p)
            assert p['settings']['variant_price_matrix']==f['after']['variant_price_matrix']
            values=next(g['values'] for g in p['settings']['option_groups'] if g['code']=='FLAVOUR')
            assert len(values)==4
            assert len([v for v in values if ' + ' in v['name']])==3
            for row in menu_variants.validated(p):
                assert menu_variants.selected_price(p,row.options)['price_halalas']==row.price_halalas
            if p['name']=='ورق عنب وملفوف':
                family=[r for r in p['settings']['variant_price_matrix'] if r['options']['SIZE']=='v-f79d0c0c352f2bb2fc40e13a']
                assert len(family)==4 and all(r['price_halalas']==4550 and r['compare_at_halalas']==6200 for r in family)
        drink=conn.execute("SELECT * FROM products WHERE id='enbishan-citrus-20261026'").fetchone()
        assert drink['price_halalas']==250 and source_approved(drink)
        assert menu_variants.selected_price(drink,{})['price_halalas']==250
        before=conn.execute("SELECT id,settings FROM products WHERE brand_id='enbishan' ORDER BY id").fetchall()
        conn.execute(sql)
        assert conn.execute("SELECT id,settings FROM products WHERE brand_id='enbishan' ORDER BY id").fetchall()==before


def test_khaliya_offer_keeps_approval_and_all_flavour_prices(database):
    f=json.loads((ROOT/'tests/fixtures/enbishan-khaliya-offer.json').read_text())
    p=f['before'];settings={k:p[k] for k in ('source_id','option_groups','variants','variant_price_matrix')}
    settings.update(source_verified=True,owner_approved_digest=db.digest(p))
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('enbishan','Synthetic') ON CONFLICT DO NOTHING")
        conn.execute('INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET settings=EXCLUDED.settings',tuple(p[k] for k in ('id','brand_id','name','category','price_halalas'))+(db.json(settings),))
        sql=(ROOT/'migrations/113_enbishan_khaliya_offer.sql').read_text()
        conn.execute(sql)
        updated=conn.execute('SELECT * FROM products WHERE id=%s',(p['id'],)).fetchone()
        assert source_approved(updated)
        assert updated['settings']['variant_price_matrix']==f['after']['variant_price_matrix']
        for row in menu_variants.validated(updated):
            assert row.price_halalas==1950
            assert menu_variants.selected_price(updated,row.options)['price_halalas']==1950
        conn.execute(sql)
        assert conn.execute('SELECT * FROM products WHERE id=%s',(p['id'],)).fetchone()==updated


def test_single_or_pair_migration_keeps_prices_approval_and_later_edits(database):
    from copy import deepcopy
    fixtures=json.loads((ROOT/'tests/fixtures/enbishan-flavour-decision.json').read_text())
    migration=(ROOT/'migrations/125_enbishan_single_or_two_flavours.sql').read_text()
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('enbishan','Synthetic') ON CONFLICT DO NOTHING")
        for f in fixtures:
            p=f['after'];settings={k:p[k] for k in ('source_id','option_groups','variants','variant_price_matrix')}
            settings.update(source_verified=True,owner_approved_digest=db.digest(p),variant_images=[{'options':r['options'],'image_url':'/assets/current-approved.webp'} for r in p['variant_price_matrix']])
            conn.execute('INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET settings=EXCLUDED.settings',tuple(p[k] for k in ('id','brand_id','name','category','price_halalas'))+(db.json(settings),))
        conn.execute(migration)
        for f in fixtures:
            updated=conn.execute('SELECT * FROM products WHERE id=%s',(f['after']['id'],)).fetchone()
            assert source_approved(updated)
            group=next(g for g in updated['settings']['option_groups'] if g['code']=='FLAVOUR')
            assert group['name']=='نكهات ورق العنب' and len(group['values'])==7
            singles=[v for v in group['values'] if v['code'].startswith('single-')]
            assert len(singles)==3
            for row in menu_variants.validated(updated):
                old=next(r for r in f['after']['variant_price_matrix'] if r['options']['SIZE']==row.options['SIZE'])
                assert row.price_halalas==old['price_halalas']
                assert row.compare_at_halalas==old.get('compare_at_halalas')
                assert menu_variants.selected_price(updated,row.options)['price_halalas']==row.price_halalas
            assert all(i['image_url']=='/assets/current-approved.webp' for i in updated['settings']['variant_images'])
            assert len(updated['settings']['variant_images'])==28
        before=conn.execute("SELECT id,settings FROM products WHERE brand_id='enbishan' ORDER BY id").fetchall()
        conn.execute(migration)
        assert conn.execute("SELECT id,settings FROM products WHERE brand_id='enbishan' ORDER BY id").fetchall()==before
        # A later approved commercial edit must never be overwritten on rerun.
        changed=deepcopy(fixtures[0]['after']);changed['price_halalas']=1201
        settings={k:changed[k] for k in ('source_id','option_groups','variants','variant_price_matrix')}
        settings.update(source_verified=True,owner_approved_digest=db.digest(changed))
        conn.execute('UPDATE products SET price_halalas=1201,settings=%s WHERE id=%s',(db.json(settings),changed['id']))
        conn.execute(migration)
        assert conn.execute('SELECT settings FROM products WHERE id=%s',(changed['id'],)).fetchone()['settings']==settings
