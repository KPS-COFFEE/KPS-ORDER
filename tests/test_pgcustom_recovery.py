"""Actual-schema pg_dump roundtrip on synthetic isolated source, including unknown tables."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import zipfile
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from app import db
from tools.pgcustom_recovery import child_environment, table_evidence, sequence_evidence, restore
from tools.private_recovery_envelope import seal


@pytest.mark.postgres
def test_unknown_source_table_binary_json_and_unused_sequences_survive(database,tmp_path):
    from conftest import _isolated_test_url
    base=_isolated_test_url()
    with db.tx() as connection:
        connection.execute('CREATE TABLE unknown_menu_stream(id integer PRIMARY KEY, payload jsonb NOT NULL, image bytea NOT NULL)')
        connection.execute("INSERT INTO unknown_menu_stream VALUES(1,'{\"unreleased\":true,\"number\":4}',%s)",(b'synthetic-image',))
        connection.execute("SELECT setval('fiscal_document_number_seq',65001,true)")
    with db.tx() as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        connection.execute("SET LOCAL TIME ZONE 'UTC'")
        snapshot=connection.execute('SELECT pg_export_snapshot() s').fetchone()['s']
        source=table_evidence(connection)
        sequences=sequence_evidence(connection)
        migrations=connection.execute('SELECT version,sha256 FROM schema_migrations ORDER BY version').fetchall()
        schema=connection.execute('SELECT current_schema() s').fetchone()['s']
        dumped=subprocess.run(['/tmp/kps-pg18-local/usr/lib/postgresql/18/bin/pg_dump','--format=custom','--no-owner','--no-acl','--snapshot='+snapshot],env=child_environment(base),capture_output=True,check=True).stdout
    asset=b'synthetic-actual-source-asset'
    manifest={'format':'kps-pgcustom-v1','source_revision':'synthetic-source',
              'tables':source,'sequences':sequences,'migrations':migrations,'migrations_schema':schema,
              'database_sha256':hashlib.sha256(dumped).hexdigest(),
              'assets':[{'path':'public/assets/test.png','sha256':hashlib.sha256(asset).hexdigest(),'bytes':len(asset),'git_linked':False}]}
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('database.dump',dumped)
        archive.writestr('manifest.json',json.dumps(manifest,default=str))
        archive.writestr('assets/public/assets/test.png',asset)
    key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    private=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    public=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    encrypted=seal(buffer.getvalue(),public)
    cipherpath=tmp_path/'source.kpspub';cipherpath.write_bytes(encrypted)
    keypath=tmp_path/'private.pem';keypath.write_bytes(private);keypath.chmod(0o600)
    result=restore(encrypted_path=cipherpath,private_key=keypath,expected_sha256=hashlib.sha256(encrypted).hexdigest(),base_url=base,
                   assets_output=tmp_path/'restored-assets',evidence_output=tmp_path/'evidence.json')
    assert result['verified'] and result['full_rows_verified'] and result['sequences_verified']
    assert any(t['t']=='unknown_menu_stream' and t['n']==1 for t in result['tables'])
    assert (tmp_path/'restored-assets/public/assets/test.png').read_bytes()==asset
    assert result['source_cleaned'] is False and result['runtime_started'] is False
