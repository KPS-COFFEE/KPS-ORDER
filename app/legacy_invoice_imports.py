"""Owner-only source-invoice import into a closed driver's separate supplement.

Invoice issue time is not delivery/collection time. Anonymous invoice labels
never become a fabricated phone or an authenticated/merged customer identity.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Header
from pydantic import Field, StrictBool, StrictInt
from . import auth, db, operations as ops, service_hours, prior_deliveries as prior

router=APIRouter(prefix='/api/prior-deliveries',tags=['prior invoice supplements'])


class InvoiceItem(prior.Body):
    name:str=Field(min_length=1,max_length=250)
    quantity:StrictInt=Field(ge=1,le=1000)
    unit_price_halalas:StrictInt=Field(ge=1,le=ops.MAX_HALALAS)


class Invoice(prior.Body):
    driver_shift_id:str
    branch_id:str
    invoice_reference:str=Field(min_length=1,max_length=150)
    invoice_issued_at:datetime
    customer_label:str=Field(default='CUSTOMER0001',min_length=1,max_length=250)
    items:list[InvoiceItem]=Field(min_length=1,max_length=100)
    total_halalas:StrictInt=Field(ge=1,le=ops.MAX_HALALAS)
    tax_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    cash_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    card_halalas:StrictInt=Field(ge=0,le=ops.MAX_HALALAS)
    reason:str=Field(min_length=5,max_length=2000)
    confirmed:StrictBool


def access(user=Depends(prior.access)):
    if user['role']!='owner':
        raise db.error(403,'LEGACY_INVOICE_OWNER_ONLY','استيراد الفاتورة الأصلية لملحق مقفل متاح للمالك فقط.')
    return user


@router.post('/grants/{gid}/legacy-invoice')
def record(gid:str,body:Invoice,user=Depends(access),key:str|None=Header(None,alias='Idempotency-Key')):
    from . import order_numbering
    with db.tx() as c:
        g,s,bids=prior.authorized(c,user,gid,body.driver_shift_id)
        if not g.get('closed_manager_supplement') or s['status']!='closed':
            raise db.error(409,'LEGACY_INVOICE_CLOSED_GRANT','يلزم اعتماد صريح لملحق سائق ومدير مقفلين.')
        issued=body.invoice_issued_at
        if (not body.confirmed or issued.tzinfo is None or issued>datetime.now(timezone.utc)
                or issued<s['opened_at'] or issued>s['closed_at']
                or service_hours.delivery_operational_date(issued)!=g['operational_date']):
            raise db.error(422,'LEGACY_INVOICE_SOURCE_TIME','أكد التنفيذ والتحصيل السابقين وحدد وقت إصدار الفاتورة داخل الوردية الأصلية. وقت التسليم الفعلي غير معلوم.')
        if body.branch_id not in bids:
            raise db.error(403,'LEGACY_INVOICE_BRANCH_SCOPE','فرع الفاتورة خارج نطاق الاعتماد.')
        if (sum(i.quantity*i.unit_price_halalas for i in body.items)!=body.total_halalas or
                body.cash_halalas+body.card_halalas!=body.total_halalas or body.tax_halalas>body.total_halalas):
            raise db.error(422,'LEGACY_INVOICE_TOTAL_MISMATCH','الأصناف والإجمالي والضريبة وتوزيع التحصيل غير متطابقة مع المصدر.')
        source_day=issued.astimezone(ZoneInfo('Asia/Riyadh')).date()
        def perform():
            if (c.execute("SELECT 1 FROM legacy_invoice_imports WHERE brand_id=%s AND branch_id=%s AND source='Sabad' AND invoice_date=%s AND invoice_reference=%s",(user['brand_id'],body.branch_id,source_day,body.invoice_reference)).fetchone() or
                    c.execute('SELECT 1 FROM prior_delivery_records WHERE brand_id=%s AND operational_date=%s AND source_reference=%s',(user['brand_id'],g['operational_date'],body.invoice_reference)).fetchone()):
                raise db.error(409,'LEGACY_INVOICE_DUPLICATE','مرجع الفاتورة مسجل مسبقًا؛ لا تضف نسخة أخرى.')
            iid=db.new_id('legacy_invoice_');oid=db.new_id('order_');sid=db.new_id('supplement_')
            customer_id=db.new_id('legacy_anonymous_')
            # Disabled, invoice-specific operational principal. No phone, login,
            # identity link, authentication proof or cross-invoice merge.
            c.execute("INSERT INTO users(id,display_name,role,brand_id,identity_kind,active) VALUES(%s,%s,'customer',%s,'guest',false)",(customer_id,body.customer_label,user['brand_id']))
            recorded=c.execute('SELECT now() n').fetchone()['n']
            evidence={'source':'Sabad','invoice_reference':body.invoice_reference,'invoice_issued_at':issued.isoformat(),
                'actual_delivered_at':None,'actual_collected_at':None,'recorded_at':recorded.isoformat(),
                'cash_receipt_confirmed':False,'reason':body.reason,'import_id':iid,'original_manager_shift_id':g['manager_shift_id'],
                'original_driver_shift_id':s['id'],'anonymous_customer':True}
            items=[dict(i.model_dump(),product_id='source-item:'+iid+':'+str(n),source_item=True,catalog_product_id=None,line_total_halalas=i.quantity*i.unit_price_halalas,price_halalas=i.unit_price_halalas) for n,i in enumerate(body.items)]
            reference=order_numbering.allocate(c,body.branch_id,issued)
            snapshot={'order_reference':reference,'order_source':'legacy_invoice_import','registration_source':'delivery',
                'created_by_staff_id':user['id'],'manager_shift_id':g['manager_shift_id'],'items':items,
                'subtotal_halalas':body.total_halalas,'total_halalas':body.total_halalas,'tax_halalas':body.tax_halalas,
                'net_halalas':body.total_halalas-body.tax_halalas,'delivery_fee_halalas':0,'legacy_invoice':evidence}
            customer={'name':body.customer_label,'identity_kind':'guest','anonymous_source_invoice':True,
                      'phone_verified':False,'phone_sms_verified':False,'phone_whatsapp_verified':False}
            method='split' if body.cash_halalas and body.card_halalas else 'cash' if body.cash_halalas else 'card'
            c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,payment_status,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer,delivery_operational_date) VALUES(%s,%s,%s,%s,'delivery','completed','delivered',%s,'paid',%s,0,%s,%s,%s,%s)",
                (oid,user['brand_id'],body.branch_id,customer_id,method,body.total_halalas,body.total_halalas,db.json(snapshot),db.json(customer),g['operational_date']))
            submission={'expected_halalas':body.cash_halalas,'expected_card_halalas':body.card_halalas,
                'terminal_total_halalas':body.card_halalas,'handover_halalas':0,'retained_halalas':0,
                'supplemental_cash_due_halalas':body.cash_halalas,'prior_delivery_supplement':True,
                'notes':body.reason,'legacy_invoice':evidence,'closure_exception':{'reason':body.reason,'grant_id':gid}}
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id,status,opened_at,closed_at,submission,approved_by,exception_reason,delivery_operational_date,supplement_of_shift_id,available) VALUES(%s,'driver',%s,%s,%s,%s,'closed',%s,%s,%s,%s,%s,%s,%s,false)",
                (sid,s['user_id'],s['brand_id'],db.json(s['branch_ids']),g['manager_shift_id'],recorded,recorded,db.json(submission),user['id'],body.reason,g['operational_date'],s['id']))
            c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,proof,reason) VALUES(%s,%s,%s,%s,'delivered',%s,%s,%s)",
                (db.new_id('task_'),oid,s['user_id'],sid,user['id'],'Sabad '+body.invoice_reference,body.reason))
            for tender,amount in [('cash',body.cash_halalas),('card',body.card_halalas)]:
                if not amount:continue
                cid=db.new_id('collection_')
                # created_at is the recording time; evidence explicitly says the
                # actual collection time is unknown, separately from invoice time.
                c.execute('INSERT INTO collections(id,order_id,shift_id,collector_id,method,amount_halalas,tendered_halalas,change_halalas,reference) VALUES(%s,%s,%s,%s,%s,%s,%s,0,%s)',
                    (cid,oid,sid,s['user_id'],tender,amount,amount if tender=='cash' else None,'Sabad '+body.invoice_reference+'; recorded, actual time unknown'))
                if tender=='cash':ops.event(c,sid,amount,'collection',cid,user['id'],'استيراد تحصيل سابق؛ وقته الفعلي غير معلوم: '+body.invoice_reference)
            c.execute("INSERT INTO legacy_invoice_imports(id,brand_id,branch_id,source,invoice_reference,invoice_issued_at,invoice_date,order_id,original_shift_id,manager_shift_id,ledger_shift_id,grant_id,actor_id,operational_date,total_halalas,tax_halalas,cash_halalas,card_halalas,reason,details) VALUES(%s,%s,%s,'Sabad',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (iid,user['brand_id'],body.branch_id,body.invoice_reference,issued,source_day,oid,s['id'],g['manager_shift_id'],sid,gid,user['id'],g['operational_date'],body.total_halalas,body.tax_halalas,body.cash_halalas,body.card_halalas,body.reason,db.json(dict(evidence,items=items))))
            db.event(c,oid,user['id'],'order.legacy_invoice_imported',evidence)
            db.audit(c,user['id'],'order.legacy_invoice_imported','order',oid,dict(evidence,card_halalas=body.card_halalas,cash_halalas=body.cash_halalas))
            from .reporting import create_snapshot
            rid=create_snapshot(c,sid,user['id'])
            return {'order':{'id':oid,'order_reference':reference,'delivery_status':'delivered','payment_status':'paid'},
                'record_id':iid,'supplement_report_id':rid,'original_shift_id':s['id'],'original_manager_shift_id':g['manager_shift_id']}
        return ops.safe(db.idempotent(c,user['id'],key,'legacy_invoice.import:'+gid,body.model_dump(mode='json'),perform))
