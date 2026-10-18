"""Driver trip evidence is brand-scoped and survives isolated database restore."""
import json
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from app import db
from app.exports import TABLES, _scoped_sql, build_archive, restore_archive, verify_archive
from test_operations import ops
from test_backup import independent_restore_database

TRIP_TABLES = ('driver_work_trips', 'driver_work_trip_orders', 'driver_work_trip_events')


def test_trip_export_dependency_order_and_parent_scope():
    for parent in ('users', 'branches', 'shifts', 'orders'):
        assert TABLES.index(parent) < TABLES.index('driver_work_trips')
    for child in TRIP_TABLES[1:]:
        assert TABLES.index('driver_work_trips') < TABLES.index(child)
        assert _scoped_sql(child, 'brand-a') == (
            'trip_id IN (SELECT id FROM driver_work_trips WHERE brand_id=%s)', ('brand-a',))
    assert _scoped_sql('driver_work_trips', 'brand-a') == ('brand_id=%s', ('brand-a',))


@pytest.mark.postgres
def test_trip_evidence_export_and_independent_restore_preserves_live_source(ops, independent_restore_database):
    actor, post, order, shifts = ops
    _, sid = shifts()
    orders = [order(), order()]
    with db.tx() as c:
        for index, oid in enumerate(orders):
            c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,picked_up_at) VALUES(%s,%s,'ops-driver',%s,'picked_up','ops-manager',now())", ('backup-trip-task-'+str(index), oid, sid))
    departed = post('driver', '/api/driver-work-hours/depart',
                    {'shift_id':sid,'branch_id':'ops-branch','order_ids':orders}, key='backup-trip-depart')
    tid = departed['trip']['id']
    with db.tx() as c:
        c.execute("UPDATE driver_tasks SET status='delivered',delivered_at=now() WHERE order_id=ANY(%s)", (orders,))
    post('driver', '/api/driver-work-hours/'+tid+'/return', {'branch_id':'ops-branch'}, key='backup-trip-return')
    business = actor('owner').get('/api/admin/export')
    assert business.status_code == 200, business.text
    archive, manifest = verify_archive(business.content)
    assert {name:manifest['counts'][name] for name in TRIP_TABLES} == dict(zip(TRIP_TABLES, (1,2,2)))
    assert json.loads(archive.read('data/driver_work_trips.json'))[0]['id'] == tid
    outside = actor('other').get('/api/admin/export')
    assert outside.status_code == 200, outside.text
    outside_archive, other = verify_archive(outside.content)
    for table in TRIP_TABLES:
        assert other['counts'][table] == 0
        assert json.loads(outside_archive.read('data/'+table+'.json')) == []
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        contents, manifest = build_archive(c, full=True)
        source = {table:c.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table))).fetchall()
                  for table in TRIP_TABLES}
    newer = order('pickup', name='trip-backup-newer-order')
    target = 'kps_restore_'+uuid4().hex
    try:
        result = restore_archive(contents, target_url=independent_restore_database, target_schema=target)
        assert result['verified'] and result['counts'] == manifest['counts']
        with psycopg.connect(independent_restore_database, row_factory=dict_row) as c:
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
            for table in TRIP_TABLES:
                restored = c.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table))).fetchall()
                assert sorted(restored, key=str) == sorted(source[table], key=str)
                assert result['identifier_sha256'][table]
            assert c.execute('SELECT 1 FROM orders WHERE id=%s', (newer,)).fetchone() is None
        with db.tx() as c:
            assert c.execute('SELECT 1 FROM orders WHERE id=%s', (newer,)).fetchone()
            assert c.execute('SELECT count(*) n FROM driver_work_trip_events WHERE trip_id=%s', (tid,)).fetchone()['n'] == 2
    finally:
        with psycopg.connect(independent_restore_database, autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(target)))
