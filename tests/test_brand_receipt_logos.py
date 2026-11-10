"""Canonical receipt identity and unchanged layout across the two brands."""
from copy import deepcopy
from datetime import datetime, timezone
import io
import zipfile

import pytest
from app import branch_printing, db
from test_enbishan_dual_brand import dual


@pytest.mark.parametrize('language', ['ar', 'en'])
@pytest.mark.parametrize('kind', ['order', 'preparation'])
def test_receipt_brand_identity_cannot_be_spoofed_by_snapshot(language, kind):
    source = {
        'brand_id': 'kps', 'order_no': 12,
        'created_at': datetime(2026, 10, 23, 12, tzinfo=timezone.utc),
        'payment_method': 'cash', 'payment_status': 'unpaid',
        'snapshot': {'order_reference': 'HFR1-261023-0012', 'brand_id': 'enbishan',
                     'logo': '../../other-brand', 'items': [
                         {'name': 'Coffee', 'product_id': 'one', 'quantity': 1,
                          'line_total_halalas': 1000}]},
        'total_halalas': 1000, 'subtotal_halalas': 1000,
    }
    frozen = deepcopy(source)
    kps = branch_printing.receipt(source, {'name': 'Branch'}, kind, language)
    enbishan = branch_printing.receipt({**source, 'brand_id': 'enbishan'},
                                     {'name': 'Branch'}, kind, language)
    assert source == frozen
    assert kps['brand_id'] == 'kps' and enbishan['brand_id'] == 'enbishan'
    assert kps['occasion_style'] == enbishan['occasion_style']
    assert [line for line in kps['lines'] if line['role'] != 'brand'] == [
        line for line in enbishan['lines'] if line['role'] != 'brand']
    assert next(line['text'] for line in enbishan['lines'] if line['role'] == 'brand') == (
        'عنبيشن' if language == 'ar' else 'ENBISHAN')
    assert next(line['text'] for line in kps['lines'] if line['role'] == 'brand') == (
        'كبز قهوة مختصة' if language == 'ar' else 'KPS COFFEE')
    assert next(line['text'] for line in enbishan['lines'] if line['role'] == 'reference') == 'HFR1-261023-0012'


def test_bridge_package_preserves_approved_brand_assets(monkeypatch):
    monkeypatch.delenv('PRINT_BRIDGE_ORIGIN', raising=False)
    with zipfile.ZipFile(io.BytesIO(branch_printing.bridge_bundle())) as archive:
        assert archive.read('logo.png') == (db.ROOT/'public/assets/kps-receipt-logo.png').read_bytes()
        assert archive.read('enbishan-logo.jpeg') == (db.ROOT/'public/assets/enbishan-original.jpeg').read_bytes()


def test_enbishan_rejects_legacy_bridge_before_claiming_and_kps_remains_compatible():
    from types import SimpleNamespace
    from fastapi import HTTPException
    branch_printing.require_brand_logo_capability({'brand_id': 'kps'}, None)
    for value in ('', 'brand-logo-v10', 'brand-logo-v1-unsafe'):
        with pytest.raises(HTTPException) as error:
            branch_printing.require_brand_logo_capability(
                {'brand_id': 'enbishan'}, SimpleNamespace(headers={'X-KPS-Print-Capabilities': value}))
        assert error.value.status_code == 409
        assert error.value.detail['code'] == 'PRINT_BRIDGE_UPDATE_REQUIRED'
    branch_printing.require_brand_logo_capability({'brand_id': 'enbishan'},
        SimpleNamespace(headers={'X-KPS-Print-Capabilities': 'future-feature, brand-logo-v1'}))


@pytest.mark.postgres
def test_enbishan_legacy_claim_stays_queued_and_compliant_claim_has_canonical_brand(dual):
    from app import auth
    client, actor, _, _, _ = dual
    token = 'synthetic-enbishan-device-' + 'x' * 40
    with db.tx() as conn:
        conn.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,token_hash,created_by) "
                     "VALUES('logo-device','enbishan','enbishan-test-1','TEST ONLY',%s,'dual-owner')",
                     (auth.token_hash(token),))
        conn.execute("INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,kind,payload,requested_by) "
                     "VALUES('logo-job','enbishan','enbishan-test-1','logo-device','test',%s,'dual-owner')",
                     (db.json({'brand_id': 'kps', 'language': 'en', 'kind': 'test', 'lines': []}),))
    headers = {'Authorization': 'Bearer ' + token}
    response = client.post('/api/printing/bridge/next', json={}, headers=headers)
    assert response.status_code == 409, response.text
    assert response.json()['detail']['code'] == 'PRINT_BRIDGE_UPDATE_REQUIRED'
    with db.tx() as conn:
        row = conn.execute("SELECT status,claimed_at,payload FROM branch_print_jobs WHERE id='logo-job'").fetchone()
        assert row['status'] == 'queued' and row['claimed_at'] is None
        assert row['payload']['brand_id'] == 'kps'  # Stored evidence is untouched.
    response = client.post('/api/printing/bridge/next', json={},
                           headers={**headers, 'X-KPS-Print-Capabilities': 'brand-logo-v1'})
    assert response.status_code == 200, response.text
    assert response.json()['job']['payload']['brand_id'] == 'enbishan'
