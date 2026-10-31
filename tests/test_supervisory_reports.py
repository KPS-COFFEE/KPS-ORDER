"""An explicit supervisory grant reads approved reports without financial writes."""
from app import employee_permissions, reporting, operating_reports


def user(**changes):
    return dict(id='supervisor',role='delivery_manager',brand_id='kps',branch_ids=['a','b'],
                extra_permissions=['reports_supervise'],permission_denials=[],active=True)|changes


def report(**changes):
    return dict(user_id='another-manager',brand_id='kps',branch_ids=['a'],
                snapshot={'manager_user_id':'another-manager','shift':{'status':'closed'}})|changes


def test_supervision_is_not_a_role_default_and_does_not_grant_accounting():
    normal=employee_permissions.effective(user(extra_permissions=[]))
    assert 'reports_supervise' not in normal
    effective=employee_permissions.effective(user())
    assert 'reports_supervise' in effective and 'reports_view' in effective
    assert 'accounting_reports' not in effective


def test_supervisor_reads_another_managers_approved_report_only_inside_scope():
    assert reporting.can_read(user(),report())
    assert not reporting.can_read(user(extra_permissions=[]),report())
    assert not reporting.can_read(user(),report(branch_ids=['a','foreign']))
    assert not reporting.can_read(user(),report(brand_id='enbishan'))
    assert not reporting.can_read(user(),report(branch_ids=[]))
    assert not reporting.can_read(user(),report(snapshot={'shift':{'status':'open'}}))
    assert not reporting.can_read(user(permission_denials=['reports_view']),report())
    assert not reporting.can_read(user(permission_denials=['reports_supervise']),report())


def test_shift_supervision_never_bypasses_branch_and_brand_filters():
    shift=dict(brand_id='kps',branch_ids=['a'],user_id='another-manager',status='closed')
    assert operating_reports.allowed_manager(user(),shift,'shift')
    assert not operating_reports.allowed_manager(user(extra_permissions=[]),shift,'shift')
    assert not operating_reports.allowed_manager(user(),dict(shift,brand_id='enbishan'),'shift')
    assert not operating_reports.allowed_manager(user(),dict(shift,branch_ids=['foreign']),'shift')
    assert not operating_reports.allowed_manager(user(),dict(shift,status='open'),'shift')
