"""Official endpoint packaging and conservative branch/device migration evidence."""
import base64
import io
import json
import zipfile

import pytest
from fastapi import HTTPException
from app import branch_printing, db
from test_operations import ops
from test_branch_printing import approve_test_device


def test_download_bundle_defaults_to_official_https_origin(monkeypatch):
    monkeypatch.delenv('PRINT_BRIDGE_ORIGIN', raising=False)
    with zipfile.ZipFile(io.BytesIO(branch_printing.bridge_bundle())) as archive:
        assert json.loads(archive.read('endpoint.json')) == {'origin':'https://order.kps.sa'}
        source=archive.read('KpsPrintBridge.cs').decode()
        assert 'onrender.com' not in source
        assert 'request.AllowAutoRedirect=false' in source
        assert 'SecureOrigin' in source and 'origin=Origin' in source
        assert 'File.Move(config,config+' not in source  # cancelled re-pair preserves credentials


@pytest.mark.parametrize('origin',[
    'http://order.kps.sa', 'https://user:secret@order.kps.sa',
    'https://order.kps.sa/api', 'https://order.kps.sa?token=1',
    'https://order.kps.sa#fragment', 'https://order.kps.sa:444',
    'https://order.kps.sa:invalid', '',
])
def test_insecure_or_non_origin_bridge_configuration_fails_closed(monkeypatch,origin):
    monkeypatch.setenv('PRINT_BRIDGE_ORIGIN',origin)
    with pytest.raises(HTTPException) as error:
        branch_printing.bridge_bundle()
    assert error.value.status_code == 503
    assert error.value.detail['code']=='PRINT_ORIGIN_INVALID'


def test_configurable_official_endpoint_is_packaged_from_trusted_setting(monkeypatch):
    monkeypatch.setenv('PRINT_BRIDGE_ORIGIN','https://admin.kps.sa/')
    with zipfile.ZipFile(io.BytesIO(branch_printing.bridge_bundle())) as archive:
        assert json.loads(archive.read('endpoint.json'))['origin']=='https://admin.kps.sa'


@pytest.mark.postgres
def test_domain_migration_requires_official_heartbeat_paper_approval_and_retiring_old_device(ops,monkeypatch):
    actor,post,_,_=ops
    # Isolated HTTP tests use testserver; production endpoint defaults remain HTTPS.
    monkeypatch.setenv('PRINT_BRIDGE_ORIGIN','https://testserver')
    old=post('owner','/api/printing/devices',{'branch_id':'ops-branch','label':'Old computer'})
    old_token=actor('customer').post('/api/printing/bridge/pair',json={
        'pairing_code':old['pairing_code'],'printer_name':'Synthetic old printer'}).json()['token']
    approve_test_device(actor,post,old['id'],old_token)
    with db.tx() as conn:
        conn.execute("UPDATE branch_print_devices SET last_api_host='old.onrender.com' WHERE id=%s",(old['id'],))
    new=post('owner','/api/printing/setup',{'branch_id':'ops-branch','label':'Replacement','reason':'Official domain migration'})
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(new['bundle']))) as archive:
        setup=json.loads(archive.read('setup.json'))
        assert setup['origin']=='https://testserver' and setup['branch_id']=='ops-branch'
    token=actor('customer').post('/api/printing/bridge/pair',json={
        'pairing_code':setup['pairing_code'],'printer_name':'Synthetic replacement'}).json()['token']
    def current():
        response=actor('owner').get('/api/printing/migration-status')
        assert response.status_code==200
        assert not response.json()['public_render_shutdown_authorized']
        return next(b for b in response.json()['branches'] if b['id']=='ops-branch')
    assert not current()['migrated']
    approve_test_device(actor,post,new['id'],token)
    # New pair has not invalidated the old registration or its audit trail.
    assert not current()['migrated'] and current()['active_devices']==2
    post('owner','/api/printing/devices/'+old['id']+'/revoke',{'reason':'Replacement test and paper confirmed'})
    assert current()['migrated'] and current()['active_devices']==1
    with db.tx() as conn:
        conn.execute("UPDATE branch_print_devices SET last_seen_at=now()-interval '2 minutes' WHERE id=%s",(new['id'],))
    assert not current()['migrated'] and current()['needs_attention']
    for who in ('driver','branch','customer'):
        assert actor(who).get('/api/printing/migration-status').status_code==403
    result=actor('other').get('/api/printing/migration-status').json()
    assert all(b['id']!='ops-branch' for b in result['branches'])
