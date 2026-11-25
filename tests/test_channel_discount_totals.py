"""Customer discounts reconcile without silently reallocating branch proceeds."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from app import channel_commercial as channel, fiscal, invoice_profiles


@pytest.mark.parametrize('branch,beneficiary', [('kps-branch-1', 'reem'), ('kps-branch-11', 'sami')])
@pytest.mark.parametrize('discount', [0, 1, 100, 999, 1000])
def test_customer_discount_reconciles_and_preserves_gross_review_basis(branch, beneficiary, discount):
    quote = dict(subtotal_halalas=1000, delivery_fee_halalas=200,
                 discount_halalas=discount, total_halalas=1200-discount)
    original = deepcopy(quote)
    result = channel.capture(invoice_profiles.defaults('kps'), branch, quote, 'test-owner')
    assert quote == original
    assert result['settlement_beneficiary'] == beneficiary
    assert result['settlement_amount_halalas'] == 1000
    assert result['settlement_basis'] == 'items_gross_excluding_delivery'
    assert result['settlement_status'] == 'review_required'
    assert result['discount_halalas'] == discount
    assert result['total_halalas'] == 1200-discount
    assert result['tax_halalas'] == fiscal.inclusive_tax(1200-discount)


def test_amendment_recalculates_discount_without_rewriting_original_or_owner():
    original = channel.capture(invoice_profiles.defaults('kps'), 'kps-branch-11',
        dict(subtotal_halalas=1000, delivery_fee_halalas=200, total_halalas=1200), 'original-actor')
    before = deepcopy(original)
    revised = channel.amounts(original, dict(subtotal_halalas=2000, delivery_fee_halalas=200,
        discount_halalas=300, total_halalas=1900))
    assert original == before
    assert revised['settlement_beneficiary'] == 'sami'
    assert revised['settlement_amount_halalas'] == 2000
    assert revised['discount_halalas'] == 300
    assert revised['total_halalas'] == 1900
    assert revised['audit_actor'] == 'original-actor'


@pytest.mark.parametrize('changed', [
    {'discount_halalas': -1}, {'discount_halalas': 1001}, {'discount_halalas': True},
    {'discount_halalas': 100.0}, {'discount_halalas': '100'}, {'total_halalas': 1101},
    {'subtotal_halalas': 1000.5}, {'delivery_fee_halalas': False},
])
def test_invalid_discount_or_amounts_fail_closed(changed):
    quote = dict(subtotal_halalas=1000, delivery_fee_halalas=200,
                 discount_halalas=100, total_halalas=1100)
    with pytest.raises(HTTPException) as exc:
        channel.amounts({}, quote | changed)
    assert exc.value.status_code == 409
    assert exc.value.detail['code'] == 'SETTLEMENT_TOTAL_MISMATCH'
