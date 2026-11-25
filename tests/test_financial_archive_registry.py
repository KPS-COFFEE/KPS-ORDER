"""Financial source evidence survives private restoration without brand leakage."""
import json
from uuid import uuid4
from pathlib import Path
import importlib.util
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest
from app import db
from app.exports import TABLES,AUTH_TABLES,_scoped_sql,build_archive,restore_archive,verify_archive
from test_operations import ops
from test_enbishan_dual_brand import dual
from test_central_workdays import central
from test_backup import independent_restore_database

BANK_TABLES=('bank_reconciliation_batches','bank_reconciliation_receipts','bank_reconciliation_decisions',
             'bank_reconciliation_source_claims','bank_reconciliation_bank_lines','bank_reconciliation_bank_claims',
             'bank_reconciliation_line_proposals','bank_reconciliation_line_decisions')


def test_private_archive_registers_financial_sources_in_dependency_order():
    assert set(BANK_TABLES)<=set(TABLES)&set(db.PRESERVED_TABLES)
    assert 'audit_events' in db.PRESERVED_TABLES
    assert TABLES.index(BANK_TABLES[0])<TABLES.index(BANK_TABLES[1])<TABLES.index(BANK_TABLES[2])
    assert TABLES.index(BANK_TABLES[4])<TABLES.index(BANK_TABLES[5])
    assert 'bank_reconciliation_bank_lines' in AUTH_TABLES
    for table in set(BANK_TABLES)-AUTH_TABLES:
        clause,params=_scoped_sql(table,'seller-a')
        assert 'brand_id=%s' in clause and params==('seller-a',)


def test_cross_brand_staff_financial_audit_follows_source_scope():
    clause,params=_scoped_sql('audit_events','seller-b')
    assert params==('seller-b',)*7
    assert 'accounting.order_financial_treatment.%%' in clause
    assert "WHEN action LIKE 'employee.ticket.%%' THEN details->>'brand_id'=%s" in clause
    assert 'closure_reports WHERE brand_id=%s' in clause
    assert 'orders WHERE brand_id=%s' in clause
    assert 'bank_reconciliation_batches WHERE brand_id=%s' in clause
    old_clause,_=_scoped_sql('audit_events','seller-b',bank_table_available=False)
    assert 'bank_reconciliation_batches' not in old_clause


@pytest.mark.postgres
def test_actual_bank_evidence_private_restore_and_brand_export_scope(ops,monkeypatch,independent_restore_database):
    from test_manual_bank_matching import setup_sources,decision,receipt
    monkeypatch.setenv('KPS_MANUAL_BANK_MATCHING_ENABLED','true')
    actor,post,_,_=ops
    _,body,_,_=setup_sources(ops)
    base='/api/accounting/bank-reconciliation'
    batch=post('accountant',base,body);bid=batch['id']
    post('bankreviewer',base+'/'+bid+'/decision',decision())
    staged=post('accountant',base+'/'+bid+'/receipts',receipt(780));rid=staged['receipts'][0]['id']
    post('bankreviewer',base+'/'+bid+'/receipts/'+rid+'/decision',decision())
    movement=receipt(150,'standalone-archive')
    proposed=post('accountant',base+'/bank-lines',movement)
    post('bankreviewer',base+'/bank-lines/'+proposed['id']+'/decision',decision())
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        archive,manifest=build_archive(c,full=True)
        original={t:c.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(t))).fetchall() for t in BANK_TABLES}
    assert all(manifest['counts'][t] for t in BANK_TABLES)
    with db.tx() as c: business,_=build_archive(c,brand_id='ops-brand')
    exported,scoped=verify_archive(business)
    assert scoped['counts']['bank_reconciliation_batches']==1
    assert 'bank_reconciliation_bank_lines' not in scoped['table_order']
    assert len(json.loads(exported.read('data/bank_reconciliation_bank_claims.json')))==1
    with db.tx() as c: other,_=build_archive(c,brand_id='ops-other')
    _,scoped_other=verify_archive(other)
    assert not any(scoped_other['counts'][t] for t in set(BANK_TABLES)-AUTH_TABLES)
    schema='kps_restore_'+uuid4().hex
    try:
        restored=restore_archive(archive,target_url=independent_restore_database,target_schema=schema)
        assert restored['verified'] and restored['counts']==manifest['counts']
        with psycopg.connect(independent_restore_database,row_factory=dict_row) as c:
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            for t in BANK_TABLES:
                data=c.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(t))).fetchall()
                assert sorted(data,key=str)==sorted(original[t],key=str)
    finally:
        with psycopg.connect(independent_restore_database,autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))


@pytest.mark.postgres
def test_short_retained_float_restore_requires_assigned_followup_evidence(central,independent_restore_database):
    from test_aggregate_driver_closure import submit_physical
    _,_,post,_,_=central
    request=post('manager','/api/driver-floats',{'driver_id':'dual-driver','amount_halalas':20000,
        'reference':'Original fixed float handover','reason':'Synthetic standing float was actually handed once'})
    post('accountant','/api/driver-floats/'+request['id']+'/decision',{'approved':True,'confirmed':True,
        'reference':'Independent original fixed float decision'})
    _,driver,path,declaration=submit_physical(central)
    actual=post('manager',path+'/physical-receipt',{'declaration_id':declaration['declaration_id'],
        'received_halalas':0,'confirmed':True,'reference':'No bag cash; driver retains actual700 only'})
    assert actual['staffing_finished']
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        archive,manifest=build_archive(c,full=True)
        followup=c.execute("SELECT * FROM audit_events WHERE action='shared_driver.float_followup'").fetchone()
    assert followup and followup['details']['responsible_user_id'] and followup['details']['due_at']
    schema='kps_restore_'+uuid4().hex
    try:
        result=restore_archive(archive,target_url=independent_restore_database,target_schema=schema)
        assert result['verified'] and result['counts']==manifest['counts']
        with psycopg.connect(independent_restore_database,row_factory=dict_row) as c:
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            saved=c.execute('SELECT * FROM audit_events WHERE id=%s',(followup['id'],)).fetchone()
            assert saved==followup
            assert c.execute('SELECT count(*) n FROM shifts WHERE id=ANY(%s) AND staffing_ended_at IS NOT NULL',
                ([leg['id'] for leg in driver['shifts']],)).fetchone()['n']==len(driver['shifts'])
    finally:
        with psycopg.connect(independent_restore_database,autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))


def test_tender_documents_are_preserved_and_exports_follow_source_brand():
    tables=('collection_tender_correction_proposals','collection_tender_corrections','collection_tender_correction_rejections')
    for table in tables:
        assert table in TABLES and table in db.PRESERVED_TABLES
        clause,params=_scoped_sql(table,'seller-b')
        assert 'brand_id=%s' in clause and params==('seller-b',)


def test_all_declared_persistent_tables_have_explicit_archive_inventory():
    # Catch a late additive migration before any real backup can reject the
    # schema. The runtime unknown-table guard remains strict and unchanged.
    import re
    declared=set()
    for directory in ('migrations','app/multibrand_migrations'):
        for path in (db.ROOT/directory).glob('*.sql'):
            declared.update(re.findall(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w]+)',path.read_text(),re.I))
    assert declared-{'schema_migrations'}<=set(TABLES), sorted(declared-set(TABLES)-{'schema_migrations'})
    for name in ('bank_reconciliation_line_proposals','bank_reconciliation_line_decisions'):
        assert name in db.PRESERVED_TABLES
    assert TABLES.index('bank_reconciliation_line_proposals')<TABLES.index('bank_reconciliation_line_decisions')
    assert TABLES.index('bank_reconciliation_bank_lines')<TABLES.index('bank_reconciliation_line_decisions')
