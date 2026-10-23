"""Restore populated engagement ledgers, not merely empty new tables."""
import base64
from uuid import uuid4
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pytest
from app import db
from app.exports import build_archive,restore_archive
from tools.portable_backup import encrypt_archive,read_archive,private_write
from test_backup import independent_restore_database
from test_customer_engagement_closure import verified_customer,enable,program
from tests.test_core import _body

pytestmark=pytest.mark.postgres


def test_encrypted_wallet_restore_keeps_projection_and_reenables_future_transactions(
        verified_customer,independent_restore_database,monkeypatch,tmp_path):
    client,user=verified_customer
    enable(monkeypatch,'customer_benefits')
    source='restore-earn-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO benefit_programs(id,brand_id,actor_id,definition) VALUES('restore-program','kps',%s,%s)",(user['id'],db.json(program())))
        c.execute("UPDATE app_settings SET settings=settings||'{\"customer_benefit_program_id\":\"restore-program\"}'::jsonb WHERE brand_id='kps'")
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps','kps-branch-1',%s,'pickup','completed','not_applicable','cash',1000,0,1000,'{}','{}')",(source,user['id']))
        c.execute("INSERT INTO customer_wallets(brand_id,customer_id) VALUES('kps',%s)",(user['id'],))
        c.execute("INSERT INTO benefit_order_states(order_id,brand_id,customer_id,discount_halalas,redeemed_halalas,earn_bps,earn_base_halalas) VALUES(%s,'kps',%s,0,0,1000,1000)",(source,user['id']))
        c.execute("INSERT INTO collections(id,order_id,collector_id,method,amount_halalas) VALUES(%s,%s,%s,'cash',1000)",('restore-collection-'+uuid4().hex,source,user['id']))
        decision=c.execute("INSERT INTO customer_marketing_consents(id,brand_id,customer_id,channel,granted,notice_version) VALUES('restore-consent','kps',%s,'sms',false,'TEST ONLY') RETURNING seq",(user['id'],)).fetchone()['seq']
    payload={**_body(),'cashback_halalas':50}
    payload['customer']['phone']=user['phone']
    created=client.post('/api/orders',json=payload,headers={'Idempotency-Key':'restore-wallet-order-0001'})
    assert created.status_code==200,created.text
    order_id=created.json()['order']['id']
    with db.tx() as c:
        original=c.execute('SELECT * FROM customer_wallets WHERE customer_id=%s',(user['id'],)).fetchone()
        entries=c.execute('SELECT * FROM customer_wallet_entries ORDER BY id').fetchall()
        assert original['balance_halalas']==50 and len(entries)==2
        raw,_=build_archive(c,full=True)
    monkeypatch.setenv('KPS_BACKUP_KEY',base64.b64encode(b'Q'*32).decode())
    archive=tmp_path/'TEST-wallet.kpsenc'
    private_write(archive,encrypt_archive(raw))
    target='kps_restore_'+uuid4().hex
    result=restore_archive(read_archive(archive),target_url=independent_restore_database,target_schema=target)
    assert result['verified']
    with psycopg.connect(independent_restore_database,row_factory=dict_row) as c:
        c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
        assert c.execute('SELECT * FROM customer_wallets WHERE customer_id=%s',(user['id'],)).fetchone()==original
        assert c.execute('SELECT * FROM customer_wallet_entries ORDER BY id').fetchall()==entries
        next_seq=c.execute("INSERT INTO customer_marketing_consents(id,brand_id,customer_id,channel,granted,notice_version) VALUES('restored-next','kps',%s,'sms',false,'TEST ONLY') RETURNING seq",(user['id'],)).fetchone()['seq']
        assert next_seq>decision
        c.execute("UPDATE orders SET preparation_status='cancelled' WHERE id=%s",(order_id,))
    # A new transaction proves restored projection triggers are enabled, and
    # that restoring history did not replay its earlier credits/debits twice.
    with psycopg.connect(independent_restore_database,row_factory=dict_row) as c:
        c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
        assert c.execute('SELECT balance_halalas FROM customer_wallets WHERE customer_id=%s',(user['id'],)).fetchone()['balance_halalas']==100
        assert c.execute("SELECT count(*) AS n FROM customer_wallet_entries WHERE kind='redemption_return'").fetchone()['n']==1
    assert client.get('/api/customer/benefits').json()['balance_halalas']==50
