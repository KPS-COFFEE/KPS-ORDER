from contextlib import contextmanager
import pytest
from fastapi import HTTPException
from app import staff_transfers as transfers

@pytest.mark.parametrize('user,expected',[
    ({'role':'owner'},True),
    ({'id':transfers.DELEGATE_ID,'role':'delivery_manager','active':True},True),
    ({'id':'other','role':'delivery_manager','active':True},False),
    ({'id':transfers.DELEGATE_ID,'role':'driver','active':True},False),
    ({'id':transfers.DELEGATE_ID,'role':'delivery_manager','active':False},False),
    ({'id':transfers.DELEGATE_ID,'role':'delivery_manager','active':True,'_morning_manager_context':{'shift_id':'x'}},False),
])
def test_delegate_identity_is_exact(user,expected):
    assert transfers.allowed(user) is expected
    if not expected:
        with pytest.raises(HTTPException):transfers.actor(user)

@pytest.mark.parametrize('blocked,stale',[(True,False),(False,True),(False,False)])
def test_moves_preserve_history_and_block_unfinished_shift(monkeypatch,blocked,stale):
    statements=[]
    class Result:
        def fetchone(self):return {'exists':1} if blocked else None
    class Connection:
        def execute(self,sql,args):
            statements.append(sql)
            return Result()
    @contextmanager
    def transaction():yield Connection()
    monkeypatch.setattr(transfers.db,'tx',transaction)
    monkeypatch.setattr(transfers,'target',lambda *a:{'branch_ids':['old'],'active':True})
    monkeypatch.setattr(transfers.auth,'_staff_scope',lambda c,u,b:b)
    monkeypatch.setattr(transfers.auth,'ensure_safe_staff_scope_change',lambda *a,**k:None)
    monkeypatch.setattr(transfers.db,'audit',lambda *a:None)
    body=transfers.Move(branch_ids=['new'],expected_branch_ids=['stale' if stale else 'old'],reason='Owner approved transfer')
    if blocked or stale:
        with pytest.raises(HTTPException):transfers.move('employee',body,{'id':'owner','role':'owner'})
        assert not any(q.startswith('UPDATE') for q in statements)
    else:
        result=transfers.move('employee',body,{'id':'owner','role':'owner'})
        assert result['branch_ids']==['new']
        assert any(q.startswith('UPDATE sessions') for q in statements)
        assert not any(q.startswith('UPDATE shifts') for q in statements)


@pytest.mark.postgres
def test_owner_phone_correction_and_future_transfer_on_postgres(client):
    from app import db
    from tests.test_owner_staff_management import owner_client
    from tests.legacy_identity_helpers import make_legacy_user, BRANCHES
    owner=owner_client(client)
    employee=make_legacy_user('driver',branches=['kps-branch-1'])
    with db.tx() as c:
        c.execute("UPDATE users SET phone='+966501111111',phone_verified_at=now() WHERE id=%s",(employee['id'],))
    response=client.patch('/api/identity/staff/'+employee['id'],json={'phone':'0502222222'})
    assert response.status_code==200,response.text
    assert response.json()['user']['phone']=='+966502222222'
    assert response.json()['user']['phone_verified_at'] is None
    response=client.post('/api/staff-transfers/'+employee['id'],json={
        'branch_ids':[BRANCHES[1]],'expected_branch_ids':[BRANCHES[0]],'reason':'Owner approved future branch'})
    assert response.status_code==200,response.text
    with db.tx() as c:
        row=c.execute('SELECT branch_ids,phone FROM users WHERE id=%s',(employee['id'],)).fetchone()
        assert row['branch_ids']==[BRANCHES[1]]
        assert row['phone']=='+966502222222'


@pytest.mark.postgres
def test_ordinary_manager_cannot_transfer_through_api(client):
    from tests.test_core import _authenticate
    from tests.legacy_identity_helpers import make_legacy_user
    manager=make_legacy_user('delivery_manager',branches=['kps-branch-1','kps-branch-5'])
    _authenticate(client,manager)
    response=client.get('/api/staff-transfers')
    assert response.status_code==403,response.text
