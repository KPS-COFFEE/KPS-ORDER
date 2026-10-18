"""Explicit operational projections and isolated delegated authority tests."""
import pytest
from app import db, delegated_operations, employee_permissions as policy
from tests.test_core import _make_user
from tests.sms_policy_helpers import sms_login, sms_step_up


def test_safe_grants_are_explicit_and_financial_approvals_remain_closed():
    for role in ('driver','branch_manager','delivery_staff','delivery_manager','admin'):
        assert 'staff_catalog_view' in policy.grantable(role)
        assert 'orders_operational_view' in policy.grantable(role)
        assert not policy.grantable(role) & {'refunds_manage','shifts_approve','accounting_review','drivers_manage','payments_collect'}
        assert 'orders_operational_view' not in policy.effective({'role':role})
    assert 'driver_hours_reports' in policy.eligible('delivery_manager')
    assert 'driver_hours_reports' not in policy.effective({'role':'driver'})
    assert 'driver_hours_reports' in policy.effective({'role':'driver','extra_permissions':['driver_hours_reports']})
    assert 'driver_hours_reports' not in policy.effective({'role':'delivery_manager','permission_denials':['driver_hours_reports']})
    for role in ('customer','owner','unknown'):
        assert not policy.grantable(role)


def test_explicit_projection_scope_never_uses_caller_brand_or_empty_global_scope():
    sql,params=delegated_operations.scope({'role':'driver','brand_id':'kps','branch_ids':[]})
    assert sql=='o.brand_id=%s AND o.branch_id=ANY(%s)' and params==['kps',[]]
    sql,params=delegated_operations.scope({'role':'owner','brand_id':'kps'},'other')
    assert sql=='o.brand_id=%s AND o.branch_id=%s' and params==['kps','other']


@pytest.mark.postgres
def test_operational_grant_cannot_expand_contacts_finances_or_mutations(client,monkeypatch):
    # Router inclusion here keeps this unit independently runnable before integration.
    from app.main import app
    if not any(getattr(route,'path','')=='/api/staff/scoped/orders' for route in app.routes):
        app.include_router(delegated_operations.router)
    driver=_make_user('driver',branches=['kps-branch-1'])
    owner=_make_user('owner')
    customer=_make_user('customer')
    with db.tx() as conn:
        for oid,branch in (('scoped-order-one','kps-branch-1'),('scoped-order-two','kps-branch-2')):
            conn.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps',%s,%s,'pickup','not_required','cash',100,0,100,'{}','{\"phone\":\"secret\"}')",(oid,branch,customer['id']))
        conn.execute("INSERT INTO brands(id,name) VALUES('scoped-other-brand','Synthetic other brand')")
        conn.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES('scoped-other-brand-order','scoped-other-brand','kps-branch-1',%s,'pickup','not_required','cash',100,0,100,'{}','{}')",(customer['id'],))
        conn.execute("INSERT INTO order_events(id,order_id,actor_id,event_type,details) VALUES('scope-event','scoped-order-one',%s,'order.preparation','{\"from\":\"new\",\"to\":\"accepted\",\"previous\":\"new\",\"phone\":\"secret\",\"amount_halalas\":100}')",(owner['id'],))
    sms_login(client,driver,monkeypatch)
    cookies=dict(client.cookies);csrf=client.headers['X-CSRF-Token']
    assert client.get('/api/staff/scoped/orders').status_code==403
    sms_login(client,owner,monkeypatch);sms_step_up(client,owner,monkeypatch)
    def save(target,permissions):
        return client.put('/api/identity/staff/'+driver['id']+'/permissions',json={'enabled_permissions':sorted(permissions),'expected_permissions':sorted(policy.effective(target)),'reason':'Explicit read only branch operational delegation'})
    baseline=policy.effective(driver)
    grant=save(driver,baseline|{'orders_operational_view'})
    assert grant.status_code==200,grant.text
    owner_cookies=dict(client.cookies);owner_csrf=client.headers['X-CSRF-Token']
    client.cookies.clear();client.cookies.update(cookies);client.headers['X-CSRF-Token']=csrf
    result=client.get('/api/staff/scoped/orders')
    assert result.status_code==200,result.text
    assert [o['id'] for o in result.json()['orders']]==['scoped-order-one']
    assert 'customer' not in result.json()['orders'][0] and 'total_halalas' not in result.json()['orders'][0]
    history=client.get('/api/staff/scoped/orders/scoped-order-one/history')
    assert history.status_code==200,history.text
    assert history.json()['events'][0]['transition']=={'from':'new','to':'accepted','previous':'new'}
    assert client.get('/api/staff/scoped/orders/scoped-order-two/history').status_code==404
    assert client.get('/api/staff/scoped/orders/scoped-other-brand-order/history').status_code==404
    assert client.get('/api/staff/scoped/orders?branch_id=kps-branch-2').status_code==404
    assert client.get('/api/orders/scoped-order-one').status_code==404 # old task ownership remains
    client.cookies.clear();client.cookies.update(owner_cookies);client.headers['X-CSRF-Token']=owner_csrf
    assert save(grant.json()['user'],baseline).status_code==200
    client.cookies.clear();client.cookies.update(cookies);client.headers['X-CSRF-Token']=csrf
    assert client.get('/api/staff/scoped/orders/scoped-order-one/history').status_code==403
