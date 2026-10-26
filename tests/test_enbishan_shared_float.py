"""Shared personnel must not imply shared cash approvals or balances."""
import pytest
from app import db
from test_enbishan_dual_brand import dual

pytestmark = pytest.mark.postgres


def test_shared_driver_float_listing_keeps_approvals_in_their_brand(dual):
    _, actor, post, _, _ = dual
    proposal = {'driver_id': 'dual-driver', 'amount_halalas': 20000,
                'reference': 'SYNTHETIC', 'reason': 'TEST ONLY standing cash'}
    kps = post('manager', '/api/driver-floats', proposal)['id']
    post('accountant', '/api/driver-floats/' + kps + '/decision',
         {'approved': True, 'confirmed': True, 'reference': 'SYNTHETIC approval'})
    for brand in ('kps', 'enbishan'):
        response = actor('manager', brand).get('/api/driver-floats')
        assert response.status_code == 200, response.text
        data = response.json()
        assert [d['id'] for d in data['drivers']] == ['dual-driver']
        if brand == 'kps':
            assert data['drivers'][0]['current'] == {'request_id': kps, 'amount': 20000}
        else:
            assert data['drivers'][0]['current'] is None
            assert data['requests'] == []
    # New issuance is fixed at SAR 200 once per person, never per brand.
    post('manager','/api/driver-floats',{**proposal,'amount_halalas':10000},'enbishan',status=422)
    post('manager','/api/driver-floats',proposal,'enbishan',status=409)
    # Explicit synthetic historical evidence predates that policy. Preserve its
    # original denomination and seller rather than approving a new duplicate.
    enb='synthetic-historical-enbishan-float'
    with db.tx() as c:
        c.execute("INSERT INTO driver_float_requests(id,brand_id,driver_id,branch_ids,amount_halalas,reference,reason,requested_by) VALUES(%s,'enbishan','dual-driver','[\"enbishan-test-1\",\"enbishan-test-2\"]',10000,'SYNTHETIC historical original','Historical record only, no new issuance','dual-manager')",(enb,))
        c.execute("INSERT INTO driver_float_decisions(request_id,brand_id,approved,reviewed_by,reference) VALUES(%s,'enbishan',true,'dual-accountant','SYNTHETIC historical independent certification')",(enb,))
    post('accountant','/api/driver-floats/'+enb+'/decision',{'approved':True,'confirmed':True,'reference':'SYNTHETIC replay denied'},status=404)
    post('accountant','/api/driver-floats/'+enb+'/decision',{'approved':True,'confirmed':True,'reference':'SYNTHETIC replay denied'},'enbishan',status=409)
    assert actor('manager','enbishan').get('/api/driver-floats').json()['drivers'][0]['current']=={'request_id':enb,'amount':10000}
    with db.tx() as c:
        rows = c.execute('SELECT brand_id,amount_halalas FROM driver_float_requests ORDER BY brand_id').fetchall()
        assert rows == [{'brand_id': 'enbishan', 'amount_halalas': 10000}, {'brand_id': 'kps', 'amount_halalas': 20000}]
        assert c.execute('SELECT count(*) n FROM custody_events').fetchone()['n'] == 0


def test_shared_driver_float_scope_and_revocation(dual):
    _, actor, post, _, _ = dual
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids=%s WHERE user_id='dual-manager' AND brand_id='enbishan'", (db.json(['enbishan-test-1']),))
    assert actor('manager', 'enbishan').get('/api/driver-floats').json()['drivers'] == []
    body = {'driver_id': 'dual-driver', 'amount_halalas': 20000, 'reference': 'SYNTHETIC', 'reason': 'TEST ONLY'}
    post('manager', '/api/driver-floats', body, 'enbishan', status=404)
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-driver' AND brand_id='enbishan'")
    assert actor('owner', 'enbishan').get('/api/driver-floats').json()['drivers'] == []
    assert actor('manager').get('/api/driver-floats').json()['drivers'][0]['id'] == 'dual-driver'
