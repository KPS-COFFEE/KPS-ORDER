"""Single-use, exact-payload approvals; never a permission to alter a paid sale."""
from collections import Counter
import json
from . import db


def approved_payload(body):
    payload=body.model_dump(mode='json')
    for key in ('authorization_id','expected_total_halalas'):
        payload.pop(key,None)
    return payload


def validate_original(conn, body):
    if body.kind not in {'compensation','completion'}:
        return
    if not body.original_order_id or not body.original_invoice_number:
        raise db.error(422,'ORIGINAL_INVOICE_REQUIRED','اربط الطلب الأصلي ورقم فاتورته.')
    original=conn.execute('SELECT * FROM orders WHERE id=%s AND brand_id=%s FOR UPDATE',
                          (body.original_order_id,body.brand_id)).fetchone()
    if not original or original['snapshot'].get('complimentary'):
        raise db.error(422,'ORIGINAL_INVOICE_INVALID','الطلب الأصلي غير صالح لهذا الإجراء.')
    invoice=conn.execute("SELECT number FROM fiscal_documents WHERE order_id=%s AND kind='invoice'",
                         (body.original_order_id,)).fetchone()
    reference=invoice['number'] if invoice else original['snapshot'].get('order_reference')
    if not reference or str(reference)!=body.original_invoice_number:
        raise db.error(422,'ORIGINAL_INVOICE_INVALID','رقم الفاتورة لا يطابق الطلب الأصلي.')
    if body.kind=='completion':
        if (original['payment_status'] != 'paid' or original['preparation_status'] in {'cancelled','rejected'}
                or original['delivery_status']=='cancelled'):
            raise db.error(409,'COMPLETION_REQUIRES_PAID_ORDER','استكمال الطلب مخصص لأصناف سبق دفع قيمتها.')
        # Reserve purchased units (including their options) across approved
        # completions, so a second approval cannot fulfill the same item twice.
        def units(items):
            result=Counter()
            for item in items:
                choices=item.get('units') or [{} for _ in range(item['quantity'])]
                for unit in choices:
                    key=(item['product_id'],json.dumps(unit.get('options') or {},sort_keys=True))
                    result[key]+=1
            return result
        remaining=units(original['snapshot'].get('items',[]))
        previous=conn.execute("SELECT payload FROM complimentary_authorizations WHERE brand_id=%s AND payload->>'kind'='completion' AND payload->>'original_order_id'=%s AND id<>%s",
                              (body.brand_id,body.original_order_id,body.authorization_id or '')).fetchall()
        for approval in previous:
            remaining.subtract(units(approval['payload']['items']))
        requested=units(body.model_dump(mode='json')['items'])
        if any(quantity>remaining[key] for key,quantity in requested.items()):
            raise db.error(422,'COMPLETION_ITEMS_INVALID','أصناف الاستكمال وخياراتها وكمياتها يجب أن تكون ضمن الطلب الأصلي وغير مستكملة سابقًا.')



def approve(conn,body,user):
    from . import catalog
    if body.authorization_id:
        raise db.error(422,'COMPLIMENTARY_APPROVAL_INPUT','إنشاء الأمر لا يقبل رقم اعتماد سابق.')
    catalog.quote_order(conn,body,complimentary=True)
    validate_original(conn,body)
    identifier=db.new_id('courtesy_')
    row=conn.execute('''INSERT INTO complimentary_authorizations
        (id,brand_id,branch_id,authorized_by,payload) VALUES(%s,%s,%s,%s,%s) RETURNING *''',
        (identifier,body.brand_id,body.branch_id,user['id'],db.json(approved_payload(body)))).fetchone()
    db.audit(conn,user['id'],'complimentary.authorized','complimentary_authorization',identifier,
             {'kind':body.kind,'original_order_id':body.original_order_id,'reason':body.reason})
    return row


def check(conn,body,*,order_id=None,actor_id=None):
    if not body.authorization_id:
        raise db.error(403,'COMPLIMENTARY_APPROVAL_REQUIRED','اختر أمرًا معتمدًا من الإدارة قبل التنفيذ.')
    row=conn.execute('SELECT * FROM complimentary_authorizations WHERE id=%s AND brand_id=%s FOR UPDATE',
                     (body.authorization_id,body.brand_id)).fetchone()
    if not row or row['payload']!=approved_payload(body):
        raise db.error(403,'COMPLIMENTARY_APPROVAL_INVALID','تفاصيل الطلب لا تطابق الأمر المعتمد.')
    if row['executed_order_id']:
        raise db.error(409,'COMPLIMENTARY_APPROVAL_USED','سبق تنفيذ الأمر المعتمد.')
    validate_original(conn,body)
    if order_id:
        conn.execute('''UPDATE complimentary_authorizations SET executed_order_id=%s,
            executed_by=%s,executed_at=clock_timestamp() WHERE id=%s''',(order_id,actor_id,row['id']))
    return {'authorization_id':row['id'],'authorized_by':row['authorized_by'],
            'authorized_at':row['created_at'].isoformat(),
            'original_order_id':body.original_order_id,'original_invoice_number':body.original_invoice_number}
