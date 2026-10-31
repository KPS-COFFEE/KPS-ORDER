"""Individual grants must hide controls and reject direct/replayed API use."""
import pytest
from fastapi import HTTPException
from app import auth, db, employee_permissions as policy
from tests.sms_policy_helpers import sms_step_up
from tests.test_core import _make_user, _authenticate

PASSWORD = 'TestOnly!EmployeePermissions-2026'


@pytest.mark.parametrize('role', ['driver', 'delivery_manager', 'delivery_staff', 'branch_manager'])
def test_empty_overrides_preserve_defaults_without_cross_role_grants(role):
    user = {'role': role, 'permissions': ['appearance_manage']}
    assert policy.effective(user) == policy.eligible(role)
    assert 'appearance_manage' not in policy.effective(user)
    for code in policy.eligible(role):
        assert code not in policy.effective(dict(user, permission_denials=[code]))
    assert policy.effective(dict(user, permission_denials=list(policy.eligible(role)))) == set()


def test_admin_grants_and_owner_access_remain_explicit():
    assert policy.effective({'role': 'admin', 'permissions': ['products_manage', 'shifts_approve']}) == {'products_manage'}
    assert policy.effective({'role': 'owner', 'permission_denials': list(policy.REGISTRY)}) == set(policy.REGISTRY)
    assert policy.effective({'role': 'customer'}) == set()
    for values in (['shifts_approve'], ['tasks_view', 'tasks_view'], [False]):
        with pytest.raises(HTTPException):
            auth.validate_permission_denials(values, 'driver')


def test_supervisory_reads_are_explicit_additive_and_do_not_grant_mutations():
    previous = {'role': 'admin', 'permissions': ['accounting_reports', 'products_manage'],
                'extra_permissions': ['driver_hours_reports']}
    grants = {'orders_view', 'chat_view'}
    updated = dict(previous, extra_permissions=[*previous['extra_permissions'], *grants])
    assert policy.effective(updated) == policy.effective(previous) | grants
    assert not policy.effective(updated) & {
        'orders_cancel', 'orders_cancel_after_print', 'orders_cancel_collected',
        'payments_collect', 'refunds_manage', 'orders_print', 'chat_reply', 'chat_manage',
        'delivery_assign', 'orders_route_preparation',
    }
    assert policy.effective(dict(updated, permission_denials=list(grants))) == policy.effective(previous)
    for code in grants:
        assert code not in policy.effective(dict(previous, permissions=[*previous['permissions'], code]))


@pytest.mark.parametrize('role', ['admin', 'delivery_manager'])
def test_collected_cancellation_requires_explicit_grant_and_honors_denial(role):
    actor = {'role': role, 'extra_permissions': ['orders_cancel_after_print']}
    assert 'orders_cancel_collected' not in policy.effective(actor)
    actor['extra_permissions'].append('orders_cancel_collected')
    assert 'orders_cancel_collected' in policy.effective(actor)
    assert 'orders_cancel_collected' not in policy.effective(dict(actor, permission_denials=['orders_cancel_collected']))


@pytest.mark.parametrize('role', ['driver', 'branch_manager', 'delivery_staff', 'customer'])
def test_other_roles_cannot_receive_collected_cancellation_through_raw_extras(role):
    assert 'orders_cancel_collected' not in policy.effective({'role': role, 'extra_permissions': ['orders_cancel_collected']})


def test_preparation_choice_is_manager_capability_and_preserves_denial():
    assert 'orders_route_preparation' in policy.effective({'role': 'delivery_manager'})
    assert 'orders_route_preparation' in policy.effective({'role': 'owner'})
    assert 'orders_route_preparation' not in policy.effective({'role': 'delivery_manager', 'permission_denials': ['orders_route_preparation']})
    for role in ['admin', 'branch_manager', 'driver', 'delivery_staff', 'customer']:
        assert 'orders_route_preparation' not in policy.effective({'role': role, 'extra_permissions': ['orders_route_preparation']})


def test_supervision_and_preparation_route_contracts_preserve_distinct_authority():
    def endpoint():
        pass
    endpoint.__module__ = 'app.order_financial_cases'
    endpoint.__name__ = 'journey'
    assert policy.required_for(endpoint, 'admin') == ('orders_view', 'accounting_review')
    endpoint.__name__ = 'cancel'
    assert policy.required_for(endpoint, 'admin') == ('orders_cancel_after_print', 'orders_cancel_collected')
    assert policy.required_for(endpoint, 'delivery_manager') == 'orders_cancel'
    endpoint.__module__ = 'app.preparation_routing'
    endpoint.__name__ = 'choose'
    assert policy.required_for(endpoint, 'delivery_manager') == 'orders_route_preparation'
    endpoint.__name__ = 'prepare'
    assert policy.required_for(endpoint, 'branch_manager') == 'orders_prepare'


def _owner(client, monkeypatch):
    owner = _make_user('owner', username=db.new_id('permission_owner_'), password=PASSWORD)
    _authenticate(client, owner)
    sms_step_up(client, owner, monkeypatch)
    return owner


def _save(client, user, enabled, expected=None):
    return client.put('/api/identity/staff/'+user['id']+'/permissions', json={
        'enabled_permissions': sorted(enabled),
        'expected_permissions': sorted(policy.effective(user) if expected is None else expected),
        'reason': 'Owner reviewed employee duties',
    })


@pytest.mark.postgres
@pytest.mark.parametrize(('role','code','path'), [
    ('driver', 'tasks_view', '/api/tasks'),
    ('driver', 'shifts_view', '/api/shifts'),
    ('driver', 'reports_view', '/api/reports'),
    ('driver', 'vehicle_faults_report', '/api/vehicle-maintenance/options'),
    ('delivery_manager', 'orders_view', '/api/orders'),
    ('delivery_manager', 'drivers_view', '/api/operations/drivers'),
    ('delivery_manager', 'chat_view', '/api/conversations'),
    ('delivery_staff', 'dashboard_view', '/api/dashboard'),
    ('branch_manager', 'orders_view', '/api/orders'),
])
def test_live_session_loses_and_regains_permission_without_signout(client,role,code,path, monkeypatch):
    target = _make_user(role, branches=['kps-branch-1'])
    _authenticate(client,target)
    target_cookies = dict(client.cookies)
    csrf = client.headers['X-CSRF-Token']
    assert client.get(path).status_code == 200
    owner = _owner(client, monkeypatch)
    saved = _save(client,target,policy.effective(target)-{code})
    assert saved.status_code == 200, saved.text
    updated = saved.json()['user']
    assert code not in updated['effective_permissions']
    client.cookies.clear(); client.cookies.update(target_cookies)
    client.headers['X-CSRF-Token'] = csrf
    assert client.get('/api/session').json()['user']['id'] == target['id']
    blocked = client.get(path)
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()['detail']['code'] == 'STAFF_PERMISSION_FORBIDDEN'
    _authenticate(client,owner)
    sms_step_up(client, owner, monkeypatch)
    assert _save(client,updated,policy.eligible(role)).status_code == 200
    client.cookies.clear(); client.cookies.update(target_cookies)
    client.headers['X-CSRF-Token'] = csrf
    assert client.get(path).status_code == 200


@pytest.mark.postgres
def test_permissions_are_owner_only_scoped_audited_and_conflict_checked(client, monkeypatch):
    target = _make_user('driver',branches=['kps-branch-1'])
    manager = _make_user('delivery_manager',branches=['kps-branch-1'])
    _authenticate(client,manager)
    assert _save(client,target,[]).status_code == 403
    owner = _owner(client, monkeypatch)
    assert _save(client,owner,[]).status_code == 409
    assert _save(client,target,['shifts_approve']).status_code == 422
    saved = _save(client,target,policy.effective(target)-{'task_photos'})
    assert saved.status_code == 200
    assert _save(client,target,[]).status_code == 409
    with db.tx() as conn:
        row = conn.execute('SELECT * FROM users WHERE id=%s',(target['id'],)).fetchone()
        assert row['branch_ids']==target['branch_ids'] and row['role']=='driver' and row['active']==target['active']
        audit = conn.execute("SELECT details FROM audit_events WHERE action='staff.permissions.updated' AND entity_id=%s",(target['id'],)).fetchone()['details']
        assert 'task_photos' in audit['before'] and 'task_photos' not in audit['after']
        conn.execute("INSERT INTO brands(id,name) VALUES('permissions-other','Other')")
        conn.execute("UPDATE users SET brand_id='permissions-other' WHERE id=%s",(target['id'],))
    assert _save(client,row,[]).status_code == 404


@pytest.mark.postgres
def test_revoked_write_permission_blocks_direct_calls_and_retry_keys(client, monkeypatch):
    manager = _make_user('delivery_manager',branches=['kps-branch-1'])
    _owner(client, monkeypatch)
    assert _save(client,manager,policy.effective(manager)-{'delivery_assign','shifts_delegate_close'}).status_code == 200
    _authenticate(client,manager)
    for path,body in [('/api/orders/missing/assign',{'driver_id':'missing','reason':'test'}),
                      ('/api/shifts/missing/driver-close',{'counted_halalas':0,'reason':'test'})]:
        for _ in range(2):
            blocked=client.post(path,json=body,headers={'Idempotency-Key':'same-withdrawn-key'})
            assert blocked.status_code==403,blocked.text
            assert blocked.json()['detail']['code']=='STAFF_PERMISSION_FORBIDDEN'


@pytest.mark.postgres
def test_staff_directory_exposes_effective_permission_names_and_catalog(client, monkeypatch):
    target=_make_user('driver',branches=['kps-branch-1'])
    _owner(client, monkeypatch)
    result=client.get('/api/identity/staff').json()
    row=next(u for u in result['users'] if u['id']==target['id'])
    assert set(row['effective_permissions'])==policy.eligible('driver')
    names={p['code']:p for p in result['permission_catalog']}
    assert set(row['available_permissions'])<=names.keys()
    assert all(p['name_ar'] and p['name_en'] and p['group_ar'] and p['group_en'] for p in names.values())
