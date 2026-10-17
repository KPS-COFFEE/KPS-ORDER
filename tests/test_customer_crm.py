"""CRM isolation, verified ownership, refund accounting and area privacy."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import auth, customer_crm, db
from tests.test_core import _authenticate, _make_user


def app_client(user=None):
    app = FastAPI()
    app.include_router(customer_crm.router)
    if user is not None:
        app.dependency_overrides[auth.current_user] = lambda: user
    return TestClient(app, base_url='http://testserver')


@pytest.mark.parametrize('role', ['customer', 'driver', 'delivery_manager', 'branch_manager', 'delivery_staff'])
def test_every_crm_route_rejects_non_owner_before_reading_data(role, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Unauthorized role must not reach the database')
    monkeypatch.setattr(db, 'tx', forbidden)
    with app_client({'role': role}) as client:
        for url in ['/api/crm/overview', '/api/crm/customers', '/api/crm/customers/known', '/customer-crm']:
            assert client.get(url).status_code == 403


@pytest.mark.postgres
def test_real_auth_revocation_and_cross_brand_customer_scope(database):
    owner = _make_user('owner')
    with db.tx() as c:
        c.execute("INSERT INTO brands(id,name) VALUES('test-crm-other','TEST')")
    other = _make_user('customer', brand='test-crm-other')
    with db.tx() as c:
        c.execute('UPDATE users SET phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s', (other['id'],))
    with app_client() as client:
        token, _ = _authenticate(client, owner)
        assert client.get('/api/crm/customers/'+other['id']).status_code == 404
        result = client.get('/api/crm/customers')
        assert result.status_code == 200, result.text
        assert result.headers['cache-control'] == 'no-store'
        assert other['id'] not in {x['id'] for x in result.json()['customers']}
        with db.tx() as c:
            c.execute('UPDATE users SET active=false WHERE id=%s', (owner['id'],))
        assert client.get('/api/crm/overview').status_code == 401


def seed_order(c, customer, *, area='District', amount=1900, refund=0, status='delivered', selected='kps-branch-2', sandbox=False):
    oid, collection = db.new_id('o_'), db.new_id('collection_')
    c.execute("""INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,
      delivery_status,payment_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,
      snapshot,customer,completed_at) VALUES(%s,'kps','kps-branch-1',%s,'delivery','handed_over',%s,
      'paid','cash',%s,0,%s,%s,%s,now())""", (oid, customer['id'], status, amount, amount,
      db.json({'internal_routing': {'selected_branch': {'branch_id': selected}}, **({'branch_payment_mode': 'test'} if sandbox else {})}),
      db.json({'name': 'Historical name', 'city': 'City', 'neighborhood': area, 'location': {'lat':28.4,'lng':45.9}})))
    c.execute("INSERT INTO collections(id,order_id,collector_id,method,amount_halalas) VALUES(%s,%s,%s,'cash',%s)",
              (collection, oid, customer['id'], amount))
    if refund:
        c.execute("INSERT INTO refunds(id,collection_id,order_id,method,amount_halalas,reason,authorized_by) VALUES(%s,%s,%s,'cash',%s,'TEST',%s)",
                  (db.new_id('refund_'), collection, oid, refund, customer['id']))
    return oid


@pytest.mark.postgres
def test_verified_ids_net_refunds_branches_areas_and_no_raw_pins(database):
    owner = _make_user('owner')
    people = [_make_user() for _ in range(6)]
    guest = _make_user()
    # A matching typed number is intentionally not a canonical ownership link.
    with db.tx() as c:
        for p in people:
            c.execute('UPDATE users SET phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s', (p['id'],))
            seed_order(c, p, refund=300)
        seed_order(c, people[0], area='Small', refund=0)
        seed_order(c, people[0], refund=1900)  # full refund is excluded
        seed_order(c, people[0], status='cancelled')
        seed_order(c, people[0], status='rejected')
        seed_order(c, people[0], sandbox=True)  # a sandbox receipt can never create actual spending
        c.execute('UPDATE users SET phone=%s WHERE id=%s', (people[0]['phone'], guest['id']))
        seed_order(c, guest, amount=9999)
        c.execute('UPDATE users SET display_name=%s WHERE id=%s', ('Current name', people[0]['id']))
    with app_client() as client:
        _authenticate(client, owner)
        result = client.get('/api/crm/overview')
        assert result.status_code == 200, result.text
        data = result.json()
        assert data['totals']['customers'] == 6
        assert data['totals']['qualifying_orders'] == 7
        assert data['totals']['net_spending_halalas'] == 6*1600+1900
        assert data['totals']['repeat_customers'] == 1
        assert len(data['areas']) == 1 and data['areas'][0]['neighborhood'] == 'District'
        assert data['areas'][0]['most_requested_branch_id'] == 'kps-branch-2'
        assert data['branches'][0]['branch_id'] == 'kps-branch-1'
        assert '28.4' not in result.text and '45.9' not in result.text
        detail = client.get('/api/crm/customers/'+people[0]['id']).json()
        assert detail['customer']['display_name'] == 'Current name'
        assert sum(r['qualifying_orders'] for r in detail['branch_channels']) == 2
        assert client.get('/api/crm/customers/'+guest['id']).status_code == 404
        page = client.get('/api/crm/customers?limit=2').json()
        page2 = client.get('/api/crm/customers?limit=2&after='+page['next_after']).json()
        assert not ({p['id'] for p in page['customers']} & {p['id'] for p in page2['customers']})

        with db.tx() as c:
            c.execute('INSERT INTO customer_identity_links(source_id,customer_id,brand_id) VALUES(%s,%s,%s)',
                      (guest['id'], people[0]['id'], 'kps'))
        # Only a separately secured canonical account claim may attribute the guest history.
        claimed = client.get('/api/crm/customers/'+people[0]['id']).json()
        assert sum(r['qualifying_orders'] for r in claimed['branch_channels']) == 3
        assert sum(r['net_spending_halalas'] for r in claimed['branch_channels']) == 1600+1900+9999
