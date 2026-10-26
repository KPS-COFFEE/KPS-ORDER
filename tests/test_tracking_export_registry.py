"""Private physical tracking evidence must survive backup without brand leakage."""
import hashlib
import importlib.util
import json
from pathlib import Path
from app import db,exports

ROOT=Path(__file__).resolve().parents[1]
TABLES={'driver_tracking_points','driver_trip_stops','driver_route_eta_state','driver_route_eta_expectations'}


def test_current_and_prior_archive_inventory_scope_and_fk_order():
    path=ROOT/'tools/rollback_application_overlay/app/exports.py'
    spec=importlib.util.spec_from_file_location('app._tracking_rollback_exports',path)
    prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
    for module in (exports,prior):
        assert TABLES<=set(module.TABLES)
        assert all(module.TABLES.count(table)==1 for table in TABLES)
        assert TABLES<=module.AUTH_TABLES
        assert module.TABLES.index('sessions')<module.TABLES.index('driver_tracking_points')
        assert module.TABLES.index('driver_work_trips')<module.TABLES.index('driver_trip_stops')
        assert module._scoped_sql('driver_tracking_points','kps')==('brand_id=%s',('kps',))
        assert module._scoped_sql('driver_trip_stops','enbishan')==('brand_id=%s',('enbishan',))
        for table in ('driver_route_eta_state','driver_route_eta_expectations'):
            assert module._scoped_sql(table,'kps')==('FALSE',())
        for table in TABLES:assert module._scoped_sql(table,None)==('TRUE',())
    assert TABLES<=set(db.PRESERVED_TABLES)


def test_prior_writer_guard_and_manifest_bind_exact_tracking_inventory():
    path=ROOT/'tools/rollback_application_overlay/app/db.py'
    spec=importlib.util.spec_from_file_location('app._tracking_rollback_db',path)
    prior=importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name]=prior
    spec.loader.exec_module(prior)
    assert TABLES<=set(prior.PRESERVED_TABLES)
    assert TABLES<=set(prior.ROLLBACK_UNSUPPORTED_STATE_TABLES)
    manifest=json.loads((ROOT/'tools/rollback_application_overlay/manifest.json').read_text())
    for name in ('app/db.py','app/exports.py'):
        assert manifest['files'][name]==hashlib.sha256((ROOT/'tools/rollback_application_overlay'/name).read_bytes()).hexdigest()


import pytest
from test_tracking_ingest import tracking_data,batch_body


@pytest.mark.postgres
def test_private_full_archive_keeps_tracking_clock_and_scoped_business_omits_it(tracking_data):
    import io,zipfile
    client,user=tracking_data
    body=batch_body(300)
    response=client.post('/api/operations/driver-location-batch',json=body)
    assert response.status_code==200,response.text
    with db.tx() as c:
        payload,full=exports.build_archive(c,full=True)
        _,business=exports.build_archive(c,brand_id='kps')
    assert full['counts']['driver_tracking_points']==1
    assert 'driver_tracking_points' not in business['counts']
    assert TABLES<=set(business['excluded_tables'])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        rows=json.loads(archive.read('data/driver_tracking_points.json'))
    assert rows[0]['id']==body['points'][0]['id']
    assert rows[0]['session_id']=='track-session'
    assert rows[0]['captured_at']==body['points'][0]['captured_at']
    assert rows[0]['captured_at']!=rows[0]['received_at']


def test_tracking_routers_registered_without_enabling_customer_tracking():
    from app.main import app
    routes={(path,method.upper()) for path,methods in app.openapi()['paths'].items() for method in methods}
    assert ('/api/operations/driver-location-batch','POST') in routes
    assert ('/api/operations/map-services/history','GET') in routes
    assert ('/api/operations/map-services/trip-stops/current','GET') in routes
    assert ('/api/operations/route-eta','POST') in routes
    from app.dispatch_map import customer_tracking_enabled
    assert customer_tracking_enabled() is False
