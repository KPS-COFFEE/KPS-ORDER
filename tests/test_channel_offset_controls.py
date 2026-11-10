"""Opposite channels remain separate even when the informational net is zero."""
from copy import deepcopy
from unittest.mock import Mock

from app.accounting import driver_settlement_values
from app.shift_reconciliation import closed_shift


def offset_snapshot():
    return {'shift': {'submission': {'expected_halalas': 20000,
        'expected_card_halalas': 20000, 'handover_halalas': 30000,
        'terminal_total_halalas': 10000}}, 'summary': {}}


def test_equal_cash_surplus_and_card_shortage_are_not_matched():
    snapshot=offset_snapshot(); original=deepcopy(snapshot)
    values=driver_settlement_values(snapshot,dict(revision=2,balance_approved=True,
        bank_halalas=10000,cash_due_halalas=30000,cash_received_halalas=30000))
    assert values['net_difference_halalas']==0
    assert values['reconciliation_complete']
    assert not values['channels_matched']
    assert values['has_channel_variance']
    assert values['channel_shortage_halalas']==10000
    assert values['channel_surplus_halalas']==10000
    assert snapshot==original


def test_each_channel_must_match_independently():
    values=driver_settlement_values(offset_snapshot(),dict(revision=3,balance_approved=True,
        bank_halalas=20000,cash_due_halalas=20000,cash_received_halalas=20000))
    assert values['channels_matched'] and not values['has_channel_variance']
    assert values['channel_shortage_halalas']==values['channel_surplus_halalas']==0


def test_unrecorded_receipt_does_not_claim_channel_match():
    values=driver_settlement_values(offset_snapshot(),dict(revision=1,balance_approved=True,
        bank_halalas=10000,cash_due_halalas=30000,cash_received_halalas=None))
    assert not values['channels_matched'] and not values['has_channel_variance']
    assert values['channel_shortage_halalas'] is None
    assert values['channel_surplus_halalas'] is None


def test_historical_net_zero_confirmation_cannot_hide_channel_offset():
    c=Mock()
    def execute(sql,params):
        result=Mock();result.fetchall.return_value=[]
        if 'FROM closure_reports' in sql:
            result.fetchone.return_value={'id':'r','snapshot':offset_snapshot()}
        elif 'FROM driver_accounting_settlements' in sql:
            result.fetchone.return_value=dict(revision=2,balance_approved=True,
                bank_halalas=10000,cash_due_halalas=30000,cash_received_halalas=30000)
        elif "accounting.variance_confirmed" in sql:
            result.fetchone.return_value={'id':'legacy-note'}
        else:result.fetchone.return_value=None
        return result
    c.execute.side_effect=execute
    values=closed_shift(c,dict(id='s',brand_id='b',kind='driver',status='closed'))
    assert values['variance_pending'] and not values['channels_matched']


def test_confirmation_endpoint_rejects_offset_without_posting_or_review(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    import pytest
    from fastapi import HTTPException
    from app import accounting, db, order_financial_cases
    snapshot=offset_snapshot()
    snapshot['shift'].update(id='s',opening_halalas=20000)
    snapshot['shift']['submission'].update(counted_halalas=30000,difference_halalas=10000)
    snapshot.update(custody_events=[],transfers=[],orders=[],collections=[],refunds=[],summary=dict(unique_orders=0,
        completed_orders=0,gross_completed_halalas=0,cash_collected_halalas=0,
        card_collected_halalas=0,cash_refunded_halalas=0,card_refunded_halalas=0,
        net_collections_halalas=0))
    row=dict(id='r',shift_id='s',user_id='driver',snapshot=snapshot)
    c=Mock();c.execute.return_value.fetchone.return_value=dict(revision=2,
        balance_approved=True,bank_halalas=10000,cash_due_halalas=30000,cash_received_halalas=30000)
    monkeypatch.setattr(db,'tx',lambda:nullcontext(c))
    monkeypatch.setattr(db,'idempotent',lambda conn,uid,key,scope,payload,fn:fn())
    audit=Mock();monkeypatch.setattr(db,'audit',audit)
    monkeypatch.setattr(accounting,'driver_settlement_scope',lambda *args:row)
    monkeypatch.setattr(accounting.auth,'has_permission',lambda *args:True)
    monkeypatch.setattr(order_financial_cases,'guard_financial_closure',lambda *args,**kwargs:None)
    body=accounting.DriverVarianceConfirmation(expected_revision=2,confirmed=True,
        note='Reviewed paper cannot settle opposite differences',action_taken='No actual correction')
    with pytest.raises(HTTPException) as error:
        accounting.confirm_driver_variance('r',body,SimpleNamespace(headers={}),user=dict(id='accountant'))
    assert error.value.status_code==409
    assert error.value.detail['code']=='FINANCIAL_TREATMENT_REQUIRED'
    audit.assert_not_called()
    assert all(call.args[0].lstrip().upper().startswith('SELECT') for call in c.execute.call_args_list)
