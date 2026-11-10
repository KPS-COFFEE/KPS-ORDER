"""Customer milestones must be grounded in order-specific operational evidence."""
import pytest
from app.customer_progress import progress


def order(**changes):
    return {'service': 'delivery', 'preparation_status': 'accepted', 'delivery_status': 'assigned',
            'snapshot': {'delivery_workflow': 'manager_dispatch'}, **changes}


def test_manager_dispatch_print_and_departure_are_independent_evidence():
    row = order(preparation_status='handed_over', delivery_status='picked_up')
    assert progress(row)['stage'] == 'received'
    assert progress(row, printed=True)['stage'] == 'preparing'
    assert progress(row, departed=True)['stage'] == 'on_the_way'
    assert progress(row)['stages'] == ['received', 'preparing', 'on_the_way', 'delivered']


@pytest.mark.parametrize('preparation,expected', [('accepted','received'), ('preparing','preparing'), ('ready','ready'), ('handed_over','ready'), ('completed','delivered')])
def test_staff_evidence_and_no_false_branch_handover(preparation, expected):
    assert progress(order(snapshot={'delivery_workflow':'branch_without_manager'}, preparation_status=preparation))['stage'] == expected


def test_truthful_exceptions_and_pickup_flow():
    assert progress(order(delivery_status='problem'), departed=True) == {
        'stage':'on_the_way', 'stages':['received','preparing','on_the_way','delivered'], 'exception':'problem'}
    assert progress(order(preparation_status='cancelled'))['exception'] == 'cancelled'
    result = progress(order(service='pickup', preparation_status='ready'))
    assert result['stages'] == ['received','preparing','ready','delivered']
    assert result['stage'] == 'ready'
    assert progress(order(delivery_status='delivered'))['stage'] == 'delivered'


def test_legacy_missing_workflow_uses_canonical_branch_default():
    assert progress(order(snapshot={}, preparation_status='ready'))['stage'] == 'ready'
