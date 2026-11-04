"""Issued originals survive approved corrections; fiscal claims and cash stay separate."""
from copy import deepcopy
from uuid import uuid4

import psycopg
import pytest

from app import db, fiscal, fiscal_issuance as issuance
from app.branch_printing import receipt


def seed():
    uid, oid = 'fowner-'+uuid4().hex, 'forder-'+uuid4().hex
    snap = {'items':[{'product_id':'synthetic','name':'Original sale','quantity':1,'line_total_halalas':1150,'units':[]}],
            'subtotal_halalas':1150,'delivery_fee_halalas':0,'total_halalas':1150}
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES(%s,'Correction test','owner','kps','[\"kps-branch-1\"]')",(uid,))
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,delivery_status,payment_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps','kps-branch-1',%s,'delivery','assigned','unpaid','cash',1150,0,1150,%s,'{}')",(oid,uid,db.json(snap)))
        c.execute("INSERT INTO fiscal_policy_decisions(id,brand_id,actor_id,decision) VALUES(%s,'kps',%s,%s)",('policy-'+oid,uid,db.json({'enabled':True,'phase':'unconfirmed','issuance_mode':'owner_requested'})))
        user=c.execute('SELECT * FROM users WHERE id=%s',(uid,)).fetchone()
        source=issuance.issue_sale(c,oid,user,required=True)
    return oid,user,source


def apply(c,oid,user,total):
    o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone()
    aid='amend-'+uuid4().hex
    q=deepcopy(o['snapshot']);q.update(subtotal_halalas=total,total_halalas=total)
    q['items'][0].update(name='Approved revision',line_total_halalas=total)
    revision=o['amendment_revision']+1
    c.execute("INSERT INTO order_amendments(id,order_id,requested_by,base_revision,proposal,status,reason,reviewed_by,consent_by,consent_channel) VALUES(%s,%s,%s,%s,%s,'applied','Approved change',%s,%s,'phone')",(aid,oid,user['id'],o['amendment_revision'],db.json({'snapshot':q}),user['id'],user['id']))
    c.execute("INSERT INTO order_revisions(order_id,revision,amendment_id,before_snapshot,before_customer,after_snapshot,after_customer,before_total,after_total,approved_by,consent_by,consent_channel) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'phone')",(oid,revision,aid,db.json(o['snapshot']),db.json(o['customer']),db.json(q),db.json(o['customer']),o['total_halalas'],total,user['id'],user['id']))
    note=issuance.correction_for_amendment(c,o,user,aid,{'snapshot':q},'Approved change')
    assert issuance.correction_for_amendment(c,o,user,aid,{'snapshot':q},'retry')==note
    c.execute('UPDATE orders SET snapshot=%s,subtotal_halalas=%s,total_halalas=%s,amendment_revision=%s,version=version+1 WHERE id=%s',(db.json(q),total,total,revision,oid))
    return aid,note


@pytest.mark.postgres
@pytest.mark.parametrize('totals',[(2300,575),(575,2300),(1150,)])
def test_corrections_then_cancel_reverse_current_claim_exactly(database,totals):
    oid,user,original=seed()
    notes=[]
    for total in totals:
        with db.tx() as c:
            aid,note=apply(c,oid,user,total);notes.append(note)
            assert issuance.original(c,oid)==original
            current=issuance.remaining_invoice(c,original)
            assert current['total_halalas']==total
            assert current['tax_halalas']==fiscal.inclusive_tax(total)
            assert not c.execute('SELECT 1 FROM refunds WHERE order_id=%s',(oid,)).fetchone()
            assert not c.execute('SELECT 1 FROM collections WHERE order_id=%s',(oid,)).fetchone()
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone()
        issuance.credit_on_cancellation(c,o,user,'Cancelled after correction')
        assert issuance.remaining_invoice(c,original)['total_halalas']==0
        assert issuance.remaining_invoice(c,original)['tax_halalas']==0
        assert issuance.original(c,oid)==original
        before=c.execute('SELECT count(*) n FROM fiscal_documents WHERE order_id=%s',(oid,)).fetchone()['n']
        issuance.credit_on_cancellation(c,o,user,'repeated cancellation')
        assert c.execute('SELECT count(*) n FROM fiscal_documents WHERE order_id=%s',(oid,)).fetchone()['n']==before
    if notes[0]['kind']=='debit_note':
        for lang,title in [('ar','إشعار مدين مبسط'),('en','Simplified debit note')]:
            rendered=receipt(fiscal.print_order(notes[0]),{'name':'Test branch'},'order',lang)
            assert title in [r['text'] for r in rendered['lines']]


@pytest.mark.postgres
def test_amendment_refund_reuses_approved_credit_without_duplicate_claim(database):
    oid,user,original=seed()
    with db.tx() as c:
        c.execute("INSERT INTO collections(id,order_id,collector_id,method,amount_halalas) VALUES(%s,%s,%s,'card',1150)",('collection-'+oid,oid,user['id']))
        aid,note=apply(c,oid,user,575)
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone()
        for amount in (100,475):
            rid='refund-'+uuid4().hex
            doc=issuance.credit_before_refund(c,o,user,rid,amount,'Actual card refund',amendment_id=aid)
            assert doc['number']==note['number']
            c.execute("INSERT INTO refunds(id,collection_id,order_id,method,amount_halalas,reason,authorized_by,amendment_id) VALUES(%s,%s,%s,'card',%s,'Actual refund',%s,%s)",(rid,'collection-'+oid,oid,amount,user['id'],aid))
        assert c.execute('SELECT count(*) n FROM fiscal_documents WHERE order_id=%s',(oid,)).fetchone()['n']==2
        assert issuance.remaining_invoice(c,original)['total_halalas']==575
    with pytest.raises(Exception) as caught:
        with db.tx() as c:
            o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone()
            issuance.credit_before_refund(c,o,user,'extra',1,'Duplicate refund',amendment_id=aid)
    assert getattr(caught.value,'status_code',None)==409


@pytest.mark.postgres
def test_correction_cannot_commit_without_matching_approved_revision(database):
    oid,user,original=seed()
    with pytest.raises(psycopg.errors.RaiseException,match='exact approved revision'):
        with db.tx() as c:
            o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone()
            q=deepcopy(o['snapshot']);q['total_halalas']=575
            issuance.correction_for_amendment(c,o,user,'missing-amendment',q,'Unbacked correction')
    with db.tx() as c:
        assert issuance.original(c,oid)==original
        assert c.execute('SELECT count(*) n FROM fiscal_documents WHERE order_id=%s',(oid,)).fetchone()['n']==1
