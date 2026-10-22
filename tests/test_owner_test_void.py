"""Real PostgreSQL verification: fictitious collection reversal, never cash payout."""
from copy import deepcopy
from datetime import datetime,timedelta
import pytest
from app import db
from app.operations import balance,TestVoid
from pydantic import ValidationError
from test_operations import ops
from test_accounting import accountant


def test_confirmation_and_identity_fields_are_explicit():
    values=dict(order_reference='TEST-0106',expected_version=1,expected_total_halalas=2050,
                reason='TEST morning activation',confirmed_no_cash_received=True)
    assert TestVoid(**values).expected_total_halalas==2050
    for patch in ({'expected_total_halalas':True},{'confirmed_no_cash_received':'true'},{'expected_version':0},{'reason':' '}):
        with pytest.raises(ValidationError):TestVoid(**{**values,**patch})


def seed(ops,amounts=(2050,2400,2500)):
    actor,post,make_order,_=ops
    with db.tx() as c:
        for sid,kind,person,parent in [('test-void-manager','manager','ops-manager',None),('test-void-driver','driver','ops-driver','test-void-manager')]:
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,manager_shift_id,delivery_window_snapshot) VALUES(%s,%s,%s,'ops-brand','[\"ops-branch\"]','open',%s,%s)",(sid,kind,person,parent,db.json({'period':'morning'})))
    result=[]
    for index,amount in enumerate(amounts):
        oid=make_order(total=amount);ref=f'TEST-MORNING-{index}'
        with db.tx() as c:
            c.execute("UPDATE orders SET snapshot=snapshot||%s,payment_status='paid',delivery_status='delivered',completed_at=now() WHERE id=%s",(db.json({'delivery_period':'morning','order_reference':ref}),oid))
            c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,delivered_at) VALUES(%s,%s,'ops-driver','test-void-driver','delivered','ops-manager',now())",('task-'+oid,oid))
            c.execute("INSERT INTO collections(id,order_id,shift_id,collector_id,method,amount_halalas) VALUES(%s,%s,'test-void-driver','ops-driver','cash',%s)",('col-'+oid,oid,amount))
            c.execute("INSERT INTO custody_events(id,shift_id,amount_halalas,kind,reference_id,actor_id,reason) VALUES(%s,'test-void-driver',%s,'collection',%s,'ops-driver','TEST fictitious cash')",('event-'+oid,amount,'col-'+oid))
            row=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        result.append((oid,dict(order_reference=ref,expected_version=row['version'],expected_total_halalas=amount,
            reason='TEST morning activation by owner; no actual cash',confirmed_no_cash_received=True)))
    return result


@pytest.mark.postgres
def test_three_voids_reverse_6950_once_preserve_originals_and_exclude_reports(ops):
    actor,post,_,_=ops;orders=seed(ops);accountant()
    with db.tx() as c:
        before=c.execute('SELECT * FROM collections ORDER BY id').fetchall()
        shift=c.execute("SELECT * FROM shifts WHERE id='test-void-driver'").fetchone()
        assert balance(c,shift)==6950
    for oid,body in orders:
        url=f'/api/orders/{oid}/test-void'
        result=post('owner',url,body,key='test-void-'+oid)
        assert result['no_cash_disbursed'] and result['amount_halalas']==body['expected_total_halalas']
        assert post('owner',url,body,key='test-void-'+oid)==result
        post('owner',url,body,status=409)
    with db.tx() as c:
        assert balance(c,shift)==0
        assert c.execute('SELECT * FROM collections ORDER BY id').fetchall()==before
        assert c.execute("SELECT count(*) n FROM custody_events WHERE kind='refund'").fetchone()['n']==0
        reversals=c.execute("SELECT * FROM refunds WHERE record_kind='test_void'").fetchall()
        assert len(reversals)==3 and sum(r['amount_halalas'] for r in reversals)==6950
        assert all(not r['line_quantities'] for r in reversals)
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='order.test_void'").fetchone()['n']==3
        assert c.execute("SELECT count(*) n FROM driver_tasks WHERE status='delivered'").fetchone()['n']==0
        from app.driver_incentives import source_rows
        user=c.execute("SELECT * FROM users WHERE id='ops-owner'").fetchone()
        assert source_rows(c,user,shift_id=shift['id'])==[]
        from app.accounting import build
        day=datetime.now().date();start=(day-timedelta(days=1)).isoformat();end=day.isoformat()
        daily=build(c,user,start,end)
        assert sum(r['cash'] for r in daily['rows'])==0
        actions=build(c,user,start,end,group='actions')
        assert len(actions['rows'])==3 and all('Test correction' in r['action'] for r in actions['rows'])
    with pytest.raises(Exception):
        with db.tx() as c:c.execute('DELETE FROM refunds WHERE id=%s',(reversals[0]['id'],))


@pytest.mark.postgres
def test_permissions_confirmation_staleness_and_closed_shift_block_without_side_effects(ops):
    actor,post,_,_=ops;[(oid,body)]=seed(ops,(2050,));url=f'/api/orders/{oid}/test-void'
    for who in ('manager','driver','branch','customer'):post(who,url,body,status=403)
    post('other',url,body,status=404)
    post('owner',url,{**body,'confirmed_no_cash_received':False},status=422)
    post('owner',url,{**body,'expected_total_halalas':1},status=409)
    with db.tx() as c:c.execute("UPDATE shifts SET status='submitted' WHERE id='test-void-driver'")
    post('owner',url,body,status=409)
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM refunds").fetchone()['n']==0
        assert c.execute('SELECT payment_status FROM orders WHERE id=%s',(oid,)).fetchone()['payment_status']=='paid'
