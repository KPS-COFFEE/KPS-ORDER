"""Actual PostgreSQL source/export/separate-target restoration, plus archive rejection."""
from __future__ import annotations

import base64
import io
import json
import os
import uuid
import zipfile

import psycopg
import pytest
from PIL import Image
from psycopg import sql
from psycopg.rows import dict_row

from app import auth, db, email_delivery
from app.exports import AUTH_TABLES, build_archive, restore_archive, verify_archive
from test_operations import ops, delivered, close_driver


def test_archive_verifier_rejects_invalid_zip_and_unknown_format():
    with pytest.raises(ValueError):
        verify_archive(b'not a backup')
    value = io.BytesIO()
    with zipfile.ZipFile(value, 'w') as output:
        output.writestr('manifest.json', json.dumps({'format': 'unsafe-format'}))
    with pytest.raises(ValueError):
        verify_archive(value.getvalue())


def _chat(actor, post, oid):
    conv = post('customer', '/api/conversations', {'order_id': oid})['conversation']['id']
    image = io.BytesIO()
    Image.new('RGB', (12, 12), 'orange').save(image, format='PNG')
    attachment = post('customer', '/api/conversations/' + conv + '/attachments',
                      {'content_type': 'image/png', 'data_base64': base64.b64encode(image.getvalue()).decode()})
    post('customer', '/api/conversations/' + conv + '/messages', {'text': 'صورة محفوظة للاختبار', 'attachment_id': attachment['id']})
    return conv, attachment, image.getvalue()


def _recovery_rows(brand='ops-brand'):
    event, alert = 'recovery-event-' + uuid.uuid4().hex, 'recovery-alert-' + uuid.uuid4().hex
    resource = 'TEST-ONLY-RESOURCE-' + brand
    with db.tx() as conn:
        conn.execute("INSERT INTO recovery_events(id,brand_id,database_id,provider_event_id,event_type,outcome,source,request_digest,occurred_at,metadata) "
                     "VALUES(%s,%s,%s,%s,'postgres_backup_failed','failed','provider_api','test-only-digest',now(),%s)",
                     (event, brand, resource, 'test-provider-' + event, db.json({'test_only': True})))
        conn.execute("INSERT INTO recovery_state(brand_id,database_id,last_failure_at,last_verified_at,verification_source,retention_days) "
                     "VALUES(%s,%s,now(),now(),'provider_api',7)", (brand, resource))
        conn.execute("INSERT INTO recovery_alerts(id,brand_id,database_id,event_id,code,event_type) "
                     "VALUES(%s,%s,%s,%s,'BACKUP_PROVIDER_FAILURE','postgres_backup_failed')", (alert, brand, resource, event))
    return event, alert, resource


@pytest.mark.postgres
def test_owner_business_export_preserves_bytea_and_excludes_other_brands_and_credentials(ops):
    actor, post, order, shifts = ops
    oid = order('pickup')
    conv, attachment, picture = _chat(actor, post, oid)
    unlinked = post('customer', '/api/conversations', {'service': 'pickup', 'branch_id': 'ops-branch'})['conversation']['id']
    recovery_event, _, _ = _recovery_rows()
    _recovery_rows('ops-other')
    with db.tx() as conn:
        conn.execute("UPDATE users SET username='private-owner-test',password_hash=%s WHERE id='ops-owner'", (auth.hash_password('PrivateTestPassword!1'),))
        conn.execute("INSERT INTO otp_challenges(id,brand_id,phone,provider_reference,expires_at) VALUES('otp-test-export','ops-brand','+966500000000','TEST-ONLY',now()+interval '5 minutes')")
    response = actor('owner').get('/api/admin/export')
    assert response.status_code == 200, response.text
    archive, manifest = verify_archive(response.content)
    assert manifest['mode'] == 'business' and manifest['scope']['brand_id'] == 'ops-brand'
    assert not set(manifest['table_order']) & AUTH_TABLES
    users = json.loads(archive.read('data/users.json'))
    assert all('password_hash' not in user and 'username' not in user and user['brand_id'] == 'ops-brand' for user in users)
    assert {row['id'] for row in json.loads(archive.read('data/brands.json'))} == {'ops-brand'}
    assert [row['id'] for row in json.loads(archive.read('data/orders.json'))] == [oid]
    contexts = {row['id']: row for row in json.loads(archive.read('data/conversations.json'))}
    assert contexts[unlinked]['order_id'] is None and contexts[unlinked]['context_service'] == 'pickup'
    assert {row['id'] for row in json.loads(archive.read('data/recovery_events.json'))} == {recovery_event}
    assert set(manifest['infrastructure_tables']) == {'recovery_events', 'recovery_state', 'recovery_alerts'}
    assert set(manifest['excluded_tables']) == AUTH_TABLES
    blob = json.loads(archive.read('data/chat_attachments.json'))[0]['data']['$binary']
    assert archive.read(blob) == picture
    assert actor('customer').get('/api/admin/export').status_code == 403
    other = actor('other').get('/api/admin/export')
    assert other.status_code == 200
    _, second = verify_archive(other.content)
    assert second['counts']['orders'] == 0 and second['scope']['brand_id'] == 'ops-other'


@pytest.mark.postgres
def test_full_backup_restores_orders_messages_images_closures_reports_without_overwriting_new_orders(ops, monkeypatch):
    actor, post, order, shifts = ops
    manager, driver = shifts()
    policy = post('manager', '/api/performance/policies', {'province_id':'restore-province', 'province_name':'محافظة اختبار الاستعادة',
        'branch_ids':['ops-branch'], 'metric':'delivery', 't1_seconds':1200, 't2_seconds':1800, 'effective_at':'2026-01-01T00:00:00Z'})['policy']
    draft = {'counted_halalas':1000,'handover_halalas':1000,'retained_halalas':0,'notes':'مسودة اختبار استعادة',
             'reference':'RESTORE-DRAFT','recipient_user_id':'ops-manager'}
    post('driver', '/api/shifts/' + driver + '/draft', draft)
    with db.tx() as conn:
        saved_draft=conn.execute('SELECT draft FROM closure_drafts WHERE shift_id=%s',(driver,)).fetchone()['draft']
    # A filled identity/outbox snapshot is synthetic; the disabled worker makes
    # an external message impossible during export, restart or isolated restore.
    monkeypatch.setenv('EMAIL_PROVIDER','resend')
    monkeypatch.setenv('EMAIL_FROM','synthetic@fixture.invalid')
    monkeypatch.setenv('RESEND_API_KEY','synthetic-never-sent')
    monkeypatch.setenv('MAIL_OUTBOX_KEY',base64.urlsafe_b64encode(b'R'*32).decode())
    with db.tx() as conn:
        conn.execute("UPDATE users SET identity_kind='guest',phone='+966500000003',contact_email='contact@fixture.invalid' WHERE id='ops-customer'")
        conn.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled) VALUES('ops-brand','guest','active',true)")
        conn.execute("INSERT INTO identity_tokens(id,brand_id,user_id,purpose,email,token_hash,expires_at) VALUES('restore-identity','ops-brand','ops-driver','invitation','synthetic@fixture.invalid','synthetic-hash-not-a-real-token',now()+interval '1 hour')")
        mid=email_delivery.enqueue_mail(conn,brand_id='ops-brand',recipient='synthetic@fixture.invalid',kind='identity',
            dedupe_key='identity:restore-identity',payload={'subject':'Synthetic restore fixture','text':'Test-only durable encrypted mail'})
        conn.execute("INSERT INTO email_webhook_events(id,event_type,provider_reference,brand_id) VALUES('restore-webhook','email.delivered','synthetic-provider-reference','ops-brand')")
        encrypted=bytes(conn.execute('SELECT encrypted_payload FROM email_outbox WHERE id=%s',(mid,)).fetchone()['encrypted_payload'])
    oid = delivered(post, order, driver)
    conv, attachment, picture = _chat(actor, post, oid)
    unlinked = post('customer', '/api/conversations', {'service': 'delivery', 'branch_id': 'ops-branch'})['conversation']['id']
    post('customer', '/api/conversations/' + unlinked + '/messages', {'text': 'سياق محفوظ دون طلب'})
    recovery_event, recovery_alert, recovery_database = _recovery_rows()
    driver_closed = close_driver(post, driver, manager)
    post('manager', '/api/shifts/' + manager + '/closing')
    post('manager', '/api/shifts/' + manager + '/submit', {'counted_halalas': 1000, 'handover_halalas': 1000})
    tid = post('manager', '/api/custody', {'from_shift_id': manager, 'recipient_user_id': 'ops-owner',
                                         'amount_halalas': 1000, 'reference': 'BACKUP-TEST', 'closure': True})['transfer']['id']
    post('owner', '/api/custody/' + tid + '/confirm')
    manager_closed = post('owner', '/api/shifts/' + manager + '/approve')
    # V5 owner view is separate. These are the immutable original report bytes
    # restored below, alongside all newly versioned renderings in the archive.
    driver_pdf = actor('owner').get('/api/reports/' + driver_closed['report_id'] + '/original/pdf').content
    manager_pdf = actor('owner').get('/api/reports/' + manager_closed['report_id'] + '/original/pdf').content
    assert driver_pdf.startswith(b'%PDF') and manager_pdf.startswith(b'%PDF')
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        contents, manifest = build_archive(conn, full=True)
    assert manifest['totals']['orders'] == 1
    assert manifest['totals']['collected_halalas'] == 1000
    newer = order('pickup', name='post-backup-new-order')
    target = 'kps_restore_' + uuid.uuid4().hex
    base_url = os.environ['TEST_DATABASE_URL']
    try:
        result = restore_archive(contents, target_url=base_url, target_schema=target)
        assert result['verified'] and result['counts'] == manifest['counts']
        with psycopg.connect(base_url, row_factory=dict_row) as restored:
            restored.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
            assert restored.execute('SELECT total_halalas FROM orders WHERE id=%s', (oid,)).fetchone()['total_halalas'] == 1000
            assert restored.execute('SELECT id FROM orders WHERE id=%s', (newer,)).fetchone() is None
            assert bytes(restored.execute('SELECT data FROM chat_attachments WHERE id=%s', (attachment['id'],)).fetchone()['data']) == picture
            reports = {row['id']: row for row in restored.execute('SELECT * FROM closure_reports').fetchall()}
            assert bytes(reports[driver_closed['report_id']]['pdf_data']) == driver_pdf
            assert bytes(reports[manager_closed['report_id']]['pdf_data']) == manager_pdf
            assert reports[manager_closed['report_id']]['snapshot']['summary']['cash_collected_halalas'] == 1000
            assert restored.execute("SELECT identity_kind,phone_verified_at FROM users WHERE id='ops-customer'").fetchone() == {'identity_kind':'guest','phone_verified_at':None}
            assert bytes(restored.execute('SELECT encrypted_payload FROM email_outbox WHERE id=%s',(mid,)).fetchone()['encrypted_payload']) == encrypted
            assert restored.execute("SELECT purpose FROM identity_tokens WHERE id='restore-identity'").fetchone()['purpose']=='invitation'
            assert restored.execute("SELECT event_type FROM email_webhook_events WHERE id='restore-webhook'").fetchone()['event_type']=='email.delivered'
            assert restored.execute('SELECT t1_seconds,t2_seconds FROM province_performance_policies WHERE id=%s',(policy['id'],)).fetchone()=={'t1_seconds':1200,'t2_seconds':1800}
            assert restored.execute('SELECT draft FROM closure_drafts WHERE shift_id=%s',(driver,)).fetchone()['draft']==saved_draft
            renderings=restored.execute("SELECT * FROM report_renderings WHERE report_id=%s AND status='ready'",(manager_closed['report_id'],)).fetchall()
            assert {row['view_kind'] for row in renderings}=={'compact','owner'}
            assert all(bytes(row['pdf_data']).startswith(b'%PDF') for row in renderings)
            assert restored.execute('SELECT count(*) n FROM report_notifications WHERE report_id=%s',(manager_closed['report_id'],)).fetchone()['n']==1
            assert restored.execute("SELECT nextval('order_human_number_seq') n").fetchone()['n']>restored.execute('SELECT max(order_no) n FROM orders').fetchone()['n']
            assert restored.execute('SELECT count(*) AS n FROM chat_messages WHERE conversation_id=%s', (conv,)).fetchone()['n'] == 1
            context = restored.execute('SELECT order_id,context_service FROM conversations WHERE id=%s', (unlinked,)).fetchone()
            assert context['order_id'] is None and context['context_service'] == 'delivery'
            assert restored.execute('SELECT event_id,acknowledged_at FROM recovery_alerts WHERE id=%s', (recovery_alert,)).fetchone()['event_id'] == recovery_event
            state = restored.execute('SELECT retention_days,last_failure_at FROM recovery_state WHERE brand_id=%s AND database_id=%s',
                                     ('ops-brand', recovery_database)).fetchone()
            assert state['retention_days'] == 7 and state['last_failure_at'] is not None
        db.migrate()  # Code restart operates on the original source schema and preserves newer arrivals.
        with db.tx() as conn:
            assert conn.execute('SELECT count(*) AS n FROM orders').fetchone()['n'] == 2
            assert conn.execute('SELECT id FROM orders WHERE id=%s', (newer,)).fetchone()
        with pytest.raises(ValueError):
            restore_archive(contents, target_url=base_url, target_schema=target)
        with pytest.raises(ValueError):
            restore_archive(contents, target_url=base_url, target_schema='public')
        with pytest.raises(ValueError):
            restore_archive(contents, target_url='postgresql://localhost/production', target_schema='kps_restore_' + uuid.uuid4().hex)
    finally:
        with psycopg.connect(base_url, autocommit=True) as cleanup:
            cleanup.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(target)))


@pytest.mark.postgres
def test_backup_tamper_count_mismatch_and_migration_mismatch_are_rejected(database):
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        contents, manifest = build_archive(conn, full=True)
    original = zipfile.ZipFile(io.BytesIO(contents))
    rewritten = io.BytesIO()
    with zipfile.ZipFile(rewritten, 'w') as output:
        for name in original.namelist():
            data = original.read(name)
            if name == 'data/brands.json':
                data = b'[]'
            output.writestr(name, data)
    with pytest.raises(ValueError, match='mismatch'):
        verify_archive(rewritten.getvalue(), require_full=True)
    changed = io.BytesIO()
    manifest['migrations'][0]['sha256'] = '0' * 64
    with zipfile.ZipFile(changed, 'w') as output:
        for name in original.namelist():
            output.writestr(name, json.dumps(manifest).encode() if name == 'manifest.json' else original.read(name))
    with pytest.raises(ValueError, match='migrations'):
        restore_archive(changed.getvalue(), target_url=os.environ['TEST_DATABASE_URL'],
                        target_schema='kps_restore_' + uuid.uuid4().hex)
