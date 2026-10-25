"""Synthetic witnesses for delivery-only historical attestations, never payment."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from app import db, service_hours
from app.operations import safe
from test_enbishan_dual_brand import dual


BASE = '/api/legacy-deliveries/shifts/legacy-driver'


@pytest.fixture
def legacy(dual):
    _, actor, post, _, make_order = dual
    opened = datetime.now(timezone.utc) - timedelta(days=1)
    day = service_hours.delivery_operational_date(opened)
    with db.tx() as c:
        for sid, kind, uid, parent in [
            ('legacy-manager', 'manager', 'dual-manager', None),
            ('legacy-driver', 'driver', 'dual-driver', 'legacy-manager'),
        ]:
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id,opened_at,delivery_operational_date) VALUES(%s,%s,%s,'enbishan',%s,%s,%s,%s)",
                      (sid, kind, uid, db.json(['enbishan-test-1']), parent, opened, day))
    def add(brand='enbishan', historical=True):
        oid = uuid4().hex
        with db.tx() as c:
            branch = 'enbishan-test-1' if brand=='enbishan' else 'kps-branch-1'
            snapshot = {'items':[], 'branch_name':branch, 'delivery_workflow':'manager_dispatch'}
            if historical: snapshot['order_source'] = 'delivery_center'
            c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,%s,%s,%s,'delivery','accepted','accepted','cash',1000,0,1000,%s,%s)",
                      (oid, brand, branch, 'dual-'+brand+'-customer', db.json(snapshot), db.json({'name':'SYNTHETIC'})))
            c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by) VALUES(%s,%s,'dual-driver','legacy-driver','accepted','dual-manager')", ('legacy-task-'+oid, oid))
        return oid
    ids = [add(), add()]
    def body():
        r = actor('owner', 'enbishan').get(BASE+'/preview')
        assert r.status_code == 200, r.text
        p = r.json()
        return {'operational_date':p['operational_date'],
                'orders':[{k:o[k] for k in ('order_id','task_id','expected_version')} for o in p['orders']],
                'reason':'Owner attests historical Sabad delivery only', 'confirmed':True}
    return actor, post, ids, body, add


def raw_state(c):
    return {name:sorted(c.execute('SELECT * FROM '+name).fetchall(),key=repr)
            for name in ('shifts','collections','custody_events','custody_transfers','closure_reports','refunds','fiscal_documents','driver_work_trips')}


@pytest.mark.postgres
def test_delivery_only_preserves_financial_physical_and_assignment_evidence(legacy):
    actor, post, ids, body, _ = legacy
    payload = body()
    with db.tx() as c:
        c.execute("UPDATE shifts SET status='closing',vehicle_log=%s,permanent_float=%s WHERE id='legacy-driver'",
                  (db.json({'start':{'odometer_km':182000,'fuel_percent':100}}), db.json({'amount':20000})))
        before = raw_state(c)
        originals = {oid:c.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone() for oid in ids}
    result = post('owner', BASE+'/complete', payload, brand='enbishan', key='delivery-only-once')
    assert result['completed_count'] == 2
    assert result['shift_closed'] is False and result['payments_changed'] is False
    assert post('owner', BASE+'/complete', payload, brand='enbishan', key='delivery-only-once') == result
    post('owner', BASE+'/complete', payload, brand='enbishan', status=409)
    with db.tx() as c:
        assert raw_state(c) == before
        assert c.execute('SELECT count(*) n FROM legacy_delivery_attestations').fetchone()['n'] == 2
        for oid in ids:
            order = c.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
            task = c.execute('SELECT * FROM driver_tasks WHERE order_id=%s', (oid,)).fetchone()
            assert order['delivery_status'] == task['status'] == 'delivered'
            assert order['preparation_status'] == 'completed'
            assert order['payment_status'] == 'unpaid'
            assert order['completed_at'] is None and task['delivered_at'] is None and task['picked_up_at'] is None
            assert task['driver_id'] == 'dual-driver' and task['shift_id'] == 'legacy-driver'
            attestation = c.execute('SELECT * FROM legacy_delivery_attestations WHERE order_id=%s', (oid,)).fetchone()
            assert attestation['original']['task']['status'] == 'accepted'
            assert attestation['original']['order'] == safe(originals[oid])
            assert attestation['original']['actual_delivery_time'] is None
            assert attestation['original']['payment_recorded'] is False
            assert attestation['actor_id'] == 'dual-owner'
            assert order['version'] == originals[oid]['version']+1


@pytest.mark.postgres
@pytest.mark.parametrize('change', ['version', 'task', 'day', 'subset', 'duplicate', 'unconfirmed'])
def test_stale_or_partial_attestation_is_atomic(legacy, change):
    _, post, ids, body, add = legacy
    payload = body()
    if change == 'version': payload['orders'][0]['expected_version'] += 1
    if change == 'task': payload['orders'][0]['task_id'] = 'unrelated-task'
    if change == 'day': payload['operational_date'] = '2001-01-01'
    if change == 'subset': payload['orders'].pop()
    if change == 'duplicate': payload['orders'].append(deepcopy(payload['orders'][0]))
    if change == 'unconfirmed': payload['confirmed'] = False
    post('owner', BASE+'/complete', payload, brand='enbishan', status=422 if change=='unconfirmed' else 409)
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM legacy_delivery_attestations').fetchone()
        assert all(r['status']=='accepted' for r in c.execute('SELECT status FROM driver_tasks').fetchall())


@pytest.mark.postgres
@pytest.mark.parametrize('change', ['paid','cancelled','not_legacy','wrong_brand','closed_shift'])
def test_noneligible_order_or_closed_shift_cannot_be_overridden(legacy, change):
    _, post, ids, body, add = legacy
    payload = body()
    with db.tx() as c:
        if change == 'paid': c.execute("UPDATE orders SET payment_status='paid' WHERE id=%s", (ids[0],))
        if change == 'cancelled': c.execute("UPDATE orders SET preparation_status='cancelled' WHERE id=%s", (ids[0],))
        if change == 'closed_shift': c.execute("UPDATE shifts SET status='closed' WHERE id='legacy-driver'")
    if change == 'not_legacy': add(historical=False)
    if change == 'wrong_brand': add(brand='kps')
    post('owner', BASE+'/complete', payload, brand='enbishan', status=409)
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM legacy_delivery_attestations').fetchone()
        assert all(r['status']=='accepted' for r in c.execute('SELECT status FROM driver_tasks').fetchall())


@pytest.mark.postgres
def test_owner_permission_is_required_and_evidence_is_immutable(legacy):
    actor, post, ids, body, _ = legacy
    payload = body()
    post('manager', BASE+'/complete', payload, brand='enbishan', status=403)
    assert actor('manager','enbishan').get(BASE+'/preview').status_code == 403
    post('owner', BASE+'/complete', payload, brand='enbishan')
    for sql in ('UPDATE legacy_delivery_attestations SET reason=\'changed\'', 'DELETE FROM legacy_delivery_attestations'):
        with pytest.raises(psycopg.Error):
            with db.tx() as c: c.execute(sql)


@pytest.mark.postgres
def test_fresh_scope_revocation_blocks_post(legacy):
    _, post, ids, body, _ = legacy
    payload = body()
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-owner' AND brand_id='enbishan'")
    post('owner', BASE+'/complete', payload, brand='enbishan', status=403)
    with db.tx() as c: assert not c.execute('SELECT 1 FROM legacy_delivery_attestations').fetchone()


@pytest.mark.postgres
def test_closure_report_counts_attested_delivery_but_never_invents_collection(legacy):
    _, post, ids, body, _ = legacy
    from app import reporting
    post('owner', BASE+'/complete', body(), brand='enbishan')
    with db.tx() as c:
        c.execute("UPDATE shifts SET submission='{}' WHERE id='legacy-driver'")
        rid = reporting.create_snapshot(c, 'legacy-driver', 'dual-owner')
        snap = c.execute('SELECT snapshot FROM closure_reports WHERE id=%s', (rid,)).fetchone()['snapshot']
    assert snap['summary']['completed_orders'] == 2
    assert snap['summary']['gross_completed_halalas'] == 2000
    assert snap['summary']['cash_collected_halalas'] == snap['summary']['card_collected_halalas'] == 0
    assert not snap['collections']
    assert len(snap['legacy_delivery_attestations']) == 2
    assert all(o['fulfilled_in_report'] and o['completed_at'] is None for o in snap['orders'])
    assert all(t['delivered_at'] is None for t in snap['tasks'])
    rendered = reporting.make_v5_print_html(snap)
    assert 'Sabad' in rendered or 'ساباد' in rendered
    assert 'Owner attests historical Sabad delivery only' in rendered
