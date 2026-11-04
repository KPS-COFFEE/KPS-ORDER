"""Approved features require explicit flags; source corrections remain owner-held."""
import pytest
from app import manual_bank_matching,order_financial_treatments,employee_ticket_workflow

FEATURES=[(order_financial_treatments.enabled,'KPS_ORDER_SOURCE_TREATMENTS_ENABLED')]
ACTIVATED=[(manual_bank_matching.enabled,'KPS_MANUAL_BANK_MATCHING_ENABLED'),
 (employee_ticket_workflow.enabled,'KPS_ADMIN_FACTUAL_WORKFLOW_ENABLED')]

@pytest.mark.parametrize('enabled,key',FEATURES)
def test_prepared_feature_is_off_by_default_and_cannot_activate_in_production(monkeypatch,enabled,key):
    monkeypatch.delenv(key,raising=False)
    assert not enabled()
    monkeypatch.setenv(key,'true')
    monkeypatch.setenv('APP_ENV','production')
    assert not enabled()
    monkeypatch.setenv('APP_ENV','development')
    assert enabled()

@pytest.mark.parametrize('enabled,key',FEATURES)
def test_isolated_preparation_requires_explicit_flag(monkeypatch,enabled,key):
    monkeypatch.setenv('APP_ENV','testing')
    monkeypatch.delenv(key,raising=False)
    assert not enabled()
    monkeypatch.setenv(key,'true')
    assert enabled()


def test_production_route_guards_reject_before_opening_a_financial_transaction(monkeypatch):
    from unittest.mock import Mock
    from fastapi import HTTPException
    from app import db
    monkeypatch.setenv('APP_ENV','production')
    for _,key in FEATURES:monkeypatch.setenv(key,'true')
    user=dict(id='owner',role='owner',active=True,brand_id='kps')
    transaction=Mock(side_effect=AssertionError('Disabled development route must not open a transaction'))
    monkeypatch.setattr(db,'tx',transaction)
    connection=Mock()
    for call in (lambda:order_financial_treatments.scoped(connection,user,'order'),):
        with pytest.raises(HTTPException) as error:call()
        assert error.value.status_code==404
    transaction.assert_not_called();connection.execute.assert_not_called()


def test_production_hold_preserves_existing_source_credit_valuation(monkeypatch):
    from unittest.mock import Mock
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.setenv('KPS_ORDER_SOURCE_TREATMENTS_ENABLED','true')
    monkeypatch.setattr(order_financial_treatments,'price_credit_rows',lambda *args:[dict(
        credit_halalas=550,original_total_halalas=2500)])
    assert not order_financial_treatments.enabled()
    assert order_financial_treatments.effective_total(Mock(),dict(id='order',total_halalas=2500))==1950


@pytest.mark.parametrize('enabled,key',ACTIVATED)
def test_approved_features_require_explicit_flags_in_production(monkeypatch,enabled,key):
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.delenv(key,raising=False)
    assert not enabled()
    monkeypatch.setenv(key,'true')
    assert enabled()
    monkeypatch.setenv(key,'false')
    assert not enabled()
