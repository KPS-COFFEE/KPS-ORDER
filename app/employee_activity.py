"""Owner-only access and activity report. Sessions are never working-time claims."""
import io
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict
from . import auth, db

router = APIRouter()
TZ = ZoneInfo('Asia/Riyadh')
PAGES = {'dashboard':'لوحة المتابعة','accounting':'المحاسبة','orders':'الطلبات',
    'customers':'العملاء','tasks':'المهام','shifts':'الشفتات','custody':'العهد',
    'chat':'المحادثات','reports':'التقارير','drivers':'السائقون','admin':'الإعدادات',
    'fleet':'المركبات','maintenance':'الصيانة','printers':'الطابعات','support':'الدعم',
    'timing':'أوقات التشغيل','tracking':'التتبع','recovery':'معالجة الطلبات','restricted':'الصلاحيات'}
LABELS = {'before':'قبل','after':'بعد','reason':'السبب','action_type':'نوع الإجراء',
    'bank_halalas':'الشبكة','cash_due_halalas':'الكاش المستحق','cash_received_halalas':'الكاش المستلم',
    'net_difference_halalas':'صافي الفرق','shortage_halalas':'العجز','surplus_halalas':'الفائض',
    'action_taken':'الإجراء المتخذ','status':'الحالة','decision':'القرار','amount_halalas':'المبلغ',
    'cash_halalas':'كاش','card_halalas':'شبكة','payment_method':'وسيلة الدفع','page':'الشاشة'}
NOTE = 'فتح الشاشة لا يثبت قراءة محتواها. مدة الجلسة ليست وقت عمل فعلي. غياب النشاط المسجل لا يثبت عدم العمل. البيانات القديمة قد لا تكون مرتبطة بجلسة محددة.'


def clean(value, depth=0):
    if depth > 4:
        return None
    if isinstance(value,dict):
        return {k:clean(v,depth+1) for k,v in value.items() if k in LABELS}
    if isinstance(value,(int,float,bool)) or value is None:
        return value
    return value[:1000] if isinstance(value,str) else None


def kind(action):
    if action=='activity.view':return 'view'
    if action.startswith(('session.','identity.','employee.sms.','otp.')):return 'access'
    if 'export' in action:return 'export'
    return 'action'


def title(action, details):
    exact = {'session.created':'دخول ناجح','session.revoked':'خروج أو إلغاء الجلسة','activity.view':'عرض شاشة'}
    typed = {'approval':'اعتماد دون تعديل','correction':'تعديل بيانات','cash_receipt':'تسجيل استلام كاش'}
    if details.get('action_type') in typed:return typed[details['action_type']]
    if action in exact:return exact[action]
    for key,label in [('correct','تصحيح بيانات'),('adjust','تعديل بيانات'),('update','تحديث بيانات'),('confirm','تأكيد'),('approv','اعتماد'),('export','تصدير تقرير')]:
        if key in action:return label
    return 'إجراء مسجل'


def detail_lines(value, prefix=''):
    result=[]
    for k,v in value.items():
        label=prefix+LABELS.get(k,k)
        if isinstance(v,dict):result.extend(detail_lines(v,label+' / '));continue
        if k.endswith('_halalas') and type(v) in (int,float):v=f'{v/100:.2f} ر.س'
        if k=='page':v=PAGES.get(v,v)
        if k=='action_type':v={'approval':'اعتماد دون تعديل','correction':'تعديل','cash_receipt':'استلام كاش'}.get(v,v)
        result.append(f'{label}: {v}')
    return result


def period(start,end):
    if end<start or (end-start).days>30:
        raise db.error(422,'ACTIVITY_PERIOD_INVALID','اختر فترة لا تتجاوز ٣١ يومًا.')
    return datetime.combine(start,time.min,TZ),datetime.combine(end+timedelta(days=1),time.min,TZ)


class View(BaseModel):
    model_config=ConfigDict(extra='forbid')
    page: str


@router.post('/api/operations/activity-view')
def view(body:View,user=Depends(auth.current_user)):
    if user['role']=='customer' or body.page not in PAGES:
        raise db.error(403,'ACTIVITY_VIEW_INVALID','الشاشة غير متاحة.')
    with db.tx() as c:
        if not db.rate_limit(c,'activity-view:'+user['session_id'],120,60):
            raise db.error(429,'ACTIVITY_RATE_LIMIT','انتظر قليلًا.')
        db.audit(c,user['id'],'activity.view','screen',body.page,{'page':body.page})
    return {'ok':True}


@router.get('/employee-activity')
def page(user=Depends(auth.require_roles('owner'))):
    return FileResponse(db.ROOT/'public/employee-activity.html',headers={'Cache-Control':'no-store'})


@router.get('/api/admin/employee-activity/employees')
def employees(user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        rows=c.execute("SELECT id,display_name,active FROM users WHERE brand_id=%s AND role<>'customer' ORDER BY display_name,id",(user.get('home_brand_id') or user['brand_id'],)).fetchall()
    return {'employees':rows}


def build(c,user,start,end,employee_id):
    lo,hi=period(start,end)
    # One shared employee identity: use its home brand for employee access history.
    brand=user.get('home_brand_id') or user['brand_id']
    if employee_id and not c.execute("SELECT id FROM users WHERE id=%s AND brand_id=%s AND role<>'customer'",(employee_id,brand)).fetchone():
        raise db.error(404,'EMPLOYEE_NOT_FOUND','الموظف غير متاح.')
    sessions=c.execute("""SELECT s.id,u.display_name,s.created_at,s.expires_at,s.revoked_at,s.activity_tracking
        FROM sessions s JOIN users u ON u.id=s.user_id WHERE u.brand_id=%s AND u.role<>'customer'
        AND (%s::text IS NULL OR u.id=%s) AND s.created_at<%s
        AND LEAST(s.expires_at,COALESCE(s.revoked_at,s.expires_at))>=%s
        ORDER BY s.created_at DESC,s.id LIMIT 1001""",(brand,employee_id,employee_id,hi,lo)).fetchall()
    events=c.execute("""SELECT a.id,a.session_id,a.action,a.entity_type,a.entity_id,a.details,a.created_at,u.display_name
        FROM audit_events a JOIN users u ON u.id=a.actor_id WHERE u.brand_id=%s AND u.role<>'customer'
        AND (%s::text IS NULL OR u.id=%s) AND a.created_at>=%s AND a.created_at<%s
        AND (a.activity_brand_id IS NULL OR a.activity_brand_id=%s)
        ORDER BY a.created_at,a.id LIMIT 5001""",(brand,employee_id,employee_id,lo,hi,user['brand_id'])).fetchall()
    if len(events)>5000 or len(sessions)>1000:
        raise db.error(422,'ACTIVITY_TOO_LARGE','السجل كبير؛ اختر موظفًا أو فترة أقصر لعرضه كاملًا.')
    summary={'logins':sum(lo<=s['created_at']<hi for s in sessions),'views':0,'actions':0,'exports':0}
    by_session={s['id']:s for s in sessions}
    for s in sessions:s.update(views=0,actions=0,exports=0,last_activity_at=None)
    for e in events:
        e['kind']=kind(e['action']);e['details']=clean(e['details']);e['title']=title(e['action'],e['details']);e['detail_lines']=detail_lines(e['details'])
        key={'view':'views','action':'actions','export':'exports'}.get(e['kind'])
        if key:
            summary[key]+=1
            if e['session_id'] in by_session:
                s=by_session[e['session_id']];s[key]+=1;s['last_activity_at']=e['created_at']
    return {'start':str(start),'end':str(end),'generated_at':datetime.now(timezone.utc),'summary':summary,'sessions':sessions,'events':events,'note':NOTE}


@router.get('/api/admin/employee-activity')
def report(start:date,end:date,employee_id:str|None=Query(None,max_length=150),format:Literal['json','pdf']='json',user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        data=build(c,user,start,end,employee_id)
        if format=='pdf':
            content=pdf(data)
            db.audit(c,user['id'],'employee_activity.export','activity_report',user['id'])
    if format=='pdf':return Response(content,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="employee-activity-{start}-{end}.pdf"','Cache-Control':'private, no-store'})
    return data


def pdf(data):
    import arabic_reshaper
    from bidi.algorithm import get_display
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font='EmployeeActivityArabic'
    if font not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(font,str(db.ROOT/'public/assets/fonts/NotoKufiArabic-Regular.ttf')))
    stream=io.BytesIO();cv=canvas.Canvas(stream,pagesize=(595,842));cv.setTitle('تقرير دخول ونشاط الموظفين');y=785;page_num=1
    def shape(v):return get_display(arabic_reshaper.reshape(str(v)))
    def footer():
        cv.setFont(font,8);cv.setFillColorRGB(.35,.4,.35);cv.drawRightString(555,25,shape(f'صفحة {page_num} | توقيت السعودية'))
    def draw(text,size):
        nonlocal y,page_num
        if y<65:footer();cv.showPage();page_num+=1;y=785
        cv.setFont(font,size);cv.setFillColorRGB(.13,.22,.16);cv.drawRightString(555,y,shape(text));y-=max(19,size*1.7)
    def line(text,size=9):
        current=''
        for word in str(text).split():
            for i in range(0,len(word),60):
                part=word[i:i+60];candidate=(current+' '+part).strip()
                if current and pdfmetrics.stringWidth(shape(candidate),font,size)>510:draw(current,size);current=part
                else:current=candidate
        draw(current,size)
    def stamp(v):return v.astimezone(TZ).strftime('%Y-%m-%d %H:%M') if v else '-'
    line('تقرير دخول ونشاط الموظفين',16);line(f"{data['start']} - {data['end']}")
    line('تاريخ التصدير: '+stamp(data['generated_at']))
    s=data['summary'];line(f"الدخول: {s['logins']} | عرض: {s['views']} | إجراءات: {s['actions']} | تصدير: {s['exports']}",11)
    line('جلسات الدخول',12)
    for s in data['sessions']:
        line(s['display_name']+' | الدخول: '+stamp(s['created_at']))
        line(('خروج/إلغاء: '+stamp(s['revoked_at'])) if s['revoked_at'] else 'انتهاء الصلاحية: '+stamp(s['expires_at']))
        line(f"عرض: {s['views']} | إجراءات: {s['actions']} | آخر نشاط: {stamp(s['last_activity_at'])}")
        if not s['activity_tracking']:line('تغطية تاريخية محدودة')
        elif not(s['views']+s['actions']+s['exports']):line('لا نشاط مسجل خلال الفترة')
    line('تسلسل النشاط',12)
    for e in data['events']:
        line(stamp(e['created_at'])+' | '+e['display_name']+' | '+e['title'],10)
        line(e['entity_type']+' / '+e['entity_id'],8)
        for detail in e['detail_lines']:line(detail,8)
    if not data['events']:line('لا نشاط مسجل خلال الفترة')
    line(NOTE,8);footer();cv.save();return stream.getvalue()
