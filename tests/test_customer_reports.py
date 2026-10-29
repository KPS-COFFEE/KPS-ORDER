"""Customer cohort arithmetic, retention, identity and financial boundaries."""
from datetime import date
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import auth, customer_reports, db
from tests.test_core import _authenticate, _make_user
from tests.test_customer_crm import seed_order


def app_client(user=None):
    app = FastAPI()
    app.include_router(customer_reports.router)
    if user is not None:
        app.dependency_overrides[auth.current_user] = lambda: user
    return TestClient(app, base_url='http://testserver', headers={'Origin':'http://testserver'})


def person():
    user = _make_user()
    with db.tx() as c:
        c.execute('UPDATE users SET phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s', (user['id'],))
    return user


def report(client, query=''):
    result = client.get('/api/reports/customers?start=2026-01-10&end=2026-01-20'+query)
    assert result.status_code == 200, result.text
    assert result.headers['cache-control'] == 'no-store'
    return result.json()


@pytest.mark.parametrize('role', ['customer','driver','delivery_manager','branch_manager','admin','delivery_staff'])
def test_owner_only_before_database_access(role, monkeypatch):
    def forbidden():
        raise AssertionError('Unauthorized role reached report database')
    monkeypatch.setattr(db, 'tx', forbidden)
    with app_client({'role':role}) as client:
        assert client.get('/api/reports/customers').status_code == 403
        assert client.get('/customer-report').status_code == 403


@pytest.mark.postgres
def test_daily_and_period_new_repeat_with_midnight_refunds_and_rankings(database):
    owner = _make_user('owner')
    old, new = person(), person()
    with db.tx() as c:
        seed_order(c, old, created_at='2026-01-09T20:59:59Z')  # Jan 9 Riyadh
        seed_order(c, old, created_at='2026-01-09T21:00:00Z', amount=1000)
        seed_order(c, old, created_at='2026-01-10T02:00:00Z', amount=1000, refund=200)
        seed_order(c, new, created_at='2026-01-10T11:00:00Z', amount=7000)
        seed_order(c, new, created_at='2026-01-10T12:00:00Z', amount=7000, refund=7000)
        seed_order(c, new, created_at='2026-01-11T11:00:00Z', status='cancelled')
        seed_order(c, old, created_at='2026-01-20T21:00:00Z', amount=99000)  # Jan 21, outside period
    with app_client() as client:
        _authenticate(client, owner)
        data = report(client)
        assert data['summary']['orders'] == 3
        assert data['summary']['value_halalas'] == 8800
        assert data['summary']['purchasing_customers'] == 2
        assert data['summary']['new_customers'] == 1
        assert data['summary']['repeat_customers'] == 1
        assert [r['id'] for r in data['customers']] == [old['id'],new['id']]
        assert data['customers'][0]['days_absent'] == 10
        assert data['daily'][0] == dict(day='2026-01-10',purchasing_customers=2,new_customers=1,
                                      repeat_customers=1,orders=3,value_halalas=8800,repeat_orders=2)
        assert len(data['daily']) == 11
        assert all(d['orders']==0 for d in data['daily'][1:])
        assert sum(d['value_halalas'] for d in data['daily']) == data['summary']['value_halalas']
        assert [r['id'] for r in report(client,'&sort=value')['customers']] == [new['id'],old['id']]
        # Moving the selection does not relabel the old customer's first purchase.
        later = client.get('/api/reports/customers?start=2026-01-10&end=2026-01-10&segment=new').json()
        assert [r['id'] for r in later['customers']] == [new['id']]


@pytest.mark.postgres
def test_same_day_repeat_orders_do_not_duplicate_new_customers(database):
    owner, user = _make_user('owner'), person()
    with db.tx() as c:
        seed_order(c,user,created_at='2026-01-10T10:00:00Z')
        seed_order(c,user,created_at='2026-01-10T11:00:00Z')
        seed_order(c,user,created_at='2026-01-11T11:00:00Z')
    with app_client() as client:
        _authenticate(client,owner)
        data = report(client)
        assert data['summary']['new_customers']==1 and data['summary']['repeat_customers']==0
        assert data['daily'][0]['new_customers']==1 and data['daily'][0]['repeat_customers']==0
        assert data['daily'][0]['repeat_orders']==1
        assert data['daily'][1]['new_customers']==0 and data['daily'][1]['repeat_customers']==1
        assert data['summary']['purchasing_customers']==1


@pytest.mark.postgres
def test_retention_requires_history_uses_distinct_days_and_period_end(database):
    owner = _make_user('owner')
    regular, late, lapsed, sparse, single, irregular = [person() for _ in range(6)]
    with db.tx() as c:
        for user, days in ((regular,[10,12,14,16,18]), (late,[5,7,9,11,13]),
                           (lapsed,[1,2,3,4,5]), (sparse,[1,2,3,4]),
                           (single,[1]), (irregular,[1,2,3,15,16])):
            for day in days:
                seed_order(c,user,created_at=f'2026-01-{day:02d}T12:00:00Z')
        # Four orders on the same day do not invent a regular cadence.
        for _ in range(4):seed_order(c,sparse,created_at='2026-01-04T13:00:00Z')
        seed_order(c,lapsed,created_at='2026-01-25T12:00:00Z')  # future to historical report
    with app_client() as client:
        _authenticate(client,owner)
        data = report(client)
        rows = {r['id']:r for r in data['customers']}
        for user, expected in ((regular,'regular'),(late,'late'),(lapsed,'lapsed'),
                               (sparse,'insufficient'),(single,'no_repeat'),(irregular,'irregular')):
            assert rows[user['id']]['activity']==expected
        assert rows[lapsed['id']]['days_absent']==15
        assert rows[lapsed['id']]['orders']==0
        filtered = report(client,'&activity=lapsed')
        assert [r['id'] for r in filtered['customers']]==[lapsed['id']]
        assert filtered['summary']['inactive_customers']==1
        assert filtered['summary']['orders']==0


@pytest.mark.postgres
def test_identity_links_tests_cutoff_and_brand_isolation(database):
    owner, user, guest = _make_user('owner'), person(), _make_user()
    with db.tx() as c:
        c.execute("INSERT INTO brands(id,name) VALUES('test-customer-reports','Other')")
        c.execute('UPDATE users SET phone=%s WHERE id=%s',(user['phone'],guest['id']))
        seed_order(c,user,created_at='2026-01-01T12:00:00Z')
        seed_order(c,user,created_at='2026-01-10T12:00:00Z',amount=1200)
        seed_order(c,user,created_at='2026-01-11T12:00:00Z',test_data=True)
        seed_order(c,user,created_at='2026-01-11T12:00:00Z',sandbox=True)
        seed_order(c,guest,created_at='2026-01-11T12:00:00Z',amount=2000)
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'",(db.json({'operational_period_started_at':'2026-01-10T00:00:00Z'}),))
    with app_client() as client:
        _authenticate(client,owner)
        before=report(client)
        assert before['summary']['orders']==1 and before['summary']['value_halalas']==1200
        with db.tx() as c:
            c.execute('INSERT INTO customer_identity_links(source_id,customer_id,brand_id) VALUES(%s,%s,%s)',(guest['id'],user['id'],'kps'))
        assert report(client)['summary']['value_halalas']==3200
        with db.tx() as c:
            c.execute("UPDATE users SET brand_id='test-customer-reports' WHERE id=%s",(owner['id'],))
        assert report(client)['summary']['orders']==0
        with db.tx() as c:c.execute('UPDATE users SET active=false WHERE id=%s',(owner['id'],))
        assert client.get('/api/reports/customers').status_code==401


@pytest.mark.postgres
def test_pagination_ties_literal_search_and_invalid_range(database):
    owner = _make_user('owner')
    people = [person() for _ in range(3)]
    with db.tx() as c:
        for user in people:seed_order(c,user,created_at='2026-01-10T12:00:00Z')
    with app_client() as client:
        _authenticate(client,owner)
        first=report(client,'&limit=2')
        second=report(client,'&limit=2&offset=2')
        assert first['next_offset']==2 and second['next_offset'] is None
        assert [r['rank'] for r in second['customers']]==[3]
        assert {r['id'] for r in first['customers']}.isdisjoint({r['id'] for r in second['customers']})
        assert report(client,'&q=%25')['customers']==[]
        assert report(client,'&q=%27%20OR%20TRUE--')['customers']==[]
        for query in ('start=2026-01-20&end=2026-01-10','start=2024-01-01&end=2026-01-10',
                      'start=9999-01-01&end=9999-12-31','sort=orders;DROP TABLE users','limit=101'):
            assert client.get('/api/reports/customers?'+query).status_code==422


@pytest.mark.postgres
def test_invalid_operating_period_is_visible_without_fallback_history(database):
    owner, user = _make_user('owner'), person()
    with db.tx() as c:
        seed_order(c,user,created_at='2026-01-10T12:00:00Z')
        c.execute("UPDATE brands SET settings=settings||'{\"operational_period_started_at\":\"bad\"}' WHERE id='kps'")
    with app_client() as client:
        _authenticate(client,owner)
        data=report(client)
        assert data['operating_period']['period_valid'] is False
        assert data['summary']['orders']==0 and data['customers']==[]


def test_filter_escapes_and_policy_do_not_authorize_marketing():
    f=customer_reports.report_filters(start=date(2026,1,10),end=date(2026,1,20),q="%' OR TRUE--",segment='all',activity='all',sort='orders',offset=0,limit=50)
    values=customer_reports.parameters({'brand_id':'kps'},f)
    assert values[7].startswith('%!%')
    assert 'No messages' in customer_reports.definitions()['campaigns']


@pytest.mark.postgres
def test_projected_owner_needs_all_brand_branches_for_page_and_cohorts(database):
    owner,user=_make_user('owner'),person()
    with db.tx() as c:
        seed_order(c,user,created_at='2026-01-10T12:00:00Z')
        branches=[r['id'] for r in c.execute("SELECT id FROM branches WHERE brand_id='kps' ORDER BY id").fetchall()]
    projected=dict(owner,home_brand_id='another-home-brand',branch_ids=branches[:1])
    with app_client(projected) as client:
        for path in ('/customer-report','/api/reports/customers?start=2026-01-10&end=2026-01-20'):
            denied=client.get(path)
            assert denied.status_code==403,denied.text
            assert denied.json()['detail']['code']=='CUSTOMER_REPORT_BRAND_SCOPE_REQUIRED'
            assert user['id'] not in denied.text and user['phone'] not in denied.text
    with app_client(dict(projected,branch_ids=branches)) as client:
        assert client.get('/customer-report').status_code==200
        assert report(client)['summary']['orders']==1
        # Future branch additions do not silently widen a previous explicit grant.
        with db.tx() as c:
            c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city) VALUES('report-new-branch',999,'report-new-branch','kps','Synthetic future branch','حفر الباطن')")
        denied=client.get('/api/reports/customers?start=2026-01-10&end=2026-01-20')
        assert denied.status_code==403,denied.text
