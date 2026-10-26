"""Bounded read-only owner investigation over canonical records, never a new ledger."""
import json
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, Query, Response
from . import auth, db, multibrand
from .operations import safe
from .reporting import can_read

router=APIRouter(prefix='/api/owner-audit',tags=['owner-audit'])
MAX_ORDERS=1000
MAX_ROWS=10000
PRIVATE={'customer','customer_id','phone','email','address','neighborhood','latitude','longitude','lat','lng','password','password_hash','token','token_hash','csrf_hash','secret','api_key','contents','data','pdf_data','csv_data','proof_data','photo_data'}


def clean(value):
    if isinstance(value,dict):
        return {k:clean(v) for k,v in value.items() if k.lower() not in PRIVATE and not any(part in k.lower() for part in ('password','secret','token')) and not isinstance(v,(bytes,bytearray,memoryview))}
    if isinstance(value,(list,tuple)):return [clean(v) for v in value]
    return value


def require_owner(user):
    if user['role']!='owner' or not user.get('active',True):
        raise db.error(403,'OWNER_AUDIT_REQUIRED','التدقيق متاح للمالك المخول فقط.')


def principal(c,user,brand):
    projected=multibrand.project(c,user,brand)
    if not projected or projected['role']!='owner':
        raise db.error(404,'AUDIT_SOURCE_NOT_FOUND','المصدر غير موجود ضمن صلاحياتك.')
    return projected


def visible_order(c,user,row):
    actor=principal(c,user,row['brand_id'])
    if not auth.can_access_order(actor,row,c):
        raise db.error(404,'AUDIT_SOURCE_NOT_FOUND','المصدر غير موجود ضمن صلاحياتك.')


def bounded(rows):
    if len(rows)>MAX_ROWS:raise db.error(413,'AUDIT_SCOPE_TOO_LARGE','نطاق التدقيق كبير؛ اختر عددًا أقل من التقفيلات.')
    return rows


def rows(c,query,args=()):
    return bounded(c.execute(query+' LIMIT '+str(MAX_ROWS+1),args).fetchall())


def bundle(c,user,order_query=None,report_ids=()):
    require_owner(user)
    report_ids=list(dict.fromkeys(report_ids))
    if not order_query and not report_ids:raise db.error(422,'AUDIT_SOURCE_REQUIRED','أدخل رقم طلب أو اختر تقفيلة واحدة أو أكثر.')
    if len(report_ids)>20 or any(not x or len(x)>150 for x in report_ids):raise db.error(422,'AUDIT_SELECTION_INVALID','اختر حتى عشرين تقفيلة.')
    reports=[];order_ids=set();shift_ids=set()
    for rid in report_ids:
        row=c.execute('SELECT id,shift_id,kind,brand_id,branch_ids,user_id,version,snapshot,created_at FROM closure_reports WHERE id=%s',(rid,)).fetchone()
        if not row or not can_read(principal(c,user,row['brand_id']),row):
            raise db.error(404,'AUDIT_SOURCE_NOT_FOUND','المصدر غير موجود ضمن صلاحياتك.')
        reports.append(row);shift_ids.add(row['shift_id'])
        order_ids.update(o['id'] for o in row['snapshot'].get('orders',[]) if o.get('id'))
        shift_ids.update(r['shift_id'] for r in row['snapshot'].get('driver_reports',[]) if r.get('shift_id'))
    if order_query:
        if len(order_query)>150:raise db.error(422,'AUDIT_QUERY_INVALID','رقم الطلب غير صالح.')
        found=c.execute("SELECT * FROM orders WHERE id=%s OR order_no::text=%s OR snapshot->>'order_reference'=%s ORDER BY created_at DESC LIMIT 51",(order_query,)*3).fetchall()
        visible=[]
        from fastapi import HTTPException
        for item in found:
            try:visible_order(c,user,item)
            except HTTPException as exc:
                if exc.status_code==404:continue
                raise
            visible.append(item['id'])
        if len(visible)>50:raise db.error(413,'AUDIT_QUERY_AMBIGUOUS','حدد مرجع الطلب الكامل أو معرّفه.')
        if not visible:raise db.error(404,'AUDIT_SOURCE_NOT_FOUND','المصدر غير موجود ضمن صلاحياتك.')
        order_ids.update(visible)
    if len(order_ids)>MAX_ORDERS:raise db.error(413,'AUDIT_SCOPE_TOO_LARGE','نطاق التدقيق كبير؛ اختر عددًا أقل من التقفيلات.')
    orders=c.execute('SELECT * FROM orders WHERE id=ANY(%s) ORDER BY created_at,id',(list(order_ids),)).fetchall()
    if len(orders)!=len(order_ids):raise db.error(409,'AUDIT_EVIDENCE_MISSING','توجد تفاصيل طلب مفقودة؛ لا يمكن اعتبار التقرير مكتملًا.')
    for order in orders:visible_order(c,user,order)
    # Discover actual linked custody shifts, validating each before exposing it.
    tasks=rows(c,'SELECT * FROM effective_driver_tasks WHERE order_id=ANY(%s) ORDER BY created_at,id',(list(order_ids),))
    collections=rows(c,'SELECT * FROM effective_collections WHERE order_id=ANY(%s) ORDER BY created_at,id',(list(order_ids),))
    shift_ids.update(r['shift_id'] for r in [*tasks,*collections] if r.get('shift_id'))
    shifts=c.execute('SELECT * FROM shifts WHERE id=ANY(%s) ORDER BY opened_at,id',(list(shift_ids),)).fetchall()
    for shift in shifts:
        projected=principal(c,user,shift['brand_id'])
        if not can_read(projected,dict(shift, snapshot={})):
            raise db.error(404,'AUDIT_SOURCE_NOT_FOUND','المصدر غير موجود ضمن صلاحياتك.')
    manager_ids={s['manager_shift_id'] for s in shifts if s.get('manager_shift_id')} - shift_ids
    if manager_ids:
        parents=c.execute('SELECT * FROM shifts WHERE id=ANY(%s) ORDER BY opened_at,id',(list(manager_ids),)).fetchall()
        for parent in parents:
            if not can_read(principal(c,user,parent['brand_id']),dict(parent,snapshot={})):
                raise db.error(404,'AUDIT_SOURCE_NOT_FOUND','المصدر غير موجود ضمن صلاحياتك.')
        shifts.extend(parents);shift_ids.update(manager_ids)
    associated=rows(c,'SELECT id,shift_id,kind,brand_id,branch_ids,user_id,version,snapshot,created_at FROM closure_reports WHERE shift_id=ANY(%s) ORDER BY created_at,id',(list(shift_ids),))
    known={r['id'] for r in reports}
    for item in associated:
        if not can_read(principal(c,user,item['brand_id']),item):
            raise db.error(404,'AUDIT_SOURCE_NOT_FOUND','المصدر غير موجود ضمن صلاحياتك.')
        if item['id'] not in known:reports.append(item);known.add(item['id'])
    related_report_ids=[r['id'] for r in reports]
    from .order_financial_cases import case
    from .financial_clarifications import present
    from .administrative_guidance import order as guidance
    order_results=[]
    for order in orders:
        financial_case=case(c,order['id'])
        order_results.append({k:order.get(k) for k in ('id','order_no','brand_id','branch_id','service','preparation_status','delivery_status','payment_status','subtotal_halalas','delivery_fee_halalas','total_halalas','created_at','completed_at','version')}
            |dict(reference=order['snapshot'].get('order_reference'),items=order['snapshot'].get('items',[]),financial_case=financial_case,
                guidance=guidance(order,financial_case=financial_case,viewer_role='owner')))
    clarifications=rows(c,'SELECT * FROM financial_case_clarifications WHERE order_id=ANY(%s) ORDER BY created_at,id',(list(order_ids),))
    # Full canonical records are shown once even when parent/child reports overlap.
    result=dict(generated_at=datetime.now(timezone.utc),selected_report_ids=report_ids,order_query=order_query,
        orders=order_results,closures=[dict({k:r[k] for k in ('id','shift_id','kind','brand_id','branch_ids','user_id','version','created_at')},
            original_summary=r['snapshot'].get('summary'),original_submission=r['snapshot'].get('shift',{}).get('submission'),original_operational_date=r['snapshot'].get('operational_date')) for r in reports],
        shifts=shifts,tasks=tasks,collections=collections,
        refunds=rows(c,'SELECT * FROM refunds WHERE order_id=ANY(%s) ORDER BY created_at,id',(list(order_ids),)),
        fiscal_documents=rows(c,"SELECT number,order_id,kind,original_number,refund_id,created_at,document->'total_halalas' AS total_halalas,document->'net_halalas' AS net_halalas,document->'tax_halalas' AS tax_halalas FROM fiscal_documents WHERE order_id=ANY(%s) ORDER BY created_at,number",(list(order_ids),)),
        custody_events=rows(c,'SELECT * FROM custody_events WHERE shift_id=ANY(%s) ORDER BY created_at,id',(list(shift_ids),)),
        custody_transfers=rows(c,'SELECT * FROM custody_transfers WHERE from_shift_id=ANY(%s) OR to_shift_id=ANY(%s) ORDER BY created_at,id',(list(shift_ids),list(shift_ids))),
        accounting_settlements=rows(c,'SELECT e.* FROM driver_accounting_settlements e WHERE e.report_id=ANY(%s) ORDER BY created_at,id',(related_report_ids,)),
        manager_financial_matches=rows(c,'SELECT * FROM manager_financial_matches WHERE report_id=ANY(%s) ORDER BY created_at,id',(related_report_ids,)),
        daily_approvals=rows(c,'SELECT id,manager_shift_id,brand_id,branch_ids,operational_date,approved_by,note,created_at FROM accounting_daily_closures WHERE manager_shift_id=ANY(%s) ORDER BY created_at,id',(list(shift_ids),)),
        report_corrections=rows(c,'SELECT * FROM report_corrections WHERE report_id=ANY(%s) ORDER BY created_at,id',(related_report_ids,)),
        clarifications=[present(c,r,principal(c,user,next(o['brand_id'] for o in orders if o['id']==r['order_id']))) for r in clarifications],
        order_events=rows(c,'SELECT e.*,u.display_name actor_name FROM order_events e LEFT JOIN users u ON u.id=e.actor_id WHERE order_id=ANY(%s) ORDER BY created_at,e.id',(list(order_ids),)),
        audit_events=rows(c,"SELECT e.*,u.display_name actor_name FROM audit_events e LEFT JOIN users u ON u.id=e.actor_id WHERE (entity_type='order' AND entity_id=ANY(%s)) OR (entity_type='report' AND entity_id=ANY(%s)) OR (entity_type='shift' AND entity_id=ANY(%s)) ORDER BY created_at,e.id",(list(order_ids),related_report_ids,list(shift_ids))),
        limitations=['recorded_evidence_only','external_bank_documents_not_independently_verified','no_real_balance_certification'],
        deduplication='canonical_record_id',financial_completion_claimed=False)
    if multibrand.enabled():
        workdays=rows(c,'SELECT DISTINCT workday_id FROM central_workday_legs WHERE shift_id=ANY(%s)',(list(shift_ids),))
        allowed_days=[]
        for day in workdays:
            legs=c.execute('SELECT s.* FROM shifts s JOIN central_workday_legs l ON l.shift_id=s.id WHERE l.workday_id=%s',(day['workday_id'],)).fetchall()
            from fastapi import HTTPException
            try:
                permitted=all(can_read(principal(c,user,s['brand_id']),dict(s,snapshot={})) for s in legs)
            except HTTPException as exc:
                if exc.status_code!=404:raise
                permitted=False
            if permitted:allowed_days.append(day['workday_id'])
            else:result['limitations'].append('shared_custody_requires_all_leg_permissions')
        for kind in ('driver','manager'):
            prefix='shared_'+kind+'_closure_'
            declarations=rows(c,'SELECT * FROM '+prefix+'declarations WHERE workday_id=ANY(%s) ORDER BY created_at,id',(allowed_days,))
            result[prefix+'declarations']=declarations
            ids=[r['id'] for r in declarations]
            for source in ('receipts','allocations','returns'):
                result[prefix+source]=rows(c,'SELECT * FROM '+prefix+source+' WHERE declaration_id=ANY(%s) ORDER BY created_at',(ids,))
    # A counterpart outside the selected scope must not leak another brand/branch.
    for transfer in result['custody_transfers']:
        for sid in (transfer['from_shift_id'],transfer.get('to_shift_id')):
            if sid and sid not in shift_ids:
                other=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone()
                if not other or not can_read(principal(c,user,other['brand_id']),dict(other,snapshot={})):
                    raise db.error(404,'AUDIT_SOURCE_NOT_FOUND','المصدر غير موجود ضمن صلاحياتك.')
    if sum(len(v) for v in result.values() if isinstance(v,list))>MAX_ROWS:
        raise db.error(413,'AUDIT_SCOPE_TOO_LARGE','نطاق التدقيق كبير؛ اختر عددًا أقل من التقفيلات.')
    actor_keys=('actor_id','collector_id','authorized_by','recorded_by','received_by','approved_by','confirmed_by','user_id','recipient_user_id')
    person_ids={r[k] for records in result.values() if isinstance(records,list) for r in records if isinstance(r,dict) for k in actor_keys if isinstance(r.get(k),str)}
    names={r['id']:r['display_name'] for r in c.execute('SELECT id,display_name FROM users WHERE id=ANY(%s)',(list(person_ids),)).fetchall()}
    for records in result.values():
        if isinstance(records,list):
            for r in records:
                if isinstance(r,dict) and not r.get('actor_name'):
                    actor=next((r[k] for k in actor_keys if r.get(k)),None)
                    if actor in names:r['actor_name']=names[actor]
    result=safe(clean(result))
    if len(json.dumps(result,ensure_ascii=False))>5_000_000:raise db.error(413,'AUDIT_SCOPE_TOO_LARGE','نطاق التدقيق كبير؛ اختر عددًا أقل من التقفيلات.')
    return result


@router.get('')
def investigation(order_query:str|None=Query(default=None,max_length=150),report_ids:list[str]=Query(default=[]),user=Depends(auth.current_user)):
    require_owner(user)
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        return bundle(c,user,order_query,report_ids)


@router.get('/export/{format}')
def export(format:Literal['json','pdf','xlsx'],order_query:str|None=Query(default=None,max_length=150),report_ids:list[str]=Query(default=[]),lang:Literal['ar','en']='ar',user=Depends(auth.current_user)):
    require_owner(user)
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        data=bundle(c,user,order_query,report_ids)
    return export_data(data,format,lang)


def export_data(data,format,lang):
    if format=='json':content=json.dumps(data,ensure_ascii=False,indent=2).encode();mime='application/json'
    else:
        from .accounting_export import pdf,xlsx
        sections=[];keys=['label','actor','day','reason']
        headers=['المصدر والمرجع','المنفذ / المسؤول','التوقيت','التفاصيل والإثباتات'] if lang=='ar' else ['Source and reference','Actor / responsible','Timestamp','Details and evidence']
        for source,records in data.items():
            if not isinstance(records,list) or not records or not isinstance(records[0],dict):continue
            values=[headers]
            for record in records:
                actor=record.get('actor_name') or record.get('actor_id') or record.get('responsible_user_id') or record.get('user_id') or record.get('collector_id') or record.get('authorized_by') or '—'
                detail=json.dumps({k:v for k,v in record.items() if k not in ('actions','history')},ensure_ascii=False,indent=1)
                values.append([source+' · '+str(record.get('id') or record.get('number') or record.get('order_no') or '—'),actor,record.get('created_at') or record.get('opened_at') or '—',detail])
            sections.append(dict(title=source,keys=keys,rows=values))
        title='تدقيق المالك — السجلات والأدلة' if lang=='ar' else 'Owner audit — records and evidence'
        note='الأدلة المسجلة فقط؛ لا يثبت هذا التقرير مطابقة الأرصدة الفعلية أو وصول مبالغ البنك.' if lang=='ar' else 'Recorded evidence only; this report does not certify real balances or bank receipt.'
        if format=='pdf':content=pdf([headers],keys,title,data['generated_at'],note,lang,sections=sections);mime='application/pdf'
        else:content=xlsx([headers,*[row for section in sections for row in section['rows'][1:]]],keys,[title,data['generated_at'],note],lang);mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return Response(content,media_type=mime,headers={'Content-Disposition':f'attachment; filename="owner-audit.{format}"','Cache-Control':'private, no-store'})
