"""Populated genuine 4e4d781 startup -> candidate -> repeated startup.

Synthetic local schema only. This is neither a production timing/backup
certificate nor an older-application rollback drill for new financial states.
The mandatory release job supplies an exact git archive, not a runtime clone.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg import sql
import pytest

from app import db, multibrand
from conftest import _isolated_test_url

ROOT = Path(__file__).resolve().parents[1]
BASELINE = json.loads((Path(__file__).with_name('fixtures') / 'startup-baseline-4e4d781.json').read_text())
IDENTITY = 'baseline-source-identity.json'
PENDING = frozenset({
    '123_enbishan_approved_small_plates.sql',
    '120_driver_tracking_history.sql', '121_route_eta.sql', '122_route_corridor.sql',
    '120_order_duplicate_contact_index.sql',
    '114_financial_clarification_deadlines.sql', '114_financial_review_audit_immutability.sql',
    '115_manual_bank_matching.sql', '116_financial_reviewer_fallback.sql',
    '117_order_price_credit.sql', '118_collection_tender_corrections.sql', '119_financial_followup_history.sql',
    '112_enbishan_product_artwork.sql', '113_enbishan_khaliya_offer.sql',
    '111_enbishan_flavours_and_citrus.sql',
    '109_enbishan_family_menu_clarity.sql', '110_enbishan_family_commercial_approval.sql',
    '064_branch_checkout_sessions.sql', '065_branch_payment_webhooks.sql',
    '066_branch_online_refunds.sql', '067_branch_payment_reconciliation.sql',
    '068_branch_payment_renewal.sql', '069_branch_refund_reconciliation.sql',
    '070_branch_payment_modes.sql', '071_late_branch_payment_receipts.sql',
    '079_customer_engagement.sql', '096_campaign_report_closures.sql',
    '097_authentica_customer_challenges.sql',
    '098_complimentary_authorizations.sql', '100_manager_handover_snapshot.sql',
    '101_complimentary_revocations.sql', '098_enbishan_guest_checkout.sql',
    '102_morning_first_operational_day.sql',
    '103_enbishan_pickup_default_paused.sql', '104_prior_deliveries.sql',
    '105_driver_attribution_corrections.sql',
    '106_legacy_delivery_attestations.sql',
    '106_staff_geo_access.sql',
    '107_closed_manager_prior_supplements.sql',
    '108_legacy_invoice_imports.sql',
    '105_collection_payment_pieces.sql', '107_driver_duty_profiles.sql',
    '108_financial_case_clarifications.sql',
})
TABLES = ('brands', 'branches', 'users', 'orders', 'collections', 'refunds',
          'branch_payment_attempts', 'branch_online_receipts', 'otp_challenges', 'shifts')


def assert_baseline_source(path, manifest=BASELINE):
    identity = json.loads((path / IDENTITY).read_text())
    assert identity == {'revision': manifest['revision'], 'tree': manifest['tree']}, 'Wrong baseline source identity'
    # A revision marker alone is not evidence: bind every startup module,
    # normal migration and seed/owner-decision input to the reviewed source.
    actual = {str(file.relative_to(path)): hashlib.sha256(file.read_bytes()).hexdigest()
              for folder in ('app', 'data', 'migrations')
              for file in (path / folder).rglob('*')
              if file.is_file() and '__pycache__' not in file.parts}
    assert actual == manifest['files'], 'Baseline startup source differs from exact reviewed archive'


def test_baseline_source_guard_requires_revision_and_actual_bytes(tmp_path):
    (tmp_path / 'app').mkdir()
    source = tmp_path / 'app/db.py'
    source.write_text('reviewed startup source\n')
    manifest = {'revision': 'a' * 40, 'tree': 'b' * 40,
                'files': {'app/db.py': hashlib.sha256(source.read_bytes()).hexdigest()}}
    identity = tmp_path / IDENTITY
    identity.write_text(json.dumps({'revision': manifest['revision'], 'tree': manifest['tree']}))
    assert_baseline_source(tmp_path, manifest)
    identity.write_text(json.dumps({'revision': 'c' * 40, 'tree': manifest['tree']}))
    with pytest.raises(AssertionError, match='Wrong baseline'):
        assert_baseline_source(tmp_path, manifest)
    identity.write_text(json.dumps({'revision': manifest['revision'], 'tree': manifest['tree']}))
    source.write_text('unreviewed source despite unchanged marker\n')
    with pytest.raises(AssertionError, match='differs'):
        assert_baseline_source(tmp_path, manifest)


def test_upgrade_inventory_retains_all_applied_normal_migrations():
    assert BASELINE['revision'] == '4e4d781b4fa82b7459410694ff5c4ac06842e6e7'
    old = {path.split('/', 1)[1]: digest for path, digest in BASELINE['files'].items()
           if path.startswith('migrations/')}
    current = {file.name: hashlib.sha256(file.read_bytes()).hexdigest()
               for file in (ROOT / 'migrations').glob('*.sql')}
    assert len(old) == 73
    assert current.keys() - old.keys() == PENDING
    assert {name: current[name] for name in old} == old


@pytest.fixture
def populated_baseline(monkeypatch, safe_test_environment):
    source_dir = os.environ.get('PENDING_MIGRATION_BASELINE_DIR')
    if not source_dir:
        pytest.skip('Dedicated upgrade gate must provide the genuine 4e4d781 archive explicitly')
    source = Path(source_dir).resolve()
    assert_baseline_source(source)
    base_url = _isolated_test_url()
    schema = 'kps_test_' + uuid.uuid4().hex
    assert re.fullmatch(r'kps_test_[0-9a-f]{32}', schema)
    with psycopg.connect(base_url, autocommit=True, connect_timeout=10) as control:
        actual_name = control.execute('SELECT current_database()').fetchone()[0]
        assert actual_name.startswith('test_') or actual_name.endswith('_test')
        control.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    parts = urlsplit(base_url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.append(('options', '-csearch_path=' + schema))
    isolated_url = urlunsplit(parts._replace(query=urlencode(query)))
    # No inherited provider/payment/release credentials enter the old process.
    env = {'PATH': os.environ.get('PATH', ''), 'PYTHONPATH': str(source),
           'PYTHONDONTWRITEBYTECODE': '1', 'DATABASE_URL': isolated_url,
           'APP_ENV': 'testing', 'PUBLIC_BASE_URL': 'http://testserver',
           'APP_ALLOWED_HOSTS': 'testserver,localhost,127.0.0.1',
           'OTP_PROVIDER': 'disabled', 'EMAIL_PROVIDER': 'disabled',
           'REPORT_WORKER_ENABLED': 'false', 'EMAIL_WORKER_ENABLED': 'false',
           'RECOVERY_POLL_ENABLED': 'false', 'INITIAL_CATALOG_IMPORT': 'true',
           'RUNTIME_REVISION': BASELINE['revision']}
    try:
        result = subprocess.run([sys.executable, '-c',
            'from app import db; db.migrate(); db.close_pools()'], cwd=source,
            env=env, capture_output=True, text=True, timeout=90)
        # Do not echo a DSN/connection exception into CI output.
        assert result.returncode == 0, 'Genuine baseline startup failed; no candidate upgrade attempted'
        monkeypatch.setenv('DATABASE_URL', isolated_url)
        monkeypatch.setenv('INITIAL_CATALOG_IMPORT', 'false')
        for key in list(os.environ):
            if key.startswith(('KPS_', 'ENBISHAN_')):
                monkeypatch.delenv(key, raising=False)
        with db.tx() as c:
            c.execute("INSERT INTO brands(id,name) VALUES('upgrade-brand','Synthetic upgrade brand')")
            c.execute("INSERT INTO branches(id,brand_id,branch_no,source_id,name,city) VALUES('upgrade-branch','upgrade-brand',1,'synthetic-upgrade','TEST','TEST')")
            c.execute("INSERT INTO users(id,brand_id,role,display_name) VALUES('upgrade-owner','upgrade-brand','owner','TEST owner'),('upgrade-customer','upgrade-brand','customer','TEST customer')")
            for sid, opened, closed, old_day, period in (
                ('upgrade-friday-morning', '2026-10-23T12:27:00+03:00', '2026-10-23T17:51:00+03:00', '2026-10-22', 'morning'),
                ('upgrade-friday-evening', '2026-10-23T16:01:00+03:00', '2026-10-24T05:00:00+03:00', '2026-10-23', 'evening'),
                ('upgrade-saturday-preparation', '2026-10-24T03:45:00+03:00', '2026-10-24T16:00:00+03:00', '2026-10-23', 'morning')):
                c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,closed_at,delivery_operational_date,delivery_window_snapshot,submission) "
                          "VALUES(%s,'manager','upgrade-owner','upgrade-brand','[\"upgrade-branch\"]','closed',%s,%s,%s,%s,%s)",
                          (sid, opened, closed, old_day, db.json({'period': period}), db.json({'expected_halalas': 10700, 'counted_halalas': 10700})))
            c.execute("INSERT INTO otp_challenges(id,brand_id,phone,provider_reference,delivery_channel,expires_at,provider_name) VALUES('upgrade-legacy-otp','upgrade-brand','+966500001999','synthetic-legacy-reference','sms',now()+interval '5 minutes','tawked')")
            for oid, method, subtotal, fee, paid, preparation in (
                ('upgrade-cash', 'cash', 1000, 500, 'partial', 'new'),
                ('upgrade-verified', 'card', 2000, 0, 'paid', 'accepted'),
                ('upgrade-pending', 'card', 1100, 0, 'unpaid', 'accepted'),
                ('upgrade-review', 'card', 1200, 0, 'unpaid', 'cancelled')):
                c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,payment_status,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'upgrade-brand','upgrade-branch','upgrade-customer','car',%s,'not_applicable',%s,%s,%s,%s,%s,%s,'{}')",
                          (oid, preparation, method, paid, subtotal, fee, subtotal + fee,
                           db.json({'items': [], 'synthetic_upgrade': True})))
            c.execute("INSERT INTO collections(id,order_id,collector_id,method,amount_halalas) VALUES('upgrade-collection','upgrade-cash','upgrade-owner','cash',1000)")
            c.execute("INSERT INTO refunds(id,collection_id,order_id,method,amount_halalas,reason,authorized_by) VALUES('upgrade-refund','upgrade-collection','upgrade-cash','cash',200,'Synthetic partial refund','upgrade-owner')")
            for oid, amount, status in (('upgrade-verified', 2000, 'verified'), ('upgrade-pending', 1100, 'pending'), ('upgrade-review', 1200, 'review_required')):
                provider_id = uuid.uuid4()
                c.execute("INSERT INTO branch_payment_attempts(id,order_id,brand_id,branch_id,seller_id,provider,provider_payment_id,mode,amount_halalas,currency,order_revision,accepted_by,status,verified_at) VALUES(%s,%s,'upgrade-brand','upgrade-branch','synthetic-seller','moyasar',%s,'test',%s,'SAR',1,'upgrade-owner',%s,CASE WHEN %s='pending' THEN NULL ELSE now() END)",
                          (oid + '-attempt', oid, provider_id, amount, status, status))
                if status != 'pending':
                    c.execute("INSERT INTO branch_online_receipts(id,attempt_id,order_id,brand_id,branch_id,seller_id,provider,provider_payment_id,mode,amount_halalas,currency,disposition) VALUES(%s,%s,%s,'upgrade-brand','upgrade-branch','synthetic-seller','moyasar',%s,'test',%s,'SAR',%s)",
                              (oid + '-receipt', oid + '-attempt', oid, provider_id, amount,
                               'applied' if status == 'verified' else 'review_required'))
        yield isolated_url
    finally:
        db.close_pools(isolated_url)
        with psycopg.connect(base_url, autocommit=True, connect_timeout=10) as control:
            control.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def original_rows(c, columns=None):
    if columns is None:
        columns = {table: [row['column_name'] for row in c.execute(
            'SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position', (table,))]
                   for table in TABLES}
    rows = {table: c.execute(sql.SQL('SELECT {} FROM {} ORDER BY id').format(
        sql.SQL(',').join(map(sql.Identifier, names)), sql.Identifier(table))).fetchall()
            for table, names in columns.items()}
    return columns, rows


@pytest.mark.postgres
def test_genuine_populated_baseline_upgrade_preserves_history_and_repeated_startup(populated_baseline):
    assert not multibrand.enabled()
    assert os.environ['OTP_PROVIDER'] == os.environ['EMAIL_PROVIDER'] == 'disabled'
    assert not any(key.startswith(('KPS_', 'ENBISHAN_')) for key in os.environ)
    with db.tx() as c:
        before_registry = c.execute('SELECT * FROM schema_migrations ORDER BY version').fetchall()
        assert len(before_registry) == 73
        assert {r['version']: r['sha256'] for r in before_registry} == {
            path.split('/', 1)[1]: digest for path, digest in BASELINE['files'].items()
            if path.startswith('migrations/')}
        columns, before = original_rows(c)
        channel_columns = [row['column_name'] for row in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='identity_channels' ORDER BY ordinal_position")]
        channel_query = sql.SQL('SELECT {} FROM identity_channels ORDER BY brand_id,channel').format(
            sql.SQL(',').join(map(sql.Identifier, channel_columns)))
        before_channels = c.execute(channel_query).fetchall()
        assert c.execute("SELECT sum(amount_halalas) AS n FROM collections").fetchone()['n'] == 1000
        assert c.execute("SELECT sum(amount_halalas) AS n FROM refunds").fetchone()['n'] == 200
        assert c.execute("SELECT sum(amount_halalas) AS n FROM branch_online_receipts WHERE disposition='applied'").fetchone()['n'] == 2000
        assert c.execute("SELECT sum(amount_halalas) AS n FROM branch_online_receipts WHERE disposition='review_required'").fetchone()['n'] == 1200
    for _ in range(2):
        db.migrate()
        with db.tx() as c:
            assert original_rows(c, columns)[1] == before
            days = c.execute("SELECT id,reporting_operational_date::text AS operational_day FROM shifts ORDER BY id").fetchall()
            assert {r['id']: r['operational_day'] for r in days} == {
                'upgrade-friday-morning': '2026-10-23', 'upgrade-friday-evening': '2026-10-23',
                'upgrade-saturday-preparation': '2026-10-24'}
            corrections = c.execute('SELECT shift_id,original_day::text,corrected_day::text FROM operational_day_reclassifications ORDER BY shift_id').fetchall()
            assert corrections == [
                {'shift_id': 'upgrade-friday-morning', 'original_day': '2026-10-22', 'corrected_day': '2026-10-23'},
                {'shift_id': 'upgrade-saturday-preparation', 'original_day': '2026-10-23', 'corrected_day': '2026-10-24'}]
            assert c.execute(channel_query).fetchall() == before_channels
            assert c.execute("SELECT authentica_contract_hash,authentica_code_length FROM otp_challenges WHERE id='upgrade-legacy-otp'").fetchone() == {
                'authentica_contract_hash': None, 'authentica_code_length': None}
            after_registry = c.execute('SELECT * FROM schema_migrations ORDER BY version').fetchall()
            assert [r for r in after_registry if r['version'] not in PENDING] == before_registry
            assert {r['version'] for r in after_registry} == {r['version'] for r in before_registry} | PENDING
            if _ == 0:
                first_registry = after_registry
            else:
                assert after_registry == first_registry  # Repeated startup preserves registration timestamps too.
            assert all(r['sha256'] == hashlib.sha256((ROOT / 'migrations' / r['version']).read_bytes()).hexdigest() for r in after_registry)
            assert c.execute('SELECT count(*) AS n FROM orders WHERE discount_halalas<>0').fetchone()['n'] == 0
            for table in ('branch_checkout_sessions', 'branch_payment_webhooks', 'branch_online_refunds',
                          'branch_refund_reconciliation', 'branch_payment_renewals',
                          'customer_marketing_consents', 'customer_wallets', 'benefit_order_states', 'customer_wallet_entries',
                          'promotion_report_closures', 'complimentary_authorizations', 'complimentary_authorization_revocations', 'driver_support_requests',
                          'prior_delivery_grants', 'prior_delivery_revocations', 'prior_delivery_records',
                          'legacy_delivery_attestations', 'legacy_invoice_imports',
                          'driver_duty_profiles', 'financial_case_clarifications', 'financial_case_clarification_events'):
                assert c.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table))).fetchone()['n'] == 0
            audit = c.execute("SELECT details FROM audit_events WHERE action='release.operational_data_preserved' ORDER BY created_at DESC LIMIT 1").fetchone()
            assert audit['details']['verified'] is True
            if _ == 1:
                assert {'complimentary_authorizations', 'complimentary_authorization_revocations', 'driver_support_requests'} <= audit['details']['checks'].keys()
                assert {'prior_delivery_grants', 'prior_delivery_revocations', 'prior_delivery_records'} <= audit['details']['checks'].keys()
                assert {'legacy_delivery_attestations','legacy_invoice_imports'} <= audit['details']['checks'].keys()
                assert {'driver_duty_profiles','financial_case_clarifications','financial_case_clarification_events'} <= audit['details']['checks'].keys()
    # Newly installed deferred triggers must leave a fresh ordinary cash sale
    # usable. Keep the existing one-collection-per-order/revision/method rule:
    # the retained legacy cash order already has its revision-zero collection.
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES('upgrade-followup-order','upgrade-brand','upgrade-branch','upgrade-customer','pickup','new','not_applicable','cash',100,0,100,%s,'{}')", (db.json({'items': [], 'synthetic_upgrade': True}),))
        c.execute("INSERT INTO collections(id,order_id,collector_id,method,amount_halalas) VALUES('upgrade-followup-collection','upgrade-followup-order','upgrade-owner','cash',100)")
    with db.tx() as c:
        assert c.execute("SELECT total_halalas,discount_halalas FROM orders WHERE id='upgrade-followup-order'").fetchone() == {'total_halalas': 100, 'discount_halalas': 0}
        assert c.execute("SELECT order_id,method,amount_halalas FROM collections WHERE id='upgrade-followup-collection'").fetchone() == {'order_id': 'upgrade-followup-order', 'method': 'cash', 'amount_halalas': 100}
        assert c.execute('SELECT count(*) AS n FROM customer_wallet_entries').fetchone()['n'] == 0
        # Creating the new sale must not mutate any of the genuine baseline's
        # earlier records; compare the same retained columns for each old ID.
        for table, retained in before.items():
            for row in retained:
                statement = sql.SQL('SELECT {} FROM {} WHERE id=%s').format(
                    sql.SQL(',').join(map(sql.Identifier, columns[table])), sql.Identifier(table))
                assert c.execute(statement, (row['id'],)).fetchone() == row
