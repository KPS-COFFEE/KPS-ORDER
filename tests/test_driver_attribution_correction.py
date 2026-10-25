"""Owner corrections preserve immutable sales, tasks, collections and cash history."""
from uuid import uuid4

import psycopg
import pytest

from app import auth, db, service_hours
from app.operations import balance
from test_operations import ops

pytestmark = pytest.mark.postgres


def seed(ops, monkeypatch, method='cash', amounts=(3500, 3750), cash_parts=None):
    """Synthetic completed evidence, with the receiving shift opened afterwards."""
    monkeypatch.setattr(auth, 'require_owner_step_up', lambda *_: None)
    actor, post, make_order, _ = ops
    with db.tx() as c:
        for sid, kind, person, parent in (
            ('correction-manager', 'manager', 'ops-manager', None),
            ('correction-source', 'driver', 'ops-driver', 'correction-manager'),
            ('correction-target', 'driver', 'ops-driver2', 'correction-manager'),
        ):
            c.execute("""INSERT INTO shifts
              (id,kind,user_id,brand_id,branch_ids,manager_shift_id,delivery_window_snapshot,
               delivery_operational_date,opened_at)
              VALUES(%s,%s,%s,'ops-brand','["ops-branch"]',%s,'{"period":"evening"}',%s,
                     now()-CASE WHEN %s='correction-target' THEN interval '0 seconds' ELSE interval '1 hour' END)""",
              (sid, kind, person, parent, service_hours.delivery_operational_date(), sid))
    result = []
    for number, amount in enumerate(amounts):
        oid = make_order(total=amount)
        tid = 'task-' + oid
        ref = 'SYNTHETIC-CORRECTION-' + str(number)
        cash = cash_parts[number] if cash_parts is not None else (amount if method == 'cash' else (1500 if method == 'split' else 0))
        card = amount - cash
        actual_method = 'split' if cash and card else ('cash' if cash else 'card')
        with db.tx() as c:
            c.execute("""UPDATE orders SET preparation_status='completed',delivery_status='delivered',payment_status='paid',
              payment_method=%s,created_at=now()-interval '35 minutes',completed_at=now()-interval '5 minutes',
              snapshot=snapshot||%s WHERE id=%s""",
              (actual_method, db.json({'delivery_period': 'evening', 'order_reference': ref}), oid))
            c.execute("""INSERT INTO driver_tasks
              (id,order_id,driver_id,shift_id,status,assigned_by,created_at,accepted_at,picked_up_at,delivered_at,proof)
              VALUES(%s,%s,'ops-driver','correction-source','delivered','ops-manager',
                     now()-interval '30 minutes',now()-interval '25 minutes',
                     now()-interval '20 minutes',now()-interval '5 minutes','Synthetic delivery evidence')""", (tid, oid))
            c.execute("""INSERT INTO driver_work_trips
              (id,brand_id,shift_id,driver_id,departure_branch_id,return_branch_id,departed_at,returned_at,departure_evidence,return_evidence)
              VALUES(%s,'ops-brand','correction-source','ops-driver','ops-branch','ops-branch',
                     now()-interval '20 minutes',now()-interval '1 minute','{}','{}')""", ('trip-' + oid,))
            c.execute('INSERT INTO driver_work_trip_orders(trip_id,order_id) VALUES(%s,%s)', ('trip-' + oid, oid))
            c.execute("""INSERT INTO driver_work_trip_events(id,trip_id,actor_id,action,branch_id,evidence)
              VALUES(%s,%s,'ops-driver','depart','ops-branch','{}')""", ('trip-event-' + oid, 'trip-' + oid))
            for payment_method, value in [('cash', cash), ('card', card)]:
                if not value:
                    continue
                cid = payment_method + '-' + oid
                c.execute("""INSERT INTO collections
                  (id,order_id,shift_id,collector_id,method,amount_halalas,tendered_halalas,change_halalas,reference,created_at)
                  VALUES(%s,%s,'correction-source','ops-driver',%s,%s,%s,%s,%s,now()-interval '6 minutes')""",
                  (cid, oid, payment_method, value, value + 500 if payment_method == 'cash' else None,
                   500 if payment_method == 'cash' else 0, 'SYNTHETIC-TERMINAL-' + oid if payment_method == 'card' else ''))
                if payment_method == 'cash':
                    c.execute("""INSERT INTO custody_events
                      (id,shift_id,amount_halalas,kind,reference_id,actor_id,reason)
                      VALUES(%s,'correction-source',%s,'collection',%s,'ops-driver','Synthetic initial cash')""",
                      ('event-' + cid, value, cid))
            version = c.execute('SELECT version FROM orders WHERE id=%s', (oid,)).fetchone()['version']
        result.append((oid, {'source_task_id': tid, 'source_shift_id': 'correction-source',
                            'target_shift_id': 'correction-target', 'expected_version': version,
                            'reason': 'بأمر المالك', 'confirmed': True}))
    return result


def url(oid):
    return '/api/orders/' + oid + '/driver-attribution-correction'


def evidence(c):
    return {
        'tasks': c.execute('SELECT * FROM driver_tasks ORDER BY id').fetchall(),
        'collections': c.execute('SELECT * FROM collections ORDER BY id').fetchall(),
        'sales': c.execute('SELECT id,total_halalas,subtotal_halalas,delivery_fee_halalas,snapshot,customer,completed_at,payment_status,payment_method,delivery_status FROM orders ORDER BY id').fetchall(),
        'collection_events': c.execute("SELECT * FROM custody_events WHERE kind='collection' ORDER BY id").fetchall(),
        'refunds': c.execute('SELECT * FROM refunds ORDER BY id').fetchall(),
        'trip_events': c.execute('SELECT * FROM driver_work_trip_events ORDER BY id').fetchall(),
        'trips': c.execute('SELECT * FROM driver_work_trips ORDER BY id').fetchall(),
        'trip_orders': c.execute('SELECT * FROM driver_work_trip_orders ORDER BY trip_id,order_id').fetchall(),
        'fiscal_documents': c.execute('SELECT * FROM fiscal_documents ORDER BY number').fetchall(),
    }


@pytest.mark.parametrize('method', ['cash', 'card', 'split'])
def test_two_order_correction_preserves_raw_evidence_and_conserves_sales_and_cash(ops, monkeypatch, method):
    rows = seed(ops, monkeypatch, method)
    actor, post, _, _ = ops
    with db.tx() as c:
        before = evidence(c)
        original_cash = sum(x['amount_halalas'] for x in before['collections'] if x['method'] == 'cash')
        original_card = 7250 - original_cash
    for oid, body in rows:
        preview = actor('owner').get(url(oid))
        assert preview.status_code == 200, preview.text
        result = post('owner', url(oid), body, key='correct-' + oid)
        assert result['version'] == body['expected_version'] + 1
        assert post('owner', url(oid), body, key='correct-' + oid) == result
        post('owner', url(oid), {**body, 'reason': 'Different reason on reused key'}, key='correct-' + oid, status=409)
        post('owner', url(oid), body, status=409)
    with db.tx() as c:
        assert evidence(c) == before
        source = c.execute("SELECT * FROM shifts WHERE id='correction-source'").fetchone()
        target = c.execute("SELECT * FROM shifts WHERE id='correction-target'").fetchone()
        assert balance(c, source) == 0
        assert balance(c, target) == original_cash
        adjustments = c.execute("SELECT * FROM custody_events WHERE kind='adjustment' ORDER BY id").fetchall()
        assert len(adjustments) == (0 if method == 'card' else 4)
        assert sum(x['amount_halalas'] for x in adjustments) == 0
        assert all(x['actor_id'] == 'ops-owner' for x in adjustments)
        tasks = c.execute('SELECT * FROM effective_driver_tasks ORDER BY id').fetchall()
        collections = c.execute('SELECT * FROM effective_collections ORDER BY id').fetchall()
        assert len(tasks) == 2 and all(x['driver_id'] == 'ops-driver2' and x['shift_id'] == target['id'] for x in tasks)
        assert len(collections) == len(before['collections'])
        assert sum(x['amount_halalas'] for x in collections) == 7250
        assert all(x['collector_id'] == 'ops-driver2' and x['shift_id'] == target['id'] for x in collections)
        assert c.execute('SELECT count(*) n FROM order_driver_attribution_corrections').fetchone()['n'] == 2
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='order.driver_attribution.corrected'").fetchone()['n'] == 2
        from app.blind_closure import card_reconciliation
        assert card_reconciliation(c, source['id'], 0)['expected_card_halalas'] == 0
        assert card_reconciliation(c, target['id'], original_card)['card_difference_halalas'] == 0
        from app.driver_incentives import source_rows
        owner = c.execute("SELECT * FROM users WHERE id='ops-owner'").fetchone()
        assert source_rows(c, owner, shift_id=source['id']) == []
        assert {x['order_id'] for x in source_rows(c, owner, shift_id=target['id'])} == {x[0] for x in rows}
    # Real read routes must agree with the effective SQL sources.
    src = actor('owner').get('/api/shifts/correction-source')
    dst = actor('owner').get('/api/shifts/correction-target')
    assert src.status_code == dst.status_code == 200
    assert src.json()['tasks'] == [] and src.json()['collections'] == []
    assert len(dst.json()['tasks']) == 2
    assert sum(x['amount_halalas'] for x in dst.json()['collections']) == 7250
    # An old application reading immutable raw rows cannot freeze misattribution,
    # including card-only corrections that leave cash balances unchanged.
    for sid, person in [('correction-source', 'ops-driver'), ('correction-target', 'ops-driver2')]:
        old_snapshot = {
            'tasks': [{k: r[k] for k in ('id', 'driver_id', 'shift_id')} for r in before['tasks'] if r['shift_id'] == sid],
            'collections': [{k: r[k] for k in ('id', 'collector_id', 'shift_id')} for r in before['collections'] if r['shift_id'] == sid],
        }
        with pytest.raises(psycopg.Error, match='compatible closure snapshot'):
            with db.tx() as c:
                c.execute("INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES(%s,%s,'driver','ops-brand','[\"ops-branch\"]',%s,%s)",
                          ('obsolete-' + sid, sid, person, db.json(old_snapshot)))
    # Newly created closure snapshots use the correction; raw history stays intact.
    with db.tx() as c:
        from app.reporting import create_snapshot
        snapshots = []
        for sid, cash, card in [('correction-source', 0, 0), ('correction-target', original_cash, original_card)]:
            submission = {'expected_halalas': cash, 'counted_halalas': cash, 'handover_halalas': cash,
                          'retained_halalas': 0, 'difference_halalas': 0, 'terminal_total_halalas': card,
                          'expected_card_halalas': card, 'card_difference_halalas': 0}
            c.execute("UPDATE shifts SET status='closed',closed_at=now(),submission=%s WHERE id=%s", (db.json(submission), sid))
            rid = create_snapshot(c, sid, 'ops-owner')
            snapshots.append(c.execute('SELECT snapshot FROM closure_reports WHERE id=%s', (rid,)).fetchone()['snapshot'])
        assert snapshots[0]['summary']['completed_orders'] == 0
        assert snapshots[0]['summary']['net_collections_halalas'] == 0
        assert snapshots[1]['summary']['completed_orders'] == 2
        assert snapshots[1]['summary']['net_collections_halalas'] == 7250
        assert evidence(c) == before


def test_existing_target_order_is_preserved_when_6700_cash_and_550_card_are_corrected(ops, monkeypatch):
    rows = seed(ops, monkeypatch, method='split', cash_parts=(3500, 3200))
    actor, post, make_order, _ = ops
    existing = make_order(total=2400)
    with db.tx() as c:
        c.execute("UPDATE orders SET preparation_status='completed',delivery_status='delivered',payment_status='paid',payment_method='card',completed_at=now() WHERE id=%s", (existing,))
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,accepted_at,picked_up_at,delivered_at) VALUES(%s,%s,'ops-driver2','correction-target','delivered','ops-manager',now(),now(),now())", ('target-task-' + existing, existing))
        c.execute("INSERT INTO collections(id,order_id,shift_id,collector_id,method,amount_halalas,reference) VALUES(%s,%s,'correction-target','ops-driver2','card',2400,'SYNTHETIC-EXISTING-TARGET')", ('target-card-' + existing, existing))
        before = evidence(c)
    for oid, body in rows:
        post('owner', url(oid), body)
    with db.tx() as c:
        assert evidence(c) == before
        assert c.execute("SELECT count(*) n FROM effective_driver_tasks WHERE shift_id='correction-source'").fetchone()['n'] == 0
        assert c.execute("SELECT count(*) n FROM effective_driver_tasks WHERE shift_id='correction-target'").fetchone()['n'] == 3
        amounts = {r['method']: r['amount'] for r in c.execute("SELECT method,sum(amount_halalas) amount FROM effective_collections WHERE shift_id='correction-target' GROUP BY method").fetchall()}
        assert amounts == {'cash': 6700, 'card': 2950}
        assert balance(c, c.execute("SELECT * FROM shifts WHERE id='correction-source'").fetchone()) == 0
        assert balance(c, c.execute("SELECT * FROM shifts WHERE id='correction-target'").fetchone()) == 6700
        from app.blind_closure import card_reconciliation
        assert card_reconciliation(c, 'correction-target', 2950)['card_difference_halalas'] == 0
        from app.reporting import create_snapshot
        submission = {'expected_halalas': 6700, 'counted_halalas': 6700, 'handover_halalas': 6700,
                      'retained_halalas': 0, 'difference_halalas': 0, 'terminal_total_halalas': 2950,
                      'expected_card_halalas': 2950, 'card_difference_halalas': 0}
        c.execute("UPDATE shifts SET status='closed',closed_at=now(),submission=%s WHERE id='correction-target'", (db.json(submission),))
        rid = create_snapshot(c, 'correction-target', 'ops-owner')
        summary = c.execute('SELECT snapshot FROM closure_reports WHERE id=%s', (rid,)).fetchone()['snapshot']['summary']
        assert summary['completed_orders'] == 3
        assert summary['net_collections_halalas'] == 9650
        assert summary['cash_collected_halalas'] == 6700
        assert summary['card_collected_halalas'] == 2950


@pytest.mark.parametrize('who', ['manager', 'driver', 'driver2', 'other'])
def test_only_authorized_owner_can_preview_or_apply(ops, monkeypatch, who):
    oid, body = seed(ops, monkeypatch, amounts=(1000,))[0]
    actor, _, _, _ = ops
    for response in (actor(who).get(url(oid)), actor(who).post(url(oid), json=body, headers={'Idempotency-Key': uuid4().hex})):
        assert response.status_code in (403, 404), response.text
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM order_driver_attribution_corrections').fetchone()['n'] == 0
        assert c.execute("SELECT count(*) n FROM custody_events WHERE kind='adjustment'").fetchone()['n'] == 0


@pytest.mark.parametrize('condition', ['source_closing', 'target_closing', 'manager_closed', 'refund', 'report', 'pending_custody', 'confirmed_custody', 'wrong_branch', 'wrong_brand', 'different_manager', 'different_day', 'different_period', 'target_inactive', 'same_shift', 'stale_version'])
def test_ineligible_corrections_are_atomic_and_leave_evidence_unchanged(ops, monkeypatch, condition):
    oid, body = seed(ops, monkeypatch, amounts=(1000,))[0]
    actor, _, _, _ = ops
    with db.tx() as c:
        if condition in ('source_closing', 'target_closing'):
            c.execute("UPDATE shifts SET status='closing' WHERE id=%s", ('correction-' + condition.split('_')[0],))
        elif condition == 'manager_closed':
            c.execute("UPDATE shifts SET status='closed' WHERE id='correction-manager'")
        elif condition == 'refund':
            c.execute("INSERT INTO refunds(id,collection_id,order_id,shift_id,method,amount_halalas,reason,authorized_by) VALUES('prior-refund',%s,%s,'correction-source','cash',100,'Synthetic refund','ops-owner')", ('cash-' + oid, oid))
        elif condition == 'report':
            c.execute("INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES('prior-report','correction-source','driver','ops-brand','[\"ops-branch\"]','ops-driver','{}')")
        elif condition in ('pending_custody', 'confirmed_custody'):
            c.execute("INSERT INTO custody_transfers(id,from_shift_id,to_shift_id,recipient_user_id,amount_halalas,reference,status,created_by) VALUES('prior-custody','correction-source','correction-manager','ops-manager',100,'Synthetic custody',%s,'ops-driver')", (condition.split('_')[0],))
        elif condition == 'wrong_branch':
            c.execute("UPDATE shifts SET branch_ids='[]' WHERE id='correction-target'")
        elif condition == 'different_manager':
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES('another-manager','manager','ops-owner','ops-brand','[\"ops-branch\"]')")
            c.execute("UPDATE shifts SET manager_shift_id='another-manager' WHERE id='correction-target'")
        elif condition == 'different_day':
            c.execute("UPDATE shifts SET delivery_operational_date=delivery_operational_date-1 WHERE id='correction-target'")
        elif condition == 'different_period':
            c.execute("UPDATE shifts SET delivery_window_snapshot='{\"period\":\"morning\"}' WHERE id='correction-target'")
        elif condition == 'target_inactive':
            c.execute("UPDATE users SET active=false WHERE id='ops-driver2'")
        elif condition == 'wrong_brand':
            c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('foreign-driver','Synthetic foreign driver','driver','ops-other','[\"ops-branch-other\"]')")
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES('foreign-manager','manager','ops-other','ops-other','[\"ops-branch-other\"]')")
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id) VALUES('foreign-target','driver','foreign-driver','ops-other','[\"ops-branch-other\"]','foreign-manager')")
            body['target_shift_id'] = 'foreign-target'
        elif condition == 'same_shift':
            body['target_shift_id'] = body['source_shift_id']
        elif condition == 'stale_version':
            body['expected_version'] += 1
        before = evidence(c)
    response = actor('owner').post(url(oid), json=body, headers={'Idempotency-Key': uuid4().hex})
    assert response.status_code in (403, 404, 409, 422), response.text
    with db.tx() as c:
        assert evidence(c) == before
        assert c.execute('SELECT count(*) n FROM order_driver_attribution_corrections').fetchone()['n'] == 0
        assert c.execute("SELECT count(*) n FROM custody_events WHERE kind='adjustment'").fetchone()['n'] == 0


def test_confirmation_and_strict_version_are_required_without_financial_writes(ops, monkeypatch):
    oid, body = seed(ops, monkeypatch, amounts=(1000,))[0]
    actor, _, _, _ = ops
    with db.tx() as c:
        before = evidence(c)
    for patch in ({'confirmed': False}, {'confirmed': 'true'}, {'expected_version': True}, {'reason': ' '}):
        response = actor('owner').post(url(oid), json={**body, **patch}, headers={'Idempotency-Key': uuid4().hex})
        assert response.status_code == 422, response.text
    with db.tx() as c:
        assert evidence(c) == before
        assert c.execute('SELECT count(*) n FROM order_driver_attribution_corrections').fetchone()['n'] == 0


def test_future_cash_refund_uses_corrected_custody_and_cannot_test_void(ops, monkeypatch):
    oid, body = seed(ops, monkeypatch, amounts=(1000,))[0]
    actor, post, _, _ = ops
    post('owner', url(oid), body)
    with db.tx() as c:
        row = c.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
    denied = actor('owner').post('/api/orders/' + oid + '/test-void', json={
        'order_reference': row['snapshot']['order_reference'], 'expected_version': row['version'],
        'expected_total_halalas': 1000, 'reason': 'Synthetic already corrected receipt',
        'confirmed_no_cash_received': True}, headers={'Idempotency-Key': uuid4().hex})
    assert denied.status_code == 409, denied.text
    result = post('owner', '/api/orders/' + oid + '/refund', {'amount_halalas': 300, 'method': 'cash', 'reason': 'Synthetic actual refund'})
    with db.tx() as c:
        refund = c.execute('SELECT * FROM refunds WHERE id=%s', (result['refund_id'],)).fetchone()
        assert refund['shift_id'] == 'correction-target'
        assert refund['collection_id'] == 'cash-' + oid
        assert balance(c, c.execute("SELECT * FROM shifts WHERE id='correction-source'").fetchone()) == 0
        assert balance(c, c.execute("SELECT * FROM shifts WHERE id='correction-target'").fetchone()) == 700


def test_replay_rechecks_owner_scope_and_raw_records_stay_immutable(ops, monkeypatch):
    oid, body = seed(ops, monkeypatch, amounts=(1000,))[0]
    actor, post, _, _ = ops
    post('owner', url(oid), body, key='protected-replay')
    with db.tx() as c:
        c.execute("UPDATE users SET role='driver' WHERE id='ops-owner'")
    denied = actor('owner').post(url(oid), json=body, headers={'Idempotency-Key': 'protected-replay'})
    assert denied.status_code == 403, denied.text
    for statement, params in (
        ('UPDATE driver_tasks SET driver_id=%s WHERE id=%s', ('ops-driver2', body['source_task_id'])),
        ('UPDATE collections SET collector_id=%s WHERE order_id=%s', ('ops-driver2', oid)),
        ("UPDATE custody_events SET amount_halalas=0 WHERE kind='collection'", ()),
        ('DELETE FROM order_driver_attribution_corrections WHERE order_id=%s', (oid,)),
    ):
        with pytest.raises(psycopg.Error):
            with db.tx() as c:
                c.execute(statement, params)
