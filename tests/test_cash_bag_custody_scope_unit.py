"""Cross-brand bank evidence must use the physical bag's authorized brand."""
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

from app import cash_bag_custody as custody
from app import manual_bank_matching
from app.accounting import TZ


def test_mixed_bag_bank_recovery_checks_and_claims_custody_brand_not_callers_brand(monkeypatch):
    now = datetime.now(TZ)
    caller = {'id': 'independent-approver', 'brand_id': 'enbishan', 'role': 'admin'}
    holding_principal = {**caller, 'brand_id': 'kps', 'branch_ids': ['kps-bag-branch']}
    other_principal = {**caller, 'branch_ids': ['enbishan-source-branch']}
    count = {'id': 'count-current', 'received_cash_halalas': 700,
             'card_halalas': 850, 'declared_card_halalas': 900, 'created_at': now}
    receipt = {'id': 'receipt-current', 'received_halalas': 700, 'received_by': 'manager-receiver'}
    count['counted_by'] = 'accounting-counter'
    bag = {'id': 'physical-bag', 'branch_id': 'kps-bag-branch'}
    day = {'user_id': 'physical-driver', 'anchor_shift_id': 'anchor'}
    # These represent the fresh per-brand principals returned by workday scope
    # authorization, in an order different from the bag's holding brand.
    legs = [(other_principal, {'brand_id': 'enbishan', 'branch_ids': ['enbishan-source-branch']}),
            (holding_principal, {'brand_id': 'kps', 'branch_ids': ['kps-bag-branch']})]
    treatment = {'id': 'custody-treatment', 'status': 'approved', 'payload': {
        'kind': 'outstanding', 'expected_count_id': count['id'], 'expected_receipt_id': receipt['id'],
        'cash_variance_halalas': 0, 'card_variance_halalas': -50}, 'recoveries': []}
    recovery_data = {
        'expected_count_id': count['id'], 'expected_receipt_id': receipt['id'],
        'direction': 'receivable_recovery', 'cash_amount_halalas': 0, 'card_amount_halalas': 50,
        'bank_reference': 'registered-bank-line', 'occurred_on': now.date(),
        'evidence_reference': 'Actual independent registered bank movement',
        'note': 'Match the physical custody obligation without posting money'}
    pending = {'id': 'recovery-pending', 'status': 'pending', 'requested_by': 'recovery-proposer',
               'payload': recovery_data}
    treatment['recoveries'] = [pending]

    class Connection:
        def execute(self, query, params):
            assert 'SELECT brand_id FROM branches' in query
            assert params == ('kps-bag-branch',)
            return SimpleNamespace(fetchone=lambda: {'brand_id': 'kps'})
    connection = Connection()

    @contextmanager
    def transaction():
        yield connection

    monkeypatch.setattr(custody.db, 'tx', transaction)
    monkeypatch.setattr(custody.db, 'idempotent', lambda _c, _actor, _key, _action, _body, perform: perform())
    monkeypatch.setattr(custody.db, 'audit', lambda *args: None)
    monkeypatch.setattr(custody, 'source', lambda *args: (bag, day, legs, count, receipt))
    monkeypatch.setattr(custody, 'history', lambda *args: [treatment])
    from app import central_workdays
    monkeypatch.setattr(central_workdays, 'lock_lanes', lambda c: None)
    monkeypatch.setattr(manual_bank_matching, 'enabled', lambda: True)
    checks = []

    def bank_line(_c, principal, reference):
        # Fail if a bank line visible only to the unrelated request brand is
        # mistaken for evidence belonging to the bag's custody brand.
        assert principal is holding_principal
        assert reference == 'registered-bank-line'
        checks.append(('validate', principal['brand_id']))
        return {'amount_halalas': 50, 'unallocated_halalas': 50, 'received_on': now.date()}

    def claim(_c, principal, reference, amount, kind, identifier):
        assert principal is holding_principal
        assert (reference, amount, kind, identifier) == ('registered-bank-line', 50, 'financial_recovery', pending['id'])
        checks.append(('claim', principal['brand_id']))

    monkeypatch.setattr(manual_bank_matching, 'bank_line', bank_line)
    monkeypatch.setattr(manual_bank_matching, 'claim_bank_line', claim)
    request = SimpleNamespace(headers={})
    custody.propose_recovery(bag['id'], treatment['id'], custody.Recovery(**recovery_data), request, user=caller)
    result = custody.approve_recovery(bag['id'], treatment['id'], pending['id'], custody.RecoveryApproval(
        expected_count_id=count['id'], expected_receipt_id=receipt['id'], confirmed=True,
        evidence_reference='Independent custody bank evidence reviewed',
        note='Approve the existing actual bank movement against custody obligation'), request, user=caller)
    assert result['status'] == 'approved' and result['no_money_posted']
    assert checks == [('validate', 'kps'), ('validate', 'kps'), ('claim', 'kps')]
    assert caller['brand_id'] == 'enbishan'
