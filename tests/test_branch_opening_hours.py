"""KPS owner-approved 24-hour branch opening except Friday 05:30–12:30."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4

import pytest
from app import admin, catalog, db, service_hours
from test_admin import owner


def local(value):
    return datetime.fromisoformat(value).replace(tzinfo=service_hours.RIYADH)


def test_approved_seed_hours_identical_for_all_20_branches_and_services_still_disabled():
    rows=json.loads((Path(__file__).resolve().parents[1]/'data/branches.json').read_text())['branches']
    assert len(rows)==20
    for branch in rows:
        assert branch['settings']['opening_hours'] == service_hours.DEFAULT_KPS_BRANCH_OPENING_HOURS
        assert branch['settings']['pickup_enabled'] is False
        assert branch['settings']['car_enabled'] is False
        assert branch['settings']['delivery_enabled'] is False
    assert admin.hours(service_hours.DEFAULT_KPS_BRANCH_OPENING_HOURS)


@pytest.mark.parametrize(('time','is_open'),[
    ('2026-10-08T12:00:00',True),     # Thursday (24h)
    ('2026-10-09T00:00:00',True),     # Friday midnight
    ('2026-10-09T05:29:59',True),
    ('2026-10-09T05:30:00',False),    # Exact Friday closure
    ('2026-10-09T10:00:00',False),
    ('2026-10-09T12:29:59',False),
    ('2026-10-09T12:30:00',True),     # Exact Friday reopening at noon
    ('2026-10-09T23:59:59',True),
    ('2026-10-10T00:00:00',True),     # Saturday midnight
    ('2026-10-11T16:00:00',True),     # Sunday
    ('2026-10-12T04:00:00',True),     # Monday
])
def test_friday_boundary_and_other_days_are_riyadh(time,is_open):
    assert service_hours.hours_open(service_hours.DEFAULT_KPS_BRANCH_OPENING_HOURS,local(time)) is is_open


def configured_branch():
    return {'id':'synthetic-kps-branch','brand_id':'kps','city':'حفر الباطن',
            'settings':{'active':True,'pickup_enabled':True,'car_enabled':True,
                'delivery_enabled':True,'operational_data_verified':True,
                'opening_hours':deepcopy(service_hours.DEFAULT_KPS_BRANCH_OPENING_HOURS),
                'delivery_hours':deepcopy(service_hours.DEFAULT_DELIVERY_HOURS),
                'payment_methods':['cash','card'],'delivery_fee_halalas':0,
                'location':{'lat':28,'lng':45},'location_verified':True,
                'coverage':{'type':'radius','center':{'lat':28,'lng':45},'radius_meters':10000}}}


def test_branch_closure_blocks_new_orders_independently_of_delivery_window():
    branch=configured_branch()
    for kind in ('delivery','pickup','car'):
        assert catalog.branch_service(branch,kind,local('2026-10-09T05:29:59'))[0] is (kind!='delivery')
        assert catalog.branch_service(branch,kind,local('2026-10-09T05:30:00'))[1]=='BRANCH_CLOSED'
        assert catalog.branch_service(branch,kind,local('2026-10-09T12:29:59'))[1]=='BRANCH_CLOSED'
        assert catalog.branch_service(branch,kind,local('2026-10-09T16:00:00'))[0]
    assert catalog.branch_service(branch,'delivery',local('2026-10-09T03:59:59'))[0]
    assert catalog.branch_service(branch,'delivery',local('2026-10-09T12:30:00'))[1]=='DELIVERY_CLOSED'
    assert catalog.branch_service(branch,'pickup',local('2026-10-09T12:30:00'))[0]
    assert catalog.branch_service(branch,'car',local('2026-10-09T12:30:00'))[0]


@pytest.mark.postgres
def test_current_branches_have_hours_without_activation_and_owner_override_survives(client):
    with db.tx() as conn:
        branches=conn.execute("SELECT * FROM branches WHERE brand_id='kps' ORDER BY branch_no").fetchall()
        assert len(branches)==20
        assert all(b['settings']['opening_hours']==service_hours.DEFAULT_KPS_BRANCH_OPENING_HOURS for b in branches)
        assert all(not b['settings'].get(s) for b in branches for s in ('pickup_enabled','car_enabled','delivery_enabled'))
        override=[{'days':[0],'start':'09:00','end':'12:00'}]
        conn.execute("UPDATE branches SET settings=jsonb_set(settings,'{opening_hours}',%s::jsonb) WHERE id='kps-branch-1'",
                     (db.json(override),))
        conn.execute("UPDATE branches SET settings=jsonb_set(settings,'{opening_hours}','[]'::jsonb) WHERE id='kps-branch-2'")
        conn.execute("UPDATE branches SET settings=jsonb_set(settings,'{opening_hours}','null'::jsonb) WHERE id='kps-branch-3'")
        # Simulate the first forward migration against imported older branch data.
        conn.execute("DELETE FROM schema_migrations WHERE version='016_kps_branch_opening_hours.sql'")
    db.migrate()
    with db.tx() as conn:
        rows=conn.execute("SELECT id,settings FROM branches WHERE brand_id='kps' ORDER BY branch_no").fetchall()
        assert rows[0]['settings']['opening_hours']==override
        assert rows[1]['settings']['opening_hours']==[]
        assert rows[2]['settings']['opening_hours']==service_hours.DEFAULT_KPS_BRANCH_OPENING_HOURS
        assert conn.execute("SELECT count(*) AS n FROM audit_events WHERE action='branch.owner_opening_hours'").fetchone()['n']==1
    db.migrate()
    with db.tx() as conn:
        assert conn.execute("SELECT settings FROM branches WHERE id='kps-branch-2'").fetchone()['settings']['opening_hours']==[]


@pytest.mark.postgres
def test_new_kps_branch_inherits_hours_without_enabling_service(owner):
    client, mutate=owner
    response=mutate('POST','/api/admin/branches',
                    {'branch_no':21,'name':'فرع اختبار جديد','city':'حفر الباطن'})
    assert response.status_code==200,response.text
    settings=response.json()['branch']['settings']
    assert settings['opening_hours']==service_hours.DEFAULT_KPS_BRANCH_OPENING_HOURS
    assert settings['pickup_enabled'] is False
    assert settings['car_enabled'] is False
    assert settings['delivery_enabled'] is False
    assert settings['ordering_verified'] is False
