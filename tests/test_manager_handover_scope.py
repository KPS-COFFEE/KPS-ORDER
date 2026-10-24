from app import manager_handover_scope as scope


class Connection:
    def __init__(self, user, shifts=()):
        self.user, self.shifts = user, shifts

    def execute(self, query, args):
        self.rows = [self.user] if 'SELECT * FROM users' in query else ([] if 'SELECT 1 FROM driver_tasks' in query else self.shifts)
        return self

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


def test_recipient_requires_live_role_brand_and_full_branch_scope(monkeypatch):
    monkeypatch.setattr('app.multibrand.enabled', lambda: False)
    s = {'id': 's', 'user_id': 'u', 'brand_id': 'kps', 'branch_ids': ['b']}
    user = {'id': 'u', 'active': True, 'role': 'delivery_manager', 'brand_id': 'kps', 'branch_ids': ['b']}
    assert scope.authorized(Connection(user), s, ['b'])
    for revoked in ({'active': False}, {'role': 'driver'}, {'brand_id': 'other'}, {'branch_ids': []}):
        assert not scope.authorized(Connection(user | revoked), s, ['b'])
    assert not scope.authorized(Connection(user), s, ['outside'])


def test_handover_grant_requires_identified_order_and_exact_target_shift(monkeypatch):
    monkeypatch.setattr('app.multibrand.enabled', lambda: False)
    user = {'id': 'u', 'active': True, 'role': 'delivery_manager', 'brand_id': 'kps', 'branch_ids': ['b']}
    order = {'id': 'o', 'brand_id': 'kps', 'branch_id': 'b'}
    s = {'id': 's', 'user_id': 'u', 'brand_id': 'kps', 'branch_ids': ['b'], 'handed_branches': ['b']}
    assert scope.accepted_order(Connection(user, [s]), user, order, shift_id='s')
    assert not scope.accepted_order(Connection(user, [s]), user, order, shift_id='another')
    assert not scope.accepted_order(Connection(user, []), user, order)
    assert not scope.accepted_order(Connection(user, [s]), user, order | {'id': None})
    assert not scope.accepted_order(Connection(user | {'active': False}, [s]), user, order)


def test_support_approval_is_explicit_and_independent_of_display_name(monkeypatch):
    from app import driver_support, employee_permissions
    monkeypatch.setattr('app.multibrand.enabled', lambda: False)
    principal = {'id':'u','active':True,'role':'delivery_manager','brand_id':'kps','branch_ids':['b']}
    assert 'driver_support_approve' not in employee_permissions.eligible('delivery_manager')
    assert 'driver_support_approve' not in employee_permissions.eligible('admin')
    assert 'driver_support_approve' in employee_permissions.grantable('delivery_manager')
    for name in ('محمد','فوزي','any manager'):
        assert not driver_support.approver(Connection(principal | {'display_name':name}), principal, 'kps', ['b'])
    granted=principal | {'extra_permissions':['driver_support_approve']}
    assert driver_support.approver(Connection(granted), principal, 'kps', ['b'])
    assert not driver_support.approver(Connection(granted), principal, 'kps', ['outside'])
    assert not driver_support.approver(Connection(principal), granted, 'kps', ['b'])
    assert not driver_support.approver(Connection(granted | {'active':False}), granted, 'kps', ['b'])


def test_morning_context_does_not_invent_support_approval():
    from app import employee_permissions
    principal={'id':'u','role':'delivery_manager','_morning_manager_context':{'shift_id':'s'}}
    assert 'driver_support_approve' not in employee_permissions.effective(principal)
    assert 'driver_support_approve' in employee_permissions.effective(principal | {'extra_permissions':['driver_support_approve']})
    assert 'driver_support_approve' not in employee_permissions.effective(principal | {'extra_permissions':['driver_support_approve'],'permission_denials':['driver_support_approve']})
