"""Review grants can open their workspace without gaining report or owner access."""
import pytest
from app import db
from test_operations import ops
from test_accounting import accountant

pytestmark = pytest.mark.postgres


def test_review_only_accountant_can_read_workspace_options_but_not_sales_reports(ops):
    actor, _, _, _ = ops
    accountant()
    with db.tx() as conn:
        conn.execute("UPDATE users SET permissions='[\"accounting_review\"]' WHERE id='ops-accountant'")
    client = actor('accountant')
    result = client.get('/api/accounting/options')
    assert result.status_code == 200, result.text
    assert result.json()['can_review'] is True
    assert result.json()['can_report'] is False
    assert {b['id'] for b in result.json()['branches']} == {'ops-branch'}
    assert client.get('/api/accounting/reports').status_code == 403
    assert client.get('/api/owner-dashboard/product-sales').status_code == 403
    assert client.get('/api/owner-dashboard').status_code == 403
    with db.tx() as conn:
        conn.execute("UPDATE users SET permissions='[]' WHERE id='ops-accountant'")
    assert actor('accountant').get('/api/accounting/options').status_code == 403
