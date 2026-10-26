"""Physical receipt boundary remains independent from entitlement review."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app import aggregate_driver_closure as physical
from app import driver_incentives


@pytest.fixture
def receipt_boundary(monkeypatch):
    user = {'id': 'actual-manager', 'role': 'delivery_manager'}
    declaration = {'id': 'immutable-declaration', 'cash_halalas': 22600,
                   'card_halalas': 37650, 'retained_halalas': 0,
                   'expected_snapshot': [{'shift_id': 'driver-leg',
                      'manager_user_id': user['id'], 'permanent_float_halalas': 0}]}
    driver = {'id': 'driver-leg', 'kind': 'driver', 'status': 'submitted',
              'manager_shift_id': 'manager-leg'}
    day = {'id': 'physical-day', 'user_id': 'driver'}
    writes = []
    connection = Mock()

    def execute(query, params=()):
        result = Mock()
        if query.startswith('SELECT created_at'):
            result.fetchone.return_value = {'created_at': datetime.now(timezone.utc)}
        else:
            result.fetchone.return_value = None
        if query.startswith(('INSERT', 'UPDATE')):
            writes.append((query, params))
        return result

    connection.execute.side_effect = execute

    @contextmanager
    def transaction():
        yield connection

    monkeypatch.setattr(physical.db, 'tx', transaction)
    monkeypatch.setattr(physical.db, 'idempotent', lambda c, actor, key, op, body, perform: perform())
    monkeypatch.setattr(physical.db, 'audit', Mock())
    monkeypatch.setattr(physical.auth, 'has_permission', lambda actor, permission: permission == 'shifts_delegate_close')
    monkeypatch.setattr(physical.workdays, 'lock_lanes', Mock())
    monkeypatch.setattr(physical, 'authorize', lambda *args, **kwargs: (day, [(user, driver)]))
    monkeypatch.setattr(physical, 'latest', lambda *args: declaration)
    operational = Mock()
    monkeypatch.setattr(physical, 'validate_operational', operational)
    monkeypatch.setattr(physical, 'view', lambda *args: {'state': 'awaiting_accounting'})
    pending_review = Mock(side_effect=HTTPException(409, 'Pending cancelled-order responsibility review'))
    monkeypatch.setattr(driver_incentives, 'require_decisions', pending_review)
    request = Mock(headers={'Idempotency-Key': 'physical-cash-receipt'})
    body = physical.PhysicalReceipt(declaration_id=declaration['id'], received_halalas=22600,
                                    confirmed=True, reference='Actual handover witnessed by manager')
    return user, declaration, driver, writes, operational, pending_review, request, body


def test_receipt_records_exact_cash_while_entitlement_review_stays_pending(receipt_boundary):
    user, declaration, driver, writes, operational, pending_review, request, body = receipt_boundary
    original = deepcopy(declaration)
    result = physical.receive('driver-leg', body, request, user)
    assert result['receipt_id'] and result['state'] == 'awaiting_accounting'
    operational.assert_called_once()
    pending_review.assert_not_called()
    assert declaration == original
    receipts = [(query, values) for query, values in writes
                if query.startswith('INSERT INTO shared_driver_closure_receipts')]
    assert len(receipts) == 1
    assert receipts[0][1][2:4] == (22600, user['id'])
    assert any('staffing_ended_at' in query for query, _ in writes)
    assert not any('shared_driver_closure_allocations' in query for query, _ in writes)


@pytest.mark.parametrize('blocked', ['amount', 'confirmation', 'recipient', 'state', 'operational'])
def test_receipt_still_checks_amount_actual_recipient_and_operational_guards(receipt_boundary, blocked):
    user, declaration, driver, writes, operational, pending_review, request, body = receipt_boundary
    if blocked == 'amount':
        body = body.model_copy(update={'received_halalas': 22601})
    elif blocked == 'confirmation':
        body = body.model_copy(update={'confirmed': False})
    elif blocked == 'recipient':
        declaration['expected_snapshot'][0]['manager_user_id'] = 'different-manager'
    elif blocked == 'state':
        driver['status'] = 'open'
    else:
        operational.side_effect = HTTPException(409, 'Actual unresolved operational task')
    with pytest.raises(HTTPException) as failure:
        physical.receive('driver-leg', body, request, user)
    assert failure.value.status_code == (422 if blocked in {'amount', 'confirmation'} else 403 if blocked == 'recipient' else 409)
    assert writes == []
    pending_review.assert_not_called()
