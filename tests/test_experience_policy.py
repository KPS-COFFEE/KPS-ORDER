"""Stage timing contracts and owner API authorization without a runtime DSN.

These tests do not certify PostgreSQL transactions or real browser layout.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app import auth, experience_policy as experience

START = datetime(2026, 10, 20, 13, tzinfo=timezone.utc)


def row(**changes):
    p = experience.defaults()
    p.update(timing_enabled=True, version=1)
    return {'id': 'test', 'service': 'delivery', 'created_at': START,
            'preparation_status': 'new', 'delivery_status': 'pending',
            'snapshot': {'branch_city': 'حفر الباطن'},
            'performance_snapshot': {'stage_policy': p}, **changes}


@pytest.mark.parametrize('seconds,color', [(59,'normal'), (60,'orange'), (89,'orange'), (90,'red')])
def test_manager_has_same_one_and_one_half_minute_limits_as_preparer(seconds, color):
    result = experience.evaluate(row(), {}, [], START+timedelta(seconds=seconds))
    assert result['color'] == color
    assert result['phases']['manager_wait']['seconds'] == seconds
    assert result['total_seconds'] == seconds


def test_driver_three_minutes_starts_at_assignment_not_order_creation():
    created = row(preparation_status='preparing', delivery_status='assigned')
    points = {'preparation.accepted': START+timedelta(seconds=10),
              'order.print_output_confirmed': START+timedelta(seconds=20),
              'preparation.ready': START+timedelta(seconds=30)}
    task = {'created_at': START+timedelta(minutes=5), 'status': 'assigned'}
    before = experience.evaluate(created, points, [task], START+timedelta(minutes=7, seconds=59))
    after = experience.evaluate(created, points, [task], START+timedelta(minutes=8))
    assert before['phases']['driver_acceptance']['color'] == 'normal'
    assert after['phases']['driver_acceptance']['color'] == 'red'
    assert after['total_seconds'] == 480
    assert after['phases']['assignment']['running'] is False


def test_assignment_basis_is_owner_selectable():
    o = row(preparation_status='accepted')
    points = {'preparation.accepted': START+timedelta(minutes=10)}
    now = START+timedelta(minutes=13)
    assert experience.evaluate(o, points, [], now)['phases']['assignment']['seconds'] == 180
    o['performance_snapshot']['stage_policy']['assignment_start'] = 'created'
    assert experience.evaluate(o, points, [], now)['phases']['assignment']['color'] == 'orange'


@pytest.mark.parametrize('city,limit', [('حفر الباطن',2700), ('حفرالباطن',2700), ('رفحاء',1800)])
def test_overall_city_limit_covers_every_stage(city, limit):
    o = row(snapshot={'branch_city': city})
    result = experience.evaluate(o, {}, [], START+timedelta(seconds=limit))
    assert result['total_limit_seconds'] == limit
    assert any(r['stage']=='total' and r['color']=='red' for r in result['reasons'])


def test_delivery_warning_starts_after_actual_pickup_and_stops_at_delivery():
    o = row(preparation_status='handed_over', delivery_status='picked_up')
    task = {'created_at': START, 'accepted_at': START+timedelta(seconds=60),
            'picked_up_at': START+timedelta(minutes=5), 'status': 'picked_up'}
    p = {'preparation.accepted': START, 'preparation.ready': START+timedelta(minutes=4)}
    amber = experience.evaluate(o, p, [task], START+timedelta(minutes=20))
    red = experience.evaluate(o, p, [task], START+timedelta(minutes=30))
    assert amber['phases']['delivery']['color'] == 'orange'
    assert red['phases']['delivery']['color'] == 'red'
    o.update(delivery_status='delivered', completed_at=START+timedelta(minutes=31))
    first = experience.evaluate(o, p, [task], START+timedelta(days=1))
    assert experience.evaluate(o, p, [task], START+timedelta(days=5)) == first
    assert first['total_seconds'] == 1860
    assert all(not phase['running'] for phase in first['phases'].values())


def test_missing_and_reassigned_events_never_invent_clocks():
    o = row(preparation_status='preparing', delivery_status='assigned')
    old = {'created_at': START, 'status': 'reassigned'}
    current = {'created_at': START+timedelta(minutes=10), 'status': 'assigned'}
    result = experience.evaluate(o, {}, [old,current], START+timedelta(minutes=11))
    assert result['phases']['assignment']['seconds'] is None
    assert result['phases']['driver_acceptance']['seconds'] == 60
    assert result['phases']['preparation']['seconds'] is None
    assert experience.evaluate(row(preparation_status='cancelled'), {}, [], START)['color'] == 'unavailable'
    assert experience.evaluate(row(performance_snapshot={}), {}, [], START) is None


@pytest.mark.parametrize('phone', ['512345678','0512345678','966512345678','+966512345678','٠٥١٢٣٤٥٦٧٨'])
def test_supported_saudi_phone_forms_share_one_canonical_identity(phone):
    assert auth.normalize_phone(phone) == '+966512345678'


@pytest.mark.parametrize('change', [
    lambda p: p['stages']['manager_wait'].update(orange=91),
    lambda p: p['stages']['driver_acceptance'].update(red=True),
    lambda p: p['total_seconds'].update(hafar=-1),
    lambda p: p.update(brand_id='another-brand'),
    lambda p: p.update(assignment_start='client-supplied-clock'),
])
def test_reject_invalid_owner_thresholds(change):
    p = experience.defaults();p.pop('version');change(p)
    with pytest.raises(HTTPException) as error:
        experience.validate(p)
    assert error.value.status_code == 422


@pytest.mark.parametrize('role', ['customer','driver','delivery_manager','delivery_staff','branch_manager','admin'])
def test_owner_api_rejects_other_roles(role):
    app = FastAPI();app.include_router(experience.router)
    app.dependency_overrides[auth.current_user] = lambda: {'role':role,'brand_id':'kps'}
    client = TestClient(app)
    assert client.get('/api/experience/policy').status_code == 403
    p = experience.defaults();p.pop('version')
    assert client.put('/api/experience/policy',json={'policy':p,'expected_version':0,'reason':'اختبار'}).status_code == 403


@pytest.mark.parametrize('brand', ['kps', 'enbishan'])
def test_owner_save_without_extra_otp_is_brand_scoped_versioned_and_audited(monkeypatch, brand):
    records = {'kps': {}, 'enbishan': {}}
    seen = []
    class Result:
        def __init__(self, value): self.value = value
        def fetchone(self): return self.value
    class Conn:
        def execute(self, query, params):
            if query.startswith('SELECT settings'):
                seen.append(('read',params[0]));return Result({'settings':deepcopy(records[params[0]])})
            if query.startswith('UPDATE app_settings'):
                records[params[1]] = deepcopy(params[0].obj);seen.append(('write',params[1]));return Result(None)
            raise AssertionError(query)
    @contextmanager
    def tx(): yield Conn()
    monkeypatch.setattr(experience.db,'tx',tx)
    monkeypatch.setattr(experience.db,'idempotent',lambda c,u,k,op,body,perform:perform())
    monkeypatch.setattr(experience.db,'audit',lambda *args:seen.append(('audit',args[4])))
    monkeypatch.setattr(auth,'require_owner_step_up',lambda c,u:pytest.fail('Operational owner save must not request an extra OTP'))
    app=FastAPI();app.include_router(experience.router)
    app.dependency_overrides[auth.current_user]=lambda:{'id':'owner','role':'owner','brand_id':brand}
    client=TestClient(app)
    p=experience.defaults();p.pop('version');p['timing_enabled']=True
    body={'policy':p,'expected_version':0,'reason':'اعتماد المالك'}
    result=client.put('/api/experience/policy',json=body)
    assert result.status_code==200, result.text
    assert result.json()['policy']['version']==1
    assert records['enbishan' if brand == 'kps' else 'kps']=={}
    assert ('audit',brand) in seen
    assert client.put('/api/experience/policy',json=body).status_code==409
    body['brand_id']='enbishan' if brand == 'kps' else 'kps'
    assert client.put('/api/experience/policy',json=body).status_code==422


def test_customer_entry_is_disabled_by_default_and_requires_owner():
    p = experience.defaults(); p.pop('version')
    assert p['customer_entry_enabled'] is False
    p['customer_entry_enabled'] = True
    assert experience.validate(p)['customer_entry_enabled'] is True
    app = FastAPI(); app.include_router(experience.router)
    app.dependency_overrides[auth.current_user] = lambda: {'role':'delivery_manager','brand_id':'kps'}
    client = TestClient(app)
    assert client.get('/api/experience/policy').status_code == 403
    assert client.put('/api/experience/policy',json={'policy':p,'expected_version':0,'reason':'test'}).status_code == 403


def test_existing_staff_policy_gains_disabled_entry_without_mutation():
    saved = experience.defaults(); saved.pop('customer_entry_enabled')
    saved['version'] = 3
    saved['stages']['manager_wait']['red'] = 110
    class Result:
        def fetchone(self): return {'settings':{'experience_policy':saved}}
    class Conn:
        def execute(self, query, params): return Result()
    restored = experience.policy(Conn(), 'kps')
    assert restored['customer_entry_enabled'] is False
    assert restored['version'] == 3
    assert restored['stages']['manager_wait']['red'] == 110
    restored['stages']['manager_wait']['red'] = 130
    assert saved['stages']['manager_wait']['red'] == 110
    assert 'customer_entry_enabled' not in saved


def test_existing_capture_hook_freezes_staff_targets_without_changing_legacy_policy():
    from app import performance
    settings = {'experience_policy': experience.defaults()}
    class Result:
        def __init__(self, value): self.value = value
        def fetchone(self): return self.value
    class Conn:
        def execute(self, query, params):
            if query.startswith('SELECT p.id,p.name'):
                return Result(None)
            assert query == 'SELECT settings FROM app_settings WHERE brand_id=%s'
            assert params == ('kps',)
            return Result({'settings': settings})
    conn = Conn()
    legacy = performance.capture_policy(conn, 'kps', 'branch')
    assert 'stage_policy' not in legacy and legacy['policies'] == {}
    settings['experience_policy'].update(timing_enabled=True, version=1)
    captured = performance.capture_policy(conn, 'kps', 'branch')
    settings['experience_policy']['stages']['manager_wait']['red'] = 120
    assert captured['stage_policy']['stages']['manager_wait']['red'] == 90
    assert captured['stage_policy']['version'] == 1
    assert captured['policies'] == legacy['policies']


def test_enrichment_reads_actual_driver_acceptance_and_stops_its_clock():
    from app import performance
    order = row(preparation_status='accepted', delivery_status='accepted')
    class Result:
        def __init__(self, values): self.values = values
        def fetchall(self): return self.values
    class Conn:
        def execute(self, query, params):
            if query.startswith('SELECT order_id,event_type'):
                return Result([{'order_id':'test','event_type':'preparation.accepted','created_at':START}])
            assert 't.accepted_at' in query
            return Result([{'order_id':'test','driver_id':'driver','display_name':'TEST',
                'created_at':START+timedelta(seconds=10),'accepted_at':START+timedelta(seconds=70),
                'picked_up_at':None,'delivered_at':None,'status':'accepted','reason':None}])
    performance.enrich_orders(Conn(), [order], now=START+timedelta(minutes=10))
    phase = order['stage_timing']['phases']['driver_acceptance']
    assert phase == {'seconds':60.0,'running':False,'color':'normal'}


def test_capture_reuses_transaction_settings_without_an_extra_database_read():
    from app import performance
    settings = {'experience_policy': experience.defaults()}
    settings['experience_policy']['timing_enabled'] = True
    class Result:
        def fetchone(self): return None
    class Conn:
        def execute(self, query, params):
            assert query.startswith('SELECT p.id,p.name'), 'Unexpected extra settings query'
            return Result()
    captured = performance.capture_policy(Conn(), 'kps', 'branch', settings=settings)
    settings['experience_policy']['stages']['driver_acceptance']['red'] = 240
    assert captured['stage_policy']['stages']['driver_acceptance']['red'] == 180
    assert 'stage_policy' not in performance.capture_policy(Conn(), 'kps', 'branch', settings={})


@pytest.mark.parametrize('entry_enabled',[False,True])
@pytest.mark.parametrize('gift_state,offer_active',[
    ('missing',False),('disabled',False),('scheduled',False),('active',True),
])
def test_public_offer_prompt_only_for_enabled_started_gift(monkeypatch,entry_enabled,gift_state,offer_active):
    """The optional prompt follows the real gift policy independently of entry UI."""
    settings={'experience_policy':{'customer_entry_enabled':entry_enabled}}
    if gift_state!='missing':
        settings['first_order_gift']={
            'version':3,'enabled':gift_state!='disabled',
            'starts_at':(datetime.now(timezone.utc)+timedelta(days=1 if gift_state=='scheduled' else -1)).isoformat(),
            'items':[{'product_id':'coffee','options':{}},{'product_id':'cookie','options':{}}],
        }
    queries=[]
    class Result:
        def __init__(self,value): self.value=value
        def fetchone(self): return self.value
    class Conn:
        def execute(self,query,params):
            queries.append((query,params))
            assert params==('kps',)
            if query.startswith('SELECT 1 FROM brands'):
                return Result({'active':True})
            if query.startswith('SELECT settings'):
                return Result({'settings':deepcopy(settings)})
            raise AssertionError(query)
    @contextmanager
    def tx(): yield Conn()
    monkeypatch.setattr(experience.db,'tx',tx)
    app=FastAPI();app.include_router(experience.router)
    response=TestClient(app).get('/api/experience/public?brand_id=kps')
    assert response.status_code==200
    assert response.json()=={'customer_entry_enabled':entry_enabled,'gift_offer_active':offer_active}
    assert queries and all(query.startswith('SELECT') for query,_ in queries)
