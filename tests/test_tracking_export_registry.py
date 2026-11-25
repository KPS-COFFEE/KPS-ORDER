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
    # The deployed b528 baseline understands tracking; its active compatibility
    # inventory must preserve those private rows and remain hash-bound.
    path=ROOT/'tools/rollback_application_overlay/app/db.py'
    spec=importlib.util.spec_from_file_location('app._tracking_rollback_db',path)
    prior=importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name]=prior
    spec.loader.exec_module(prior)
    assert TABLES<=set(prior.PRESERVED_TABLES)
    assert all(prior.PRESERVED_TABLES.count(table)==1 for table in TABLES)
    manifest=json.loads((ROOT/'tools/rollback_application_overlay/manifest.json').read_text())
    assert manifest['baseline_sha']=='b528f14c5ea93e32a15a5c37f43f7e93477136e8'
    for name in ('app/db.py','app/exports.py'):
        assert manifest['files'][name]==hashlib.sha256((ROOT/'tools/rollback_application_overlay'/name).read_bytes()).hexdigest()
    # Preserve the original unsupported-writer witness. Moving the supported
    # fallback forward must never make the historical 80a writer safe to use.
    historical=ROOT/'tests/fixtures/rollback_80a'
    spec=importlib.util.spec_from_file_location('app._tracking_historical_db',historical/'db.py')
    old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)
    assert TABLES<=set(old.PRESERVED_TABLES)
    assert TABLES<=set(old.ROLLBACK_UNSUPPORTED_STATE_TABLES)
    frozen=json.loads((historical/'manifest.json').read_text())
    assert frozen['baseline_sha']=='80a1d727f6695c4ce3685e726ab5b34674e1cfa0'
    assert frozen['files']['app/db.py']=='1766f5c71ac2be2d25d4e80ec0d7c33e471fe0f5f771ac36db55a91e993facc6'
    assert frozen['files']['app/db.py']==hashlib.sha256((historical/'db.py').read_bytes()).hexdigest()
    from test_rollback_shared_state_guard import Catalog
    for table in TABLES:
        connection=Catalog(populated=table)
        with pytest.raises(RuntimeError,match='ROLLBACK_FEATURE_STATE_UNSUPPORTED.*'+table):
            old.guard_rollback_feature_state(connection)
        assert len(connection.queries)==2  # Read-only discovery and refusal.


import pytest
from test_tracking_ingest import tracking_data,batch_body


@pytest.mark.postgres
def test_private_full_archive_keeps_tracking_clock_and_scoped_business_omits_it(tracking_data):
    import io,zipfile
    client,user=tracking_data
    body=batch_body(300)
    response=client.post('/api/operations/driver-location-batch',json=body)
    assert response.status_code==200,response.text
    path=ROOT/'tools/rollback_application_overlay/app/exports.py'
    spec=importlib.util.spec_from_file_location('app._tracking_populated_rollback_exports',path)
    prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
    archives=[]
    with db.tx() as c:
        for module in (exports,prior):
            payload,full=module.build_archive(c,full=True)
            _,business=module.build_archive(c,brand_id='kps')
            archives.append((payload,full,business))
    decoded=[]
    for payload,full,business in archives:
        assert full['counts']['driver_tracking_points']==1
        assert 'driver_tracking_points' not in business['counts']
        assert TABLES<=set(business['excluded_tables'])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            rows=json.loads(archive.read('data/driver_tracking_points.json'))
        assert rows[0]['id']==body['points'][0]['id']
        assert rows[0]['session_id']=='track-session'
        assert rows[0]['captured_at']==body['points'][0]['captured_at']
        assert rows[0]['captured_at']!=rows[0]['received_at']
        decoded.append(rows)
    assert decoded[0]==decoded[1]


def test_tracking_routers_registered_without_enabling_customer_tracking():
    from app.main import app
    routes={(path,method.upper()) for path,methods in app.openapi()['paths'].items() for method in methods}
    assert ('/api/operations/driver-location-batch','POST') in routes
    assert ('/api/operations/map-services/history','GET') in routes
    assert ('/api/operations/map-services/trip-stops/current','GET') in routes
    assert ('/api/operations/route-eta','POST') in routes
    from app.dispatch_map import customer_tracking_enabled
    assert customer_tracking_enabled() is False
