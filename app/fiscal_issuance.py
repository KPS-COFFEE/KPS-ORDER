"""Transactional invoice issuance with separately recorded applicability.

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
    verified = d.get('phase') == 'phase1' and bool(d.get('evidence_reference'))
    owner_requested = d.get('issuance_mode') == 'owner_requested' and d.get('phase') == 'unconfirmed'
    enabled = brand_id == 'kps' and d.get('enabled') is True and (verified or owner_requested)
    return {'issuance_enabled': enabled, 'official_issuance_enabled': enabled and verified, 'phase': d.get('phase', 'unconfirmed'),
            'policy_id': p['id'], 'phase2_supported': False,
            'remaining_requirements': ([] if verified else ['phase_applicability_unconfirmed']) + ([] if enabled else ['owner_activation']),
            'phase2_gaps': ['UBL_XML', 'hash_chain', 'onboarded_CSID_and_private_key',
                            'cryptographic_stamp_and_QR_tags_6_to_9', 'reporting_and_recovery']}


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    issuance_mode: Literal['verified_phase1', 'owner_requested'] = 'verified_phase1'
    enabled: StrictBool = False
    phase: Literal['unconfirmed', 'phase1', 'phase2'] = 'unconfirmed'
    evidence_reference: str = Field(default='', max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)
    expected_policy_id: str | None = None


@router.get('/policy')
def read_policy(user=Depends(fiscal.owner)):
    with db.tx() as conn:
        return {**status(conn,user['brand_id']), 'decision':policy(conn,user['brand_id'])['decision']}


@router.post('/policy')
def configure(body: PolicyDecision, user=Depends(fiscal.owner), idem: str | None=Header(None, alias='Idempotency-Key')):
    if user['brand_id'] != 'kps':
        raise db.error(403, 'FISCAL_SELLER_SCOPE', 'هذه الهوية الضريبية خاصة بكبز.')
    if not body.reason.strip():
        raise db.error(422,'FISCAL_POLICY_REASON','يلزم سبب واضح للقرار.')
    owner_requested = body.issuance_mode == 'owner_requested' and body.phase == 'unconfirmed'
    verified_phase1 = body.issuance_mode == 'verified_phase1' and body.phase == 'phase1' and bool(body.evidence_reference.strip())
    if body.enabled and not (verified_phase1 or owner_requested):
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
    if order['snapshot'].get('complimentary'):
        if required:raise db.error(409,'COMPLIMENTARY_NOT_SALE','طلب تجهيز ضيافة أو تعويض؛ ليس فاتورة بيع.')
        return None
    existing = original(conn, order_id)
    if existing: return existing
    if order['brand_id'] != 'kps' or order['service'] != 'delivery':
        if required: raise db.error(409,'FISCAL_SALE_SCOPE','هذا الإصدار مخصص لمبيعات توصيل كبز.')
        return None
    conn.execute('SELECT id FROM brands WHERE id=%s FOR SHARE', (order['brand_id'],))
    p = policy(conn,order['brand_id'])
    if not status(conn,order['brand_id'])['issuance_enabled']:
        if required: raise db.error(409,'FISCAL_ISSUANCE_DISABLED','الإصدار معطل حتى توثيق المرحلة النظامية واعتماد التفعيل.')
        return None
    if order['preparation_status'] in ('cancelled','rejected') or order['delivery_status'] == 'cancelled':
        raise db.error(409,'FISCAL_CANCELLED_ORDER','لا يمكن إصدار فاتورة جديدة لطلب ملغى.')
    if conn.execute("SELECT 1 FROM order_amendments WHERE order_id=%s AND status IN ('requested','branch_confirmed','offered') AND expires_at>now()",(order_id,)).fetchone():
        raise db.error(409,'FISCAL_PENDING_AMENDMENT','أكمل تعديل الطلب قبل إصدار الفاتورة.')
    early = p['decision'].get('issuance_mode') == 'owner_requested'
    if not early and (order['payment_status'] != 'paid' or order['delivery_status'] != 'delivered'):
        raise db.error(409,'FISCAL_SALE_NOT_COMPLETE','يلزم إتمام التسليم والتحصيل قبل إصدار فاتورة البيع.')
    if conn.execute('SELECT 1 FROM refunds WHERE order_id=%s AND amendment_id IS NULL LIMIT 1',(order_id,)).fetchone():
        raise db.error(409,'FISCAL_HISTORICAL_REVIEW','لا يمكن إصدار أصل جديد لطلب له مرتجعات تاريخية.')
    n = conn.execute("SELECT nextval('fiscal_document_number_seq') n").fetchone()['n']
    from . import print_translations
    order['print_labels_en'] = print_translations.for_order(conn,order)
    try:
        doc = fiscal.build(order,order['snapshot'].get('order_reference') or 'KPS-'+str(n),datetime.now(timezone.utc))
    except ValueError as exc:
        raise db.error(409,'FISCAL_TOTAL_MISMATCH','مبالغ الطلب غير متطابقة؛ لم يتم الإصدار.') from exc
    doc.update(status='issued', uuid=str(uuid4()), policy_id=p['id'], supply_at=str(order.get('completed_at') or doc['issued_at']),
               applicability_status=p['decision'].get('phase', 'unconfirmed'),
               issuance_mode=p['decision'].get('issuance_mode', 'verified_phase1'),
               zatca_integrated=False, payment_status=order['payment_status'],
               preparation_status=order['preparation_status'],
               issued_before_collection=order['payment_status'] != 'paid')
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
    if not status(conn,order['brand_id'])['issuance_enabled']:
        raise db.error(409,'FISCAL_CREDIT_DISABLED','تعذر إصدار الإشعار الدائن؛ لم يسجل الاسترداد.')
    prior = conn.execute("SELECT coalesce(sum((document->>'total_halalas')::bigint),0)::bigint n FROM fiscal_documents WHERE original_number=%s", (source['number'],)).fetchone()['n']
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
        order = scoped_order(conn,order_id,user)
        rows = conn.execute('SELECT document FROM fiscal_documents WHERE order_id=%s ORDER BY created_at,number',(order_id,)).fetchall()
        return {'documents':[r['document'] for r in rows], 'orders':[fiscal.print_order(r['document'],order) for r in rows]}


@router.post('/orders/{order_id}/print')
def print_invoice(order_id: str, user=Depends(auth.require_roles('owner','admin','delivery_manager','delivery_staff','branch_manager','driver')), idem: str | None=Header(None, alias='Idempotency-Key')):
    from .orders import scoped_order
    from . import invoice_profiles, print_translations
    with db.tx() as conn:
        order = scoped_order(conn,order_id,user,lock=True)
        def perform():
            existing = original(conn,order_id)
            if existing:
                return {'number':existing['number']}
            if order['brand_id']=='kps' and order['service']=='delivery' and status(conn,'kps')['issuance_enabled']:
                doc = issue_sale(conn,order_id,user,required=True)
                return {'number':doc['number']}
            return {'number':None}
        db.idempotent(conn,user['id'],idem,'fiscal.print',{'order_id':order_id},perform)
        doc = original(conn,order_id)
        if doc:
            printed = fiscal.print_order(doc,order)
            # Collection and operational status are live annotations, never fabricated payment.
            printed.update(payment_status=order['payment_status'],payment_method=order['payment_method'],preparation_status=order['preparation_status'])
            return {'order':{**order,**printed}}
        order['print_labels_en']=print_translations.for_order(conn,order)
        order['receipt_qr']=invoice_profiles.ordinary_qr(order)
        return {'order':order}


def credit_on_cancellation(conn, order, actor, reason):
    source = original(conn,order['id'])
    if not source: return
    prior = conn.execute("SELECT coalesce(sum((document->>'total_halalas')::bigint),0)::bigint n FROM fiscal_documents WHERE original_number=%s",(source['number'],)).fetchone()['n']
    amount = source['total_halalas']-prior
    if amount <= 0: return
    n = conn.execute("SELECT nextval('fiscal_document_number_seq') n").fetchone()['n']
    doc = fiscal.credit(source,amount,prior,'KPS-CN-'+str(n),datetime.now(timezone.utc),None,reason)
    doc.update(uuid=str(uuid4()),original_uuid=source['uuid'],credit_reason='order_cancelled',payment_status='unpaid')
    conn.execute("INSERT INTO fiscal_documents(number,order_id,kind,original_number,policy_id,document) VALUES(%s,%s,'credit_note',%s,%s,%s)",(doc['number'],order['id'],source['number'],source['policy_id'],db.json(doc)))
    db.audit(conn,actor['id'],'fiscal.cancellation_credit_issued','order',order['id'],{'number':doc['number'],'original_number':source['number']})
