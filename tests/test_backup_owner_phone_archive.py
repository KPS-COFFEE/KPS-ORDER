"""Private full recovery preserves archive policy and sensitive owner challenges."""
import base64
import json
import uuid
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from app import db
from app.exports import build_archive, restore_archive, verify_archive
from tools.portable_backup import encrypt_archive, read_archive, private_write
from test_operations import ops
from test_backup import independent_restore_database


def test_encrypted_full_restore_preserves_owner_challenge_and_test_archive(ops, independent_restore_database, monkeypatch, tmp_path):
    with db.tx() as c:
        c.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES('private-test-session','synthetic-token-hash','synthetic-csrf-hash','ops-owner',now()+interval '5 minutes')")
        c.execute("INSERT INTO owner_phone_challenges(id,user_id,session_id,brand_id,phone,account_updated_at,provider,provider_reference,expires_at) VALUES('private-test-phone','ops-owner','private-test-session','ops-brand','+966500000001',now(),'tawked','SYNTHETIC-PRIVATE-REFERENCE',now()+interval '5 minutes')")
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,closed_at) VALUES('archived-source','driver','ops-driver','ops-brand','[\"ops-branch\"]','closed',now())")
        c.execute("INSERT INTO retained_carryovers(id,from_shift_id,user_id,amount_halalas,approved_by,reason,archived_test_at,archived_test_by,archived_test_reason) VALUES('archived-test-cash','archived-source','ops-driver',30000,'ops-owner','original history',now(),'ops-owner','Owner confirmed test')")
        original_challenge=c.execute("SELECT * FROM owner_phone_challenges WHERE id='private-test-phone'").fetchone()
        original_carry=c.execute("SELECT * FROM retained_carryovers WHERE id='archived-test-cash'").fetchone()
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        business,business_manifest=build_archive(c,brand_id='ops-brand')
        raw,manifest=build_archive(c,full=True)
    business_zip,_=verify_archive(business)
    assert 'owner_phone_challenges' in business_manifest['excluded_tables']
    assert 'data/owner_phone_challenges.json' not in business_zip.namelist()
    assert all(b'SYNTHETIC-PRIVATE-REFERENCE' not in business_zip.read(name) for name in business_zip.namelist())
    assert manifest['counts']['owner_phone_challenges']==1
    monkeypatch.setenv('KPS_BACKUP_KEY',base64.b64encode(b'X' * 32).decode())
    # Test-only key is exactly 32 bytes; never supplies runtime credentials.
    path=tmp_path/'source.kpsenc'
    private_write(path,encrypt_archive(raw))
    assert b'SYNTHETIC-PRIVATE-REFERENCE' not in path.read_bytes()
    assert read_archive(path)==raw
    target='kps_restore_'+uuid.uuid4().hex
    result=restore_archive(read_archive(path),target_url=independent_restore_database,target_schema=target)
    assert result['verified']
    with psycopg.connect(independent_restore_database,row_factory=dict_row) as c:
        c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
        assert c.execute("SELECT * FROM owner_phone_challenges WHERE id='private-test-phone'").fetchone()==original_challenge
        assert c.execute("SELECT * FROM retained_carryovers WHERE id='archived-test-cash'").fetchone()==original_carry
        assert not c.execute('SELECT 1 FROM retained_carryovers WHERE to_shift_id IS NULL AND archived_test_at IS NULL').fetchone()
