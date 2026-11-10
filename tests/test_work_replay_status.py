"""Opaque receipt recovery stays scoped to the current employee and brand."""
import pytest
from app import db
from test_enbishan_dual_brand import dual

@pytest.mark.postgres
def test_receipt_status_never_returns_payload_or_another_actor_brand(dual):
    _,actor,_,_,_=dual
    key='SYNTHETIC-confirmed-operation'
    with db.tx() as c:
        for operation in ['cash.event:synthetic','brand:enbishan:cash.event:synthetic']:
            c.execute('INSERT INTO idempotency_keys(user_id,operation,key,request_digest,response) VALUES(%s,%s,%s,%s,%s)',
                ('dual-manager',operation,key+('-enb' if operation.startswith('brand:') else ''),'synthetic-digest',db.json({'secret_old_payload':'must-never-be-returned'})))
    path='/api/operations/replay-status?key='
    result=actor('manager','kps').get(path+key)
    assert result.status_code==200,result.text
    assert result.json()=={'recorded':True}
    assert 'no-store' in result.headers['cache-control']
    assert actor('manager','enbishan').get(path+key).json()=={'recorded':False}
    assert actor('manager','enbishan').get(path+key+'-enb').json()=={'recorded':True}
    assert actor('manager','kps').get(path+key+'-enb').json()=={'recorded':False}
    assert actor('owner','kps').get(path+key).json()=={'recorded':False}
    assert actor('manager','kps').get(path+'SYNTHETIC-unknown').json()=={'recorded':False}
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-manager' AND brand_id='enbishan'")
    denied=actor('manager','enbishan').get(path+key+'-enb')
    assert denied.status_code==403
    with db.tx() as c:c.execute("UPDATE users SET active=false WHERE id='dual-manager'")
    assert actor('manager','kps').get(path+key).status_code==401
