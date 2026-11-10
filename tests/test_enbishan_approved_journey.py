"""Delivery-only defaults, explicit opt-in and unchanged shared brand behavior."""
from copy import deepcopy
from pathlib import Path
import pytest
from app import catalog, db

@pytest.mark.parametrize('service,key',[('pickup','pickup'),('car','car_pickup')])
def test_enbishan_pickup_requires_explicit_owner_opt_in(monkeypatch,service,key):
    branch={'id':'synthetic','brand_id':'enbishan','city':'حفر الباطن','branch_no':1,
            'settings':{'active':True,'pickup_enabled':True,'car_enabled':True,
                        'operational_data_verified':True,'opening_hours':{'all':'fixture'},'payment_methods':['cash','card']}}
    before=deepcopy(branch)
    monkeypatch.setattr(catalog,'_hours_open',lambda *args:True)
    assert catalog.branch_service(branch,service,brand_settings={})[1]=='BRAND_SERVICE_DISABLED'
    assert catalog.branch_service(branch,service,brand_settings={'services':{key:False}})[1]=='BRAND_SERVICE_DISABLED'
    assert catalog.branch_service(branch,service,brand_settings={'services':{key:True}})==(True,None,None)
    branch['brand_id']='kps'
    assert catalog.branch_service(branch,service,brand_settings={})==(True,None,None)
    branch['brand_id']='enbishan'
    assert branch==before

def test_service_migration_preserves_delivery_and_other_brands(database):
    sql=(Path(__file__).resolve().parents[1]/'migrations/103_enbishan_pickup_default_paused.sql').read_text()
    with db.tx() as conn:
        kps=conn.execute("SELECT settings FROM brands WHERE id='kps'").fetchone()['settings']
        conn.execute("INSERT INTO brands(id,name,settings) VALUES ('enbishan','Synthetic',%s) ON CONFLICT(id) DO UPDATE SET settings=EXCLUDED.settings",
                     (db.json({'services':{'delivery':False,'pickup':True,'car_pickup':True},'sentinel':'preserve'}),))
        conn.execute(sql)
        settings=conn.execute("SELECT settings FROM brands WHERE id='enbishan'").fetchone()['settings']
        assert settings=={'services':{'delivery':False,'pickup':False,'car_pickup':False},'sentinel':'preserve'}
        assert conn.execute("SELECT settings FROM brands WHERE id='kps'").fetchone()['settings']==kps
