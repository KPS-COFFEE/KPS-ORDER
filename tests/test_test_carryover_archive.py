import pytest
import psycopg
from app import db
from tools.archive_test_carryover import archive
from tests.test_operations import ops


def seed():
    with db.tx() as c:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,closed_at) VALUES('test-source','driver','ops-driver','ops-brand','[\"ops-branch\"]','closed',now())")
        c.execute("INSERT INTO retained_carryovers(id,from_shift_id,user_id,amount_halalas,approved_by,reason) VALUES('test-carry','test-source','ops-driver',30000,'ops-owner','historical test')")
        row=c.execute("SELECT * FROM retained_carryovers WHERE id='test-carry'").fetchone()
    return {k:row[k] for k in ('id','user_id','from_shift_id','amount_halalas','created_at')}


def test_archived_test_carry_does_not_enter_next_shift_and_history_survives(ops):
    actor,post,order,shifts=ops
    expected=seed()
    with db.tx() as c:
        before=c.execute("SELECT to_jsonb(s) snapshot FROM shifts s WHERE id='test-source'").fetchone()['snapshot']
        assert archive(c,owner_id='ops-owner',expected=expected,reason='Owner classified exact row as test')['already_archived'] is False
        assert archive(c,owner_id='ops-owner',expected=expected,reason='Owner classified exact row as test')['already_archived'] is True
    manager,driver=shifts()
    assert actor('manager').get('/api/shifts/'+driver).json()['shift']['opening_halalas']==0
    with db.tx() as c:
        row=c.execute("SELECT * FROM retained_carryovers WHERE id='test-carry'").fetchone()
        assert row['amount_halalas']==30000 and row['from_shift_id']=='test-source'
        assert row['to_shift_id'] is None and row['carried_at'] is None and row['archived_test_at'] is not None
        assert c.execute("SELECT to_jsonb(s) snapshot FROM shifts s WHERE id='test-source'").fetchone()['snapshot']==before
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='carryover.archive_owner_classified_test'").fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM custody_events").fetchone()['n']==0
        assert c.execute("SELECT count(*) n FROM collections").fetchone()['n']==0
    # Old application code cannot silently consume archived cash after rollback.
    with pytest.raises(psycopg.errors.CheckViolation), db.tx() as c:
        c.execute("UPDATE retained_carryovers SET to_shift_id=%s,carried_at=now() WHERE id='test-carry'",(driver,))


def test_archive_requires_owner_exact_snapshot_and_closed_driver(ops):
    expected=seed()
    for owner,values in [('ops-manager',expected),('ops-other',expected),('ops-owner',{**expected,'amount_halalas':1})]:
        with pytest.raises(ValueError), db.tx() as c:
            archive(c,owner_id=owner,expected=values,reason='Owner classified test')
    with db.tx() as c:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES('active-driver','driver','ops-driver','ops-brand','[\"ops-branch\"]')")
    with pytest.raises(ValueError,match='active shift'), db.tx() as c:
        archive(c,owner_id='ops-owner',expected=expected,reason='Owner classified test')
    with db.tx() as c:
        assert c.execute("SELECT archived_test_at FROM retained_carryovers WHERE id='test-carry'").fetchone()['archived_test_at'] is None


def test_consumed_carryover_cannot_be_reclassified(ops):
    expected=seed()
    ops[3]()
    with pytest.raises(ValueError,match='consumed'), db.tx() as c:
        archive(c,owner_id='ops-owner',expected=expected,reason='Owner classified test')


def test_archive_metadata_must_be_complete(ops):
    seed()
    with pytest.raises(psycopg.errors.CheckViolation), db.tx() as c:
        c.execute("UPDATE retained_carryovers SET archived_test_at=now(),archived_test_by='ops-owner' WHERE id='test-carry'")
