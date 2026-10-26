"""A physical driver's standing float is not reissued for the second brand."""
import pytest
from app import db
from test_enbishan_dual_brand import dual

pytestmark=pytest.mark.postgres


def approved_float(post):
    body={'driver_id':'dual-driver','amount_halalas':20000,'reference':'Actual float handed once','reason':'Verified original shared driver float'}
    request=post('manager','/api/driver-floats',body)
    post('accountant','/api/driver-floats/'+request['id']+'/decision',{'approved':True,'confirmed':True,'reference':'Original actual float reviewed'})
    return request,body


def test_other_brand_cannot_issue_a_second_standing_float(dual):
    _,_,post,_,_=dual
    original,body=approved_float(post)
    post('manager','/api/driver-floats',body,'enbishan',status=409)
    with db.tx() as c:
        rows=c.execute("SELECT r.id,r.amount_halalas,r.brand_id FROM driver_float_requests r JOIN driver_float_decisions d ON d.request_id=r.id WHERE r.driver_id='dual-driver' AND d.approved").fetchall()
        assert [(r['id'],r['amount_halalas'],r['brand_id']) for r in rows]==[(original['id'],20000,'kps')]


def test_historical_second_brand_pending_float_cannot_be_approved(dual):
    _,_,post,_,_=dual
    original,_=approved_float(post)
    with db.tx() as c:
        c.execute("INSERT INTO driver_float_requests(id,brand_id,driver_id,branch_ids,amount_halalas,reference,reason,requested_by) VALUES('historical-pending-float','enbishan','dual-driver','[\"enbishan-test-1\",\"enbishan-test-2\"]',20000,'Historical pending declaration','Synthetic pending duplication','dual-manager')")
    post('accountant','/api/driver-floats/historical-pending-float/decision',{'approved':True,'confirmed':True,'reference':'Attempted second issuance'},'enbishan',status=409)
    with db.tx() as c:
        assert c.execute("SELECT 1 FROM driver_float_decisions WHERE request_id='historical-pending-float'").fetchone() is None
        assert c.execute('SELECT amount_halalas FROM driver_float_requests WHERE id=%s',(original['id'],)).fetchone()['amount_halalas']==20000
