"""Ended duty responsibility survives future branch grants, never new work."""
import pytest
from fastapi import HTTPException
from app import auth, db, operations as ops
from test_enbishan_dual_brand import dual
from test_central_workdays import central
from test_aggregate_driver_closure import submit_physical


@pytest.mark.postgres
def test_ended_ledgers_keep_self_and_linked_manager_access_after_grant_removal(central):
    _,actor,post,_,make_order=central
    manager,driver,path,declared=submit_physical(central)
    post('manager',path+'/physical-receipt',{'declaration_id':declared['declaration_id'],
         'received_halalas':700,'confirmed':True,'reference':'Actual ended duty receipt'})
    order_id=make_order('kps')
    with db.tx() as c:
        before=c.execute('SELECT id,brand_id,branch_ids,manager_shift_id,submission FROM shifts WHERE user_id=%s ORDER BY id',('dual-driver',)).fetchall()
        c.execute("UPDATE users SET branch_ids='[]' WHERE id IN ('dual-driver','dual-manager')")
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id IN ('dual-driver','dual-manager')")
    for name in ('driver','manager'):
        listing=actor(name).get('/api/shifts/current')
        assert listing.status_code==200,listing.text
        assert driver['shift']['id'] in {s['id'] for s in listing.json()['shifts']}
        detail=actor(name).get(path)
        assert detail.status_code==200,detail.text
        physical=actor(name).get(path+'/physical-closure')
        assert physical.status_code==200,physical.text
        if name=='driver':
            assert 'expected_cash_halalas' not in physical.json()
            assert 'expected_halalas' not in detail.json()
        assert actor(name).get('/api/orders/'+order_id).status_code in (403,404)
    with db.tx() as c:
        shift=c.execute('SELECT * FROM shifts WHERE id=%s',(driver['shift']['id'],)).fetchone()
        principal=c.execute("SELECT * FROM users WHERE id='dual-driver'").fetchone()
        with pytest.raises(HTTPException):
            ops.shift_access(c,principal,shift)
        assert not auth.can_access_order(principal,c.execute('SELECT * FROM orders WHERE id=%s',(order_id,)).fetchone(),c)
        after=c.execute('SELECT id,brand_id,branch_ids,manager_shift_id,submission FROM shifts WHERE user_id=%s ORDER BY id',('dual-driver',)).fetchall()
        assert after==before


@pytest.mark.postgres
def test_historical_access_does_not_cover_live_duty_or_unrelated_or_inactive_actor(central):
    _,_,post,_,_=central
    _,driver,path,declared=submit_physical(central)
    with db.tx() as c:
        shift=c.execute('SELECT * FROM shifts WHERE id=%s',(driver['shift']['id'],)).fetchone()
        user=c.execute("SELECT * FROM users WHERE id='dual-driver'").fetchone()
        assert ops.historical_closure_principal(c,user,shift) is None
    post('manager',path+'/physical-receipt',{'declaration_id':declared['declaration_id'],
         'received_halalas':700,'confirmed':True,'reference':'Canonical end receipt'})
    with db.tx() as c:
        shift=c.execute('SELECT * FROM shifts WHERE id=%s',(shift['id'],)).fetchone()
        assert ops.historical_closure_principal(c,user,shift)
        unrelated=c.execute("SELECT * FROM users WHERE id='dual-accountant'").fetchone()
        assert ops.historical_closure_principal(c,unrelated,shift) is None
        c.execute("UPDATE users SET active=false WHERE id='dual-driver'")
        assert ops.historical_closure_principal(c,user,shift) is None
