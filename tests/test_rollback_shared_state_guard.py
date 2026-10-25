"""Prior writer refuses new operational contracts before any migration/write.

This proves fail-closed incompatibility, not operational rollback support for
shared closing. A current-aware rollback image requires its own later drill.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import pytest
from test_enbishan_dual_brand import dual
from test_operations import ops
from test_central_workdays import central

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'tools/rollback_application_overlay/app/db.py'
spec=importlib.util.spec_from_file_location('prior_shared_state_guard',PATH)
prior=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=prior
spec.loader.exec_module(prior)

class Read:
    def __init__(self, rows):self.rows=rows
    def fetchall(self):return self.rows
    def fetchone(self):return self.rows[0] if self.rows else None

class Catalog:
    def __init__(self, populated=None, available=()):
        self.populated=populated
        self.available=set(available)|({populated} if populated else set())
        self.queries=[]
    def execute(self, statement):
        query=statement.as_string() if hasattr(statement,'as_string') else statement
        self.queries.append(query)
        assert query.startswith('SELECT '),query
        if 'information_schema.tables' in query:return Read([{'table_name':t} for t in self.available])
        return Read([{'exists':1}] if self.populated and '"'+self.populated+'"' in query else [])

@pytest.mark.parametrize('table',prior.ROLLBACK_UNSUPPORTED_STATE_TABLES)
def test_prior_writer_refuses_each_new_contract_without_writes(table):
    c=Catalog(populated=table)
    with pytest.raises(RuntimeError,match='ROLLBACK_FEATURE_STATE_UNSUPPORTED'):
        prior.guard_rollback_feature_state(c)
    assert len(c.queries)==2


def test_old_or_empty_new_schema_remains_eligible_for_separate_prior_contract_drill():
    prior.guard_rollback_feature_state(Catalog())
    prior.guard_rollback_feature_state(Catalog(available=prior.ROLLBACK_UNSUPPORTED_STATE_TABLES))


def test_overlay_manifest_hash_binds_guard_and_records_incompatibility():
    manifest=json.loads((ROOT/'tools/rollback_application_overlay/manifest.json').read_text())
    assert manifest['files']['app/db.py']==hashlib.sha256(PATH.read_bytes()).hexdigest()
    assert manifest['rollback_feature_policy']['operational_after_feature_use'] is False
    assert manifest['rollback_feature_policy']['current_aware_image_certified'] is False


@pytest.mark.postgres
def test_real_profile_state_blocks_prior_guard_and_preserves_source(ops):
    from app import db
    with db.tx() as c:
        c.execute("INSERT INTO driver_duty_profiles(driver_id,minutes,updated_by) VALUES('ops-driver',480,'ops-owner')")
        before=c.execute('SELECT * FROM driver_duty_profiles ORDER BY driver_id').fetchall()
        with pytest.raises(RuntimeError,match='driver_duty_profiles'):
            prior.guard_rollback_feature_state(c)
        assert c.execute('SELECT * FROM driver_duty_profiles ORDER BY driver_id').fetchall()==before
    db.migrate()
    with db.tx() as c:
        assert c.execute('SELECT * FROM driver_duty_profiles ORDER BY driver_id').fetchall()==before
        event=c.execute("SELECT details FROM audit_events WHERE action='release.operational_data_preserved' ORDER BY created_at DESC,id DESC LIMIT 1").fetchone()['details']
        assert 'driver_duty_profiles' in event['checks']
        assert event['checks']['driver_duty_profiles']['count']==1


@pytest.mark.postgres
def test_real_shared_declaration_receipt_and_allocation_block_prior_writer_without_mutation(central,monkeypatch):
    from app import db,reporting
    from test_aggregate_driver_closure import submit_physical,rows
    monkeypatch.setattr(reporting,'generate_files',lambda *_:None)
    _,_,post,_,_=central
    _,driver,path,declared=submit_physical(central)
    tables=('shifts','shared_driver_closure_declarations','shared_driver_closure_receipts','shared_driver_closure_allocations')
    def refuse():
        with db.tx() as c:
            before={t:c.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in tables}
            with pytest.raises(RuntimeError,match='ROLLBACK_FEATURE_STATE_UNSUPPORTED'):
                prior.guard_rollback_feature_state(c)
            assert {t:c.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in tables}==before
        # The candidate may restart on this same database; the prior writer may not.
        db.migrate()
        with db.tx() as c:
            assert {t:c.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in tables}==before
            event=c.execute("SELECT details FROM audit_events WHERE action='release.operational_data_preserved' ORDER BY created_at DESC,id DESC LIMIT 1").fetchone()['details']
            assert set(tables)<=set(event['checks'])
    refuse()
    post('manager',path+'/physical-receipt',{'declaration_id':declared['declaration_id'],'received_halalas':700,'confirmed':True,'reference':'Synthetic rollback source receipt'})
    refuse()
    post('owner',path+'/physical-allocation',{'declaration_id':declared['declaration_id'],'legs':rows(driver['shifts']),'reason':'Synthetic accounting source attribution','confirmed':True})
    refuse()


@pytest.mark.postgres
def test_real_accepted_clarification_refuses_prior_writer_without_rewriting_finance(ops):
    from uuid import uuid4
    from app import db
    from test_financial_clarifications import setup_case,financial_source
    actor,post,_,_=ops
    oid=setup_case(ops)
    before=financial_source(oid)
    context=actor('accountant').get('/api/financial-clarifications/orders/'+oid).json()
    created=post('accountant','/api/financial-clarifications/orders/'+oid,{'version':context['order_version'],'assignee_id':'ops-manager','question':'Confirm the immutable source receipt'})['clarification']
    post('manager','/api/financial-clarifications/'+created['id']+'/decision',{'expected_version':1,'action':'accept','note':'I accept this evidence request'})
    with db.tx() as c:
        rows=c.execute('SELECT * FROM financial_case_clarifications ORDER BY id').fetchall()
        events=c.execute('SELECT * FROM financial_case_clarification_events ORDER BY id').fetchall()
        with pytest.raises(RuntimeError,match='financial_case_clarifications'):
            prior.guard_rollback_feature_state(c)
        assert c.execute('SELECT * FROM financial_case_clarifications ORDER BY id').fetchall()==rows
        assert c.execute('SELECT * FROM financial_case_clarification_events ORDER BY id').fetchall()==events
    assert financial_source(oid)==before
    db.migrate()
    with db.tx() as c:
        assert c.execute('SELECT * FROM financial_case_clarifications ORDER BY id').fetchall()==rows
        assert c.execute('SELECT * FROM financial_case_clarification_events ORDER BY id').fetchall()==events
        audit=c.execute("SELECT details FROM audit_events WHERE action='release.operational_data_preserved' ORDER BY created_at DESC,id DESC LIMIT 1").fetchone()['details']
        assert {'financial_case_clarifications','financial_case_clarification_events'}<=audit['checks'].keys()
    assert financial_source(oid)==before


def test_current_startup_inventory_covers_all_new_operational_evidence():
    from app import db
    assert set(prior.ROLLBACK_UNSUPPORTED_STATE_TABLES)<=set(db.PRESERVED_TABLES)
