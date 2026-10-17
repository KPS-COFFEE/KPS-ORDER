"""Restore an encrypted actual-schema pg_dump into a NEW LOCAL PostgreSQL18 DB.

No application migrations/registry assumptions, source writes, or runtime workers.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import uuid
import zipfile
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from tools.private_recovery_envelope import unseal, write_private


def child_environment(url):
    values=conninfo_to_dict(url)
    environment={k:v for k,v in os.environ.items() if not k.startswith('PG')}
    mapping={'dbname':'PGDATABASE','host':'PGHOST','port':'PGPORT','user':'PGUSER','password':'PGPASSWORD','sslmode':'PGSSLMODE','connect_timeout':'PGCONNECT_TIMEOUT'}
    if set(values)-set(mapping):raise ValueError('Unexpected connection options')
    for key,value in values.items():environment[mapping[key]]=value
    environment['PGCONNECT_TIMEOUT']='10'
    library='/tmp/kps-pg18-local/usr/lib/x86_64-linux-gnu'
    environment['LD_LIBRARY_PATH']=library+(':'+environment['LD_LIBRARY_PATH'] if environment.get('LD_LIBRARY_PATH') else '')
    return environment


def table_evidence(connection):
    result=[]
    tables=connection.execute("SELECT n.nspname s,c.relname t FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind IN ('r','p') AND n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2").fetchall()
    for row in tables:
        query=sql.SQL("SELECT count(*) n,encode(sha256(convert_to(coalesce(string_agg(h,'' ORDER BY h),''),'UTF8')),'hex') sha256 FROM (SELECT encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex') h FROM {}.{} t) a").format(sql.Identifier(row['s']),sql.Identifier(row['t']))
        result.append(dict(row,**connection.execute(query).fetchone()))
    return result


def sequence_evidence(connection):
    return [dict(row,**connection.execute(sql.SQL('SELECT last_value,is_called FROM {}.{}').format(sql.Identifier(row['schemaname']),sql.Identifier(row['sequencename']))).fetchone())
            for row in connection.execute("SELECT * FROM pg_sequences WHERE schemaname NOT LIKE 'pg_%' AND schemaname<>'information_schema' ORDER BY schemaname,sequencename")]


def restore(*, encrypted_path, private_key, expected_sha256, base_url, assets_output, evidence_output, assets_reference=None):
    ciphertext=Path(encrypted_path).read_bytes()
    if hashlib.sha256(ciphertext).hexdigest()!=expected_sha256:raise ValueError('Encrypted source digest mismatch')
    content=unseal(ciphertext,Path(private_key).read_bytes())
    archive=zipfile.ZipFile(io.BytesIO(content))
    names=archive.namelist()
    if len(names)!=len(set(names)):raise ValueError('Duplicate archive members')
    manifest=json.loads(archive.read('manifest.json'))
    if manifest.get('format')!='kps-pgcustom-v1':raise ValueError('Actual-schema snapshot required')
    dump=archive.read('database.dump')
    if not dump.startswith(b'PGDMP') or hashlib.sha256(dump).hexdigest()!=manifest['database_sha256']:
        raise ValueError('Custom dump checksum mismatch')
    values=conninfo_to_dict(base_url)
    if values.get('host') not in {'127.0.0.1','localhost','::1'} or values.get('options'):
        raise ValueError('Only explicit local recovery server allowed')
    database='test_kps_actual_restore_'+uuid.uuid4().hex
    target=make_conninfo(base_url,dbname=database)
    destination=Path(assets_output)
    if destination.exists() or destination.is_symlink():raise ValueError('New private asset directory required')
    # Validate all file bytes before creating a database or restoring anything.
    assets=[]
    for item in manifest['assets']:
        path=PurePosixPath(item['path'])
        if path.is_absolute() or '..' in path.parts or not item['path'].startswith('public/assets/'):
            raise ValueError('Unsafe asset path')
        if item.get('git_linked'):
            if not assets_reference:raise ValueError('Exact asset reference bytes required')
            reference=Path(assets_reference)/item['path']
            if reference.is_symlink():raise ValueError('Symlink asset reference prohibited')
            data=reference.read_bytes()
        else:data=archive.read('assets/'+item['path'])
        if len(data)!=item['bytes'] or hashlib.sha256(data).hexdigest()!=item['sha256']:
            raise ValueError('Actual source asset bytes differ')
        assets.append((item['path'],data))
    with psycopg.connect(base_url,autocommit=True) as control:
        if int(control.execute('SHOW server_version_num').fetchone()[0])//10000!=18:
            raise ValueError('PostgreSQL18 target required')
        control.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(database)))
    try:
        executable=os.getenv('KPS_LOCAL_PG_RESTORE','/tmp/kps-pg18-local/usr/lib/postgresql/18/bin/pg_restore')
        result=subprocess.run([executable,'--dbname='+database,'--single-transaction','--exit-on-error','--no-owner','--no-acl'],
                              input=dump,capture_output=True,env=child_environment(target),timeout=240)
        if result.returncode:raise ValueError('Custom restore failed; private target retained for diagnosis')
        with psycopg.connect(target,row_factory=dict_row) as connection:
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            connection.execute("SET LOCAL TIME ZONE 'UTC'")
            tables=table_evidence(connection)
            sequences=sequence_evidence(connection)
            remove_owner=lambda rows:[{k:v for k,v in row.items() if k!='sequenceowner'} for row in rows]
            if tables!=manifest['tables']:raise ValueError('Actual source full-row fingerprints differ')
            if remove_owner(sequences)!=remove_owner(manifest['sequences']):raise ValueError('Actual sequence state differs')
            migrations=connection.execute(sql.SQL('SELECT version,sha256 FROM {}.schema_migrations ORDER BY version').format(sql.Identifier(manifest.get('migrations_schema','public')))).fetchall()
            if migrations!=manifest['migrations']:raise ValueError('Actual migration records differ')
        destination.mkdir(mode=0o700)
        for name,data in assets:
            path=destination/name
            path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            write_private(path,data)
        evidence={'verified':True,'mode':'ACTUAL_SCHEMA_PG18_CUSTOM_RESTORE',
                  'source_revision':manifest['source_revision'],'encrypted_source_sha256':expected_sha256,
                  'database_dump_sha256':manifest['database_sha256'],'database':database,
                  'tables_verified':len(tables),'full_rows_verified':True,'sequences_verified':True,
                  'migration_records_verified':len(migrations),'assets_verified':len(assets),
                  'source_cleaned':False,'runtime_started':False,'tables':tables}
        write_private(evidence_output,json.dumps(evidence,sort_keys=True,separators=(',',':')).encode())
        return evidence
    except Exception:
        # This database was newly created here; source and older targets remain untouched.
        # Retain failed target for authorized diagnosis; do not declare restoration success.
        raise


def main():
    parser=argparse.ArgumentParser()
    for name in ('input','private-key','expected-sha256','assets-output','evidence-output'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--assets-reference')
    args=parser.parse_args()
    result=restore(encrypted_path=args.input,private_key=args.private_key,expected_sha256=args.expected_sha256,
                   base_url=os.environ['KPS_LOCAL_RECOVERY_BASE_URL'],assets_output=args.assets_output,
                   evidence_output=args.evidence_output,assets_reference=args.assets_reference)
    print(json.dumps({k:v for k,v in result.items() if k!='tables'},sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception:
        print('Private actual-schema restore failed; no sensitive exception details emitted.')
        raise SystemExit(1) from None
