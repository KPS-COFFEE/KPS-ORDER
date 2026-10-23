"""Real PostgreSQL negative inserts and valid controls; no provider is contacted."""
from uuid import uuid4

import psycopg
import pytest

from app import db, multibrand
from test_enbishan_dual_brand import dual

pytestmark = pytest.mark.postgres


def order_row(c, brand, *, customer=None, branch=None):
    identifier = 'ownership-order-' + uuid4().hex
    c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,%s,%s,%s,'pickup','new','not_applicable','cash',1000,0,1000,'{}','{}')",
              (identifier, brand, branch or ('kps-branch-1' if brand == 'kps' else 'enbishan-test-1'), customer or 'dual-'+brand+'-customer'))
    return identifier


def attempt_row(c, brand, order, *, branch=None, status='pending'):
    identifier = 'ownership-attempt-' + uuid4().hex
    c.execute("INSERT INTO branch_payment_attempts(id,order_id,brand_id,branch_id,seller_id,provider,provider_payment_id,mode,amount_halalas,currency,order_revision,accepted_by,status) VALUES(%s,%s,%s,%s,'TEST ONLY','moyasar',%s,'test',1000,'SAR',1,'dual-owner',%s)",
              (identifier, order, brand, branch or ('kps-branch-1' if brand == 'kps' else 'enbishan-test-1'), uuid4(), status))
    return identifier


def receipt_row(c, brand, attempt, order, branch):
    identifier = 'ownership-receipt-' + uuid4().hex
    c.execute("INSERT INTO branch_online_receipts(id,attempt_id,order_id,brand_id,branch_id,seller_id,provider,provider_payment_id,mode,amount_halalas,currency,disposition) VALUES(%s,%s,%s,%s,%s,'TEST ONLY','moyasar',%s,'test',1000,'SAR','review_required')",
              (identifier, attempt, order, brand, branch, uuid4()))
    return identifier


def refund_row(c, brand, attempt, order, branch):
    identifier = 'ownership-refund-' + uuid4().hex
    c.execute("INSERT INTO branch_online_refunds(id,attempt_id,order_id,brand_id,branch_id,amount_halalas,previous_refunded_halalas,reason,authorized_by) VALUES(%s,%s,%s,%s,%s,100,0,'SYNTHETIC','dual-owner')",
              (identifier, attempt, order, brand, branch))
    return identifier


@pytest.fixture
def ownership(dual):
    values = {}
    with db.tx() as c:
        for brand in ('kps', 'enbishan'):
            order = order_row(c, brand)
            values[brand] = {'order': order, 'attempt': attempt_row(c, brand, order),
                             'branch': 'kps-branch-1' if brand == 'kps' else 'enbishan-test-1'}
    return values


@pytest.mark.parametrize('brand', ['kps', 'enbishan'])
@pytest.mark.parametrize('foreign_key', ['order', 'branch'])
def test_attempt_brand_must_match_order_and_branch(ownership, brand, foreign_key):
    own = ownership[brand]
    foreign = ownership['enbishan' if brand == 'kps' else 'kps']
    with db.tx() as c:
        fresh = order_row(c, brand)
        fresh_foreign = order_row(c, 'enbishan' if brand == 'kps' else 'kps')
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.tx() as c:
            attempt_row(c, brand, fresh_foreign if foreign_key == 'order' else fresh,
                        branch=foreign['branch'] if foreign_key == 'branch' else own['branch'])
    with db.tx() as c:
        assert attempt_row(c, brand, fresh, branch=own['branch'])


@pytest.mark.parametrize('table', ['branch_checkout_sessions', 'branch_payment_webhooks'])
@pytest.mark.parametrize('brand', ['kps', 'enbishan'])
def test_checkout_and_webhook_must_match_attempt_brand(ownership, table, brand):
    own = ownership[brand]
    foreign = ownership['enbishan' if brand == 'kps' else 'kps']
    if table == 'branch_checkout_sessions':
        statement = 'INSERT INTO branch_checkout_sessions(attempt_id,brand_id) VALUES(%s,%s)'
        wrong = (foreign['attempt'], brand)
        correct = (own['attempt'], brand)
    else:
        statement = "INSERT INTO branch_payment_webhooks(id,attempt_id,brand_id,provider_event_id,event_type) VALUES(%s,%s,%s,'SYNTHETIC','payment_paid')"
        wrong = (uuid4().hex, foreign['attempt'], brand)
        correct = (uuid4().hex, own['attempt'], brand)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.tx() as c:
            c.execute(statement, wrong)
    with db.tx() as c:
        c.execute(statement, correct)


@pytest.mark.parametrize('write', [receipt_row, refund_row], ids=['receipt', 'refund'])
@pytest.mark.parametrize('mismatch', ['brand', 'same_brand_order', 'same_brand_branch'])
def test_receipt_and_refund_match_the_immutable_attempt_tuple(ownership, write, mismatch):
    own = ownership['enbishan']
    foreign = ownership['kps']
    with db.tx() as c:
        other_order = order_row(c, 'enbishan')
    brand, attempt, order, branch = 'enbishan', own['attempt'], own['order'], own['branch']
    if mismatch == 'brand':
        attempt = foreign['attempt']
    elif mismatch == 'same_brand_order':
        order = other_order
    else:
        branch = 'enbishan-test-2'
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.tx() as c:
            write(c, brand, attempt, order, branch)
    with db.tx() as c:
        assert write(c, 'enbishan', own['attempt'], own['order'], own['branch'])


@pytest.mark.parametrize('foreign_parent', ['previous', 'next'])
def test_renewal_parents_must_match_brand_and_same_order(ownership, foreign_parent):
    own = ownership['enbishan']
    with db.tx() as c:
        # Retain the old history; the normal renewal transition frees one slot.
        c.execute("UPDATE branch_payment_attempts SET status='superseded' WHERE id=%s", (own['attempt'],))
        successor = attempt_row(c, 'enbishan', own['order'])
        other_order = order_row(c, 'enbishan')
        other_attempt = attempt_row(c, 'enbishan', other_order)
    statement = "INSERT INTO branch_payment_renewals(previous_attempt_id,next_attempt_id,brand_id,reason,actor_id) VALUES(%s,%s,'enbishan','provider_expired','dual-owner')"
    wrong = (ownership['kps']['attempt'], successor) if foreign_parent == 'previous' else (own['attempt'], ownership['kps']['attempt'])
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.tx() as c:
            c.execute(statement, wrong)
    with pytest.raises(psycopg.errors.RaiseException, match='order ownership'):
        with db.tx() as c:
            c.execute(statement, (own['attempt'], other_attempt))
    with db.tx() as c:
        c.execute(statement, (own['attempt'], successor))


def test_refund_reconciliation_must_match_refund_brand(ownership):
    own = ownership['enbishan']
    with db.tx() as c:
        refund = refund_row(c, 'enbishan', own['attempt'], own['order'], own['branch'])
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.tx() as c:
            c.execute("INSERT INTO branch_refund_reconciliation(refund_id,brand_id) VALUES(%s,'kps')", (refund,))
    with db.tx() as c:
        c.execute("INSERT INTO branch_refund_reconciliation(refund_id,brand_id) VALUES(%s,'enbishan')", (refund,))


@pytest.mark.parametrize('mismatch', ['brand', 'customer'])
def test_benefit_order_ownership_rejects_at_commit_and_accepts_deferred_creation(ownership, mismatch):
    own = ownership['enbishan']
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id) VALUES('ownership-other-customer','SYNTHETIC','customer','enbishan')")
    selected_order = ownership['kps']['order'] if mismatch == 'brand' else own['order']
    customer = 'dual-enbishan-customer' if mismatch == 'brand' else 'ownership-other-customer'
    statement = 'INSERT INTO benefit_order_states(order_id,brand_id,customer_id,discount_halalas,redeemed_halalas,earn_bps,earn_base_halalas) VALUES(%s,%s,%s,0,0,0,1000)'
    # Deferral matters: the invalid row exists within its transaction but cannot
    # commit, matching the canonical state-before-order creation sequence.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.tx() as c:
            c.execute(statement, (selected_order, 'enbishan', customer))
            assert c.execute('SELECT 1 FROM benefit_order_states WHERE order_id=%s', (selected_order,)).fetchone()
    identifier = 'ownership-deferred-' + uuid4().hex
    with db.tx() as c:
        c.execute(statement, (identifier, 'enbishan', 'dual-enbishan-customer'))
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'enbishan','enbishan-test-1','dual-enbishan-customer','pickup','new','not_applicable','cash',1000,0,1000,'{}','{}')", (identifier,))
    with db.tx() as c:
        assert c.execute('SELECT order_id FROM benefit_order_states WHERE order_id=%s', (identifier,)).fetchone()['order_id'] == identifier
        metadata = c.execute("SELECT condeferrable,condeferred,confdeltype FROM pg_constraint WHERE conname='dual_benefit_order_customer_brand' AND conrelid='benefit_order_states'::regclass").fetchone()
        assert metadata == {'condeferrable': True, 'condeferred': True, 'confdeltype': 'a'}


def test_guarded_migration_replay_preserves_all_financial_history(ownership):
    tables = ('orders', 'branch_payment_attempts', 'branch_checkout_sessions', 'branch_payment_webhooks',
              'branch_online_receipts', 'branch_online_refunds', 'branch_payment_renewals',
              'branch_refund_reconciliation', 'benefit_order_states')
    with db.tx() as c:
        before = {table: c.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall() for table in tables}
        multibrand.migrate(c)
        after = {table: c.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall() for table in tables}
        assert after == before
        assert c.execute("SELECT count(*) n FROM schema_migrations WHERE version='003_financial_brand_ownership.sql'").fetchone()['n'] == 1
        constraints = c.execute("SELECT conname,confdeltype,confupdtype,convalidated FROM pg_constraint WHERE connamespace=current_schema()::regnamespace AND conname=ANY(%s)", ([
            'dual_attempt_order_brand', 'dual_attempt_branch_brand', 'dual_checkout_attempt_brand',
            'dual_webhook_attempt_brand', 'dual_receipt_attempt_order_brand', 'dual_refund_attempt_order_brand',
            'dual_renewal_previous_brand', 'dual_renewal_next_brand', 'dual_reconciliation_refund_brand',
            'dual_benefit_order_customer_brand'],)).fetchall()
        assert len(constraints) == 10
        assert all(row['confdeltype'] == row['confupdtype'] == 'a' and row['convalidated'] for row in constraints)


def test_conflicting_legacy_payment_aborts_guarded_migrations_without_repair(database, monkeypatch):
    # The normal schema admits this independently valid but cross-brand row.
    # Enable the guarded migration only after synthetic legacy data exists.
    with db.tx() as c:
        c.execute("INSERT INTO brands(id,name) VALUES('enbishan','SYNTHETIC')")
        c.execute("INSERT INTO users(id,display_name,role,brand_id) VALUES('dual-kps-customer','SYNTHETIC','customer','kps'),('dual-owner','SYNTHETIC','owner','kps')")
        order = order_row(c, 'kps')
        conflict = attempt_row(c, 'enbishan', order, branch='kps-branch-1')
        original = c.execute('SELECT * FROM branch_payment_attempts WHERE id=%s', (conflict,)).fetchone()
    monkeypatch.setenv('ENBISHAN_ISOLATED_ENABLED', 'true')
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.tx() as c:
            multibrand.migrate(c)
    with db.tx() as c:
        assert c.execute('SELECT * FROM branch_payment_attempts WHERE id=%s', (conflict,)).fetchone() == original
        assert c.execute("SELECT count(*) n FROM schema_migrations WHERE version IN ('001_enbishan_isolated.sql','002_customer_brand_ownership.sql','003_financial_brand_ownership.sql')").fetchone()['n'] == 0
        assert c.execute("SELECT count(*) n FROM pg_constraint WHERE conrelid='branch_payment_attempts'::regclass AND conname='dual_attempt_order_brand'").fetchone()['n'] == 0


def test_conflicting_legacy_renewal_aborts_without_relinking_orders(database, monkeypatch):
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id) VALUES('dual-kps-customer','SYNTHETIC','customer','kps'),('dual-owner','SYNTHETIC','owner','kps')")
        previous = attempt_row(c, 'kps', order_row(c, 'kps'))
        following = attempt_row(c, 'kps', order_row(c, 'kps'))
        c.execute("INSERT INTO branch_payment_renewals(previous_attempt_id,next_attempt_id,brand_id,reason,actor_id) VALUES(%s,%s,'kps','provider_expired','dual-owner')", (previous, following))
        original = c.execute('SELECT * FROM branch_payment_renewals WHERE previous_attempt_id=%s', (previous,)).fetchone()
    monkeypatch.setenv('ENBISHAN_ISOLATED_ENABLED', 'true')
    with pytest.raises(psycopg.errors.RaiseException, match='history crosses orders'):
        with db.tx() as c:
            multibrand.migrate(c)
    with db.tx() as c:
        assert c.execute('SELECT * FROM branch_payment_renewals WHERE previous_attempt_id=%s', (previous,)).fetchone() == original
        assert c.execute("SELECT count(*) n FROM schema_migrations WHERE version='003_financial_brand_ownership.sql'").fetchone()['n'] == 0
