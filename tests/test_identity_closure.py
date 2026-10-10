"""Identity release closure on local PostgreSQL with synthetic contacts only."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import auth, db, email_delivery
from tests.test_core import _authenticate, _make_user
from tests.test_final_identity import _code, _request, _staff, _verify
from tests.test_v5_identity import _invite, _mail_setup


PROVENANCE={'decision_id':'synthetic-owner-decision-2026','source_sha256':'a'*64}


def _body(role='driver',**changes):
    values={'display_name':'موظف اختبار','email':'delivery@fixture.invalid','phone':'0507778899',
            'role':role,'branch_ids':['kps-branch-1','kps-branch-5']}
    return auth.StaffInvitationBody.model_validate(values|changes)


@pytest.mark.postgres
class TestIdentityClosure:
    def test_owner_code_proof_is_exclusive_to_its_new_session(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        owner=_staff('owner',email='owner@fixture.invalid')
        old_token,_=_authenticate(client,owner)
        challenge=_request(client)
        assert _verify(client,challenge).status_code==200
        with db.tx() as conn:
            previous=conn.execute('SELECT * FROM sessions WHERE token_hash=%s',
                                  (auth.token_hash(old_token),)).fetchone()
            latest=conn.execute('SELECT * FROM sessions WHERE user_id=%s AND token_hash<>%s',
                                (owner['id'],auth.token_hash(old_token))).fetchone()
            assert previous['step_up_at'] is None and previous['revoked_at'] is None
            assert latest['step_up_at'] is not None and latest['id']!=previous['id']
            with pytest.raises(HTTPException) as denied:
                auth.require_owner_step_up(conn,dict(owner,session_id=previous['id']))
            assert denied.value.detail['code']=='OWNER_STEP_UP_REQUIRED'

    @pytest.mark.parametrize('mutation',[
        'active=false',"invitation_status='paused'","email='replacement@fixture.invalid'",
        "role='owner'","branch_ids='[\"kps-branch-1\"]'::jsonb","phone='+966500000009'",
        'email_verified_at=NULL',
    ])
    def test_waiting_email_cancels_after_staff_authority_changes(self,client,monkeypatch,mutation):
        _mail_setup(monkeypatch)
        staff=_staff()
        challenge=_request(client)
        with db.tx() as conn:
            conn.execute('UPDATE users SET '+mutation+' WHERE id=%s',(staff['id'],))
        monkeypatch.setattr(email_delivery,'_send',lambda row:pytest.fail('Revoked code must never be sent.'))
        assert email_delivery.dispatch_once()['attempted']==0
        with db.tx() as conn:
            assert conn.execute('SELECT status FROM email_outbox WHERE dedupe_key=%s',
                                ('identity:'+challenge,)).fetchone()['status']=='cancelled'

    def test_grant_snapshot_blocks_privilege_changes_for_code_and_dispatch(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        staff=_staff('admin')
        with db.tx() as conn:
            conn.execute('UPDATE users SET permissions=%s WHERE id=%s',
                         (db.json(['branches_manage']),staff['id']))
        challenge=_request(client)
        correct=_code(challenge)
        with db.tx() as conn:
            conn.execute('UPDATE users SET permissions=%s WHERE id=%s',
                         (db.json(['branches_manage','products_manage']),staff['id']))
        assert _verify(client,challenge,correct).status_code==400
        monkeypatch.setattr(email_delivery,'_send',lambda row:pytest.fail('Changed admin grants must invalidate mail.'))
        assert email_delivery.dispatch_once()['attempted']==0

    @pytest.mark.parametrize('mutation',['active=false',"role='branch_manager'",
                                         "branch_ids='[\"kps-branch-1\"]'::jsonb"])
    def test_invitation_queue_rechecks_the_inviter(self,client,monkeypatch,mutation):
        _mail_setup(monkeypatch)
        manager=_make_user('delivery_manager',branches=['kps-branch-1','kps-branch-5'])
        _authenticate(client,manager)
        invitation=_invite(client).json()
        with db.tx() as conn:
            conn.execute('UPDATE users SET '+mutation+' WHERE id=%s',(manager['id'],))
        monkeypatch.setattr(email_delivery,'_send',lambda row:pytest.fail('No mail after inviter loses authority.'))
        assert email_delivery.dispatch_once()['attempted']==0
        with db.tx() as conn:
            assert conn.execute('SELECT status FROM email_outbox WHERE dedupe_key=%s',
                                ('identity:'+invitation['challenge_id'],)).fetchone()['status']=='cancelled'

    def test_registered_email_normalizes_without_selecting_a_different_recipient(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        staff=_staff(email='  Driver@FIXTURE.INVALID  ')
        challenge=_request(client)
        with db.tx() as conn:
            token=conn.execute('SELECT * FROM identity_tokens WHERE id=%s',(challenge,)).fetchone()
            mail=conn.execute('SELECT * FROM email_outbox WHERE dedupe_key=%s',
                              ('identity:'+challenge,)).fetchone()
            assert token['email']==mail['recipient']=='driver@fixture.invalid'
            assert auth.identity_mail_authorized(conn,mail)
        assert _verify(client,challenge).json()['user']['id']==staff['id']

    def test_outbox_recipient_tampering_cancels_before_send(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        _staff()
        challenge=_request(client)
        with db.tx() as conn:
            conn.execute('UPDATE email_outbox SET recipient=%s WHERE dedupe_key=%s',
                         ('different@fixture.invalid','identity:'+challenge))
        monkeypatch.setattr(email_delivery,'_send',lambda row:pytest.fail('Mail recipient is challenge bound.'))
        assert email_delivery.dispatch_once()['attempted']==0

    def test_internal_owner_draft_provisioning_cannot_activate_or_send(self,client,monkeypatch):
        _mail_setup(monkeypatch)
        owner=_make_user('owner')
        with db.tx() as conn:
            result=auth.create_staff_invitation(conn,_body('delivery_manager'),owner,
                                               draft_only=True,maintenance_provenance=PROVENANCE)
            user=result['user']
            assert result['invitation_status']=='draft' and result['delivery_status']=='draft_only'
            assert result['challenge_id'] is None and user['active'] is False
            assert user['email_verified_at'] is None and user['permissions']==[]
            assert conn.execute('SELECT count(*) AS n FROM identity_tokens').fetchone()['n']==0
            assert conn.execute('SELECT count(*) AS n FROM email_outbox').fetchone()['n']==0
            assert conn.execute('SELECT count(*) AS n FROM sessions').fetchone()['n']==0
            event=conn.execute("SELECT * FROM audit_events WHERE action='staff.draft.created'").fetchone()
            assert event['actor_id']==owner['id'] and event['details']['owner_decision']==PROVENANCE
            assert '@' not in str(event['details']) and user['phone'] not in str(event['details'])
        _authenticate(client,owner)
        assert client.post('/api/identity/staff/'+user['id']+'/resend').status_code==403
        assert client.patch('/api/identity/staff/'+user['id'],json={'active':True}).status_code==403

    @pytest.mark.parametrize('role',['owner','admin','customer','delivery_manager','driver'])
    def test_internal_draft_requires_fresh_owner_and_narrow_delivery_role(self,client,role):
        actor=_make_user(role,branches=['kps-branch-1','kps-branch-5'])
        with db.tx() as conn:
            if role=='owner':
                conn.execute('UPDATE users SET active=false WHERE id=%s',(actor['id'],))
            with pytest.raises(HTTPException) as denied:
                auth.create_staff_invitation(conn,_body(),actor,draft_only=True,maintenance_provenance=PROVENANCE)
            assert denied.value.status_code==403
            assert conn.execute("SELECT count(*) AS n FROM users WHERE email='delivery@fixture.invalid'").fetchone()['n']==0

    @pytest.mark.parametrize('role',['owner','admin','delivery_staff','branch_manager'])
    def test_internal_draft_cannot_register_privileged_non_delivery_accounts(self,client,role):
        owner=_make_user('owner')
        with db.tx() as conn:
            with pytest.raises(HTTPException) as denied:
                auth.create_staff_invitation(conn,_body(role),owner,draft_only=True,maintenance_provenance=PROVENANCE)
            assert denied.value.detail['code']=='DELIVERY_DRAFT_OWNER_REQUIRED'

    def test_internal_draft_provenance_and_duplicate_identity_are_enforced(self,client):
        owner=_make_user('owner')
        with db.tx() as conn:
            with pytest.raises(HTTPException) as denied:
                auth.create_staff_invitation(conn,_body(),owner,draft_only=True)
            assert denied.value.detail['code']=='DRAFT_APPROVAL_PROVENANCE_REQUIRED'
            result=auth.create_staff_invitation(conn,_body(),owner,draft_only=True,maintenance_provenance=PROVENANCE)
            with pytest.raises(HTTPException) as duplicate:
                auth.create_staff_invitation(conn,_body(email='second@fixture.invalid',phone='٠٥٠٧٧٧٨٨٩٩'),
                                             owner,draft_only=True,maintenance_provenance=PROVENANCE)
            assert duplicate.value.detail['code']=='STAFF_IDENTITY_CONFLICT'
            assert conn.execute("SELECT count(*) AS n FROM users WHERE role='driver'").fetchone()['n']==1
            assert result['user']['id']

    def test_public_api_cannot_select_internal_draft_policy(self,client):
        owner=_make_user('owner')
        _authenticate(client,owner)
        payload=_body('delivery_manager').model_dump()|{'draft_only':True,'maintenance_provenance':PROVENANCE}
        assert client.post('/api/identity/staff/invitations',json=payload).status_code==422
        assert client.post('/api/identity/staff/invitations',json=_body('delivery_manager').model_dump()).status_code==403

    def test_disabled_active_email_channel_preserves_invitation_as_draft(self,client,monkeypatch):
        _mail_setup(monkeypatch,state='active')
        manager=_make_user('delivery_manager',branches=['kps-branch-1','kps-branch-5'])
        _authenticate(client,manager)
        with db.tx() as conn:
            conn.execute("UPDATE identity_channels SET enabled=false WHERE brand_id='kps' AND channel='email'")
        result=_invite(client)
        assert result.status_code==200
        assert result.json()['invitation_status']=='draft' and result.json()['challenge_id'] is None

