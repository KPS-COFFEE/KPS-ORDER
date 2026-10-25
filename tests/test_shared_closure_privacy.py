"""Shared actuals remain visible without exposing accounting expectations."""
from copy import deepcopy
from app.blind_closure import driver_view


def test_shared_declaration_hides_ledger_expectations_at_every_depth():
    original = {
        'declaration': {'cash_halalas': 1900, 'card_halalas': 800,
            'expected_snapshot': [{'expected_halalas': 2000, 'brand_id': 'kps'}]},
        'expected_cash_halalas': 2000, 'cash_difference_halalas': -100,
        'receipt': {'received_halalas': 1700},
        'pending': [{'expected_cash_halalas': 2000, 'cash_difference_halalas': -100,
                     'financial_state': 'awaiting_allocation'}],
    }
    untouched = deepcopy(original)
    visible = driver_view({'role':'driver'}, original)
    assert visible == {'declaration': {'cash_halalas':1900, 'card_halalas':800},
                       'receipt': {'received_halalas':1700},
                       'pending': [{'financial_state':'awaiting_allocation'}]}
    assert original == untouched
    assert driver_view({'role':'delivery_manager'}, original) is original
