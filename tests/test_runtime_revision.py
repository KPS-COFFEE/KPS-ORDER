"""Runtime provenance cannot approve or label other code using a stale pin."""
import pytest
from app import activation, db
from app.runtime_revision import revision

ACTUAL = 'a' * 40
OTHER = 'b' * 40


@pytest.fixture(autouse=True)
def isolated_revision(monkeypatch):
    monkeypatch.delenv('RENDER_GIT_COMMIT', raising=False)
    monkeypatch.delenv('RUNTIME_REVISION', raising=False)


def test_local_execution_retains_explicit_fallback_and_unversioned_label(monkeypatch):
    assert revision() == ''
    assert revision('local-unversioned') == 'local-unversioned'
    monkeypatch.setenv('RUNTIME_REVISION', OTHER)
    assert revision('local-unversioned') == OTHER


@pytest.mark.parametrize('pin', ['', ACTUAL])
def test_actual_render_source_is_the_identity_when_unpinned_or_matching(monkeypatch, pin):
    monkeypatch.setenv('RENDER_GIT_COMMIT', ACTUAL)
    monkeypatch.setenv('RUNTIME_REVISION', pin)
    assert revision() == ACTUAL


@pytest.mark.parametrize('actual,pin', [(ACTUAL, OTHER), (ACTUAL, 'local-unversioned'), ('invalid', OTHER), (ACTUAL.upper(), ACTUAL)])
def test_mismatch_or_invalid_platform_source_fails_closed(monkeypatch, actual, pin):
    monkeypatch.setenv('RENDER_GIT_COMMIT', actual)
    monkeypatch.setenv('RUNTIME_REVISION', pin)
    with pytest.raises(RuntimeError, match='revision'):
        revision()


def test_old_approval_cannot_pass_for_new_actual_code(monkeypatch):
    monkeypatch.setenv('RENDER_GIT_COMMIT', ACTUAL)
    monkeypatch.setenv('RUNTIME_REVISION', OTHER)
    for key, value in {'KPS_MULTIBRAND_ENABLED':'true', 'KPS_MULTIBRAND_ACCEPTANCE_SHA':OTHER,
                       'KPS_MULTIBRAND_OWNER_APPROVAL_SHA':OTHER}.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RuntimeError, match='differs'):
        activation.enabled('multibrand')
    monkeypatch.setenv('RUNTIME_REVISION', ACTUAL)
    with pytest.raises(Exception) as failure:
        activation.enabled('multibrand')
    assert failure.value.detail['code'] == 'FEATURE_RELEASE_NOT_APPROVED'
    for key in ('KPS_MULTIBRAND_ACCEPTANCE_SHA','KPS_MULTIBRAND_OWNER_APPROVAL_SHA'):
        monkeypatch.setenv(key, ACTUAL)
    assert activation.enabled('multibrand') is True


def test_startup_rejects_stale_pin_before_opening_connection_or_migrating(monkeypatch):
    monkeypatch.setenv('RENDER_GIT_COMMIT', ACTUAL)
    monkeypatch.setenv('RUNTIME_REVISION', OTHER)
    def must_not_connect():
        pytest.fail('A provenance mismatch must fail before database access or migration.')
    monkeypatch.setattr(db, 'pooled_connection', must_not_connect)
    with pytest.raises(RuntimeError, match='differs'):
        db.migrate()


def test_health_uses_the_same_actual_identity_and_rejects_a_stale_pin(monkeypatch):
    from app.main import health
    monkeypatch.setenv('RENDER_GIT_COMMIT', ACTUAL)
    monkeypatch.setenv('RUNTIME_REVISION', ACTUAL)
    assert health()['revision'] == ACTUAL
    monkeypatch.setenv('RUNTIME_REVISION', OTHER)
    with pytest.raises(RuntimeError, match='differs'):
        health()
