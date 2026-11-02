"""Final operational approval without a database or any synthetic cash receipt."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from app import aggregate_driver_closure as closure, cash_bags


class Result:
    def __init__(self,row=None,rows=None):self.row=row;self.rows=rows or []
    def fetchone(self):return self.row
    def fetchall(self):return self.rows


class Connection:
    def __init__(self):
        self.submission={'shared_declaration_id':'decl'}
        self.queries=[];self.returned=False;self.receipt=False;self.remote=True
    def execute(self,query,params=()):
        self.queries.append((query,params))
        if 'shared_driver_closure_returns' in query:return Result({'id':'return'} if self.returned else None)
        if 'SELECT submission FROM shifts' in query:return Result({'submission':deepcopy(self.submission)})
        if 'SELECT * FROM driver_cash_bags' in query:return Result({'id':'bag','destination':'branch' if self.remote else 'manager_vault'})
        if 'SELECT city FROM branches' in query:return Result(rows=[{'city':'القيصومة' if self.remote else 'حفر الباطن'}])
        if 'SELECT id FROM shared_driver_closure_receipts' in query:return Result({'id':'receipt'} if self.receipt else None)
        if 'SELECT now()' in query:return Result({'approved_at':datetime(2026,10,28,tzinfo=timezone.utc)})
        if query.startswith('UPDATE shifts SET staffing_ended_at='):
            self.submission.update(params[1].obj)
            return Result()
        raise AssertionError(query)


@pytest.fixture
def approval_context(monkeypatch):
    c=Connection()
    user={'id':'manager','role':'delivery_manager'}
    day={'id':'day','user_id':'driver','anchor_shift_id':'shift'}
    shift={'id':'shift','status':'submitted','branch_ids':['branch'],'submission':c.submission}
    legs=[(user,shift)]
    declaration={'id':'decl','recorded_by':'driver','reason':'','expected_snapshot':[{'manager_user_id':'manager'}]}
    audit=[];cache={}
    @contextmanager
    def tx():yield c
    monkeypatch.setattr(closure.db,'tx',tx)
    monkeypatch.setattr(closure.workdays,'lock_lanes',lambda *_:None)
    monkeypatch.setattr(closure,'authorize',lambda *_,**__: (day,legs))
    monkeypatch.setattr(closure,'latest',lambda *_:declaration)
    monkeypatch.setattr(closure.auth,'has_permission',lambda *_:True)
    monkeypatch.setattr(closure,'validate_operational',lambda *_:None)
    monkeypatch.setattr(closure.db,'audit',lambda *args:audit.append(args))
    def idempotent(conn,uid,key,action,body,perform):
        if key not in cache:cache[key]=perform()
        return cache[key]
    monkeypatch.setattr(closure.db,'idempotent',idempotent)
    monkeypatch.setattr(closure,'view',lambda *args:{'manager_approval':closure.manager_approval(c,legs,declaration)})
    return SimpleNamespace(c=c,user=user,day=day,shift=shift,declaration=declaration,audit=audit)


def approve(ctx,**overrides):
    body=closure.ManagerApproval(**{'declaration_id':'decl','confirmed':True,**overrides})
    return closure.approve_physical('shift',body,SimpleNamespace(headers={'Idempotency-Key':'approval-key'}),ctx.user)


def test_remote_final_approval_is_idempotent_audited_and_never_records_cash(approval_context):
    ctx=approval_context
    result=approve(ctx)
    assert result['manager_approval']['declaration_id']=='decl'
    assert result['manager_approval']['approved_by']=='manager'
    assert approve(ctx)==result
    assert len(ctx.audit)==1
    assert ctx.audit[0][2]=='shared_driver.manager_approved'
    assert ctx.audit[0][-1]['cash_receipt_recorded'] is False
    mutations=[q for q,_ in ctx.c.queries if q.startswith(('INSERT','UPDATE'))]
    assert len(mutations)==1 and mutations[0].startswith('UPDATE shifts SET staffing_ended_at=')
    assert not any('INSERT INTO shared_driver_closure_receipts' in q for q,_ in ctx.c.queries)


@pytest.mark.parametrize('case,code',[
    ('stale','SHARED_DECLARATION_STALE'),('returned','SHARED_DECLARATION_STALE'),
    ('driver','SHARED_APPROVAL_SELF'),
    ('wrong_manager','SHARED_APPROVAL_MANAGER'),('not_submitted','SHARED_CLOSURE_STATE'),
    ('mismatched_submission','SHARED_CLOSURE_STATE'),('already_approved','SHARED_APPROVAL_EXISTS'),
    ('local','SHARED_APPROVAL_NOT_REQUIRED'),('unconfirmed','SHARED_APPROVAL_CONFIRMATION')])
def test_final_approval_refuses_invalid_evidence_without_releasing_staff(approval_context,case,code):
    ctx=approval_context;body={}
    if case=='stale':body['declaration_id']='old'
    if case=='returned':ctx.c.returned=True
    if case=='driver':ctx.day['user_id']='manager'
    if case=='wrong_manager':ctx.declaration['expected_snapshot'][0]['manager_user_id']='other'
    if case=='not_submitted':ctx.shift['status']='closing'
    if case=='mismatched_submission':ctx.c.submission['shared_declaration_id']='old'
    if case=='already_approved':ctx.c.submission['manager_approval']={'declaration_id':'decl'}
    if case=='local':ctx.c.remote=False
    if case=='unconfirmed':body['confirmed']=False
    with pytest.raises(HTTPException) as error:approve(ctx,**body)
    assert error.value.detail['code']==code
    assert not ctx.audit
    assert not any(q.startswith('UPDATE') for q,_ in ctx.c.queries)


@pytest.mark.parametrize('city,expected',[('حفر الباطن','manager_vault'),('حفرالباطن','manager_vault'),('القيصومة','branch'),('النعيرية','branch'),('رفحاء','branch')])
def test_only_hafar_bags_go_to_manager_vault(city,expected):
    assert cash_bags.branch_destination({'city':city})==expected


def test_accountant_cash_receipt_does_not_count_as_manager_approval(approval_context):
    ctx=approval_context;ctx.c.receipt=True
    assert closure.manager_approval(ctx.c,[(ctx.user,ctx.shift)],ctx.declaration) is None
    assert closure.manager_approval_required(ctx.c,[(ctx.user,ctx.shift)]) is True


@pytest.mark.parametrize('city',['القيصومة','النعيرية'])
def test_branch_bag_deposit_does_not_release_staffing(monkeypatch,city):
    writes=[]
    def execute(query,params=()):
        writes.append((query,params))
        if query.startswith('SELECT * FROM branches'):return Result({'id':'branch','city':city})
        if query.startswith('INSERT INTO driver_cash_bags'):return Result({'id':'bag','destination':'branch','declared_cash_halalas':700})
        if query.startswith('UPDATE shifts SET submission='):return Result()
        raise AssertionError(query)
    monkeypatch.setattr(cash_bags.db,'audit',lambda *_:None)
    bag=cash_bags.create(SimpleNamespace(execute=execute),{'id':'driver'}, {'id':'day','user_id':'driver'},
        [(None,{'id':'shift','branch_ids':['branch']})],
        {'id':'decl','cash_halalas':700,'card_halalas':900,'retained_halalas':0,'expected_snapshot':[]},'branch','Original actual bag',True)
    assert bag['destination']=='branch'
    assert not any('staffing_ended_at' in query for query,_ in writes)
    update=next(params[0].obj for query,params in writes if query.startswith('UPDATE'))
    assert update['manager_approval_required'] is True and update['physical_state']=='bag_at_branch'


@pytest.mark.parametrize('period',['morning','evening'])
def test_manager_closure_preserves_anchor_operational_date_and_period(monkeypatch,period):
    from app import aggregate_manager_closure as manager
    monkeypatch.setattr(manager,'latest',lambda *_:None)
    monkeypatch.setattr(manager.ops,'balance',lambda *_:0)
    def execute(query,params=()):
        if 'SELECT display_name FROM users' in query:return Result({'display_name':'Manager'})
        if 'manager_vault_closures' in query:return Result()
        raise AssertionError(query)
    anchor={'id':'anchor','brand_id':'kps','status':'open','delivery_operational_date':'2026-10-27','delivery_window_snapshot':{'period':period}}
    other={'id':'other','brand_id':'enbishan','status':'open','delivery_operational_date':'2026-10-28','delivery_window_snapshot':{'period':'evening'}}
    result=manager.view(SimpleNamespace(execute=execute),{'id':'manager','role':'delivery_manager'},
        {'id':'day','anchor_shift_id':'anchor','user_id':'manager'},[(None,other),(None,anchor)])
    assert result['shift']['delivery_operational_date']=='2026-10-27'
    assert result['shift']['delivery_period']==period


def test_restore_staffing_proof_requires_new_approval_audit_and_keeps_legacy_proof():
    from app.exports import _verify_staffing_markers
    queries=[]
    def execute(query):
        queries.append(query)
        return Result()
    _verify_staffing_markers(SimpleNamespace(execute=execute),{'driver_cash_bags','manager_vault_closures'})
    query=queries[0]
    assert "e.action='shared_driver.manager_approved'" in query
    assert "e.details @> (s.submission->'manager_approval')" in query
    assert "(e.details->>'approved_at')::timestamptz=s.staffing_ended_at" in query
    assert "b.deposited_at=s.staffing_ended_at" in query
    assert query.count("coalesce(s.submission->>'manager_approval_required','false')<>'true'")==2


def test_completed_legacy_duty_does_not_gain_a_retroactive_approval_requirement():
    shift={'staffing_ended_at':'2026-10-27','submission':{'cash_bag_id':'legacy'}}
    assert closure.legacy_released([(None,shift)])
    assert not closure.legacy_released([(None,{**shift,'submission':{'manager_approval_required':True}})])
    assert not closure.legacy_released([(None,{**shift,'staffing_ended_at':None})])



def test_canonical_manager_can_approve_authorized_delegated_operational_submission(approval_context):
    ctx=approval_context
    ctx.declaration.update(recorded_by='manager',reason='Driver unavailable; actual remote bag declared on their behalf')
    result=approve(ctx)
    approval=result['manager_approval']
    assert approval['delegated_submission'] is True
    assert approval['submitted_by']=='manager'
    assert approval['submission_reason']==ctx.declaration['reason']
    audit=ctx.audit[0][-1]
    assert audit['delegated_submission'] is True
    assert audit['submission_reason']==ctx.declaration['reason']
    assert audit['cash_receipt_recorded'] is False
    assert not any('INSERT INTO shared_driver_closure_receipts' in q for q,_ in ctx.c.queries)
