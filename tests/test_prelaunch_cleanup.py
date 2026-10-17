import pytest
from app.prelaunch_cleanup import rehearse, _digest


@pytest.mark.postgres
def test_cleanup_rehearsal_exact_rows_rollback_and_cascade_guard(database):
    from app import db
    with db.tx() as conn:
        conn.execute('CREATE SCHEMA cleanup_test')
        conn.execute('SET LOCAL search_path TO cleanup_test')
        conn.execute('CREATE TABLE conversations(id int PRIMARY KEY, label text)')
        conn.execute('CREATE TABLE chat_messages(id int PRIMARY KEY, conversation_id int REFERENCES conversations(id) ON DELETE CASCADE)')
        conn.execute("INSERT INTO conversations VALUES(1,'test'),(2,'keep')")
        conn.execute('INSERT INTO chat_messages VALUES(1,1)')
        row = {'table':'conversations','id_column':'id','id':1,
               'sha256':_digest({'id':1,'label':'test'})}
        with pytest.raises(ValueError, match='Unexpected cascade'):
            rehearse(conn, [row], source_database='source_unavailable')
        assert conn.execute('SELECT count(*) AS n FROM conversations').fetchone()['n']==2
        assert conn.execute('SELECT count(*) AS n FROM chat_messages').fetchone()['n']==1
        child = {'table':'chat_messages','id_column':'id','id':1,
                 'sha256':_digest({'id':1,'conversation_id':1})}
        report = rehearse(conn, [child,row], source_database='source_unavailable')
        assert report['source_cleaned'] is False
        assert report['reviewed_rows']==2
        assert conn.execute('SELECT count(*) AS n FROM conversations').fetchone()['n']==2
        assert conn.execute('SELECT count(*) AS n FROM chat_messages').fetchone()['n']==1
        wrong=dict(row, sha256='0'*64)
        with pytest.raises(ValueError, match='missing or changed'):
            rehearse(conn, [wrong], source_database='source_unavailable')
        name=conn.execute('SELECT current_database() AS name').fetchone()['name']
        with pytest.raises(ValueError, match='separate'):
            rehearse(conn, [row], source_database=name)
        conn.execute('CREATE TABLE fiscal_documents(id int PRIMARY KEY, conversation_id int REFERENCES conversations(id))')
        with pytest.raises(ValueError, match='preserved'):
            rehearse(conn, [row], source_database='source_unavailable')
        conn.execute('SET LOCAL search_path TO public')
        conn.execute('DROP SCHEMA cleanup_test CASCADE')
