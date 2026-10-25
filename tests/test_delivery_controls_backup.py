"""Populated shared actuals and workflow evidence survive a separate restore."""
from uuid import uuid4
import psycopg
import pytest
from test_enbishan_dual_brand import dual
from fastapi import HTTPException
from psycopg import sql
from psycopg.rows import dict_row
from app import db
from app.exports import TABLES, AUTH_TABLES, SHARED_PHYSICAL_TABLES, _scoped_sql, build_archive, restore_archive
from test_backup import independent_restore_database
from test_central_workdays import central, inspection
from test_aggregate_driver_closure import submit_physical, rows

PRESERVED = ('driver_duty_profiles','shared_driver_closure_declarations','shared_driver_closure_returns',
    'shared_driver_closure_receipts','shared_driver_closure_allocations',
    'financial_case_clarifications','financial_case_clarification_events')


def test_new_export_dependencies_and_explicit_combined_scope_exclusions():
    assert TABLES.index('users') < TABLES.index('driver_duty_profiles')
    assert TABLES.index('central_workdays') < TABLES.index('shared_driver_closure_declarations')
    assert TABLES.index('shared_driver_closure_declarations') < TABLES.index('shared_driver_closure_receipts') < TABLES.index('shared_driver_closure_allocations')
    assert TABLES.index('order_events') < TABLES.index('financial_case_clarifications') < TABLES.index('financial_case_clarification_events')
    assert SHARED_PHYSICAL_TABLES <= AUTH_TABLES
    assert _scoped_sql('driver_duty_profiles','kps') == ('driver_id IN (SELECT id FROM users WHERE brand_id=%s)',('kps',))
    assert _scoped_sql('financial_case_clarifications','kps') == ('order_id IN (SELECT id FROM orders WHERE brand_id=%s)',('kps',))
    with pytest.raises(ValueError): _scoped_sql('shared_driver_closure_declarations','kps')


@pytest.mark.postgres
def test_populated_physical_closure_profile_and_clarification_lossless_restore(central,monkeypatch,independent_restore_database):
    from app import reporting
    monkeypatch.setattr(reporting,'generate_files',lambda *_:None)
    _,actor,post,_,order=central
    manager,driver,path,declared=submit_physical(central)
    receipt=post('manager',path+'/physical-receipt',{'declaration_id':declared['declaration_id'],'received_halalas':700,'confirmed':True,'reference':'Backup actual receipt 001'})
    post('accountant',path+'/physical-allocation',{'declaration_id':declared['declaration_id'],'receipt_id':receipt['receipt_id'],
        'legs':rows(driver['shifts']),'reason':'Verified original brand evidence for preservation','confirmed':True})
    second=post('driver','/api/central-operations/workdays',{'kind':'driver','manager_shift_id':manager['shift']['id'],'vehicle_inspection':inspection(1010)})
    second_path='/api/central-operations/workdays/'+second['shift']['id']
    pending=post('driver',second_path+'/request',{'reason':'Synthetic second shift ended'})
    post('manager',second_path+'/decision',{'request_id':pending['shift']['closure_authorization']['id'],'decision':'approved','reason':'Confirm second physical shift ended'})
    post('driver',second_path+'/closing',{})
    dec2=post('driver',second_path+'/physical-submit',{'cash_halalas':0,'card_halalas':0,'vehicle_inspection':inspection(1020)})
    post('manager',second_path+'/physical-receipt',{'declaration_id':dec2['declaration_id'],'received_halalas':0,'confirmed':True,'reference':'Backup actual zero receipt 002'})
    current=post('driver','/api/central-operations/workdays',{'kind':'driver','manager_shift_id':manager['shift']['id'],'vehicle_inspection':inspection(1020)})
    oid=order('kps')
    with db.tx() as c:
        c.execute("INSERT INTO driver_duty_profiles(driver_id,minutes,updated_by) VALUES('dual-driver',480,'dual-owner')")
        db.event(c,oid,'dual-manager','financial_case.opened',{'kind':'collection_shortfall','reason':'Synthetic original accounting follow-up'})
        event=c.execute("SELECT id FROM order_events WHERE order_id=%s AND event_type='financial_case.opened'",(oid,)).fetchone()['id']
        c.execute("INSERT INTO financial_case_clarifications(id,order_id,case_event_id,requested_by,assignee_id,question,source_order_version,status) VALUES('backup-clarification',%s,%s,'dual-accountant','dual-manager','Provide original payment evidence',1,'requested')",(oid,event))
        c.execute("INSERT INTO financial_case_clarification_events(id,clarification_id,version,action,actor_id,assignee_id,to_status,note,evidence) VALUES('backup-clarification-event','backup-clarification',1,'requested','dual-accountant','dual-manager','requested','Original evidence request','[\"Original receipt reference\"]')")
    with pytest.raises(HTTPException) as error:
        with db.tx() as c:build_archive(c,brand_id='kps',full=False)
    assert error.value.detail['code']=='EXPORT_SHARED_PHYSICAL_SCOPE'
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        contents,manifest=build_archive(c,full=True)
        source={name:c.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(name))).fetchall() for name in PRESERVED}
        markers=c.execute("SELECT id,status,staffing_ended_at FROM shifts WHERE user_id='dual-driver' ORDER BY id").fetchall()
    assert manifest['counts']['shared_driver_closure_declarations']==2
    assert manifest['counts']['shared_driver_closure_receipts']==2
    assert manifest['counts']['shared_driver_closure_allocations']==1
    assert manifest['counts']['financial_case_clarification_events']==1
    schema='kps_restore_'+uuid4().hex
    try:
        result=restore_archive(contents,target_url=independent_restore_database,target_schema=schema)
        assert result['verified'] and result['counts']==manifest['counts']
        with psycopg.connect(independent_restore_database,row_factory=dict_row) as c:
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            for name in PRESERVED:
                restored=c.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(name))).fetchall()
                assert sorted(restored,key=str)==sorted(source[name],key=str)
            assert c.execute("SELECT id,status,staffing_ended_at FROM shifts WHERE user_id='dual-driver' ORDER BY id").fetchall()==markers
            enabled=c.execute("SELECT tgenabled FROM pg_trigger WHERE tgrelid='shifts'::regclass AND tgname='guarded_staffing_end'").fetchone()
            assert enabled['tgenabled']=='O'
        with db.tx() as c:
            assert c.execute('SELECT status FROM shifts WHERE id=%s',(current['shift']['id'],)).fetchone()['status']=='open'
    finally:
        with psycopg.connect(independent_restore_database,autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
