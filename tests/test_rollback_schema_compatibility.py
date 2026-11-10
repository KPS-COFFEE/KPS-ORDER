"""Prior app archives retain new schema without enabling new business behavior."""
import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from app import db, exports
from test_enbishan_dual_brand import dual

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / 'tools/rollback_application_overlay'
spec = importlib.util.spec_from_file_location('app._rollback_schema_exports', OVERLAY/'app/exports.py')
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


def preserved_tables():
    tree = ast.parse((OVERLAY/'app/db.py').read_text())
    tables = ()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == 'PRESERVED_TABLES':
            tables = ast.literal_eval(node.value)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == 'PRESERVED_TABLES':
            assert isinstance(node.op, ast.Add)
            tables += ast.literal_eval(node.value)
    return tables


def overlay_preservation(conn, baseline=None):
    tree = ast.parse((OVERLAY/'app/db.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'preservation_snapshot')
    namespace = {'sql': db.sql, 'PRESERVED_TABLES': preserved_tables()}
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'prior-preservation', 'exec'), namespace)
    return namespace['preservation_snapshot'](conn, baseline)


def test_explicit_overlay_schema_is_complete_and_hashes_are_exact():
    assert compat.TABLES == exports.TABLES
    assert compat.AUTH_TABLES == exports.AUTH_TABLES
    assert set(db.PRESERVED_TABLES) <= set(preserved_tables())
    assert {'staff_brand_memberships', 'driver_brand_dedications'} <= set(preserved_tables())
    manifest = json.loads((OVERLAY/'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        assert hashlib.sha256((OVERLAY/name).read_bytes()).hexdigest() == expected


def test_new_business_tables_have_explicit_brand_scope_and_auth_grants_are_excluded():
    for table in set(exports.TABLES) - set(compat.AUTH_TABLES):
        # No blanket TRUE clause is allowed for a brand business archive.
        clause, params = compat._scoped_sql(table, 'enbishan')
        assert clause != 'TRUE' and params and all(value == 'enbishan' for value in params)
        assert clause.count('%s') == len(params)
    assert {'staff_brand_memberships', 'driver_brand_dedications', 'branch_checkout_sessions',
            'branch_payment_webhooks', 'branch_refund_reconciliation'} <= compat.AUTH_TABLES


def test_unknown_schema_still_rejects_archive():
    class Rows:
        def fetchall(self): return [{'table_name': 'unreviewed_sensitive_table'}]
    class UnknownSchema:
        def execute(self, *args): return Rows()
    with pytest.raises(HTTPException) as rejected:
        compat.build_archive(UnknownSchema(), full=True)
    assert rejected.value.detail['code'] == 'EXPORT_SCHEMA_UNSUPPORTED'


@pytest.mark.postgres
def test_prior_export_preserves_populated_brand_ledgers_and_private_grants(dual):
    _, _, _, _, order = dual
    for brand in ('kps', 'enbishan'):
        customer = 'dual-'+brand+'-customer'
        oid = order(brand)
        with db.tx() as c:
            c.execute("INSERT INTO customer_followups(id,brand_id,customer_id,actor_id,status,note) VALUES(%s,%s,%s,%s,'open','TEST ONLY')",('compat-follow-'+brand,brand,customer,customer))
            c.execute("INSERT INTO customer_marketing_notices(id,brand_id,version,text_ar,text_en,actor_id) VALUES(%s,%s,'compat-v1','اختبار','TEST ONLY',%s)",('compat-notice-'+brand,brand,customer))
            c.execute("INSERT INTO customer_marketing_consents(id,brand_id,customer_id,channel,granted,notice_version) VALUES(%s,%s,%s,'sms',false,'compat-v1')",('compat-consent-'+brand,brand,customer))
            c.execute("INSERT INTO benefit_programs(id,brand_id,actor_id,definition) VALUES(%s,%s,%s,'{}')",('compat-program-'+brand,brand,customer))
            c.execute("INSERT INTO benefit_rules(id,brand_id,code,actor_id,definition) VALUES(%s,%s,'TEST ONLY',%s,'{}')",('compat-rule-'+brand,brand,customer))
            c.execute('INSERT INTO customer_wallets(brand_id,customer_id) VALUES(%s,%s)',(brand,customer))
            c.execute('INSERT INTO benefit_order_states(order_id,brand_id,customer_id,discount_halalas,redeemed_halalas,earn_bps,earn_base_halalas) VALUES(%s,%s,%s,0,0,1000,1000)',(oid,brand,customer))
            c.execute("INSERT INTO customer_wallet_entries(id,brand_id,customer_id,order_id,amount_halalas,kind,source_key) VALUES(%s,%s,%s,%s,100,'earn',%s)",('compat-entry-'+brand,brand,customer,oid,'compat-source-'+brand))
    with db.tx() as c:
        before = overlay_preservation(c)
        assert {'staff_brand_memberships', 'customer_wallet_entries'} <= set(before)
        raw, manifest = compat.build_archive(c, full=True)
        archive, verified = exports.verify_archive(raw, require_full=True)
        assert verified == manifest
        for table in ('customer_followups','customer_marketing_notices','customer_marketing_consents',
                      'benefit_programs','benefit_rules','customer_wallets','benefit_order_states','customer_wallet_entries'):
            actual = c.execute('SELECT * FROM '+table).fetchall()
            assert manifest['counts'][table] == 2
            # Actual encoded values and manifest hashes are verified, including
            # wallet amounts, consent sequence, timestamps and nullable fields.
            assert json.loads(archive.read('data/'+table+'.json')) == json.loads(compat._encode(actual))
        assert manifest['counts']['staff_brand_memberships'] == 4
        assert 'data/staff_brand_memberships.json' in archive.namelist()
        business, scope = compat.build_archive(c, brand_id='enbishan')
        business_archive, _ = exports.verify_archive(business)
        assert not set(scope['table_order']) & compat.AUTH_TABLES
        for table in ('customer_followups','customer_wallets','customer_wallet_entries','benefit_order_states'):
            rows = json.loads(business_archive.read('data/'+table+'.json'))
            assert len(rows) == 1 and rows[0]['brand_id'] == 'enbishan'
        assert overlay_preservation(c, before) == before


@pytest.mark.postgres
@pytest.mark.parametrize('change', ['append', 'update', 'delete', 'replace'])
def test_prior_startup_preserves_original_audit_bytes_and_allows_additions(database, monkeypatch, change):
    spec = importlib.util.spec_from_file_location('app._rollback_audit_db', OVERLAY/'app/db.py')
    prior = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, prior)
    spec.loader.exec_module(prior)
    with db.tx() as conn:
        db.audit(conn, None, 'test.prior.original', 'runtime', 'audit-preservation', {'value': 1})
        original = conn.execute("SELECT id FROM audit_events WHERE action='test.prior.original'").fetchone()['id']
        before = prior.preservation_snapshot(conn)

    def migration(conn, **kwargs):
        db.audit(conn, None, 'test.prior.appended', 'runtime', 'new-evidence', {'value': 2})
        if change == 'update':
            conn.execute("UPDATE audit_events SET details='{}'::jsonb WHERE id=%s", (original,))
        elif change in {'delete', 'replace'}:
            conn.execute('DELETE FROM audit_events WHERE id=%s', (original,))
            if change == 'replace':
                conn.execute("INSERT INTO audit_events(id,action,entity_type,entity_id) VALUES(%s,'test.prior.replaced','runtime','replacement')", (original,))

    monkeypatch.setattr(prior, 'tx', db.tx)
    monkeypatch.setattr(prior, 'apply_migrations', migration)
    if change == 'append':
        prior.migrate()
        with db.tx() as conn:
            assert prior.preservation_snapshot(conn, before) == before
            assert db.preservation_snapshot(conn, before) == before
            proof = conn.execute("SELECT details FROM audit_events WHERE action='release.operational_data_preserved' ORDER BY created_at DESC LIMIT 1").fetchone()['details']
            assert proof['audit_events_appended'] == 1
            assert 'row_ids' not in str(proof) and original not in str(proof)
    else:
        with pytest.raises(RuntimeError, match='operational data changed'):
            prior.migrate()
        with db.tx() as conn:
            assert prior.preservation_snapshot(conn, before) == before
            assert not conn.execute("SELECT 1 FROM audit_events WHERE action='test.prior.appended'").fetchone()
