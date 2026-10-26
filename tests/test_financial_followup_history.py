import pytest
from psycopg.errors import RaiseException
from app import db
from test_financial_clarifications import setup_case

@pytest.mark.postgres
def test_case_evidence_and_administrative_followup_cannot_be_rewritten(ops):
    oid=setup_case(ops)
    with db.tx() as c:
        event=c.execute("SELECT id FROM order_events WHERE order_id=%s AND event_type='financial_case.opened'",(oid,)).fetchone()['id']
        db.audit(c,'ops-manager','employee.ticket.followup','employee_ticket','test-existing-task',{'version':1,'responsible_user_id':'ops-manager'})
        audit=c.execute("SELECT id FROM audit_events WHERE entity_id='test-existing-task'").fetchone()['id']
    for query,args in [
        ('UPDATE order_events SET details=%s WHERE id=%s',(db.json({'untrue':True}),event)),
        ('DELETE FROM order_events WHERE id=%s',(event,)),
        ('UPDATE audit_events SET details=%s WHERE id=%s',(db.json({'untrue':True}),audit)),
        ('DELETE FROM audit_events WHERE id=%s',(audit,)),
    ]:
        with pytest.raises(RaiseException):
            with db.tx() as c:c.execute(query,args)
    with db.tx() as c:
        assert c.execute('SELECT details FROM audit_events WHERE id=%s',(audit,)).fetchone()['details']['responsible_user_id']=='ops-manager'
        assert c.execute('SELECT id FROM order_events WHERE id=%s',(event,)).fetchone()
