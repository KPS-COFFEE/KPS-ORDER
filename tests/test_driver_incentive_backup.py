"""New driver eligibility records remain scoped and survive full restoration."""
import json
import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row
from app import db
from app.exports import TABLES, _scoped_sql, build_archive, restore_archive, verify_archive
from test_operations import ops


def test_incentive_export_tables_are_order_scoped_and_dependency_ordered():
    for name in ('driver_incentive_decisions', 'driver_incentive_decision_events'):
        assert TABLES.index(name) > TABLES.index('driver_tasks')
        clause, params = _scoped_sql(name, 'synthetic-brand')
        assert clause == 'order_id IN (SELECT id FROM orders WHERE brand_id=%s)'
        assert params == ('synthetic-brand',)


@pytest.mark.postgres
def test_nonempty_incentive_decisions_export_and_restore(ops, tmp_path):
    actor, post, order, shifts = ops
    manager, driver = shifts()
    oid = order()
    task = post('manager', '/api/orders/'+oid+'/assign', {'driver_shift_id': driver})['task']['id']
    event_id = 'TEST-incentive-'+uuid4().hex
    decision = {'responsibility': 'customer', 'reason': 'TEST customer cancellation'}
    with db.tx() as c:
        c.execute("INSERT INTO driver_incentive_decisions(order_id,task_id,brand_id,responsibility,reason,incident_key,decided_by) VALUES(%s,%s,'ops-brand','customer',%s,'TEST-incident','ops-manager')", (oid, task, decision['reason']))
        c.execute("INSERT INTO driver_incentive_decision_events(id,order_id,version,actor_id,decision) VALUES(%s,%s,1,'ops-manager',%s)", (event_id, oid, db.json(decision)))
    response = actor('owner').get('/api/admin/export')
    assert response.status_code == 200, response.text
    archive, _ = verify_archive(response.content)
    assert [r['order_id'] for r in json.loads(archive.read('data/driver_incentive_decisions.json'))] == [oid]
    assert json.loads(archive.read('data/driver_incentive_decision_events.json'))[0]['decision'] == decision
    other = actor('other').get('/api/admin/export')
    assert other.status_code == 200, other.text
    _, manifest = verify_archive(other.content)
    assert manifest['counts']['driver_incentive_decisions'] == 0
    assert manifest['counts']['driver_incentive_decision_events'] == 0
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        contents, manifest = build_archive(c, full=True)
    target = 'kps_restore_'+uuid4().hex
    base = os.environ['TEST_DATABASE_URL']
    try:
        result = restore_archive(contents, target_url=base, target_schema=target, asset_directory=tmp_path/'assets')
        assert result['verified'] and result['counts'] == manifest['counts']
        with psycopg.connect(base, row_factory=dict_row) as c:
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
            assert c.execute('SELECT task_id FROM driver_incentive_decisions WHERE order_id=%s', (oid,)).fetchone()['task_id'] == task
            assert c.execute('SELECT decision FROM driver_incentive_decision_events WHERE id=%s', (event_id,)).fetchone()['decision'] == decision
        with db.tx() as c:
            assert c.execute('SELECT task_id FROM driver_incentive_decisions WHERE order_id=%s', (oid,)).fetchone()['task_id'] == task
    finally:
        with psycopg.connect(base, autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(target)))
