"""Database-level witnesses: a caller cannot create cross-brand ownership."""
from uuid import uuid4

import psycopg
import pytest

from app import db, multibrand
from test_enbishan_dual_brand import dual


@pytest.mark.postgres
@pytest.mark.parametrize('customer', ['dual-kps-customer', 'dual-enbishan-customer'])
def test_address_insert_and_update_require_matching_customer_brand(dual, customer):
    brand = 'kps' if customer == 'dual-kps-customer' else 'enbishan'
    foreign = 'enbishan' if brand == 'kps' else 'kps'
    identifier = uuid4().hex
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.tx() as c:
            c.execute("INSERT INTO customer_addresses(id,customer_id,brand_id,label,address) VALUES(%s,%s,%s,'TEST','TEST')", (identifier,customer,foreign))
    with db.tx() as c:
        c.execute("INSERT INTO customer_addresses(id,customer_id,brand_id,label,address) VALUES(%s,%s,%s,'TEST','TEST')", (identifier,customer,brand))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.tx() as c:
            c.execute('UPDATE customer_addresses SET brand_id=%s WHERE id=%s',(foreign,identifier))


@pytest.mark.postgres
@pytest.mark.parametrize('brand', ['kps', 'enbishan'])
def test_guest_alias_cannot_link_across_brands(dual, brand):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.tx() as c:
            c.execute('INSERT INTO customer_identity_links(source_id,customer_id,brand_id) VALUES(%s,%s,%s)',
                      ('dual-kps-customer','dual-enbishan-customer',brand))


@pytest.mark.postgres
def test_order_customer_cannot_belong_to_other_brand(dual):
    _, _, _, _, order = dual
    identifier = order('enbishan')
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.tx() as c:
            c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) SELECT %s,brand_id,branch_id,'dual-kps-customer',service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer FROM orders WHERE id=%s", (uuid4().hex,identifier))


@pytest.mark.postgres
def test_same_brand_alias_and_repeated_migration_preserve_history(dual):
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id) VALUES('synthetic-guest','TEST','customer','enbishan')")
        c.execute("INSERT INTO customer_identity_links(source_id,customer_id,brand_id) VALUES('synthetic-guest','dual-enbishan-customer','enbishan')")
        before = c.execute('SELECT * FROM customer_identity_links ORDER BY source_id').fetchall()
        multibrand.migrate(c)
        assert c.execute('SELECT * FROM customer_identity_links ORDER BY source_id').fetchall() == before
        assert c.execute("SELECT count(*) n FROM schema_migrations WHERE version='002_customer_brand_ownership.sql'").fetchone()['n'] == 1


@pytest.mark.postgres
def test_same_verified_phone_keeps_addresses_wallets_and_accounts_separate(dual):
    _, actor, _, _, _ = dual
    for brand, balance in [('kps', 1200), ('enbishan', 3400)]:
        actor(brand+'-customer')
        with db.tx() as c:
            c.execute("UPDATE users SET phone='+966500009999',phone_sms_verified_at=now() WHERE id=%s", ('dual-'+brand+'-customer',))
            c.execute('UPDATE sessions SET customer_verified_at=now() WHERE user_id=%s', ('dual-'+brand+'-customer',))
            c.execute('INSERT INTO customer_wallets(brand_id,customer_id,balance_halalas) VALUES(%s,%s,%s)', (brand,'dual-'+brand+'-customer',balance))
            c.execute("INSERT INTO customer_addresses(id,customer_id,brand_id,label,address) VALUES(%s,%s,%s,'TEST','TEST ONLY')", ('scope-'+brand,'dual-'+brand+'-customer',brand))
    for brand, balance in [('kps', 1200), ('enbishan', 3400)]:
        client = actor(brand+'-customer')
        profile = client.get('/api/customer/profile')
        assert profile.status_code == 200, profile.text
        assert profile.json()['user']['id'] == 'dual-'+brand+'-customer'
        assert [row['id'] for row in profile.json()['addresses']] == ['scope-'+brand]
        wallet = client.get('/api/customer/benefits')
        assert wallet.status_code == 200, wallet.text
        assert wallet.json()['brand_id'] == brand
        assert wallet.json()['balance_halalas'] == balance
        other = 'enbishan' if brand == 'kps' else 'kps'
        assert client.delete('/api/customer/addresses/scope-'+other).status_code == 404
        assert client.patch('/api/customer/addresses/scope-'+other, json={'label':'ATTEMPT','address':'ATTEMPT'},
                            headers={'Idempotency-Key':uuid4().hex}).status_code == 404
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM customer_identity_links').fetchone()['n'] == 0
        assert c.execute('SELECT count(*) n FROM customer_addresses').fetchone()['n'] == 2


@pytest.mark.postgres
def test_restore_manifest_matches_all_applied_multibrand_migrations(dual):
    from app.exports import migration_manifest
    with db.tx() as c:
        applied = c.execute('SELECT version,sha256 FROM schema_migrations ORDER BY version').fetchall()
    assert migration_manifest() == applied
    assert any(row['version'] == '002_customer_brand_ownership.sql' for row in applied)
