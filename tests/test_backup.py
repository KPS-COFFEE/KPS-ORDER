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

from app import auth, db
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


@pytest.mark.postgres
def test_owner_business_export_preserves_bytea_and_excludes_other_brands_and_credentials(ops):
    actor, post, order, shifts = ops
    oid = order('pickup')
    conv, attachment, picture = _chat(actor, post, oid)
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
    blob = json.loads(archive.read('data/chat_attachments.json'))[0]['data']['$binary']
    assert archive.read(blob) == picture
    assert actor('customer').get('/api/admin/export').status_code == 403
    other = actor('other').get('/api/admin/export')
    assert other.status_code == 200
    _, second = verify_archive(other.content)
    assert second['counts']['orders'] == 0 and second['scope']['brand_id'] == 'ops-other'


@pytest.mark.postgres
def test_full_backup_restores_orders_messages_images_closures_reports_without_overwriting_new_orders(ops):
    actor, post, order, shifts = ops
    manager, driver = shifts()
    oid = delivered(post, order, driver)
    conv, attachment, picture = _chat(actor, post, oid)
    driver_closed = close_driver(post, driver, manager)
    post('manager', '/api/shifts/' + manager + '/closing')
    post('manager', '/api/shifts/' + manager + '/submit', {'counted_halalas': 1000, 'handover_halalas': 1000})
    tid = post('manager', '/api/custody', {'from_shift_id': manager, 'recipient_user_id': 'ops-owner',
                                         'amount_halalas': 1000, 'reference': 'BACKUP-TEST', 'closure': True})['transfer']['id']
    post('owner', '/api/custody/' + tid + '/confirm')
    manager_closed = post('owner', '/api/shifts/' + manager + '/approve')
    driver_pdf = actor('owner').get('/api/reports/' + driver_closed['report_id'] + '/pdf').content
    manager_pdf = actor('owner').get('/api/reports/' + manager_closed['report_id'] + '/pdf').content
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
            assert restored.execute('SELECT count(*) AS n FROM chat_messages WHERE conversation_id=%s', (conv,)).fetchone()['n'] == 1
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
