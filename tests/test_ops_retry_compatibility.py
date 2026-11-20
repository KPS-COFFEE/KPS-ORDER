"""Legacy KPS retry identities survive an additive multi-brand release."""
from pathlib import Path
import subprocess
import pytest
from fastapi import HTTPException
from app import db, multibrand
from test_enbishan_dual_brand import dual


def test_actual_staff_api_retains_production_browser_fingerprint():
    subprocess.run(['node', 'tests/test_ops_retry_compatibility.cjs'],
                   cwd=Path(__file__).resolve().parents[1], check=True)


@pytest.mark.parametrize('selected,expected', [('kps','ordinary.write'), ('enbishan','brand:enbishan:ordinary.write')])
def test_replay_operation_preserves_kps_names_and_scopes_other_brands(monkeypatch, selected, expected):
    monkeypatch.setattr(multibrand, 'enabled', lambda: True)
    monkeypatch.setattr(multibrand, 'isolation_guard', lambda: None)
    class Connection:
        def execute(self, query, args):
            assert query == 'SELECT brand_id FROM users WHERE id=%s' and args == ('SYNTHETIC',)
            return self
        def fetchone(self): return {'brand_id':'kps'}
    token = multibrand.operation_brand.set(selected)
    try:
        assert multibrand.replay_operation(Connection(), 'SYNTHETIC', 'ordinary.write') == expected
    finally:
        multibrand.operation_brand.reset(token)


@pytest.mark.postgres
def test_historical_kps_response_and_digest_replay_after_enable_without_foreign_brand_reuse(dual, monkeypatch):
    user_id, operation, key = 'dual-owner', 'SYNTHETIC.retry-compat', 'SYNTHETIC-same-browser-key'
    body = {'amount_halalas':1000,'reference':'SYNTHETIC'}
    expected = {'saved':'historical KPS'}
    # Model a committed production response before multi-brand was enabled.
    with monkeypatch.context() as historical:
        historical.setenv('ENBISHAN_ISOLATED_ENABLED', 'false')
        with db.tx() as c:
            assert db.idempotent(c,user_id,key,operation,body,lambda:expected) == expected
    token = multibrand.operation_brand.set('kps')
    try:
        with db.tx() as c:
            assert db.idempotent(c,user_id,key,operation,body,lambda:pytest.fail('A historical committed mutation must never execute again')) == expected
        with pytest.raises(HTTPException) as changed:
            with db.tx() as c:
                db.idempotent(c,user_id,key,operation,{**body,'amount_halalas':1001},lambda:pytest.fail('Changed bodies cannot consume a historical replay'))
        assert changed.value.status_code == 409 and changed.value.detail['code'] == 'IDEMPOTENCY_CONFLICT'
        multibrand.operation_brand.set('enbishan')
        with db.tx() as c:
            assert db.idempotent(c,user_id,key,operation,body,lambda:{'saved':'new ENBISHAN'}) == {'saved':'new ENBISHAN'}
        with db.tx() as c:
            rows = c.execute('SELECT operation,request_digest,response FROM idempotency_keys WHERE user_id=%s AND key=%s ORDER BY operation',(user_id,key)).fetchall()
            assert {row['operation'] for row in rows} == {operation,'brand:enbishan:'+operation}
            assert all(row['request_digest']==db.digest(body) for row in rows)
            assert next(row['response'] for row in rows if row['operation']==operation)==expected
    finally:
        multibrand.operation_brand.reset(token)
