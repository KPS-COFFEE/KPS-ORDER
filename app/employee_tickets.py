"""Employee requests are review records, never permission or financial commands."""
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from . import auth, db
from .operations import safe

router=APIRouter(prefix='/api/employee-tickets',tags=['employee-tickets'])
STAFF=auth.ROLES-{'customer'}
CREATED='employee.ticket.created'
REVIEWED='employee.ticket.reviewed'


def fail(code,message,status=422):
    raise db.error(status,code,message)


def staff(user=Depends(auth.current_user)):
    if user['role'] not in STAFF or not user.get('brand_id'):
        fail('STAFF_REQUIRED','التذاكر متاحة لحسابات الموظفين فقط.',403)
    return user


def option(code,ar,en,**extra):
    return dict(code=code,ar=ar,en=en,**extra)


COMMON_REASONS=[option('work_required','متطلب لتنفيذ العمل','Required for work'),
    option('incorrect_entry','خطأ في الإدخال','Entry error'),
    option('not_working','الخدمة لا تعمل كما ينبغي','Not working as expected'),
    option('other','سبب آخر','Other reason')]
ROLE_ISSUES={
    'driver':[option('delivery_issue','مشكلة في تنفيذ التوصيل','Delivery issue'),option('shift_issue','مشكلة في المناوبة','Shift issue')],
    'delivery_manager':[option('dispatch_issue','مشكلة في الإسناد أو متابعة السائقين','Dispatch or driver follow-up issue'),option('shift_issue','مشكلة في المناوبة','Shift issue')],
    'delivery_staff':[option('order_followup','مشكلة في متابعة الطلب','Order follow-up issue')],
    'branch_manager':[option('preparation_issue','مشكلة في تجهيز الطلب','Order preparation issue'),option('branch_issue','مشكلة في إجراءات الفرع','Branch workflow issue')],
    'admin':[option('records_issue','مشكلة في السجلات أو التقارير','Records or reports issue')],
    'owner':[option('business_issue','طلب متعلق بإدارة العمل','Business management request')],
}
ISSUE_REASONS={
    'delivery_issue':[option('customer_unreachable','تعذر التواصل مع العميل','Customer unreachable'),option('address_problem','مشكلة في عنوان التوصيل','Delivery address problem')],
    'dispatch_issue':[option('assignment_problem','مشكلة في إسناد الطلب','Order assignment problem'),option('driver_availability','عدم توفر سائق','Driver availability')],
    'preparation_issue':[option('item_unavailable','صنف غير متوفر','Item unavailable'),option('preparation_delay','تأخر التجهيز','Preparation delay')],
    'shift_issue':[option('shift_start','مشكلة في بدء المناوبة','Starting shift problem'),option('shift_close','مشكلة في إقفال المناوبة','Closing shift problem')],
    'accounting_issue':[option('reconciliation_issue','مشكلة في المطابقة','Reconciliation issue'),option('receipt_entry','خطأ في تسجيل الاستلام','Receipt entry error')],
    'cash_receipt_error':[option('physical_recount','إعادة عد الكاش المستلم','Physical cash recount'),option('typing_error','خطأ في كتابة المبلغ','Amount typing error')],
}
ROLE_LABELS={
    'driver':('السائق','Driver'),'delivery_manager':('مدير التوصيل','Delivery manager'),
    'delivery_staff':('موظف التوصيل','Delivery staff'),'branch_manager':('مسؤول الفرع','Branch manager'),
    'admin':('الإدارة','Administration'),'owner':('المالك','Owner'),'accounting':('المحاسبة','Accounting')}


def catalog(user):
    accounting=auth.has_permission(user,'accounting_review')
    issues=[dict(i) for i in ROLE_ISSUES[user['role']]]
    if accounting:issues.append(option('accounting_issue','مشكلة في مراجعة المحاسبة','Accounting review issue'))
    other=option('other','مشكلة أخرى','Other issue')
    def choices(values):
        return [dict(i,reasons=[dict(r) for r in [*ISSUE_REASONS.get(i['code'],[]),*COMMON_REASONS]]) for i in [*values,other]]
    return [
        option('service_request','طلب خدمة','Service request',issues=choices(issues)),
        option('permission_change','تغيير صلاحية','Permission change',issues=choices([option('incorrect_permission','صلاحية أو نطاق غير مناسب','Incorrect permission or scope')])),
        option('permission_addition','إضافة صلاحية','Permission addition',issues=choices([option('missing_permission','صلاحية مطلوبة غير متاحة','Required permission unavailable')])),
        option('technical','مشكلة تقنية','Technical issue',issues=choices([option('app_error','خطأ في النظام','Application error'),option('login_issue','مشكلة في الدخول','Sign-in issue'),*([option('printer_issue','مشكلة في الطباعة','Printing issue')] if user['role'] in {'owner','admin','branch_manager','delivery_manager'} else []),*([option('location_issue','مشكلة في الموقع أو الخرائط','Location or maps issue')] if user['role'] in {'driver','delivery_manager','delivery_staff'} else [])])),
        option('data_correction','تصحيح بيانات','Data correction',issues=choices([option('personal_data','خطأ في بيانات الموظف','Employee information error'),*issues])),
        option('cash_correction','تصحيح كاش','Cash correction',issues=choices([option('cash_receipt_error','خطأ في مبلغ الكاش المستلم','Received cash entry error'),option('cash_handover_issue','مشكلة في تسليم الكاش','Cash handover issue')])),
        option('other','أخرى','Other',issues=choices([])),
    ]


@router.get('/options')
def options(user=Depends(staff)):
    profile='accounting' if user['role']!='owner' and auth.has_permission(user,'accounting_review') else user['role']
    ar,en=ROLE_LABELS[profile]
    return dict(types=catalog(user),role_profile=option(profile,ar,en),can_review=user['role']=='owner',
        cash_correction_requires_accounting_ticket=True,review_only=True)


class TicketBody(BaseModel):
    model_config=ConfigDict(str_strip_whitespace=True,extra='forbid')
    type:Literal['service_request','permission_change','permission_addition','technical','data_correction','cash_correction','other']
    issue:str=Field(min_length=1,max_length=80)
    reason:str=Field(min_length=1,max_length=80)
    description:str=Field(default='',max_length=2000)


class TicketReview(BaseModel):
    model_config=ConfigDict(str_strip_whitespace=True,extra='forbid')
    expected_version:StrictInt=Field(ge=1)
    status:Literal['open','in_progress','approved','resolved','rejected']
    note:str=Field(min_length=3,max_length=2000)


def ticket_events(c,user,identifier,lock=False):
    if lock:
        # No mutable ticket row is needed: serialize append-only ledger decisions.
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('employee-ticket:'+identifier,))
    created=c.execute("""SELECT a.*,u.display_name actor_name FROM audit_events a JOIN users u ON u.id=a.actor_id
      WHERE a.action=%s AND a.entity_type='employee_ticket' AND a.entity_id=%s AND a.details->>'brand_id'=%s""",
      (CREATED,identifier,user['brand_id'])).fetchone()
    if not created or (user['role']!='owner' and created['actor_id']!=user['id']):
        fail('TICKET_SCOPE','التذكرة غير متاحة.',404)
    changes=c.execute("""SELECT a.*,u.display_name actor_name FROM audit_events a JOIN users u ON u.id=a.actor_id
      WHERE a.action=%s AND a.entity_type='employee_ticket' AND a.entity_id=%s
      AND a.details->>'brand_id'=%s ORDER BY (a.details->>'version')::int""",
      (REVIEWED,identifier,user['brand_id'])).fetchall()
    return created,changes


def render_ticket(created,changes):
    d=created['details'];last=changes[-1]['details'] if changes else dict(status='open',version=1)
    result=safe(dict(id=created['entity_id'],reference=d['reference'],type=d['type'],issue=d['issue'],reason=d['reason'],
        description=d['description'],type_label=d['type_label'],issue_label=d['issue_label'],reason_label=d['reason_label'],requested_by=created['actor_id'],requester_name=created['actor_name'],
        requester_role=d['requester_role'],created_at=created['created_at'],status=last['status'],version=last['version']))
    from .administrative_guidance import employee_ticket
    return dict(result,guidance=employee_ticket(result))


@router.post('')
def create_ticket(body:TicketBody,request:Request,user=Depends(staff)):
    selected=next(t for t in catalog(user) if t['code']==body.type)
    issue=next((i for i in selected['issues'] if i['code']==body.issue),None)
    reason=next((r for r in issue['reasons'] if r['code']==body.reason),None) if issue else None
    if not issue or not reason:
        fail('TICKET_OPTION','اختر المشكلة والسبب المتاحين لدورك.')
    if 'other' in {body.type,body.issue,body.reason} and len(body.description)<3:
        fail('OTHER_DESCRIPTION_REQUIRED','وضّح المشكلة أو السبب الآخر في الوصف.')
    with db.tx() as c:
        def perform():
            identifier=db.new_id('et_')
            db.audit(c,user['id'],CREATED,'employee_ticket',identifier,
                dict(brand_id=user['brand_id'],requester_role=user['role'],reference='ET-'+identifier.removeprefix('et_')[:12].upper(),
                     status='open',version=1,type_label={k:selected[k] for k in ('ar','en')},
                     issue_label={k:issue[k] for k in ('ar','en')},reason_label={k:reason[k] for k in ('ar','en')},**body.model_dump()))
            return render_ticket(*ticket_events(c,user,identifier))
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'employee.ticket.create:'+user['brand_id'],body.model_dump(),perform)


@router.get('')
def list_tickets(user=Depends(staff)):
    with db.tx() as c:
        created=c.execute("""SELECT a.*,u.display_name actor_name FROM audit_events a JOIN users u ON u.id=a.actor_id
          WHERE a.action=%s AND a.entity_type='employee_ticket' AND a.details->>'brand_id'=%s
          AND (%s OR a.actor_id=%s) ORDER BY a.created_at DESC,a.id DESC LIMIT 200""",
          (CREATED,user['brand_id'],user['role']=='owner',user['id'])).fetchall()
        return {'tickets':[render_ticket(*ticket_events(c,user,t['entity_id'])) for t in created]}


@router.get('/{identifier}')
def detail(identifier:str,user=Depends(staff)):
    with db.tx() as c:
        created,changes=ticket_events(c,user,identifier)
        history=[dict(status='open',note=created['details']['description'],actor_name=created['actor_name'],created_at=created['created_at'],version=1)]
        history.extend(dict(status=e['details']['status'],note=e['details']['note'],actor_name=e['actor_name'],created_at=e['created_at'],version=e['details']['version']) for e in changes)
        return safe(dict(ticket=render_ticket(created,changes),history=history))


@router.post('/{identifier}/review')
def review_ticket(identifier:str,body:TicketReview,request:Request,user=Depends(staff)):
    if user['role']!='owner':fail('OWNER_REQUIRED','متابعة واعتماد طلبات الموظفين متاح للمالك فقط.',403)
    with db.tx() as c:
        created,changes=ticket_events(c,user,identifier,True)
        def perform():
            current=render_ticket(created,changes)
            if body.expected_version!=current['version']:fail('TICKET_CHANGED','توجد متابعة أحدث؛ حدّث التذكرة.',409)
            db.audit(c,user['id'],REVIEWED,'employee_ticket',identifier,
                dict(brand_id=user['brand_id'],version=current['version']+1,status=body.status,note=body.note,
                     previous_status=current['status'],review_only=True,no_permission_grant=True,no_cash_movement=True))
            return render_ticket(*ticket_events(c,user,identifier))
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key'),'employee.ticket.review:'+identifier,body.model_dump(),perform)
