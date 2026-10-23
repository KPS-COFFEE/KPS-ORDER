"""Real PostgreSQL ledger reporting; no provider or operational data access."""
from datetime import date
import pytest
from fastapi import HTTPException
from app import db
from app.branch_payment_reports import period
from test_branch_refunds import paid_order, refund_provider, configure_paid, authorize
from test_branch_payments import branch_payment
from test_operations import ops


def test_period_uses_riyadh_midnight_and_inclusive_end():
    start, end = period(date(2026, 10, 16), date(2026, 10, 17))
    assert start.isoformat() == '2026-10-16T00:00:00+03:00'
    assert end.isoformat() == '2026-10-18T00:00:00+03:00'
    for dates in [(date(2026, 2, 1), date(2026, 1, 1)), (date(2020, 1, 1), date(2026, 1, 1))]:
        with pytest.raises(HTTPException): period(*dates)


@pytest.mark.postgres
def test_branch_payment_report_refunds_pending_and_scope(paid_order, refund_provider):
    actor, post, oid, a, state, calls = configure_paid(paid_order, refund_provider)
    url = '/api/branch-payments/report?branch_id=ops-branch'
    initial = actor('owner').get(url)
    assert initial.status_code == 200
    assert initial.json()['totals']['received_halalas'] == 1000
    assert initial.json()['totals']['net_movement_halalas'] == 1000
    row = authorize(post, oid, 300)
    waiting = actor('owner').get(url).json()
    assert waiting['totals']['pending_refund_halalas'] == 300
    assert waiting['totals']['refunded_halalas'] == 0
    post('owner', '/api/branch-payments/refunds/'+row['id']+'/execute')
    result = actor('owner').get(url).json()
    assert result['totals']['refunded_halalas'] == 300
    assert result['totals']['pending_refund_halalas'] == 0
    assert result['totals']['net_movement_halalas'] == 700
    assert result['rows'][0]['seller_id'] == 'fixture-seller'
    assert result['rows'][0]['service'] == 'car'
    assert result['basis'] == 'verified_online_money_movements'
    assert actor('branch').get(url).status_code == 200
    assert actor('other').get(url).status_code == 404
    assert actor('customer').get(url).status_code == 403
    assert actor('driver').get(url).status_code == 403
    assert actor('owner').get('/api/branch-payments/report?branch_id=ops-branch-other').status_code == 404
    assert actor('branch').get('/api/branch-payments/report?branch_id=ops-branch-other').status_code == 404
    empty = actor('owner').get(url+'&start=2000-01-01&end=2000-01-01').json()
    assert empty['totals']['net_movement_halalas'] == 0 and empty['rows'] == []
    assert 'customer' not in str(result) and 'secret' not in str(result)
