"""Informational email preserves current inbox authorization and report readiness."""
from contextlib import contextmanager
from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from app import manager_notifications as notices, email_delivery
from test_operations import ops


@pytest.fixture
def pref():
    return dict(actions=False,conversations=False,shift_reports=False,daily_reports=False,
                created_at=datetime.now(timezone.utc))


def test_payload_has_no_private_message_or_accounting_data_and_no_bearer_link(monkeypatch):
    monkeypatch.setenv('APP_ENV','production');monkeypatch.setenv('PUBLIC_BASE_URL','https://order.kps.sa')
    item=dict(notices.event('conversation','conversation-id','99'),id='notice-id',brand_id='kps',
              customer_phone='secret phone',text='secret private message',amount_halalas=123456)
    payload=notices.payload(item)
    assert 'secret' not in str(payload) and '123456' not in str(payload)
    assert 'manager_notification=notice-id' in payload['text']
    assert 'token=' not in payload['text']
    monkeypatch.setenv('PUBLIC_BASE_URL','http://order.kps.sa')
    assert notices.payload(item) is None
    monkeypatch.setenv('PUBLIC_BASE_URL','https://user:pass@order.kps.sa')
    assert notices.payload(item) is None


def test_report_links_reenter_authenticated_notice_resolution(monkeypatch):
    monkeypatch.setenv('PUBLIC_BASE_URL','https://order.kps.sa')
    item=dict(notices.event('shift_report','report-7','3'),id='n',brand_id='kps')
    assert '/ops?manager_notification=n&operations_brand=kps' in notices.payload(item)['text']
    item=dict(notices.event('daily_report','2026-10-31'),id='n',brand_id='kps')
    assert '/ops?manager_notification=n&operations_brand=kps' in notices.payload(item)['text']


def test_conversation_requires_both_current_reply_and_view_permissions(monkeypatch,pref):
    from app import chat
    pref['conversations']=True
    monkeypatch.setattr(notices.auth,'has_permission',lambda user,permission:permission=='chat_view')
    monkeypatch.setattr(chat,'reply_alerts',lambda **_:pytest.fail('Read-only grant cannot receive owed-reply email'))
    assert notices.current_events({'role':'admin'},pref)=={}


def test_latest_unanswered_message_gets_one_event_key_and_reply_removes_it(monkeypatch,pref):
    from app import chat
    pref['conversations']=True
    monkeypatch.setattr(notices.auth,'has_permission',lambda user,permission:permission in {'chat_view','chat_reply'})
    current={'awaiting_reply':[{'id':'c','seq':31}]}
    monkeypatch.setattr(chat,'reply_alerts',lambda **_:current)
    first=notices.current_events({'role':'admin'},pref)
    assert list(first)==['conversation:c:31']
    assert notices.current_events({'role':'admin'},pref)==first
    current['awaiting_reply']=[]
    assert notices.current_events({'role':'admin'},pref)=={}
    current['awaiting_reply']=[{'id':'c','seq':33}]
    assert list(notices.current_events({'role':'admin'},pref))==['conversation:c:33']


class ReportRows:
    def __init__(self,rows):self.rows=rows
    def execute(self,*_):return self
    def fetchall(self):return self.rows


@pytest.mark.parametrize('complete,missing,expected',[(True,False,True),(True,True,False),(False,False,False)])
def test_daily_report_requires_canonical_completion_and_evidence(monkeypatch,pref,complete,missing,expected):
    from app import reporting,operating_reports
    pref.update(shift_reports=True,daily_reports=True)
    monkeypatch.setattr(notices.service_hours,'delivery_operational_date',lambda:date(2026,11,1))
    monkeypatch.setattr(notices.auth,'has_permission',lambda user,permission:permission=='reports_view')
    @contextmanager
    def tx():yield ReportRows([dict(id='report',version=2,snapshot={'operational_date':'2026-10-31'})])
    monkeypatch.setattr(notices.db,'tx',tx)
    monkeypatch.setattr(reporting,'can_read',lambda *_:True)
    monkeypatch.setattr(operating_reports,'build',lambda *_:dict(operational_complete=complete,evidence_missing=missing))
    output=notices.current_events({'role':'delivery_manager','brand_id':'kps'},pref)
    assert 'shift_report:report:2' in output
    assert ('daily_report:2026-10-31:' in output)==expected
    monkeypatch.setattr(reporting,'can_read',lambda *_:False)
    assert notices.current_events({'role':'delivery_manager','brand_id':'kps'},pref)=={}


def test_combined_daily_report_is_not_duplicated_by_foreign_brand_subscription(monkeypatch,pref):
    from app import reporting,operating_reports
    pref.update(shift_reports=True,daily_reports=True)
    monkeypatch.setattr(notices.service_hours,'delivery_operational_date',lambda:date(2026,11,1))
    monkeypatch.setattr(notices.auth,'has_permission',lambda user,permission:permission=='reports_view')
    @contextmanager
    def tx():yield ReportRows([dict(id='side-report',version=1,snapshot={'operational_date':'2026-10-31'})])
    monkeypatch.setattr(notices.db,'tx',tx)
    monkeypatch.setattr(reporting,'can_read',lambda *_:True)
    monkeypatch.setattr(operating_reports,'build',lambda *_:pytest.fail('Aggregate daily report belongs to home subscription'))
    output=notices.current_events({'role':'delivery_manager','brand_id':'enbishan','home_brand_id':'kps'},pref)
    assert list(output)==['shift_report:side-report:1']


@pytest.mark.parametrize('changed',[{'enabled':False},{'revision':2},{'recipient_email':'other@example.com'}])
def test_queued_mail_is_denied_after_subscription_change(monkeypatch,changed):
    row=dict(enabled=True,revision=1,subscription_revision=1,recipient_email='manager@example.com')|changed
    class Conn:
        def execute(self,*_):return self
        def fetchone(self):return row
    monkeypatch.setattr(notices,'principal',lambda *_:pytest.fail('Stale subscription must stop before resource lookup'))
    assert notices.authorized(Conn(),{'id':'mail','recipient':'manager@example.com'}) is False


def test_queued_mail_rechecks_membership_and_pending_state(monkeypatch):
    row=dict(enabled=True,revision=1,subscription_revision=1,recipient_email='manager@example.com',
             recipient_user_id='manager',brand_id='kps',event_key='conversation:c:31',subscription_created_at='day',category='conversation',resource_id='c',conversations=True)
    class Conn:
        def execute(self,*_):return self
        def fetchone(self):return row
    mail={'id':'mail','recipient':'manager@example.com'}
    monkeypatch.setattr(notices,'principal',lambda *_:None)
    assert notices.authorized(Conn(),mail) is False
    monkeypatch.setattr(notices,'principal',lambda *_:{'id':'manager'})
    monkeypatch.setattr(notices,'current_events',lambda *_:{})
    assert notices.authorized(Conn(),mail) is False
    monkeypatch.setattr(notices,'current_events',lambda *_:{'conversation:c:31':{}})
    assert notices.authorized(Conn(),mail) is True


@pytest.mark.postgres
def test_queue_is_deduplicated_and_resolved_action_is_cancelled_before_send(ops,monkeypatch):
    from app import db
    actor,_,_,_=ops
    manager=actor('manager').app.dependency_overrides[notices.auth.current_user]()
    monkeypatch.setenv('PUBLIC_BASE_URL','http://testserver')
    monkeypatch.setattr(email_delivery,'configuration',lambda:{'configured':True})
    monkeypatch.setattr(email_delivery,'_key',lambda:b'k'*32)
    item=notices.event('action','synthetic-order','intervention:one')
    monkeypatch.setattr(notices,'current_events',lambda *_:{item['event_key']:item})
    with db.tx() as c:
        c.execute("INSERT INTO manager_notification_subscriptions(id,user_id,brand_id,recipient_email,configured_by) VALUES('test-notice-pref',%s,%s,'manager@example.com',%s)",
                  (manager['id'],manager['brand_id'],manager['id']))
    assert notices.process_once()['queued']==1
    assert notices.process_once()['queued']==0
    with db.tx() as c:
        mail=c.execute("SELECT * FROM email_outbox WHERE kind='manager_notification'").fetchall()
    assert len(mail)==1
    # Business notification opt-in does not turn on login email. It is checked
    # against its own live subscription even with an inactive identity channel.
    monkeypatch.setattr(notices,'current_events',lambda *_:{})
    monkeypatch.setattr(email_delivery,'_send',lambda *_:pytest.fail('Resolved action must not be emailed'))
    result=email_delivery.dispatch_once()
    assert result['attempted']==0
    with db.tx() as c:
        assert c.execute('SELECT status FROM email_outbox WHERE id=%s',(mail[0]['id'],)).fetchone()['status']=='cancelled'


def test_notice_recheck_limits_work_to_its_category_and_daily_resource():
    row=dict(category='conversation',resource_id='c',subscription_created_at='day',actions=True,conversations=True,shift_reports=True,daily_reports=True)
    pref=notices.notice_preferences(row)
    assert pref['conversations'] is True
    assert not pref['actions'] and not pref['shift_reports'] and not pref['daily_reports']
    row.update(category='daily_report',resource_id='2026-10-30')
    pref=notices.notice_preferences(row)
    assert pref['_daily_resource']=='2026-10-30' and pref['daily_reports']
    assert not pref['actions'] and not pref['conversations'] and not pref['shift_reports']


def test_returned_intervention_has_a_new_notice_after_each_workflow_revision(monkeypatch,pref):
    from app import order_interventions
    pref['actions']=True
    monkeypatch.setattr(notices.auth,'has_permission',lambda *_:False)
    pending={'requests':[dict(id='request',order_id='order',workflow={'revision':1})]}
    monkeypatch.setattr(order_interventions,'pending_cancellations',lambda **_:pending)
    first=notices.current_events({'role':'admin'},pref)
    pending['requests'][0]['workflow']['revision']=3
    second=notices.current_events({'role':'admin'},pref)
    assert set(first)=={'action:order:intervention:request:1'}
    assert set(second)=={'action:order:intervention:request:3'}


def test_report_recheck_reads_the_target_even_after_it_leaves_recent_inbox(monkeypatch):
    from app import reporting
    report=dict(version=2,snapshot={'shift':{'status':'closed'}})
    class Conn:
        def execute(self,*_):return self
        def fetchone(self):return report
    monkeypatch.setattr(notices.auth,'has_permission',lambda *_:True)
    monkeypatch.setattr(notices,'current_events',lambda *_:pytest.fail('A report must not rebuild unrelated inboxes'))
    monkeypatch.setattr(reporting,'can_read',lambda *_:True)
    notice=dict(category='shift_report',shift_reports=True,resource_id='old-report',resource_version='2',brand_id='kps')
    assert notices.resource_authorized(Conn(),{'id':'manager'},notice)
    monkeypatch.setattr(reporting,'can_read',lambda *_:False)
    assert not notices.resource_authorized(Conn(),{'id':'manager'},notice)


def test_daily_recheck_builds_only_its_completed_day(monkeypatch):
    from datetime import date
    from app import operating_reports
    monkeypatch.setattr(notices.auth,'has_permission',lambda *_:True)
    monkeypatch.setattr(notices.service_hours,'delivery_operational_date',lambda:date(2026,11,1))
    calls=[]
    monkeypatch.setattr(operating_reports,'build',lambda c,u,day,scope:calls.append((day,scope)) or dict(operational_complete=True,evidence_missing=False))
    notice=dict(category='daily_report',daily_reports=True,resource_id='2026-10-30')
    assert notices.resource_authorized(object(),{'id':'manager'},notice)
    assert calls==[(date(2026,10,30),'day')]
    notice['resource_id']='2026-11-01'
    assert not notices.resource_authorized(object(),{'id':'manager'},notice)
    assert len(calls)==1


@pytest.mark.postgres
def test_directive_seed_preserves_existing_user_bytes_and_login_identity(client):
    from pathlib import Path
    from app import db
    uid='staff_a9c2cb536601498aa89edc6b9fc6fef8'
    with db.tx() as c:
        c.execute("INSERT INTO users(id,role,brand_id,branch_ids,display_name,email) VALUES(%s,'delivery_manager','kps',%s,%s,'unchanged@fixture.invalid')",
                  (uid,db.json(['kps-branch-1']),'محمد عبدالسميع'))
        before=c.execute('SELECT row_to_json(u) saved FROM users u WHERE id=%s',(uid,)).fetchone()['saved']
        c.execute((Path(__file__).resolve().parents[1]/'migrations/132_manager_notifications.sql').read_text())
        c.execute((Path(__file__).resolve().parents[1]/'migrations/132_manager_notifications.sql').read_text())
        after=c.execute('SELECT row_to_json(u) saved FROM users u WHERE id=%s',(uid,)).fetchone()['saved']
        assert before==after
        rows=c.execute('SELECT recipient_email FROM manager_notification_subscriptions WHERE user_id=%s',(uid,)).fetchall()
        assert rows==[{'recipient_email':'mohamedmeaa021@gmail.com'}]


@pytest.mark.postgres
def test_actual_conversation_scope_revocation_cancels_waiting_email(ops,monkeypatch):
    from app import db
    actor,post,_,_=ops
    monkeypatch.setenv('PUBLIC_BASE_URL','http://testserver')
    monkeypatch.setattr(email_delivery,'configuration',lambda:{'configured':True})
    monkeypatch.setattr(email_delivery,'_key',lambda:b'k'*32)
    post('customer','/api/conversations',{'branch_id':'ops-branch','service':'delivery','details':'PRIVATE customer text'})
    with db.tx() as c:
        c.execute("INSERT INTO manager_notification_subscriptions(id,user_id,brand_id,recipient_email,actions,shift_reports,daily_reports) VALUES('scoped-notice','ops-manager','ops-brand','manager@fixture.invalid',false,false,false)")
    assert notices.process_once()['queued']==1
    assert notices.process_once()['queued']==0
    with db.tx() as c:
        mail=c.execute("SELECT * FROM email_outbox WHERE kind='manager_notification'").fetchone()
        assert 'PRIVATE customer text' not in email_delivery._payload(mail)['text']
        c.execute("UPDATE users SET branch_ids='[]'::jsonb WHERE id='ops-manager'")
    monkeypatch.setattr(email_delivery,'_send',lambda *_:pytest.fail('Revoked branch must not receive mail'))
    assert email_delivery.dispatch_once()['attempted']==0
    with db.tx() as c:
        assert c.execute('SELECT status FROM email_outbox WHERE id=%s',(mail['id'],)).fetchone()['status']=='cancelled'
