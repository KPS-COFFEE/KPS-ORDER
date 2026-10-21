"""Ephemeral morning capability and branch/period resource contracts."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from app import auth, employee_permissions, morning_manager_context as context


def user():
    return {'id':'manager','role':'delivery_manager','brand_id':'kps','active':True,
            'branch_ids':['branch2','branch3'],'permission_denials':['orders_view','delivery_assign']}


def scoped_user():
    return user() | {'branch_ids':['branch2'], '_morning_manager_context':{
        'period':'morning','shift_id':'duty','branch_ids':['branch2']}}


def test_context_capabilities_are_ephemeral_and_exact():
    base=user(); before=deepcopy(base)
    scoped=scoped_user()
    assert employee_permissions.effective(scoped) == set(context.CAPABILITIES)
    assert not auth.has_permission(scoped,'orders_create_customer')
    assert not auth.has_permission(scoped,'reports_view')
    assert not auth.has_permission(scoped,'custody_transfer')
    assert not auth.has_permission(base,'orders_view')
    assert base==before


@pytest.mark.parametrize('changed',[{'branch_id':'branch3'},{'brand_id':'enbishan'},
    {'snapshot':{'delivery_period':'evening'}},{'snapshot':{}},{'service':'pickup'}])
def test_order_scope_fail_closed(changed):
    order={'brand_id':'kps','branch_id':'branch2','service':'delivery','snapshot':{'delivery_period':'morning'}}
    assert context.order_allowed(scoped_user(),order)
    assert not context.order_allowed(scoped_user(),order|changed)


@pytest.mark.parametrize('changed',[{'branch_ids':['branch3']},{'brand_id':'enbishan'},
    {'delivery_window_snapshot':{'period':'evening'}},{'manager_shift_id':'other'}, {'branch_ids':[]}])
def test_shift_scope_requires_actual_morning_parent(changed):
    shift={'id':'driver','manager_shift_id':'duty','brand_id':'kps','branch_ids':['branch2'],
           'delivery_window_snapshot':{'period':'morning'}}
    assert context.shift_allowed(scoped_user(),shift)
    assert not context.shift_allowed(scoped_user(),shift|changed)
    with pytest.raises(HTTPException):context.require_shift(scoped_user(),shift|changed)


def test_normal_context_retains_existing_permission_and_scope_behavior():
    principal=user(); before=deepcopy(principal)
    req=Request({'type':'http','headers':[]})
    assert context.apply(None,principal,req) is principal
    assert principal==before
    assert context.order_allowed(principal,{'brand_id':'another'})


def test_unknown_header_and_unlisted_endpoint_are_rejected_before_db_reads():
    for value in ('evening','Morning','morning,evening',''):
        req=Request({'type':'http','headers':[(b'x-delivery-period',value.encode())]})
        with pytest.raises(HTTPException) as e:context.apply(None,user(),req)
        assert e.value.status_code==400
    req=Request({'type':'http','headers':[(b'x-delivery-period',b'morning')],
                 'endpoint':SimpleNamespace(__module__='app.admin',__name__='export')})
    with pytest.raises(HTTPException) as e:context.apply(None,user(),req)
    assert e.value.status_code==403
