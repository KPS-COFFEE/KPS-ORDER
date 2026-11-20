"""Selected branch remains the customer contract during morning execution routing."""
from datetime import datetime, timezone
from uuid import uuid4
import pytest
from app import auth, db, service_hours
from test_operations import ops
from test_core import _branch
from test_morning_delivery import at

pytestmark=pytest.mark.postgres


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_customer_quote_order_chat_keep_selected_branch_and_commercial_terms(ops,monkeypatch):
    actor,post,_,_=ops
    monkeypatch.setattr(service_hours,'now_riyadh',lambda:at('08:00:00'))
    selected='kps-branch-1';execution='kps-branch-2'
    with db.tx() as c:
        c.execute('UPDATE branches SET settings=settings||%s WHERE id=%s',
                  (db.json(_branch()['settings']|{'minimum_order_halalas':2000,'location_verified':True}),selected))
        c.execute('UPDATE branches SET settings=settings||%s WHERE id=%s',
                  (db.json(_branch()['settings']|{'minimum_order_halalas':9000,'delivery_fee_halalas':1500,'location_verified':True,
                    'coverage':{'type':'radius','center':{'lat':29,'lng':46},'radius_meters':100}}),execution))
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'",(db.json({'morning_delivery':{
            'enabled':True,'version':1,'execution_branch_ids':[execution],
            'preparer_user_ids':['ops-branch'],'manager_user_ids':['ops-manager']}}),))
        c.execute("UPDATE users SET branch_ids=%s WHERE id IN ('ops-manager','ops-branch')",(db.json([execution]),))
        c.execute("INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES('morning-paid','kps','اختبار صباحي','اختبار',3000,%s)",
                  (db.json({'source_verified':True,'enabled':True,'option_groups':[]}),))
        c.execute("UPDATE users SET phone='+966512345678',phone_verified_at=now(),phone_sms_verified_at=now() WHERE id='ops-customer'")
        user=c.execute("SELECT * FROM users WHERE id='ops-customer'").fetchone()
    user.update(customer_verified_at=datetime.now(timezone.utc),customer_phone_required=True)
    def customer():
        client=actor('customer');client.app.dependency_overrides[auth.current_user]=lambda:user
        return client
    body={'brand_id':'kps','branch_id':selected,'service':'delivery','payment_method':'cash',
          'items':[{'product_id':'morning-paid','quantity':1}],
          'customer':{'name':'اختبار','phone':'512345678','neighborhood':'حي اختبار','address':'عنوان اختبار',
                      'location':{'lat':28.4,'lng':45.9}}}
    quote=customer().post('/api/quote',json=body)
    assert quote.status_code==200,quote.text
    q=quote.json()['quote']
    assert q['branch_id']==selected and q['delivery_fee_halalas']==500 and q['total_halalas']==3500
    assert 'internal_routing' not in q and 'morning_policy_version' not in q
    absent=customer().post('/api/orders',json=body,headers={'Idempotency-Key':uuid4().hex})
    assert absent.status_code==409 and absent.json()['detail']['code']=='MORNING_DUTY_UNAVAILABLE'
    post('manager','/api/shifts',{'kind':'manager','branch_ids':[execution],'delivery_period':'morning'})
    created=customer().post('/api/orders',json=body,headers={'Idempotency-Key':uuid4().hex})
    assert created.status_code==200,created.text
    order=created.json()['order'];oid=order['id']
    assert order['branch_id']==selected and order['snapshot']['branch_id']==selected
    with db.tx() as c:
        stored=c.execute('SELECT branch_id,snapshot FROM orders WHERE id=%s',(oid,)).fetchone()
        assert stored['branch_id']==execution
        assert stored['snapshot']['delivery_period']=='morning'
    chat=customer().post('/api/conversations',json={'order_id':oid,'category':'help','details':'اختبار متابعة'},headers={'Idempotency-Key':uuid4().hex})
    assert chat.status_code==200,chat.text
    assert chat.json()['conversation']['branch_id']==selected
    inbox=actor('manager').get('/api/conversations').json()['conversations']
    assert any(row['order_id']==oid and row['branch_id']==execution for row in inbox)
    assert actor('other').get('/api/orders/'+oid).status_code==404
