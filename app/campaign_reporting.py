"""Scoped online/gift reporting over canonical orders and permanent claims.

Dates are Riyadh calendar dates, not accounting closure dates. This report never
releases a gift claim, edits an order, or replaces an immutable shift report.
"""
import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from . import auth, db

router = APIRouter(prefix='/api/reports/promotions', tags=['campaign-reporting'])
RIYADH = ZoneInfo('Asia/Riyadh')


def period(start: date, end: date):
    if end == date.max or end < start or (end-start).days > 92:
        raise db.error(422, 'REPORT_PERIOD_INVALID', 'اختر فترة لا تتجاوز 93 يومًا وبنهاية بعد البداية.')
    return datetime.combine(start, time.min, RIYADH), datetime.combine(end+timedelta(days=1), time.min, RIYADH)


def report_scope(conn, user, branch_id):
    if user['role'] not in {'owner', 'delivery_manager'} or not auth.has_permission(user, 'reports_view'):
        raise db.error(403, 'REPORT_FORBIDDEN', 'التقرير متاح للمالك ومدير التوصيل المخول فقط.')
    # Morning duty has stricter shift-specific history rules. A branch-wide
    # marketing report must not broaden those permissions.
    if user.get('_morning_manager_context'):
        raise db.error(403, 'REPORT_FORBIDDEN', 'استخدم تقرير المناوبة ضمن نطاق شفتك الحالي.')
    rows = conn.execute('SELECT id,name FROM branches WHERE brand_id=%s ORDER BY branch_no', (user['brand_id'],)).fetchall()
    allowed = {row['id'] for row in rows if user['role']=='owner' or row['id'] in user.get('branch_ids', [])}
    if branch_id and branch_id not in allowed:
        raise db.error(404, 'BRANCH_NOT_FOUND', 'الفرع غير موجود ضمن صلاحياتك.')
    return [row for row in rows if row['id'] in allowed and (not branch_id or row['id']==branch_id)]


# The historical customer key is canonical Saudi mobile when possible, with a
# private customer-id fallback for records without a valid mobile. It is never
# returned. Current normalized orders need no data correction/deduplication.
BASE = r"""
WITH scoped_orders AS (
 SELECT o.*,regexp_replace(translate(o.customer->>'phone','٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹','01234567890123456789'),
        U&'[[:space:]()\200B-\200F\202A-\202E\2066-\2069\FEFF-]', '', 'g') compact_phone
 FROM orders o WHERE o.brand_id=%s AND o.branch_id=ANY(%s)
   AND o.snapshot->>'order_source' IS DISTINCT FROM 'delivery_center'
   AND o.test_void IS NULL AND o.created_at<%s
), history AS (
 SELECT o.*,coalesce(normalized_identity_phone(CASE
        WHEN compact_phone ~ '^5[0-9]{8}$' THEN '+966'||compact_phone
        WHEN compact_phone ~ '^9665[0-9]{8}$' THEN '+'||compact_phone
        ELSE compact_phone END),'customer:'||o.customer_id) customer_key,
        (o.created_at AT TIME ZONE 'Asia/Riyadh')::date report_date,
        (o.preparation_status IN ('cancelled','rejected') OR o.delivery_status='cancelled') cancelled
 FROM scoped_orders o
), first_orders AS (
 SELECT DISTINCT ON (customer_key) customer_key,id first_order_id
 FROM history WHERE NOT cancelled ORDER BY customer_key,created_at,id
), selected AS (
 SELECT h.*,f.first_order_id,g.policy_version,g.claimed_at,
        CASE WHEN g.phone IS NULL THEN NULL ELSE '••••'||right(g.phone,4) END phone_suffix
 FROM history h LEFT JOIN first_orders f USING(customer_key)
 LEFT JOIN first_order_gift_claims g ON g.order_id=h.id AND g.brand_id=h.brand_id
 WHERE h.created_at>=%s
)
"""


def _totals_sql(policy_version):
    claim_filter = 'policy_version IS NOT NULL' + (' AND policy_version=%s' if policy_version else '')
    return """count(*) online_orders,
      count(*) FILTER (WHERE NOT cancelled) non_cancelled_orders,
      count(*) FILTER (WHERE cancelled) cancelled_orders,
      count(*) FILTER (WHERE NOT cancelled AND (delivery_status='delivered' OR preparation_status='completed')) completed_orders,
      count(DISTINCT customer_key) FILTER (WHERE NOT cancelled) unique_customers,
      count(DISTINCT customer_key) FILTER (WHERE NOT cancelled AND id=first_order_id) first_time_customers,
      count(DISTINCT customer_key) FILTER (WHERE NOT cancelled AND id<>first_order_id) repeat_customers,
      count(*) FILTER (WHERE """+claim_filter+""") gift_claims,
      count(*) FILTER (WHERE ("""+claim_filter+""") AND NOT cancelled) non_cancelled_gift_orders,
      count(*) FILTER (WHERE ("""+claim_filter+""") AND cancelled) cancelled_gift_orders"""


def _daily_report(conn, user, start, end, branches, policy_version, branch_id=None):
    start_at, end_at = period(start, end)
    branch_ids = [row['id'] for row in branches]
    args = (user['brand_id'], branch_ids, end_at, start_at)
    # Revision filters claims only; it is never a campaign/exposure identity.
    totals = _totals_sql(policy_version)
    query_args = (*args, *((policy_version,)*3 if policy_version else ()))
    summary = conn.execute(BASE+'SELECT '+totals+' FROM selected', query_args).fetchone()
    daily = conn.execute(BASE+'SELECT report_date,'+totals+' FROM selected GROUP BY report_date ORDER BY report_date', query_args).fetchall()
    return _metadata(user,start,end,branches,policy_version,branch_id)|{'summary':summary,'daily':daily}


def _metadata(user,start,end,branches,policy_version,branch_id):
    return {'brand_id':user['brand_id'], 'timezone':'Asia/Riyadh', 'date_basis':'calendar_day',
            'start':start.isoformat(), 'end':end.isoformat(), 'branches':branches,
            'policy_version':policy_version, 'selected_branch_id':branch_id, 'customer_history_scope':'brand' if user['role']=='owner' and not branch_id else 'selected_branches',
            'definitions':{'online_orders':'Customer-entered orders; staff delivery-center orders and owner-voided tests excluded.',
                           'first_time_customers':'First non-cancelled online order in the visible branch history.',
                           'repeat_customers':'Another non-cancelled online order exists earlier in the visible branch history; a customer may be first and repeat within one period.',
                           'gift_claims':'Permanent gift redemptions at order creation; cancellation never restores eligibility.'},
            'summary':{}, 'daily':[]}


@router.get('/daily')
def daily_report(start: date, end: date, response: Response,
                 branch_id: str | None = Query(default=None, min_length=1, max_length=80),
                 policy_version: int | None = Query(default=None, ge=1),
                 user=Depends(auth.current_user)):
    period(start, end)
    response.headers['Cache-Control'] = 'private, no-store'
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        branches = report_scope(conn, user, branch_id)
        report = _daily_report(conn,user,start,end,branches,policy_version,branch_id)
        db.audit(conn, user['id'], 'campaign.report.read', 'brand', user['brand_id'],
                 {'start':start.isoformat(), 'end':end.isoformat(), 'branch_ids':[b['id'] for b in branches], 'policy_version':policy_version})
    return report


CLAIM_COLUMNS = """id order_id,order_no,branch_id,report_date,created_at,claimed_at,
    policy_version,phone_suffix,cancelled,payment_status,(id=first_order_id) first_online_order"""


def _beneficiaries(conn,user,start,end,branches,policy_version,limit,offset=0):
    start_at,end_at=period(start,end)
    args=(user['brand_id'],[b['id'] for b in branches],end_at,start_at)
    where='policy_version IS NOT NULL'
    if policy_version:
        where+=' AND policy_version=%s'
        args+=(policy_version,)
    return conn.execute(BASE+'SELECT '+CLAIM_COLUMNS+' FROM selected WHERE '+where+
        ' ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s',(*args,limit,offset)).fetchall()


@router.get('/beneficiaries')
def beneficiaries(start: date, end: date, response: Response,
                  branch_id: str | None = Query(default=None, min_length=1, max_length=80),
                  policy_version: int | None = Query(default=None, ge=1),
                  offset: int = Query(default=0, ge=0, le=100000),
                  limit: int = Query(default=50, ge=1, le=100),
                  user=Depends(auth.current_user)):
    start_at, end_at = period(start, end)
    response.headers['Cache-Control'] = 'private, no-store'
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        branches = report_scope(conn, user, branch_id)
        branch_ids = [row['id'] for row in branches]
        rows = _beneficiaries(conn,user,start,end,branches,policy_version,limit,offset)
        db.audit(conn, user['id'], 'campaign.beneficiaries.read', 'brand', user['brand_id'],
                 {'start':start.isoformat(),'end':end.isoformat(),'branch_ids':branch_ids,'policy_version':policy_version,'offset':offset,'returned':len(rows)})
    return {'brand_id':user['brand_id'],'timezone':'Asia/Riyadh','date_basis':'calendar_day',
            'beneficiaries':rows,'offset':offset,'limit':limit,'phone_display':'masked_suffix_only'}


def _full_snapshot(conn,user,body,branches):
    # One PostgreSQL statement observes summary, all days and all beneficiary
    # rows at precisely the same MVCC snapshot. The surrounding idempotency
    # transaction stays READ COMMITTED, so concurrent retries waiting on the
    # existing advisory lock can see the first committed replay response.
    start_at,end_at=period(body.start,body.end)
    version=body.policy_version;totals=_totals_sql(version)
    claim_filter='policy_version IS NOT NULL'+(' AND policy_version=%s' if version else '')
    args=(user['brand_id'],[b['id'] for b in branches],end_at,start_at)
    args+=((version,)*6 if version else ())
    args+=((version,) if version else ())+(MAX_FINAL_BENEFICIARIES+1,)
    row=conn.execute(BASE+", report_summary AS (SELECT "+totals+" FROM selected), "
        +"report_days AS (SELECT report_date,"+totals+" FROM selected GROUP BY report_date), "
        +"report_claims AS (SELECT "+CLAIM_COLUMNS+" FROM selected WHERE "+claim_filter
        +" ORDER BY created_at DESC,id DESC LIMIT %s) "
        +"SELECT (SELECT row_to_json(s) FROM report_summary s) summary, "
        +"(SELECT coalesce(jsonb_agg(to_jsonb(d) ORDER BY report_date),'[]'::jsonb) FROM report_days d) daily, "
        +"(SELECT coalesce(jsonb_agg(to_jsonb(c) ORDER BY created_at DESC,order_id DESC),'[]'::jsonb) FROM report_claims c) beneficiaries",args).fetchone()
    return jsonable_encoder(_metadata(user,body.start,body.end,branches,version,body.branch_id)|row)


# These are technical response/storage limits, not truncated business reports.
# Exceeding either aborts the transaction; owners can select a shorter period.
MAX_FINAL_BENEFICIARIES=20000
MAX_FINAL_BYTES=16*1024*1024


class FinalizePeriod(BaseModel):
    model_config=ConfigDict(extra='forbid')
    start:date
    end:date
    branch_id:str|None=Field(default=None,min_length=1,max_length=80)
    policy_version:StrictInt|None=Field(default=None,ge=1,le=2147483647)
    reason:str=Field(min_length=3,max_length=500)


def _closure_view(row,offset=0,limit=50):
    snapshot=row['snapshot']
    if db.digest(snapshot)!=row['sha256']:
        raise db.error(409,'REPORT_INTEGRITY_INVALID','تعذر التحقق من سلامة التقرير النهائي.')
    return {'closure':{k:row[k] for k in ('id','brand_id','actor_id','reason','created_at','sha256')},
            'report':{k:v for k,v in snapshot.items() if k!='beneficiaries'},
            'beneficiaries':snapshot['beneficiaries'][offset:offset+limit],
            'beneficiary_count':len(snapshot['beneficiaries']),'offset':offset,'limit':limit,
            'immutable':True,'campaign_state_changed':False}


@router.post('/closures')
def finalize_period(body:FinalizePeriod,response:Response,
                    user=Depends(auth.current_user),
                    idem:str|None=Header(None,alias='Idempotency-Key')):
    period(body.start,body.end)
    if len(body.reason.strip())<3 or '\x00' in body.reason:
        raise db.error(422,'REASON_REQUIRED','أدخل سبب حفظ التقرير النهائي.')
    response.headers['Cache-Control']='private, no-store'
    with db.tx() as conn:
        branches=report_scope(conn,user,body.branch_id)
        if user['role']!='owner':
            raise db.error(403,'REPORT_FINALIZE_FORBIDDEN','حفظ التقرير النهائي متاح للمالك فقط.')
        # Current authorization runs before replay. No OTP or campaign mutation.
        def perform():
            report=_full_snapshot(conn,user,body,branches)
            claims=report['beneficiaries']
            if len(claims)>MAX_FINAL_BENEFICIARIES:
                raise db.error(409,'REPORT_TOO_LARGE','اختر فترة أقصر لحفظ تقرير كامل دون اقتطاع.')
            snapshot=jsonable_encoder(report|{'format':'online-gift-period-v1',
                'attribution':'observed_online_history_only','finalization':'explicit_owner_period',
                'campaign_state_changed':False})
            if len(json.dumps(snapshot,ensure_ascii=False,separators=(',',':')).encode())>MAX_FINAL_BYTES:
                raise db.error(409,'REPORT_TOO_LARGE','اختر فترة أقصر لحفظ تقرير كامل دون اقتطاع.')
            identifier=db.new_id('promotion_report_');digest=db.digest(snapshot)
            row=conn.execute('INSERT INTO promotion_report_closures(id,brand_id,actor_id,start_date,end_date,branch_ids,policy_version,reason,sha256,snapshot) '
                'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,sha256,created_at',
                (identifier,user['brand_id'],user['id'],body.start,body.end,db.json([b['id'] for b in branches]),
                 body.policy_version,body.reason.strip(),digest,db.json(snapshot))).fetchone()
            db.audit(conn,user['id'],'campaign.report.finalized','promotion_report',identifier,
                {'start':body.start.isoformat(),'end':body.end.isoformat(),'branch_ids':[b['id'] for b in branches],
                 'policy_version':body.policy_version,'sha256':digest,'beneficiary_count':len(claims)})
            return {'brand_id':user['brand_id'],'closure':row,'immutable':True,'campaign_state_changed':False,
                    'beneficiary_count':len(claims)}
        return db.idempotent(conn,user['id'],idem,'campaign.report.finalize',body.model_dump(mode='json'),perform)


@router.get('/closures')
def finalized_periods(response:Response,offset:int=Query(default=0,ge=0,le=100000),user=Depends(auth.current_user)):
    response.headers['Cache-Control']='private, no-store'
    with db.tx() as conn:
        branches=report_scope(conn,user,None)
        rows=conn.execute('SELECT id,brand_id,actor_id,start_date,end_date,branch_ids,policy_version,reason,sha256,created_at,'
            "jsonb_array_length(snapshot->'beneficiaries') beneficiary_count FROM promotion_report_closures "
            'WHERE brand_id=%s AND branch_ids <@ %s::jsonb ORDER BY created_at DESC,id DESC LIMIT 50 OFFSET %s',
            (user['brand_id'],db.json([b['id'] for b in branches]),offset)).fetchall()
        db.audit(conn,user['id'],'campaign.report.closures.read','brand',user['brand_id'],{'offset':offset,'returned':len(rows)})
    return {'brand_id':user['brand_id'],'closures':rows,'offset':offset,'limit':50}


@router.get('/closures/{identifier}')
def finalized_period(identifier:str,response:Response,offset:int=Query(default=0,ge=0,le=100000),
                     limit:int=Query(default=50,ge=1,le=100),user=Depends(auth.current_user)):
    response.headers['Cache-Control']='private, no-store'
    with db.tx() as conn:
        branches=report_scope(conn,user,None)
        row=conn.execute('SELECT * FROM promotion_report_closures WHERE id=%s AND brand_id=%s '
            'AND branch_ids <@ %s::jsonb',(identifier,user['brand_id'],db.json([b['id'] for b in branches]))).fetchone()
        if not row:
            raise db.error(404,'REPORT_NOT_FOUND','التقرير النهائي غير موجود ضمن صلاحياتك.')
        db.audit(conn,user['id'],'campaign.report.closure.read','promotion_report',identifier,
                 {'offset':offset,'limit':limit,'sha256':row['sha256']})
    return _closure_view(row,offset,limit)


def _csv_report(row,lang):
    import csv
    import io
    ar=lang=='ar';snap=row['snapshot'];out=io.StringIO();writer=csv.writer(out)
    def write(values):
        # Staff branch names/reasons are data, including formula-like strings.
        def cell(value):
            text='' if value is None else str(value)
            return "'"+text if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r','\n')) else text
        writer.writerow([cell(v) for v in values])
    titles={
        'online_orders':('طلبات أونلاين','Online orders'),'non_cancelled_orders':('طلبات غير ملغاة','Non-cancelled orders'),
        'cancelled_orders':('طلبات ملغاة','Cancelled orders'),'completed_orders':('طلبات مكتملة','Completed orders'),
        'unique_customers':('عملاء فريدون','Unique customers'),'first_time_customers':('عملاء جدد أونلاين','First-time online customers'),
        'repeat_customers':('عملاء متكررون','Repeat customers'),'gift_claims':('استفادات مسجلة من الهدية','Recorded gift claims'),
        'non_cancelled_gift_orders':('هدايا بطلبات غير ملغاة','Non-cancelled gift orders'),
        'cancelled_gift_orders':('هدايا بطلبات ملغاة','Cancelled gift orders')}
    t=lambda key:titles[key][0 if ar else 1]
    write(['تقرير نهائي للفترة' if ar else 'Final period report',row['id']])
    write(['البراند' if ar else 'Brand',row['brand_id']]);write(['وقت الحفظ' if ar else 'Saved at',row['created_at'].isoformat()])
    write(['من' if ar else 'From',snap['start'],'إلى' if ar else 'To',snap['end'],'Asia/Riyadh'])
    write(['الفروع' if ar else 'Branches',*['%s (%s)'%(b['name'],b['id']) for b in snap['branches']]])
    write(['إصدار إعداد الهدية' if ar else 'Gift policy revision',snap['policy_version'] or ('الكل' if ar else 'All')])
    write(['سبب الحفظ' if ar else 'Reason',row['reason']]);write(['SHA-256',row['sha256']])
    write(['بيانات مشاهدة وقت الحفظ فقط؛ ليست نسبة إحالات للإعلان. لا يوقف التقرير الهدية أو الإعلان.' if ar else
           'Observed history at save time; no advertising exposure attribution. This report does not stop gifts or advertisements.'])
    write(['العميل الجديد صاحب أول طلب أونلاين غير ملغى ضمن نطاق الفروع؛ قد يحسب جديدا ومتكررا خلال الفترة. إلغاء طلب الهدية لا يعيد الاستحقاق.' if ar else
           'First-time means the first non-cancelled online order in these branches; a customer can be first-time and repeat within the period. Cancelled gift orders never restore eligibility.'])
    write([]);write(['الملخص' if ar else 'Summary'])
    for key,value in snap['summary'].items():write([t(key),value])
    write([]);write(['الطلبات حسب اليوم' if ar else 'Orders by day'])
    keys=list(titles);write(['التاريخ' if ar else 'Date',*[t(k) for k in keys]])
    for day in snap['daily']:write([day['report_date'],*[day[k] for k in keys]])
    write([]);write(['جميع المستفيدين' if ar else 'All beneficiaries',len(snap['beneficiaries'])])
    write(['الطلب','الفرع','آخر أرقام الجوال','تاريخ الطلب','تسجيل الهدية','إصدار الإعداد','ملغى','أول طلب أونلاين'] if ar else
          ['Order','Branch','Phone suffix','Order date','Gift claimed','Policy revision','Cancelled','First online order'])
    names={b['id']:b['name'] for b in snap['branches']}
    boolean=lambda value:('نعم' if value else 'لا') if ar else ('Yes' if value else 'No')
    for claim in snap['beneficiaries']:
        write([claim['order_no'] or claim['order_id'],names.get(claim['branch_id'],claim['branch_id']),claim['phone_suffix'],
               claim['created_at'],claim['claimed_at'],claim['policy_version'],boolean(claim['cancelled']),
               boolean(claim['first_online_order']) if claim['first_online_order'] is not None else ''])
    return ('\ufeff'+out.getvalue()).encode('utf-8')


@router.get('/closures/{identifier}/download')
def download_finalized_period(identifier:str,lang:str=Query(default='ar',pattern='^(ar|en)$'),user=Depends(auth.current_user)):
    with db.tx() as conn:
        branches=report_scope(conn,user,None)
        row=conn.execute('SELECT * FROM promotion_report_closures WHERE id=%s AND brand_id=%s '
            'AND branch_ids <@ %s::jsonb',(identifier,user['brand_id'],db.json([b['id'] for b in branches]))).fetchone()
        if not row:
            raise db.error(404,'REPORT_NOT_FOUND','التقرير النهائي غير موجود ضمن صلاحياتك.')
        _closure_view(row,0,0)  # Verify exact persisted content before exporting.
        db.audit(conn,user['id'],'campaign.report.closure.download','promotion_report',identifier,{'sha256':row['sha256'],'lang':lang})
    return Response(content=_csv_report(row,lang),media_type='text/csv; charset=utf-8',
                    headers={'Cache-Control':'private, no-store','Content-Disposition':'attachment; filename="online-gift-report-'+lang+'.csv"'})
