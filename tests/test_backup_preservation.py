"""Source watermarks and complete fiscal values must survive an isolated restore."""
import io
import json
import uuid
import zipfile
from copy import deepcopy
import pytest
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from app import db
from app.exports import build_archive, restore_archive, _restored_evidence, _sequence_state
from test_backup import independent_restore_database
from test_operations import ops


@pytest.mark.parametrize('changed', ['snapshot','total_halalas','qr_payload'])
def test_complete_fiscal_row_mutation_rejected_even_with_same_ids_and_totals(changed):
    source={'id':'synthetic-fiscal','snapshot':{'vat':15,'number':'TEST-0001'},
            'total_halalas':115,'qr_payload':'synthetic-qr'}
    target=deepcopy(source)
    target[changed] = {'vat':0,'number':'TEST-0001'} if changed=='snapshot' else 114 if changed=='total_halalas' else 'changed-qr'
    class Cursor:
        def __init__(self,rows):self.rows=rows
        def fetchall(self):return self.rows
    class Connection:
        def execute(self,query,params=None):
            return Cursor([{'column_name':'id'}] if params else [target])
    contents=io.BytesIO()
    with zipfile.ZipFile(contents,'w') as archive:
        archive.writestr('data/fiscal_documents.json',json.dumps([source]))
    with zipfile.ZipFile(io.BytesIO(contents.getvalue())) as archive:
        with pytest.raises(ValueError,match='complete row'):
            _restored_evidence(Connection(),archive,{'table_order':['fiscal_documents']})


@pytest.mark.postgres
def test_allocated_unused_sequence_high_water_and_uncalled_state_survive(ops,independent_restore_database,tmp_path):
    actor,post,order,_ = ops
    oid=order('pickup')
    conversation=post('customer','/api/conversations',{'order_id':oid})['conversation']['id']
    with db.tx() as conn:
        ticket=conn.execute("INSERT INTO support_tickets(id,order_id,conversation_id,category,note,created_by) "
                           "VALUES('synthetic-ticket',%s,%s,'other','synthetic restore','ops-customer') RETURNING ticket_no",(oid,conversation)).fetchone()['ticket_no']
        session='synthetic-session'
        conn.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES(%s,'synthetic-unusable-token','synthetic-unusable-csrf','ops-owner',now()+interval '1 hour')",(session,))
        conn.execute("INSERT INTO owner_phone_challenges(id,user_id,session_id,brand_id,phone,account_updated_at,provider,provider_reference,expires_at) "
                     "VALUES('synthetic-owner-proof','ops-owner',%s,'ops-brand','+966500000000',now(),'tawked','synthetic-reference',now()+interval '5 minutes')",(session,))
        # Allocated IDs may exceed the highest retained row. Never reconstruct
        # these from max(row.number), including an untouched is_called=false.
        conn.execute("SELECT setval('order_human_number_seq',90001,true)")
        conn.execute("SELECT setval('fiscal_document_number_seq',70001,true)")
        conn.execute("SELECT setval('fiscal_preview_number_seq',60001,false)")
        before=_sequence_state(conn)
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        contents,manifest=build_archive(conn,full=True)
    target='kps_restore_'+uuid.uuid4().hex
    result=restore_archive(contents,target_url=independent_restore_database,target_schema=target,asset_directory=tmp_path/'assets')
    assert result['complete_rows_verified'] and result['sequence_state_verified']
    assert set(result['row_sha256'])==set(manifest['table_order'])
    with psycopg.connect(independent_restore_database,row_factory=dict_row) as conn:
        conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
        assert _sequence_state(conn)==before
        assert conn.execute("SELECT ticket_no FROM support_tickets WHERE id='synthetic-ticket'").fetchone()['ticket_no']==ticket
        assert conn.execute("SELECT provider_reference FROM owner_phone_challenges WHERE id='synthetic-owner-proof'").fetchone()['provider_reference']=='synthetic-reference'
        assert conn.execute("SELECT nextval('order_human_number_seq') n").fetchone()['n']==90002
        assert conn.execute("SELECT nextval('fiscal_document_number_seq') n").fetchone()['n']==70002
        assert conn.execute("SELECT nextval('fiscal_preview_number_seq') n").fetchone()['n']==60001
    with db.tx() as conn:
        assert _sequence_state(conn)==before
