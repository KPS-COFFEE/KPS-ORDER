"""Single-use, exact-payload approvals; never a permission to alter a paid sale."""
from collections import Counter
import json
from . import auth, db


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
        previous=conn.execute("SELECT payload FROM complimentary_authorizations WHERE brand_id=%s AND payload->>'kind'='completion' AND payload->>'original_order_id'=%s AND id<>%s AND NOT EXISTS (SELECT 1 FROM complimentary_authorization_revocations r WHERE r.authorization_id=complimentary_authorizations.id)",
                              (body.brand_id,body.original_order_id,body.authorization_id or '')).fetchall()
        for approval in previous:
            remaining.subtract(units(approval['payload']['items']))
        requested=units(body.model_dump(mode='json')['items'])
        if any(quantity>remaining[key] for key,quantity in requested.items()):
            raise db.error(422,'COMPLETION_ITEMS_INVALID','أصناف الاستكمال وخياراتها وكمياتها يجب أن تكون ضمن الطلب الأصلي وغير مستكملة سابقًا.')



def approver_scope(user, brand_id, *branch_ids):
    if not auth.has_permission(user,'complimentary_approve'):
        raise db.error(403,'COMPLIMENTARY_APPROVER_REQUIRED','اعتماد الأوامر يحتاج تفويضًا صريحًا من المالك.')
    if user['brand_id']!=brand_id or (user['role']!='owner' and not set(branch_ids)<=set(user.get('branch_ids') or [])):
        raise db.error(403,'COMPLIMENTARY_APPROVER_SCOPE','الأمر أو الطلب الأصلي خارج نطاق تفويضك.')


def validate_approval_scope(conn,body,user,quote):
    approver_scope(user,body.brand_id,body.branch_id,quote['branch_id'])
    if body.kind in {'compensation','completion'} and body.original_order_id:
        original=conn.execute('SELECT branch_id FROM orders WHERE id=%s AND brand_id=%s',
                              (body.original_order_id,body.brand_id)).fetchone()
        if original:approver_scope(user,body.brand_id,original['branch_id'])


def revoke(conn,identifier,user,reason):
    row=conn.execute('SELECT * FROM complimentary_authorizations WHERE id=%s AND brand_id=%s FOR UPDATE',
                     (identifier,user['brand_id'])).fetchone()
    if not row:raise db.error(404,'COMPLIMENTARY_APPROVAL_NOT_FOUND','الأمر غير موجود ضمن نطاقك.')
    approver_scope(user,row['brand_id'],row['branch_id'])
    if row['executed_order_id']:
        raise db.error(409,'COMPLIMENTARY_APPROVAL_USED','الأمر منفذ؛ معالجته تتم من رحلة الطلب.')
    if row['payload'].get('original_order_id'):
        original=conn.execute('SELECT branch_id FROM orders WHERE id=%s AND brand_id=%s FOR UPDATE',
                              (row['payload']['original_order_id'],row['brand_id'])).fetchone()
        if original:approver_scope(user,row['brand_id'],original['branch_id'])
    if conn.execute('SELECT 1 FROM complimentary_authorization_revocations WHERE authorization_id=%s',(identifier,)).fetchone():
        raise db.error(409,'COMPLIMENTARY_APPROVAL_REVOKED','سبق إلغاء الأمر غير المنفذ.')
    result=conn.execute("""INSERT INTO complimentary_authorization_revocations
        (authorization_id,brand_id,revoked_by,reason) VALUES(%s,%s,%s,%s) RETURNING *""",
        (identifier,row['brand_id'],user['id'],reason)).fetchone()
    db.audit(conn,user['id'],'complimentary.revoked','complimentary_authorization',identifier,{'reason':reason})
    return result


def approve(conn,body,user):
    approver_scope(user,body.brand_id,body.branch_id)
    from . import catalog
    if body.authorization_id:
        raise db.error(422,'COMPLIMENTARY_APPROVAL_INPUT','إنشاء الأمر لا يقبل رقم اعتماد سابق.')
    quote=catalog.quote_order(conn,body,complimentary=True)
    validate_approval_scope(conn,body,user,quote)
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
    if conn.execute('SELECT 1 FROM complimentary_authorization_revocations WHERE authorization_id=%s',(row['id'],)).fetchone():
        raise db.error(409,'COMPLIMENTARY_APPROVAL_REVOKED','أُلغي الأمر قبل التنفيذ؛ اختر أمرًا معتمدًا آخر.')
    validate_original(conn,body)
    if order_id:
        conn.execute('''UPDATE complimentary_authorizations SET executed_order_id=%s,
            executed_by=%s,executed_at=clock_timestamp() WHERE id=%s''',(order_id,actor_id,row['id']))
    return {'authorization_id':row['id'],'authorized_by':row['authorized_by'],
            'authorized_at':row['created_at'].isoformat(),
            'original_order_id':body.original_order_id,'original_invoice_number':body.original_invoice_number}
