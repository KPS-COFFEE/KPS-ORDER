"""A shared obligation remains pending until every original allocation report
has documented review/recovery evidence; missing evidence never becomes zero."""
from unittest.mock import Mock, patch
from app.shared_financial_treatments import allocated_review


def connection(second_report=True):
    c=Mock()
    c.execute.return_value.fetchone.side_effect=[
        {'kind':'driver'},{'legs':[{'shift_id':'k'},{'shift_id':'e'}]},
        {'id':'report-k'},None,{'id':'report-e'} if second_report else None,None,
    ]
    return c


def test_missing_report_keeps_obligation_pending_and_amount_unverified():
    c=connection(second_report=False)
    with patch('app.shared_financial_treatments.ft.reviewed_values',return_value={}) as reviewed:
        result=allocated_review(c,'day')
    assert result['financially_complete'] is False and result['period_approval_allowed'] is False
    assert result['verified'] is False and result['outstanding_receivable_halalas'] is None
    assert reviewed.call_count==1


def test_partial_recovery_retains_exact_remaining_and_blocks_unapproved_period():
    values=[{'reconciliation_complete':True,'financial_approval_allowed':True,'financially_complete':True,'period_approval_allowed':True,'outstanding_receivable_halalas':0,'outstanding_payable_halalas':0},
        {'reconciliation_complete':True,'financial_approval_allowed':True,'financially_complete':False,'period_approval_allowed':False,'outstanding_receivable_halalas':50,'outstanding_payable_halalas':0}]
    with patch('app.shared_financial_treatments.ft.reviewed_values',side_effect=values):result=allocated_review(connection(),'day')
    assert result['verified'] is True and result['outstanding_receivable_halalas']==50
    assert result['financially_complete'] is False and result['period_approval_allowed'] is False


def test_all_reports_settled_by_canonical_evidence_release_parent_guard():
    complete={'reconciliation_complete':True,'financial_approval_allowed':True,'financially_complete':True,'period_approval_allowed':True,'outstanding_receivable_halalas':0,'outstanding_payable_halalas':0}
    with patch('app.shared_financial_treatments.ft.reviewed_values',return_value=complete):result=allocated_review(connection(),'day')
    assert result['verified'] is True and result['financially_complete'] is True
    assert result['period_approval_allowed'] is True and result['outstanding_receivable_halalas']==0
    assert [r['report_id'] for r in result['reports']]==['report-k','report-e']
