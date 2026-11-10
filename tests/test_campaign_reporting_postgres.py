"""Real scoped order history, never production databases or messaging."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import auth, campaign_reporting as reports, db
from test_operations import ops

pytestmark=pytest.mark.postgres


@pytest.fixture
def campaign_data(ops):
    actor,order,_,_=ops
    ids={}
    # Different legacy formatting and identities must map to one canonical phone.
    records=[
      ('older','ops-branch','ops-customer','2026-10-20T18:00:00+00:00','0512345678',{},False),
      ('repeat','ops-branch','ops-customer','2026-10-21T21:00:00+00:00','\u200f+966 51 234 5678\u200e',{},False),
      ('first','ops-branch','ops-customer','2026-10-22T09:00:00+00:00','۰۵۱۱۱۱۱۱۱۱',{},False),
      ('cancelled','ops-branch','ops-customer','2026-10-22T10:00:00+00:00','966522222222',{},False),
      ('staff','ops-branch','ops-customer','2026-10-22T10:30:00+00:00','+966533333333',{'order_source':'delivery_center'},False),
      ('test','ops-branch','ops-customer','2026-10-22T11:00:00+00:00','+966544444444',{},True),
      ('midnight-end','ops-branch','ops-customer','2026-10-22T21:00:00+00:00','+966555555555',{},False),
      ('other','ops-branch-other','ops-customer','2026-10-22T12:00:00+00:00','+966566666666',{},False),
    ]
    with db.tx() as c:
      for name,branch,customer,created,phone,snapshot,test_void in records:
        identifier='campaign-'+name;ids[name]=identifier
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer,created_at,test_void) VALUES(%s,%s,%s,%s,'delivery',%s,'pending','cash',1000,0,1000,%s,%s,%s,%s)",
          (identifier,'ops-other' if name=='other' else 'ops-brand',branch,customer,'cancelled' if name=='cancelled' else 'new',db.json(snapshot),db.json({'phone':phone}),created,db.json({'test':True}) if test_void else None))
      for name,phone,version in [('repeat','+966512345678',1),('first','+966511111111',2),('cancelled','+966522222222',1),('other','+966566666666',1)]:
        c.execute('INSERT INTO first_order_gift_claims(brand_id,phone,order_id,customer_id,policy_version) VALUES(%s,%s,%s,%s,%s)',
          ('ops-other' if name=='other' else 'ops-brand',phone,ids[name],'ops-customer',version))
    app=FastAPI();app.include_router(reports.router)
    def client(who='owner',**changes):
      with db.tx() as c:u=c.execute('SELECT * FROM users WHERE id=%s',('ops-'+who,)).fetchone()
      u.update(changes);app.dependency_overrides[auth.current_user]=lambda:u
      return TestClient(app)
    return client,ids


def test_online_totals_riyadh_boundaries_normalization_and_cancelled_claims(campaign_data):
    client,ids=campaign_data
    response=client().get('/api/reports/promotions/daily?start=2026-10-22&end=2026-10-22')
    assert response.status_code==200,response.text
    data=response.json();s=data['summary']
    assert s=={'online_orders':3,'non_cancelled_orders':2,'cancelled_orders':1,'completed_orders':0,
               'unique_customers':2,'first_time_customers':1,'repeat_customers':1,
               'gift_claims':3,'non_cancelled_gift_orders':2,'cancelled_gift_orders':1}
    assert len(data['daily'])==1 and data['daily'][0]['report_date']=='2026-10-22'
    assert response.headers['cache-control']=='private, no-store'
    assert '512345678' not in response.text and '511111111' not in response.text
    scoped=client().get('/api/reports/promotions/daily?start=2026-10-22&end=2026-10-22&policy_version=1').json()['summary']
    assert scoped['online_orders']==3 and scoped['gift_claims']==2
    with db.tx() as c:
      assert c.execute("SELECT count(*) n FROM first_order_gift_claims").fetchone()['n']==4
      assert c.execute("SELECT count(*) n FROM audit_events WHERE action='campaign.report.read'").fetchone()['n']==2


def test_beneficiaries_mask_phones_and_filter_policy_version(campaign_data):
    client,ids=campaign_data
    response=client().get('/api/reports/promotions/beneficiaries?start=2026-10-22&end=2026-10-22&policy_version=1&limit=1')
    assert response.status_code==200,response.text
    data=response.json();assert data['beneficiaries'][0]['order_id']==ids['cancelled']
    assert data['beneficiaries'][0]['phone_suffix']=='••••2222'
    assert '+966' not in response.text and 'customer_id' not in response.text
    page2=client().get('/api/reports/promotions/beneficiaries?start=2026-10-22&end=2026-10-22&policy_version=1&limit=1&offset=1').json()
    assert page2['beneficiaries'][0]['order_id']==ids['repeat']
    assert page2['beneficiaries'][0]['first_online_order'] is False


def test_brand_branch_and_permission_isolation(campaign_data):
    client,ids=campaign_data
    base='/api/reports/promotions/daily?start=2026-10-22&end=2026-10-22'
    assert client().get(base+'&branch_id=ops-branch-other').status_code==404
    other=client('other').get(base).json()
    assert other['brand_id']=='ops-other' and other['summary']['online_orders']==1
    assert other['summary']['gift_claims']==1
    for who in ['driver','customer','branch']:
      assert client(who).get(base).status_code==403
    assert client('manager',branch_ids=[]).get(base).json()['summary']['online_orders']==0
    assert client('manager',permission_denials=['reports_view']).get(base).status_code==403
    assert client('manager',_morning_manager_context={'shift_id':'morning'}).get(base).status_code==403
    assert client('manager').get(base).json()['summary']['online_orders']==3
