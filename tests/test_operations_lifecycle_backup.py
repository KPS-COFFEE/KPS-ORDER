"""Scoped archives and full restoration retain courtesy and support decisions."""
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


TABLE_NAMES=('complimentary_authorizations','driver_support_requests')


def test_lifecycle_export_dependencies_and_brand_scope():
    for table in TABLE_NAMES:
        assert TABLES.index(table)>TABLES.index('users')
        assert TABLES.index(table)>TABLES.index('branches')
        assert _scoped_sql(table,'synthetic-brand')==('brand_id=%s',('synthetic-brand',))
        assert _scoped_sql(table,None)==('TRUE',())
    assert TABLES.index('complimentary_authorizations')>TABLES.index('orders')
    assert TABLES.index('driver_support_requests')>TABLES.index('shifts')


@pytest.mark.postgres
def test_nonempty_courtesy_and_driver_support_survive_full_restore(
        ops,tmp_path,independent_restore_database):
    _,_,order,shifts=ops
    manager,driver=shifts()
    original,executed=order(),order()
    target_shift=db.new_id('backup_evening_')
    approval_id,support_id=db.new_id('backup_courtesy_'),db.new_id('backup_support_')
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('backup-evening-manager','TEST evening','delivery_manager','ops-brand','[\"ops-branch\"]')")
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager','backup-evening-manager','ops-brand','[\"ops-branch\"]')",(target_shift,))
        payload={'kind':'compensation','original_order_id':original,'original_invoice_number':'TEST-ORIGINAL',
                 'reason':'TEST replacement approval','customer':{'name':'TEST recipient'},
                 'items':[{'product_id':'ops-product','quantity':1}]}
        c.execute("""INSERT INTO complimentary_authorizations(id,brand_id,branch_id,authorized_by,payload,
            executed_order_id,executed_by,executed_at) VALUES(%s,'ops-brand','ops-branch','ops-owner',%s,
            %s,'ops-manager',clock_timestamp())""",(approval_id,db.json(payload),executed))
        # Accepted responsibility retains its original balances and task list;
        # copying the record must not manufacture any receipt or cash transfer.
        snapshot={'driver_id':'ops-driver','driver_shift_id':driver,'cash_expected_halalas':1000,
                  'pending_order_ids':[original],'accepted_orders':[original],
                  'note':'TEST operational responsibility only; cash stays with driver'}
        c.execute("""INSERT INTO driver_support_requests(id,driver_shift_id,from_shift_id,to_shift_id,
            brand_id,branch_ids,reason,status,requested_by,accepted_by,operational_snapshot,accepted_at,group_id)
            VALUES(%s,%s,%s,%s,'ops-brand','["ops-branch"]','TEST evening support','accepted',
                   'ops-manager','backup-evening-manager',%s,clock_timestamp(),'TEST-support-group')""",
                  (support_id,driver,manager,target_shift,db.json(snapshot)))
        source={table:c.execute(sql.SQL('SELECT * FROM {} ORDER BY id').format(sql.Identifier(table))).fetchall()
                for table in TABLE_NAMES}
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        raw,_=build_archive(c,brand_id='ops-brand')
        archive,scoped=verify_archive(raw)
        for table in TABLE_NAMES:
            assert scoped['counts'][table]==1
            assert json.loads(archive.read(f'data/{table}.json'))[0]['brand_id']=='ops-brand'
        _,other=build_archive(c,brand_id='ops-other')
        assert all(other['counts'][table]==0 for table in TABLE_NAMES)
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        raw,manifest=build_archive(c,full=True)
    target='kps_restore_'+uuid4().hex
    try:
        result=restore_archive(raw,target_url=independent_restore_database,target_schema=target,
                               asset_directory=tmp_path/'restored-assets')
        assert result['verified'] and result['counts']==manifest['counts']
        with psycopg.connect(independent_restore_database,row_factory=dict_row) as c:
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
            for table in TABLE_NAMES:
                assert c.execute(sql.SQL('SELECT * FROM {} ORDER BY id').format(sql.Identifier(table))).fetchall()==source[table]
            assert c.execute('SELECT id FROM orders WHERE id=%s',(original,)).fetchone()['id']==original
            assert c.execute('SELECT id FROM orders WHERE id=%s',(executed,)).fetchone()['id']==executed
            assert c.execute('SELECT user_id FROM shifts WHERE id=%s',(driver,)).fetchone()['user_id']=='ops-driver'
            assert c.execute('SELECT count(*) n FROM collections').fetchone()['n']==0
            assert c.execute('SELECT count(*) n FROM custody_transfers').fetchone()['n']==0
        # Restore is isolated and leaves the source archive evidence untouched.
        with db.tx() as c:
            for table in TABLE_NAMES:
                assert c.execute(sql.SQL('SELECT * FROM {} ORDER BY id').format(sql.Identifier(table))).fetchall()==source[table]
    finally:
        with psycopg.connect(independent_restore_database,autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(target)))
