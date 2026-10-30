"""Task-scoped driver receipts never grant access to the general chat inbox."""
import pytest
from fastapi import HTTPException

from app import chat, operations


@pytest.mark.parametrize('kind,result', [
    ('message', {'message_id': 'message', 'conversation_id': 'conversation'}),
    ('attachment', {'id': 'attachment'}),
])
@pytest.mark.parametrize('owned', [True, False])
def test_driver_receipt_revalidates_task_and_binds_saved_resource(monkeypatch, kind, result, owned):
    user = {'id': 'driver', 'role': 'driver'}
    order = {'id': 'order', 'brand_id': 'brand', 'branch_id': 'branch'}
    scopes = []
    queries = []

    def contact(conn, principal, tid):
        scopes.append((principal, tid))
        return {'id': tid}, order

    class Connection:
        def execute(self, query, parameters):
            queries.append((query, parameters))
            return self
        def fetchone(self):
            return {'owned': 1} if owned else None

    monkeypatch.setattr(chat.auth, 'has_permission', lambda principal, permission: permission == 'tasks_update')
    monkeypatch.setattr(chat, 'driver_contact_scope', contact)
    monkeypatch.setattr(chat, 'conversation', lambda *args: pytest.fail('Driver replay must not enter the general inbox'))
    operation = 'driver.customer_' + kind + ':task'
    if owned:
        operations.authorize_replay(Connection(), user, operation, {}, result)
    else:
        with pytest.raises(HTTPException) as failure:
            operations.authorize_replay(Connection(), user, operation, {}, result)
        assert failure.value.status_code == 404
    assert scopes == [(user, 'task')]
    assert len(queries) == 1
    query, parameters = queries[0]
    assert 'JOIN conversations' in query
    assert parameters == (('message', 'driver', 'conversation', 'task', 'order', 'brand', 'branch')
                          if kind == 'message' else ('attachment', 'driver', 'task', 'order', 'brand', 'branch'))


def test_withdrawn_update_permission_prevents_receipt_lookup(monkeypatch):
    monkeypatch.setattr(chat.auth, 'has_permission', lambda *args: False)
    monkeypatch.setattr(chat, 'driver_contact_scope', lambda *args: pytest.fail('Permission must be checked first'))
    with pytest.raises(HTTPException) as failure:
        operations.authorize_replay(None, {'id': 'driver'}, 'driver.customer_message:task', {}, {})
    assert failure.value.status_code == 403


def test_reassignment_denial_is_not_overridden_by_a_saved_receipt(monkeypatch):
    monkeypatch.setattr(chat.auth, 'has_permission', lambda *args: True)
    def withdrawn(*args):
        raise HTTPException(404, 'task no longer belongs to this driver')
    monkeypatch.setattr(chat, 'driver_contact_scope', withdrawn)
    with pytest.raises(HTTPException) as failure:
        operations.authorize_replay(None, {'id': 'driver'}, 'driver.customer_message:task', {}, {'conversation_id': 'old'})
    assert failure.value.status_code == 404
