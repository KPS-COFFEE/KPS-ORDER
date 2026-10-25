"""Guidance follows source states, never invents handoffs or financial completion."""
import pytest
from app.administrative_guidance import order, employee_ticket


def source(**changes):
    return dict(service='delivery',preparation_status='ready',delivery_status='pending',**changes)


def test_independent_cancellation_and_collection_actions_remain_visible():
    o=source()
    pending={'id':'case','status':'pending','kind':'collection_shortfall','pending_amount_halalas':550}
    request={'id':'request','decision':None}
    g=order(o,financial_case=pending,requests=[request],viewer_role='delivery_manager')
    assert [s['responsible_role'] for s in g['stages']]==['delivery_manager','accounting']
    assert g['financial_status']=='pending'
    assert all(not s['return_supported'] for s in g['stages'])
    assert g['assignment_recorded'] is False
    assert '550' not in str(g)  # Describes the path without manufacturing amount displays.


def test_rejected_cancellation_does_not_remove_pending_collection_step():
    o=source()
    g=order(o,financial_case={'id':'case','status':'pending','kind':'collection_shortfall','pending_amount_halalas':550},requests=[{'id':'request','decision':{'status':'rejected'}}],viewer_role='owner')
    assert [s['code'] for s in g['stages']]==['collection_recovery']


def test_operational_delivery_is_independent_of_financial_resolution():
    o={'service':'delivery','preparation_status':'handed_over','delivery_status':'delivered'}
    g=order(o,financial_case={'id':'case','status':'pending','kind':'collection_shortfall','pending_amount_halalas':0},viewer_role='owner')
    assert g['operational_status']=='completed' and g['financial_status']=='pending'
    assert [s['code'] for s in g['stages']]==['financial_case_review']


@pytest.mark.parametrize('role',['customer','driver'])
def test_no_administrative_financial_metadata_for_customer_or_driver(role):
    assert order(source(),financial_case={'status':'pending'},viewer_role=role) is None


def test_no_case_does_not_assert_bank_or_accounting_completion():
    g=order({'service':'delivery','preparation_status':'handed_over','delivery_status':'delivered'},viewer_role='owner')
    assert g['financial_status']=='no_case_recorded' and g['stages']==[]
    assert g['last_recorded_event'] is None


def test_financial_case_does_not_invent_named_assignee_or_later_correction():
    g=order(source(),financial_case={'id':'case','status':'pending','kind':'cancellation','pending_amount_halalas':1000},viewer_role='owner')
    s=g['stages'][0]
    assert s['code']=='refund_execution' and s['responsible_role']=='accounting'
    assert not s['return_supported'] and s['limitation']
    assert 'assignee_id' not in s


def test_only_real_recorded_last_event_is_exposed_without_private_details():
    event={'id':'event','event_type':'order.collected','actor_id':'driver','actor_name':'Driver','created_at':'time','details':{'secret':'administrative'}}
    g=order(source(),events=[event],viewer_role='owner')
    assert g['last_recorded_event']['id']=='event'
    assert 'details' not in g['last_recorded_event']


@pytest.mark.parametrize('status',['open','approved','resolved','rejected'])
def test_employee_ticket_decision_never_establishes_execution(status):
    g=employee_ticket({'status':status})
    assert g['review_only'] and g['execution_status']=='not_established_by_ticket'
    assert not g['return_supported']

from test_operations import ops


@pytest.mark.postgres
def test_existing_order_dialog_exposes_only_staff_guidance(ops):
    actor,post,make_order,_=ops
    oid=make_order()
    request=post('customer',f'/api/order-requests/orders/{oid}',{'kind':'cancel','note':'Please cancel this delivery'})
    staff=actor('manager').get(f'/api/order-amendments/orders/{oid}')
    assert staff.status_code==200,staff.text
    guidance=staff.json()['guidance']
    assert guidance['stages'][0]['code']=='cancellation_review'
    assert guidance['stages'][0]['source_id']==request['id']
    customer=actor('customer').get(f'/api/order-amendments/orders/{oid}')
    assert customer.status_code==200 and customer.json()['guidance'] is None
    outsider=actor('other').get(f'/api/order-amendments/orders/{oid}')
    assert outsider.status_code==404
    journey=actor('manager').get(f'/api/order-financial-cases/orders/{oid}/journey')
    assert journey.status_code==200 and journey.json()['guidance']['financial_status']=='no_case_recorded'
    assert journey.json()['guidance']['stages'][0]['code']=='cancellation_review'


@pytest.mark.postgres
def test_employee_review_reply_preserves_nonexecution_guidance(ops):
    actor,post,_,_=ops
    created=post('driver','/api/employee-tickets',{'type':'service_request','issue':'shift_issue','reason':'shift_start','description':'Cannot start my shift'})
    reviewed=post('owner',f'/api/employee-tickets/{created["id"]}/review',{'expected_version':1,'status':'approved','note':'Request reviewed; execution remains separate'})
    detail=actor('driver').get('/api/employee-tickets/'+created['id'])
    assert detail.status_code==200,detail.text
    ticket=detail.json()['ticket']
    assert ticket['status']=='approved'
    assert ticket['guidance']['execution_status']=='not_established_by_ticket'
    assert len(detail.json()['history'])==2


def test_expired_amendment_offers_only_supported_exit_before_new_offer():
    from datetime import datetime, timezone, timedelta
    g=order(source(),amendments=[{'id':'expired','status':'offered','expires_at':datetime.now(timezone.utc)-timedelta(seconds=1)}],viewer_role='owner')
    assert [s['code'] for s in g['stages']]==['amendment_expired']
    assert g['stages'][0]['responsible_role']=='delivery_manager'
