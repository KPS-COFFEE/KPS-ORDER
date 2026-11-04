"""Startup must roll back any migration that alters existing trip/print evidence."""
import pytest
from psycopg import sql

from app import db
from test_operations import ops


MUTATIONS = {
    'driver_work_trips': "UPDATE driver_work_trips SET departure_evidence='{\"unexpected\":true}'",
    'driver_work_trip_orders': "INSERT INTO driver_work_trip_orders(trip_id,order_id) SELECT id,%s FROM driver_work_trips",
    'driver_work_trip_events': "INSERT INTO driver_work_trip_events(id,trip_id,actor_id,action,branch_id,evidence) SELECT 'unexpected-trip-event',id,'ops-driver','depart','ops-branch','{}' FROM driver_work_trips",
    'branch_print_devices': "UPDATE branch_print_devices SET label='Unexpected migration change'",
    'branch_print_jobs': "UPDATE branch_print_jobs SET status='cancelled',finished_at=now()",
}


def rows(conn):
    return {name:conn.execute(sql.SQL('SELECT * FROM {} ORDER BY 1').format(sql.Identifier(name))).fetchall()
            for name in MUTATIONS}


@pytest.mark.postgres
@pytest.mark.parametrize('table', MUTATIONS)
def test_startup_rejects_and_rolls_back_trip_or_printer_mutation(ops, monkeypatch, table):
    _, post, order, shifts = ops
    _, shift = shifts()
    first, second = order(), order()
    with db.tx() as conn:
        conn.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,picked_up_at) VALUES('migration-trip-task',%s,'ops-driver',%s,'picked_up','ops-manager',now())", (first,shift))
    post('driver','/api/driver-work-hours/depart',
         {'shift_id':shift,'branch_id':'ops-branch','order_ids':[first]},key='migration-proof-trip')
    with db.tx() as conn:
        conn.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,created_by) VALUES('migration-printer','ops-brand','ops-branch','Synthetic migration printer','ops-owner')")
        conn.execute("INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,order_id,kind,payload,requested_by) VALUES('migration-print-job','ops-brand','ops-branch','migration-printer',%s,'order','{}','ops-owner')", (first,))
        original = rows(conn)
    def mutate(conn, **_kwargs):
        conn.execute(MUTATIONS[table], (second,) if table=='driver_work_trip_orders' else ())
    monkeypatch.setattr(db,'apply_migrations',mutate)
    with pytest.raises(RuntimeError,match='existing operational data changed'):
        db.migrate()
    # Independent row comparison proves the rejected transaction retained all
    # identifiers, keys, payloads and immutable events; no old backup is restored.
    with db.tx() as conn:
        assert rows(conn) == original
