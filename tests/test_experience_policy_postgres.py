"""Real PostgreSQL replay, version conflict, brand scope and captured targets."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import auth, db, experience_policy as experience, performance
from test_operations import ops

pytestmark = pytest.mark.postgres


def test_policy_transaction_replay_scope_and_order_capture(ops, monkeypatch):
    actor, _, _, _ = ops
    # This certificate covers the settings transaction. Existing identity suites
    # certify the real owner SMS proof; role checks remain active on these APIs.
    monkeypatch.setattr(auth, 'require_owner_step_up', lambda conn,user: None)
    with db.tx() as conn:
        conn.execute("INSERT INTO app_settings(brand_id,settings) VALUES('ops-brand','{}'),('ops-other','{}')")
    initial = actor('owner').get('/api/experience/policy').json()['policy']
    policy = {k:v for k,v in initial.items() if k!='version'}
    policy['timing_enabled'] = True
    policy['customer_entry_enabled'] = True
    body = {'policy':policy,'expected_version':0,'reason':'اعتماد معايير المالك'}
    key = uuid4().hex
    first = actor('owner').put('/api/experience/policy',json=body,headers={'Idempotency-Key':key})
    assert first.status_code == 200, first.text
    replay = actor('owner').put('/api/experience/policy',json=body,headers={'Idempotency-Key':key})
    assert replay.json() == first.json()
    assert actor('manager').put('/api/experience/policy',json=body).status_code == 403
    assert actor('other').get('/api/experience/policy').json()['policy']['version'] == 0
    with db.tx() as conn:
        settings = conn.execute("SELECT settings FROM app_settings WHERE brand_id='ops-brand'").fetchone()['settings']
        frozen = performance.capture_policy(conn,'ops-brand','ops-branch',settings=settings)['stage_policy']
        assert frozen['stages']['manager_wait'] == {'orange':60,'red':90}
        assert conn.execute("SELECT count(*) n FROM audit_events WHERE action='experience.policy.updated' AND entity_id='ops-brand'").fetchone()['n'] == 1
    body['expected_version'] = 1
    body['policy']['stages']['manager_wait']['red'] = 120
    assert actor('owner').put('/api/experience/policy',json=body,headers={'Idempotency-Key':uuid4().hex}).status_code == 200
    assert frozen['stages']['manager_wait']['red'] == 90


def test_two_owner_sessions_cannot_overwrite_each_other(ops, monkeypatch):
    monkeypatch.setattr(auth,'require_owner_step_up',lambda conn,user:None)
    with db.tx() as conn:
        conn.execute("INSERT INTO app_settings(brand_id,settings) VALUES('ops-brand','{}')")
    def update(red):
        p = experience.defaults();p.pop('version');p['stages']['manager_wait']['red'] = red
        try:
            return experience.save_policy({'policy':p,'expected_version':0,'reason':'تعديل جلسة المالك'},
                user={'id':'ops-owner','brand_id':'ops-brand','role':'owner'},idem=uuid4().hex)['policy']['version']
        except HTTPException as error:
            return error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update,[120,180]))
    assert sorted(results) == [1,409]
    with db.tx() as conn:
        assert experience.policy(conn,'ops-brand')['version'] == 1
