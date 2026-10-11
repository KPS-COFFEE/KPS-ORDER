"""Drivers execute assigned work; managers control scope and availability."""
import pytest
from app import db
from test_operations import ops

pytestmark = pytest.mark.postgres


def test_driver_cannot_pause_or_choose_shift_branches(ops):
    actor, post, order, shifts = ops
    with db.tx() as c:
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES('ops-second',2,'ops-second-source','ops-brand','Second','Test','{}')")
        c.execute("UPDATE users SET branch_ids=%s WHERE id IN ('ops-manager','ops-driver')", (db.json(['ops-branch','ops-second']),))
    manager = post('manager','/api/shifts',{'kind':'manager','branch_ids':['ops-branch','ops-second']})['shift']['id']
    post('driver','/api/shifts',{'kind':'driver','branch_ids':['ops-branch'],'manager_shift_id':manager},status=403)
    driver = post('driver','/api/shifts',{'kind':'driver','manager_shift_id':manager})['shift']['id']
    with db.tx() as c:
        assert set(c.execute('SELECT branch_ids FROM shifts WHERE id=%s',(driver,)).fetchone()['branch_ids']) == {'ops-branch','ops-second'}
    for available in (False, True):
        post('driver',f'/api/shifts/{driver}/availability',{'available':available},status=403)
        post('driver2',f'/api/shifts/{driver}/availability',{'available':available},status=403)
    post('manager',f'/api/shifts/{driver}/availability',{'available':False})
    with db.tx() as c:
        assert c.execute('SELECT available FROM shifts WHERE id=%s',(driver,)).fetchone()['available'] is False
    post('driver',f'/api/shifts/{driver}/availability',{'available':True},status=403)
    post('manager',f'/api/shifts/{driver}/availability',{'available':True})
    post('other',f'/api/shifts/{driver}/availability',{'available':False},status=404)
    with db.tx() as c:
        assert c.execute('SELECT available FROM shifts WHERE id=%s',(driver,)).fetchone()['available'] is True
        assert c.execute("SELECT count(*) n FROM audit_events WHERE actor_id='ops-driver' AND action='shift.availability'").fetchone()['n'] == 0
