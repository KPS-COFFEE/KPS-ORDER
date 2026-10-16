"""Transactional Phase 1 issuance. Disabled until an evidenced owner decision.

Phase 2 cannot be selected as an enabled mode without a real signing/reporting
implementation. This module never treats a five-tag QR as Phase 2 compliance.
"""
from datetime import datetime, timezone
from uuid import uuid4
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from typing import Literal
from . import auth, db, fiscal

router = APIRouter(prefix='/api/fiscal')


def policy(conn, brand_id):
    row = conn.execute('SELECT id,decision FROM fiscal_policy_decisions WHERE brand_id=%s ORDER BY created_at DESC,id DESC LIMIT 1', (brand_id,)).fetchone()
    return row or {'id': None, 'decision': {'enabled': False, 'phase': 'unconfirmed'}}


def status(conn, brand_id):
    p = policy(conn, brand_id)
    d = p['decision']
    enabled = d.get('enabled') is True and d.get('phase') == 'phase1' and bool(d.get('evidence_reference'))
    return {'official_issuance_enabled': enabled, 'phase': d.get('phase', 'unconfirmed'),
            'policy_id': p['id'], 'phase2_supported': False,
            'remaining_requirements': [] if enabled else ['verified_phase_applicability_and_owner_activation'],
            'phase2_gaps': ['UBL_XML', 'hash_chain', 'onboarded_CSID_and_private_key',
                            'cryptographic_stamp_and_QR_tags_6_to_9', 'reporting_and_recovery']}


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool = False
    phase: Literal['unconfirmed', 'phase1', 'phase2'] = 'unconfirmed'
    evidence_reference: str = Field(default='', max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)
    expected_policy_id: str | None = None


@router.post('/policy')
def configure(body: PolicyDecision, user=Depends(fiscal.owner), idem: str | None=Header(None, alias='Idempotency-Key')):
    if user['brand_id'] != 'kps':
        raise db.error(403, 'FISCAL_SELLER_SCOPE', 'هذه الهوية الضريبية خاصة بكبز.')
    if body.enabled and (body.phase != 'phase1' or not body.evidence_reference.strip()):
        raise db.error(409, 'FISCAL_ISSUANCE_DISABLED', 'يلزم إثبات انطباق المرحلة الأولى؛ المرحلة الثانية غير مهيأة.')
    with db.tx() as conn:
        conn.execute('SELECT id FROM brands WHERE id=%s FOR UPDATE', (user['brand_id'],))
        def perform():
            if policy(conn,user['brand_id'])['id'] != body.expected_policy_id:
                raise db.error(409, 'FISCAL_POLICY_CONFLICT', 'تغير إعداد الإصدار؛ حدّث الصفحة.')
            pid = db.new_id('fpolicy_')
            conn.execute('INSERT INTO fiscal_policy_decisions(id,brand_id,actor_id,decision) VALUES(%s,%s,%s,%s)',
                         (pid,user['brand_id'],user['id'],db.json(body.model_dump())))
            db.audit(conn,user['id'],'fiscal.policy_decided','brand',user['brand_id'],body.model_dump())
            return status(conn,user['brand_id'])
        return db.idempotent(conn,user['id'],idem,'fiscal.policy',body.model_dump(),perform)


def original(conn, order_id):
    row = conn.execute("SELECT document FROM fiscal_documents WHERE order_id=%s AND kind='invoice'", (order_id,)).fetchone()
    return row['document'] if row else None


def issue_sale(conn, order_id, actor, *, required=False):
    # All entry paths lock the canonical sale, including distinct retry keys.
    order = conn.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE', (order_id,)).fetchone()
    existing = original(conn, order_id)
    if existing: return existing
    if order['brand_id'] != 'kps' or order['service'] != 'delivery':
        if required: raise db.error(409,'FISCAL_SALE_SCOPE','هذا الإصدار مخصص لمبيعات توصيل كبز.')
        return None
    conn.execute('SELECT id FROM brands WHERE id=%s FOR SHARE', (order['brand_id'],))
    p = policy(conn,order['brand_id'])
    if not status(conn,order['brand_id'])['official_issuance_enabled']:
        if required: raise db.error(409,'FISCAL_ISSUANCE_DISABLED','الإصدار معطل حتى توثيق المرحلة النظامية واعتماد التفعيل.')
        return None
    if order['payment_status'] != 'paid' or order['delivery_status'] != 'delivered':
        raise db.error(409,'FISCAL_SALE_NOT_COMPLETE','يلزم إتمام التسليم والتحصيل قبل إصدار فاتورة البيع.')
    if conn.execute('SELECT 1 FROM refunds WHERE order_id=%s AND amendment_id IS NULL LIMIT 1',(order_id,)).fetchone():
        raise db.error(409,'FISCAL_HISTORICAL_REVIEW','لا يمكن إصدار أصل جديد لطلب له مرتجعات تاريخية.')
    n = conn.execute("SELECT nextval('fiscal_document_number_seq') n").fetchone()['n']
    try:
        doc = fiscal.build(order,'KPS-'+str(n),datetime.now(timezone.utc))
    except ValueError as exc:
        raise db.error(409,'FISCAL_TOTAL_MISMATCH','مبالغ الطلب غير متطابقة؛ لم يتم الإصدار.') from exc
    doc.update(status='issued', uuid=str(uuid4()), policy_id=p['id'], supply_at=str(order.get('completed_at') or doc['issued_at']))
    conn.execute("INSERT INTO fiscal_documents(number,order_id,kind,policy_id,document) VALUES(%s,%s,'invoice',%s,%s)",
                 (doc['number'],order_id,p['id'],db.json(doc)))
    db.audit(conn,actor['id'],'fiscal.invoice_issued','order',order_id,{'number':doc['number'],'uuid':doc['uuid']})
    return doc


def credit_before_refund(conn, order, actor, refund_id, amount, reason, *, amendment_id=None):
    # Caller holds the order row lock. Deferred FK requires the matching refund
    # to be persisted in this same transaction; rollback removes both effects.
    source = original(conn,order['id'])
    if not source: return None  # Historical/unactivated operational sale.
    conn.execute('SELECT id FROM brands WHERE id=%s FOR SHARE', (order['brand_id'],))
    if amendment_id:
        raise db.error(409,'FISCAL_ISSUED_ADJUSTMENT','الطلب المصدر لا يعدل؛ استخدم مرتجعًا مرتبطًا بالفاتورة.')
    if not status(conn,order['brand_id'])['official_issuance_enabled']:
        raise db.error(409,'FISCAL_CREDIT_DISABLED','تعذر إصدار الإشعار الدائن؛ لم يسجل الاسترداد.')
    prior = conn.execute("SELECT coalesce(sum((document->>'total_halalas')::bigint),0) n FROM fiscal_documents WHERE original_number=%s", (source['number'],)).fetchone()['n']
    n = conn.execute("SELECT nextval('fiscal_document_number_seq') n").fetchone()['n']
    try: doc = fiscal.credit(source,amount,prior,'KPS-CN-'+str(n),datetime.now(timezone.utc),refund_id,reason)
    except ValueError as exc:
        raise db.error(409,'FISCAL_CREDIT_EXCEEDS_ORIGINAL','مبلغ المرتجع يتجاوز المتبقي من الفاتورة.') from exc
    doc.update(uuid=str(uuid4()), original_uuid=source['uuid'])
    conn.execute("INSERT INTO fiscal_documents(number,order_id,kind,original_number,refund_id,policy_id,document) VALUES(%s,%s,'credit_note',%s,%s,%s,%s)",
                 (doc['number'],order['id'],source['number'],refund_id,source['policy_id'],db.json(doc)))
    db.audit(conn,actor['id'],'fiscal.credit_issued','order',order['id'],{'number':doc['number'],'refund_id':refund_id,'original_number':source['number']})
    return doc


@router.get('/orders/{order_id}/documents')
def documents(order_id: str, user=Depends(auth.current_user)):
    from .orders import scoped_order
    with db.tx() as conn:
        scoped_order(conn,order_id,user)
        rows = conn.execute('SELECT document FROM fiscal_documents WHERE order_id=%s ORDER BY created_at,number',(order_id,)).fetchall()
        return {'documents':[r['document'] for r in rows], 'orders':[fiscal.print_order(r['document']) for r in rows]}
