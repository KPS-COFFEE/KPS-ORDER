"""Canonical parity, historical immutability and rollout guards."""
from copy import deepcopy
from unittest.mock import Mock, patch
import pytest
from app import operations, blind_closure
from app.accounting import driver_settlement_values
from app.shift_reconciliation import closed_shift, meaningful_reason


def fixture_rows(latest=None):
    shift = dict(id='driver-shift', brand_id='kps', kind='driver', status='closed')
    snap = dict(operational_date='2026-10-23', shift=dict(submission=dict(
        expected_halalas=19750, expected_card_halalas=48600,
        handover_halalas=17550, terminal_total_halalas=48550,
        difference_halalas=-2200, retained_halalas=0)),
        summary=dict(completed_orders=37,gross_completed_halalas=68350,net_collections_halalas=68350),
        external_enbishan_totals={})
    c = Mock()
    def execute(sql, params):
        result = Mock()
        if 'FROM closure_reports' in sql:
            assert params == ('driver-shift','kps')
            result.fetchone.return_value = dict(id='report-1',snapshot=snap)
        elif 'FROM driver_accounting_settlements' in sql:
            assert params == ('report-1','kps')
            result.fetchone.return_value = latest
        else:
            result.fetchone.return_value = None
        return result
    c.execute.side_effect = execute
    return c,shift,snap


@pytest.mark.parametrize('latest', [None,
    dict(revision=1,balance_approved=False,bank_halalas=48550,cash_due_halalas=17550,cash_received_halalas=17550),
    dict(revision=2,balance_approved=True,bank_halalas=48550,cash_due_halalas=17550,cash_received_halalas=17550),
    dict(revision=3,balance_approved=True,bank_halalas=48600,cash_due_halalas=19750,cash_received_halalas=19750)])
def test_operations_uses_exact_accounting_values_and_preserves_original(latest):
    c,shift,snap=fixture_rows(latest)
    original=deepcopy(snap)
    result=closed_shift(c,shift)
    for key,value in driver_settlement_values(snap,latest).items():
        assert result[key]==value
    assert result['summary']['completed_orders']==37
    assert result['operational_date']=='2026-10-23'
    assert snap==original
    assert all('FROM orders' not in call.args[0] for call in c.execute.call_args_list)
    assert result['variance_pending']==bool(latest and latest['revision']==2)


def test_open_shift_does_not_claim_final_reconciliation():
    c,shift,_=fixture_rows()
    assert closed_shift(c,{**shift,'status':'open'}) is None
    c.execute.assert_not_called()


def test_missing_snapshot_is_not_zero_or_balanced():
    c,shift,_=fixture_rows()
    c.execute.side_effect=None
    c.execute.return_value.fetchone.return_value=None
    assert closed_shift(c,shift)['state']=='missing_snapshot'


def test_driver_never_sees_reconciliation_even_nested():
    c,shift,_=fixture_rows()
    data={'ledgers':[{'reconciliation':closed_shift(c,shift)}]}
    assert blind_closure.driver_view({'role':'driver'},data,shift_money=True)=={'ledgers':[{}]}


@pytest.mark.parametrize('reason',['','0','٠','12345','-----','ok','   '])
def test_placeholder_reason_rejected(reason):
    assert not meaningful_reason(reason)


def test_real_reason_is_accepted():
    assert meaningful_reason('فرق شبكة قيد المراجعة مع إيصال الجهاز')


def test_website_rollout_rejects_new_external_summary_but_preserves_legacy():
    c=Mock();c.execute.return_value.fetchall.return_value=[{'city':'حفر الباطن'}]
    shift=dict(brand_id='kps',kind='driver',branch_ids=['branch'])
    with patch('app.central_operations.enabled',return_value=True):
        assert not operations.external_enbishan_eligible(c,shift)
        c.execute.assert_not_called()
        assert operations.external_enbishan_eligible(c,{**shift,'external_enbishan':{'revision':1,'cash_halalas':28250}})
    with patch('app.central_operations.enabled',return_value=False):
        assert operations.external_enbishan_eligible(c,shift)

from test_operations import ops
from test_driver_accounting_settlements import make_report
from test_accounting import accountant
from app import db


@pytest.mark.postgres
def test_live_shift_and_accounting_endpoints_share_revision(ops):
    actor,post,_,_=ops
    accountant()
    original,_=make_report()
    def compare():
        accounting=actor('accountant').get('/api/accounting/driver-settlements').json()['settlements'][0]
        response=actor('manager').get('/api/shifts/settlement-shift')
        assert response.status_code==200,response.text
        linked=response.json()['reconciliation']
        for key in ('revision','system_total_halalas','system_cash_halalas','system_bank_halalas',
                    'bank_halalas','cash_due_halalas','cash_received_halalas','reconciliation_complete',
                    'net_difference_halalas','variance_pending','stage'):
            assert linked[key]==accounting[key],key
    compare()
    post('accountant','/api/accounting/driver-settlements/settlement-report/approve-balance',
         dict(expected_revision=0,bank_halalas=5100,cash_due_halalas=9900,note='TEST verified cash/card allocation'))
    compare()
    with db.tx() as c:
        assert c.execute("SELECT snapshot FROM closure_reports WHERE id='settlement-report'").fetchone()['snapshot']==original
    assert actor('driver').get('/api/shifts/settlement-shift').json().get('reconciliation') is None
