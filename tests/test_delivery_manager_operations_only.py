"""Owner administration remains isolated from daily delivery operations."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import auth, branch_printing, employee_permissions as policy, operations
from app.main import app


def test_manager_has_operational_capabilities_but_no_account_or_financial_administration():
    manager = {'role':'delivery_manager', 'permissions':list(policy.REGISTRY)}
    effective = policy.effective(manager)
    assert not effective & {'drivers_manage','refunds_manage','branch_printers_manage','vehicle_register',
                            'accounting_reports','accounting_review','vehicle_maintenance_manage'}
    assert {'drivers_view','drivers_availability','driver_terminal','shifts_approve',
            'delivery_assign','shifts_delegate_close','custody_transfer','support_manage',
            'orders_print','reports_view'} <= effective
    assert not branch_printing.can_manage_printers(manager)
    assert branch_printing.can_manage_printers({'role':'owner'})
    assert branch_printing.can_manage_printers({'role':'admin','permissions':['branch_printers_manage']})


@pytest.mark.parametrize('method,path,body', [
    ('get','/api/identity/staff',None),
    ('post','/api/identity/staff/invitations',{'display_name':'Test Driver','email':'driver@example.test','phone':'0501234567','branch_ids':['kps-branch-1']}),
    ('patch','/api/identity/staff/driver',{'display_name':'Changed'}),
    ('patch','/api/identity/staff/driver',{'phone':'0501234567','branch_ids':['kps-branch-2'],'active':False}),
    ('patch','/api/identity/staff/driver',{'vehicle':{'plate':'123','owned_by_kps':True}}),
    ('post','/api/identity/staff/driver/resend',{}),
    ('post','/api/identity/staff/driver/manage',{'action':'pause','reason':'Forbidden change'}),
    ('post','/api/identity/staff/driver/owner-activate',{'reason':'Forbidden activation'}),
    ('post','/api/identity/staff/driver/revoke-invitation',{}),
    ('put','/api/identity/staff/driver/permissions',{'enabled_permissions':[],'expected_permissions':[],'reason':'Rejected manager override'}),
    ('post','/api/orders/old-order/refund',{'amount_halalas':100,'method':'cash','reason':'Forbidden refund'}),
    ('post','/api/printing/devices',{'branch_id':'kps-branch-1','label':'Forbidden printer'}),
    ('get','/api/printing/devices',None),
    ('post','/api/printing/devices/old-device/test',{}),
    ('post','/api/printing/devices/old-device/revoke',{'reason':'Forbidden change'}),
    ('get','/api/printing/windows-bridge.zip',None),
])
def test_existing_manager_session_and_retried_keys_fail_before_writes(monkeypatch,method,path,body):
    # Exercise the real current_user dependency, including capability enforcement.
    # The only DB operation allowed is its fresh session lookup. A write or an
    # idempotency read would raise and fail this test.
    reads=[]
    @contextmanager
    def session_only_tx(**kwargs):
        assert kwargs == {'pipeline':True}
        reads.append('session')
        yield None
    monkeypatch.setattr(auth.db,'tx',session_only_tx)
    monkeypatch.setattr(auth,'_find_session',lambda conn, request:{
        'id':'existing-manager','role':'delivery_manager','brand_id':'kps',
        'branch_ids':['kps-branch-1'],'active':True,'csrf_hash':auth.token_hash('proof'),
        'permissions':list(policy.REGISTRY)})
    with TestClient(app,base_url='http://testserver',headers={
            'Origin':'http://testserver','X-CSRF-Token':'proof','Idempotency-Key':'previous-success-key'}) as client:
        for _ in range(2):
            result=client.request(method,path,json=body)
            assert result.status_code==403,result.text
            assert result.json()['detail']['code'] in {'ROLE_FORBIDDEN','STAFF_PERMISSION_FORBIDDEN'}
    assert reads==['session','session']


def test_internal_invitation_helper_rechecks_current_actor_role():
    manager={'id':'manager','role':'delivery_manager','brand_id':'kps','active':True}
    class ReadOnlyConnection:
        def execute(self,query,args):
            assert query.startswith('SELECT u.* FROM users')
            return SimpleNamespace(fetchone=lambda:manager)
    with pytest.raises(HTTPException) as rejected:
        auth.create_staff_invitation(ReadOnlyConnection(),{
            'display_name':'Driver','email':'driver@example.test','phone':'0501234567',
            'branch_ids':['kps-branch-1']},manager)
    assert rejected.value.status_code==403


def test_refund_authority_precedes_idempotency_even_if_called_internally():
    with pytest.raises(HTTPException) as rejected:
        operations.refund('previous-order',operations.Refund(amount_halalas=100,method='cash',reason='No grant'),
                          None,{'role':'delivery_manager'})
    assert rejected.value.status_code==403


def test_owner_vehicle_profile_is_required_for_legacy_driver_write():
    with pytest.raises(HTTPException) as rejected:
        auth.register_driver_vehicle({'plate':'123','owned_by_kps':False},{'role':'driver'})
    assert rejected.value.status_code==403
    assert rejected.value.detail['code']=='VEHICLE_OWNER_REQUIRED'


def test_previous_manager_invitation_cannot_activate_an_employee():
    class ReadOnlyConnection:
        def execute(self,query,args):
            assert query.startswith('SELECT u.* FROM users')
            return SimpleNamespace(fetchone=lambda:{'id':'manager','role':'delivery_manager',
                                                     'brand_id':'kps','branch_ids':['kps-branch-1']})
    assert not auth._invitation_authorized(ReadOnlyConnection(),
        {'created_by':'manager','brand_id':'kps','purpose':'invitation'},
        {'role':'driver','branch_ids':['kps-branch-1']})
