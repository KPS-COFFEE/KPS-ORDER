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
    return TestClient(app, base_url='http://testserver', headers={'Origin':'http://testserver'})


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


def seed_order(c, customer, *, area='District', amount=1900, refund=0, status='delivered', selected='kps-branch-2', sandbox=False, created_at=None, test_data=False):
    oid, collection = db.new_id('o_'), db.new_id('collection_')
    c.execute("""INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,
      delivery_status,payment_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,
      snapshot,customer,completed_at,created_at) VALUES(%s,'kps','kps-branch-1',%s,'delivery','handed_over',%s,
      'paid','cash',%s,0,%s,%s,%s,now(),COALESCE(%s::timestamptz,now()))""", (oid, customer['id'], status, amount, amount,
      db.json({'internal_routing': {'selected_branch': {'branch_id': selected}}, 'test_data':test_data, **({'branch_payment_mode': 'test'} if sandbox else {})}),
      db.json({'name': 'Historical name', 'city': 'City', 'neighborhood': area, 'location': {'lat':28.4,'lng':45.9}}),created_at))
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


@pytest.mark.postgres
def test_certified_period_excludes_retained_history_without_mutating_orders(database):
    owner = _make_user('owner')
    live, registered_only, historical, alias = [_make_user() for _ in range(4)]
    with db.tx() as c:
        for person in (live, registered_only, historical):
            c.execute('UPDATE users SET phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s', (person['id'],))
        old = seed_order(c, live, created_at='2026-10-16T23:59:59Z')
        current = seed_order(c, live, created_at='2026-10-17T00:00:00Z',refund=300)
        test = seed_order(c, live, created_at='2026-10-17T01:00:00Z',test_data=True)
        retained = seed_order(c, historical, created_at='2026-10-17T01:00:00Z')
        linked = seed_order(c, alias, created_at='2026-10-17T01:00:00Z')
        c.execute('INSERT INTO customer_identity_links(source_id,customer_id,brand_id) VALUES(%s,%s,%s)', (alias['id'],live['id'],'kps'))
        before=c.execute('SELECT id,snapshot,customer,total_halalas FROM orders WHERE id=ANY(%s) ORDER BY id',([old,current,test,retained,linked],)).fetchall()
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'", (db.json({'operational_period_started_at':'2026-10-17T00:00:00Z','historical_test_customer_ids':[historical['id'],alias['id']]}),))
    with app_client() as client:
        _authenticate(client, owner)
        overview=client.get('/api/crm/overview')
        assert overview.status_code==200,overview.text
        data=overview.json()
        assert data['operating_period']['configured'] is True
        assert data['operating_period']['period_valid'] is True
        assert data['totals']['registered_customers']==2
        assert data['totals']['purchasing_customers']==1
        assert data['totals']['qualifying_orders']==1
        assert data['totals']['net_spending_halalas']==1600
        assert client.get('/api/crm/customers/'+historical['id']).status_code==404
        profiles=client.get('/api/crm/customers').json()['customers']
        assert {p['id'] for p in profiles}=={live['id'],registered_only['id']}
        stats=next(p for p in profiles if p['id']==live['id'])
        assert stats['first_order_at']==stats['latest_order_at']
        assert stats['first_order_at'].startswith('2026-10-17T00:00:00')
    with db.tx() as c:
        after=c.execute('SELECT id,snapshot,customer,total_halalas FROM orders WHERE id=ANY(%s) ORDER BY id',([old,current,test,retained,linked],)).fetchall()
        assert after==before


@pytest.mark.postgres
def test_invalid_period_is_visible_and_fails_closed_instead_of_reusing_history(database):
    owner, person=_make_user('owner'),_make_user()
    with db.tx() as c:
        c.execute('UPDATE users SET phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s',(person['id'],))
        seed_order(c,person)
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'",(db.json({'operational_period_started_at':'invalid-period'}),))
    with app_client() as client:
        _authenticate(client,owner)
        response=client.get('/api/crm/overview')
        assert response.status_code==200,response.text
        data=response.json()
        assert data['operating_period']['period_valid'] is False
        assert data['totals']['qualifying_orders']==0
        assert data['totals']['net_spending_halalas']==0


@pytest.mark.parametrize('value', ['=SUM(1,1)', ' +cmd', '-1+2', '@formula', '\t=1'])
def test_csv_export_neutralizes_formulas(value):
    assert customer_crm.csv_cell(value).startswith("'")


def test_filter_values_cannot_change_sql():
    predicate, values = customer_crm.customer_filters(q="%' OR TRUE --", segment='all',
        branch_id='other', service='', city='', neighborhood='', registered_from=None, registered_to=None)
    assert "OR TRUE --" not in predicate
    assert values[0] == "%' OR TRUE --"
    assert values[1].startswith('%!%')


@pytest.mark.postgres
def test_customer_search_segments_history_and_scoped_export(database, monkeypatch):
    owner = _make_user('owner')
    purchaser = _make_user()
    nonbuyer = _make_user()
    with db.tx() as c:
        for person in (purchaser, nonbuyer):
            c.execute('UPDATE users SET phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s', (person['id'],))
        c.execute('UPDATE users SET display_name=%s WHERE id=%s', ('=SYNTHETIC', purchaser['id']))
        completed = seed_order(c, purchaser, refund=300)
        cancelled = seed_order(c, purchaser, status='cancelled')
    with app_client() as client:
        _authenticate(client, owner)
        data = client.get('/api/crm/customers?q=SYNTHETIC&segment=new').json()
        assert [row['id'] for row in data['customers']] == [purchaser['id']]
        assert data['customers'][0]['total_orders'] == 2
        assert data['customers'][0]['net_spending_halalas'] == 1600
        assert [row['id'] for row in client.get('/api/crm/customers?segment=nonbuyer').json()['customers']] == [nonbuyer['id']]
        assert client.get('/api/crm/customers?branch_id=not-this-brand').json()['customers'] == []
        assert client.get('/api/crm/customers?registered_from=2026-10-20&registered_to=2026-10-01').status_code == 422
        detail = client.get('/api/crm/customers/'+purchaser['id']).json()
        assert {row['id'] for row in detail['orders']} == {completed, cancelled}
        assert client.post('/api/crm/export?q=SYNTHETIC', headers={'Origin': 'http://testserver'}).status_code == 403
        from app import employee_sms
        from tests.sms_policy_helpers import sms_step_up
        client.app.include_router(employee_sms.router)
        sms_step_up(client, owner, monkeypatch)
        result = client.post('/api/crm/export?q=SYNTHETIC', headers={'Origin': 'http://testserver'})
        assert result.status_code == 200, result.text
        assert "'=SYNTHETIC" in result.text
        assert nonbuyer['id'] not in result.text
        assert result.headers['cache-control'] == 'no-store'
        with db.tx() as c:
            assert c.execute("SELECT 1 FROM audit_events WHERE action='crm.export' AND actor_id=%s", (owner['id'],)).fetchone()
