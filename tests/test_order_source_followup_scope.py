"""Named debt follow-up uses accounting's explicit branch scope."""
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from app import order_financial_treatments as treatments


@pytest.mark.parametrize('role,home,branches,allowed', [
    ('admin', None, ['branch'], True),
    ('admin', None, [], False),
    ('owner', 'other-brand', [], False),
    ('owner', 'other-brand', ['branch'], True),
    ('owner', None, [], True),
])
def test_accounting_followup_checks_projected_branch_scope(monkeypatch, role, home, branches, allowed):
    target = dict(id='followup', active=True, role=role, brand_id='kps',
                  home_brand_id=home, branch_ids=branches, permissions=['accounting_review'])
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = target
    monkeypatch.setattr(treatments.multibrand, 'enabled', lambda: False)
    if allowed:
        assert treatments.eligible_followup(conn, {}, dict(brand_id='kps', branch_id='branch'), 'followup') == target
    else:
        with pytest.raises(HTTPException) as exc:
            treatments.eligible_followup(conn, {}, dict(brand_id='kps', branch_id='branch'), 'followup')
        assert exc.value.status_code == 422


def test_followup_rechecks_live_multibrand_projection(monkeypatch):
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = dict(id='followup', active=True,
        role='owner', brand_id='original', branch_ids=['branch'])
    monkeypatch.setattr(treatments.multibrand, 'enabled', lambda: True)
    monkeypatch.setattr(treatments.multibrand, 'project', lambda *args: None)
    with pytest.raises(HTTPException):
        treatments.eligible_followup(conn, {}, dict(brand_id='kps', branch_id='branch'), 'followup')
