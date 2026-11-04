"""Avoid an irrelevant policy query; retain explicit and live policy gates."""
from unittest.mock import Mock
import pytest
from app import sms_rollout


def test_mandatory_work_sms_context_does_not_read_irrelevant_pilot_policy(monkeypatch):
    monkeypatch.delenv('SESSION_CONTEXT_COOKIES_ENABLED', raising=False)
    conn = Mock()
    assert sms_rollout.context_cookies_enabled(conn) is True
    conn.execute.assert_not_called()


@pytest.mark.parametrize('override,expected', [('true', True), ('false', False), ('', False)])
def test_explicit_context_cookie_override_still_takes_precedence(monkeypatch, override, expected):
    monkeypatch.setenv('SESSION_CONTEXT_COOKIES_ENABLED', override)
    conn = Mock()
    assert sms_rollout.context_cookies_enabled(conn) is expected
    conn.execute.assert_not_called()


def test_optional_policy_is_fresh_on_each_call_when_work_sms_not_required(monkeypatch):
    monkeypatch.delenv('SESSION_CONTEXT_COOKIES_ENABLED', raising=False)
    monkeypatch.setattr(sms_rollout, 'work_sms_required', lambda: False)
    conn = Mock()
    conn.execute.return_value.fetchone.side_effect = [
        {'settings': {'employee_sms_rollout': {'mode': 'pilot'}}},
        {'settings': {'employee_sms_rollout': {'mode': 'disabled'}}},
    ]
    assert sms_rollout.context_cookies_enabled(conn) is True
    assert sms_rollout.context_cookies_enabled(conn) is False
    assert conn.execute.call_count == 2
