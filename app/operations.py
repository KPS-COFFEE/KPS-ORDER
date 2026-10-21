"""Independent delivery operations. Monetary values are integer halalas."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
import json as jsonlib
from uuid import uuid4
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictBool, field_validator
from . import db, auth, service_hours, shift_vehicle, shift_order_counts, blind_closure

router = APIRouter(prefix='/api', tags=['operations'])
RIYADH = ZoneInfo('Asia/Riyadh')
TERMINAL = ('delivered', 'cancelled', 'transferred', 'reassigned')
MAX_HALALAS = 9223372036854775807


class OperationBody(BaseModel):
    # Required reasons and handover references must contain actual text. Reject
    # PostgreSQL-invalid NULs at the API boundary instead of failing after work
    # has started inside a financial transaction.
    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator('*', mode='before')
    @classmethod
    def valid_text(cls, value):
        def has_nul(item):
            if isinstance(item, str):
                return '\x00' in item
            if isinstance(item, dict):
                return any(has_nul(key) or has_nul(item) for key, item in item.items())
            if isinstance(item, (list, tuple)):
                return any(has_nul(item) for item in item)
            return False
        if has_nul(value):raise ValueError('Invalid text')
        return value


def uid(): return uuid4().hex

def safe(value):
    def encode(o):
        if isinstance(o,Decimal):return int(o) if o==o.to_integral_value() else float(o)
        return o.isoformat() if hasattr(o,'isoformat') else str(o)
    return jsonlib.loads(jsonlib.dumps(value,default=encode))

def fail(status, message): raise HTTPException(status, message)

def roles(user, *allowed):
    if user['role'] not in allowed: fail(403, 'الصلاحية غير كافية')

def same_scope(user, row):
    return user.get('brand_id') == row['brand_id']

def order_access(c, user, order):
    if not order or not auth.can_access_order(user, order, c): fail(404, 'الطلب غير موجود')

def delegated_shift(user, capability):
    """An explicit accounting grant, never an alternate employee role."""
    return user['role']=='admin' and auth.has_permission(user, capability)

def shift_roles(user, capability, *allowed):
    if not delegated_shift(user, capability):roles(user, *allowed)

def close_capability(shift):
    return 'shifts_delegate_close' if shift and shift['kind']=='driver' else 'shifts_close'

def shift_access(c, user, shift, manager=False, capability=None):
    from .morning_manager_context import require_shift
    require_shift(user, shift)
    context = user.get('_morning_manager_context')
    if context and shift and shift['kind'] == 'manager' and c.execute("SELECT 1 FROM shifts WHERE manager_shift_id=%s AND (brand_id<>%s OR NOT (branch_ids <@ %s::jsonb) OR COALESCE(delivery_window_snapshot->>'period','evening')<>'morning') LIMIT 1", (shift['id'], user['brand_id'], db.json(user['branch_ids']))).fetchone():
        raise db.error(403, 'MORNING_SHIFT_MIXED_DATA', 'تحتاج المناوبة مراجعة المالك لوجود شفت خارج نطاق الصباح.')
    if context and c.execute("SELECT 1 FROM driver_tasks t JOIN orders o ON o.id=t.order_id WHERE (t.shift_id=%s OR t.shift_id IN (SELECT id FROM shifts WHERE manager_shift_id=%s)) AND (o.brand_id<>%s OR NOT (o.branch_id=ANY(%s)) OR o.service<>'delivery' OR COALESCE(o.snapshot->>'delivery_period','evening')<>'morning') LIMIT 1", (shift['id'], shift['id'], user['brand_id'], user['branch_ids'])).fetchone():
        raise db.error(403, 'MORNING_SHIFT_MIXED_DATA', 'تحتاج المناوبة مراجعة المالك لوجود طلب خارج نطاق الصباح.')
    if not shift or not same_scope(user, shift): fail(404, 'الشفت غير موجود')
    if user['role']=='owner': return
    if not set(shift['branch_ids'])<=set(user.get('branch_ids',[])):fail(403,'تم سحب صلاحية أحد فروع الشفت')
    if capability and delegated_shift(user, capability) and shift['branch_ids']: return
    if shift['user_id']==user['id'] and not manager: return
    if user['role']=='delivery_manager':
        parent = c.execute('SELECT user_id FROM shifts WHERE id=%s', (shift.get('manager_shift_id'),)).fetchone()
        if parent and parent['user_id']==user['id']: return
    fail(403, 'الشفت خارج نطاق صلاحيتك')

def lock_shift(c, sid):
    return c.execute('SELECT * FROM shifts WHERE id=%s FOR UPDATE', (sid,)).fetchone()

def balance(c, s):
    v=c.execute('SELECT coalesce(sum(amount_halalas),0) n FROM custody_events WHERE shift_id=%s',(s['id'],)).fetchone()['n']
    return s['opening_halalas']+int(v)

def available_balance(c,s):
    pending=c.execute("SELECT coalesce(sum(amount_halalas),0) n FROM custody_transfers WHERE from_shift_id=%s AND status='pending'",(s['id'],)).fetchone()['n']
    return balance(c,s)-int(pending)

def unresolved(c, sid):
    return c.execute("SELECT count(*) n FROM driver_tasks WHERE shift_id=%s AND status NOT IN ('delivered','cancelled','transferred')",(sid,)).fetchone()['n']

def morning_handover_coverage(c, shift):
    """Only a real accepted, still-operational successor releases dispatch duty."""
    from . import morning_delivery
    covered=set()
    successors=c.execute("SELECT s.*,h.branch_ids AS handed_branch_ids FROM manager_handovers h "
        "JOIN shifts s ON s.id=h.to_shift_id WHERE h.from_shift_id=%s AND h.status='accepted' "
        "AND s.kind='manager' AND s.status='open' AND s.brand_id=%s "
        "AND s.delivery_window_snapshot->>'period'='morning' FOR SHARE OF s",
        (shift['id'],shift['brand_id'])).fetchall()
    for successor in successors:
        principal=c.execute('SELECT * FROM users WHERE id=%s FOR SHARE',(successor['user_id'],)).fetchone()
        branches=set(shift['branch_ids']) & set(successor['handed_branch_ids']) & set(successor['branch_ids'])
        for bid in branches:
            try:
                morning_delivery.authorize_order(c,principal,{'brand_id':shift['brand_id'],'branch_id':bid,
                                                              'snapshot':{'delivery_period':'morning'}})
            except HTTPException as error:
                if error.status_code!=403:raise
                continue
            covered.add(bid)
    return covered

def guard_accepted_awaiting_dispatch(c, shift):
    """A separately accepted order cannot be abandoned by closing its manager."""
    if shift['kind'] != 'manager':
        return
    if (shift.get('delivery_window_snapshot') or {}).get('period') == 'morning':
        remaining_branches=list(set(shift['branch_ids'])-morning_handover_coverage(c,shift))
        # Morning preparation acceptance belongs to the branch, not a manager
        # event. Even a newly arrived request must retain an open dispatcher.
        # The manager row is locked by the caller; admission holds that row FOR
        # SHARE until the order commits, so closure cannot race past a new order.
        morning_pending = c.execute("SELECT 1 FROM orders o WHERE o.brand_id=%s "
            "AND o.branch_id=ANY(%s) AND o.service='delivery' "
            "AND o.snapshot->>'delivery_period'='morning' AND o.completed_at IS NULL "
            "AND o.preparation_status NOT IN ('cancelled','rejected','completed') "
            "AND o.delivery_status NOT IN ('cancelled','delivered') "
            "AND NOT EXISTS(SELECT 1 FROM driver_tasks t WHERE t.order_id=o.id "
            "AND t.status NOT IN ('cancelled','reassigned','transferred')) LIMIT 1",
            (shift['brand_id'], remaining_branches)).fetchone()
        if morning_pending:
            raise db.error(409, 'MORNING_ORDERS_AWAITING_DISPATCH',
                           'توجد طلبات صباحية بانتظار المعالجة أو الإسناد. عالجها قبل تقفيل المناوبة.')
    pending = c.execute("SELECT 1 FROM order_events e JOIN orders o ON o.id=e.order_id "
        "WHERE e.event_type='preparation.accepted' AND e.details->>'manager_shift_id'=%s "
        "AND e.details->>'dispatch_pending'='true' AND o.brand_id=%s "
        "AND o.completed_at IS NULL AND o.preparation_status NOT IN ('cancelled','rejected','completed') "
        "AND o.delivery_status NOT IN ('cancelled','delivered') "
        "AND NOT EXISTS(SELECT 1 FROM driver_tasks t WHERE t.order_id=o.id "
        "AND t.status NOT IN ('cancelled','reassigned','transferred')) LIMIT 1",
        (shift['id'],shift['brand_id'])).fetchone()
    if pending:
        raise db.error(409, 'ACCEPTED_ORDERS_AWAITING_DISPATCH',
                       'لديك طلبات مقبولة بانتظار إسناد سائق. عالجها قبل تقفيل المناوبة.')

def valid_cash_amount(amount):
    if not -MAX_HALALAS-1<=amount<=MAX_HALALAS:fail(422,'المبلغ النقدي يتجاوز النطاق المدعوم')

def event(c,sid,amount,kind,ref,actor,reason):
    valid_cash_amount(amount)
    c.execute('INSERT INTO custody_events(id,shift_id,amount_halalas,kind,reference_id,actor_id,reason) VALUES(%s,%s,%s,%s,%s,%s,%s)',(uid(),sid,amount,kind,ref,actor,reason))

def mutate(c,user,request,operation,body,perform):
    # One small-site financial mutation lane per brand keeps multi-row handovers
    # and task/collection changes in a consistent order across endpoints.
    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('operations:'+user['brand_id'],))
    key=request.headers.get('Idempotency-Key','')
    replay=c.execute('SELECT response FROM idempotency_keys WHERE user_id=%s AND operation=%s AND key=%s',(user['id'],operation,key)).fetchone()
    if replay:authorize_replay(c,user,operation,body,replay['response'])
    return blind_closure.driver_view(user,db.idempotent(c,user['id'],key,operation,safe(body),lambda: safe(perform())),shift_money=operation.startswith('shift.') and not operation.startswith('shift.draft:'))

def history_result_access(c,user,oid):
    order=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
    if user['role']=='driver':
        if not order or order['brand_id']!=user['brand_id'] or order['branch_id'] not in user.get('branch_ids',[]) or not c.execute('SELECT 1 FROM driver_tasks WHERE order_id=%s AND driver_id=%s',(oid,user['id'])).fetchone():fail(404,'السجل خارج الصلاحية الحالية')
    else:order_access(c,user,order)

def authorize_replay(c,user,operation,body,result):
    # Replay protection never extends withdrawn authorization. Validate current
    # resources, not the scope frozen in a formerly successful response.
    if operation.startswith('ticket.'):
        oid=body.get('order_id')
        if not oid:
            ticket=c.execute('SELECT order_id FROM support_tickets WHERE id=%s',(result.get('id'),)).fetchone()
            oid=ticket['order_id'] if ticket else None
        if not oid:fail(404,'التذكرة غير متاحة')
        history_result_access(c,user,oid)
    sid=result.get('shift_id') or (result.get('shift') or {}).get('id')
    if operation.startswith('cash.event:'):sid=operation.split(':',1)[1]
    if sid:
        shift=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone()
        capability = ('shifts_open' if operation=='shift.open' else
                      'shifts_approve' if operation.startswith(('shift.approve:', 'shift.return:')) else
                      'shifts_handover' if operation.startswith('manager.handover:') else
                      close_capability(shift) if operation.startswith(('shift.closing:', 'shift.submit:')) else None)
        shift_access(c,user,shift,capability=capability)
    task=(result.get('task') or {}).get('id') or result.get('task_id')
    if task:
        t=c.execute('SELECT * FROM driver_tasks WHERE id=%s',(task,)).fetchone()
        if not t:fail(404,'المهمة غير متاحة')
        history_result_access(c,user,t['order_id'])
    for field,table in [('collection_id','collections'),('refund_id','refunds')]:
        if result.get(field):
            row=c.execute('SELECT order_id FROM '+table+' WHERE id=%s',(result[field],)).fetchone()
            if not row:fail(404,'السجل غير متاح')
            history_result_access(c,user,row['order_id'])
            if user['role']=='driver':
                order_access(c,user,c.execute('SELECT * FROM orders WHERE id=%s',(row['order_id'],)).fetchone())
    cid=result.get('conversation_id') or (result.get('conversation') or {}).get('id')
    if operation.startswith('chat.') and ':' in operation:cid=operation.split(':',1)[1]
    if cid:
        if operation.startswith('chat.transfer:'):
            completed=c.execute('SELECT t.actor_id,v.brand_id,v.branch_id FROM conversation_transfers t JOIN conversations v ON v.id=t.conversation_id WHERE t.id=%s AND t.conversation_id=%s',(result.get('transfer_id'),cid)).fetchone()
            if not completed or completed['actor_id']!=user['id'] or completed['brand_id']!=user['brand_id'] or (user['role']!='owner' and completed['branch_id'] not in user.get('branch_ids',[])):fail(404,'التحويل خارج الصلاحية الحالية')
        else:
            from .chat import conversation
            conversation(c,user,cid)
    transfer_id=result.get('transfer_id') or (result.get('transfer') or {}).get('id')
    if transfer_id and operation.startswith('custody.'):
        row=c.execute('SELECT * FROM custody_transfers WHERE id=%s',(transfer_id,)).fetchone()
        if not row:fail(404,'التسليم غير متاح')
        source=c.execute('SELECT * FROM shifts WHERE id=%s',(row['from_shift_id'],)).fetchone()
        if row['recipient_user_id']==user['id']:
            permitted=(row.get('receiving_branch_id') in user.get('branch_ids',[]) if user['role']=='branch_manager' else set(source['branch_ids'])<=set(user.get('branch_ids',[])))
            if not same_scope(user,source) or (user['role']!='owner' and not permitted):fail(403,'تم سحب صلاحية التسليم')
        else:shift_access(c,user,source)
    if result.get('report_id') and not (operation.startswith('shift.approve:') and delegated_shift(user,'shifts_approve')):
        from .reporting import report
        report(c,user,result['report_id'])
    if result.get('handover_id'):
        row=c.execute('SELECT h.*,s.brand_id,s.branch_ids,s.user_id source_user,t.user_id target_user FROM manager_handovers h JOIN shifts s ON s.id=h.from_shift_id JOIN shifts t ON t.id=h.to_shift_id WHERE h.id=%s',(result['handover_id'],)).fetchone()
        if row and operation.startswith('manager.handover:') and delegated_shift(user,'shifts_handover'):
            for key in ('from_shift_id','to_shift_id'):
                shift_access(c,user,c.execute('SELECT * FROM shifts WHERE id=%s',(row[key],)).fetchone(),capability='shifts_handover')
        elif not row or not same_scope(user,row) or (user['role']!='owner' and (user['id'] not in (row['source_user'],row['target_user']) or not set(row['branch_ids'])<=set(user.get('branch_ids',[])))):fail(403,'تسليم المناوبة خارج الصلاحية')

class ShiftOpen(OperationBody):
    delivery_period: Literal['morning','evening']|None=None
    receipt_trial: StrictBool=False
    vehicle_inspection: shift_vehicle.Inspection|None=None
    kind: str
    branch_ids: list[str] = Field(default_factory=list)
    manager_shift_id: str|None=None
    user_id: str|None=None
    opening_halalas: StrictInt=Field(0,ge=0,le=MAX_HALALAS)
    reason: str=''


def delivery_shift_window(c, brand, branches, *, manager_preparation=False, delivery_period=None, principal=None):
    """Gate fresh staffing; existing tasks and closures never expire by clock.

    Unconfigured legacy/internal brands retain their existing staffing flow.
    The approved default configures eligible KPS branches without enabling them.
    """
    from . import catalog, morning_delivery
    now = service_hours.now_riyadh()
    windows = {}
    preparation_date = None
    brand_settings = c.execute('SELECT settings FROM brands WHERE id=%s', (brand,)).fetchone()['settings']
    if brand_settings.get('services', {}).get('delivery', True) is not True:
        fail(409, 'خدمة التوصيل موقوفة من إدارة البراند؛ يمكن إكمال الشفتات القائمة وتقفيلها.')
    morning = delivery_period == 'morning'
    if morning:
        approved = morning_delivery.policy(brand_settings)
        if (not principal or not principal.get('active') or principal.get('brand_id') != brand
                or (principal['role'] != 'owner' and
                    (principal['role'] != 'delivery_manager' or principal['id'] not in approved['manager_user_ids']))):
            fail(403, 'مدير الفترة الصباحية غير معتمد.')
        if not branches or any(not morning_delivery.active(b, brand_settings, now)
                               or b['id'] not in approved['execution_branch_ids'] for b in branches):
            fail(409, 'الفترة الصباحية مغلقة أو الفرع غير معتمد للتنفيذ.')
    for branch in branches:
        settings = branch['settings']
        hours = morning_delivery.HOURS if morning else settings.get('delivery_hours')
        if not hours:
            continue
        if catalog.normalize_city(branch['city']) not in catalog.DELIVERY_CITIES:
            fail(409, 'هذا الفرع غير مؤهل لشفتات التوصيل.')
        if settings.get('active', True) is not True or settings.get('delivery_enabled') is not True:
            fail(409, 'التوصيل موقوف لهذا الفرع؛ يمكن إكمال الشفتات القائمة وتقفيلها.')
        status = (service_hours.manager_shift_status(hours, now) if manager_preparation and not morning
                  else service_hours.hours_status(hours, now))
        if not status['configured']:
            fail(503, 'ساعات التوصيل لهذا الفرع غير صحيحة.')
        if not status['open']:
            if manager_preparation:
                raise db.error(409, 'MANAGER_SHIFT_HOURS',
                               'شفت المدير مغلق الآن. لفترة التوصيل التي تبدأ 16:00، يتاح التجهيز من 15:45 بتوقيت السعودية؛ استقبال العملاء يبدأ 16:00.')
            fail(409, service_hours.closed_delivery_message(hours, now))
        windows[branch['id']] = {'hours': hours, 'window_start': status['current_window_start'],
                                  'window_end': status['current_window_end']}
        if status.get('preparation'):
            preparation_date = datetime.fromisoformat(status['current_window_start']).date()
            windows[branch['id']]['preparation_start'] = status['preparation_start']
    if not windows:
        return None, None
    operational_date = preparation_date or service_hours.delivery_operational_date(now)
    return operational_date, {'timezone': 'Asia/Riyadh', 'operational_date': operational_date.isoformat(),
                              'operational_day_start': '16:00', 'branches': windows,
                              'period': 'morning' if morning else 'evening'}

@router.post('/shifts')
def open_shift(body:ShiftOpen,request:Request,user=Depends(auth.current_user)):
    context = user.get('_morning_manager_context')
    if context and (not context.get('bootstrap') or context.get('shift_id') is not None
            or body.kind != 'manager' or body.user_id not in (None, user['id'])
            or body.manager_shift_id is not None or body.delivery_period != 'morning'
            or body.branch_ids and set(body.branch_ids) != set(user['branch_ids'])
            or body.opening_halalas != 0 or body.receipt_trial or body.vehicle_inspection is not None):
        raise db.error(403, 'MORNING_SHIFT_SCOPE', 'افتح مناوبتك الصباحية فقط دون عهدة أو شفت موظف آخر.')
    shift_roles(user,'shifts_open','owner','delivery_manager','driver')
    if user['role']=='driver' and body.branch_ids and set(body.branch_ids)!=set(user.get('branch_ids',[])):
        fail(403,'فروع السائق يحددها مدير التوصيل؛ لا يمكن تغيير نطاق الشفت')
    with db.tx() as c:
        if context and c.execute("SELECT 1 FROM retained_carryovers WHERE user_id=%s AND to_shift_id IS NULL AND archived_test_at IS NULL LIMIT 1",(user['id'],)).fetchone():
            raise db.error(403,'MORNING_CARRYOVER_NOT_ALLOWED','راجع اعتماد العهدة السابقة قبل فتح مناوبة الصباح المحدودة.')
        def perform():
            target=user if not body.user_id or body.user_id==user['id'] else c.execute('SELECT * FROM users WHERE id=%s',(body.user_id,)).fetchone()
            if not target or not target['active'] or not same_scope(user,target): fail(404,'الموظف غير موجود')
            if user['role']=='admin' and target['role'] not in ('driver','delivery_manager'):
                fail(403,'افتح الشفت لموظف تشغيل مخول فقط')
            if target['id']!=user['id']:
                shift_roles(user,'shifts_open','owner','delivery_manager')
                if user['role']=='delivery_manager' and (target['role']!='driver' or body.kind!='driver'):fail(403,'المدير يفتح شفتات السائقين التابعين فقط')
            if body.kind not in ('driver','manager'): fail(422,'نوع الشفت غير صالح')
            if target['role'] != ('driver' if body.kind=='driver' else 'delivery_manager') and target['role']!='owner': fail(403,'الدور غير مطابق للشفت')
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('shift:'+target['id'],))
            if c.execute("SELECT 1 FROM shifts WHERE user_id=%s AND status<>'closed'",(target['id'],)).fetchone():
                raise db.error(409,'SHIFT_ALREADY_OPEN','لديك شفت مفتوح بالفعل. انتقل إلى مهام التوصيل أو الشفتات بدل فتح شفت جديد.')
            brand=target.get('brand_id') or user.get('brand_id')
            bids=body.branch_ids or target.get('branch_ids',[])
            manager = None
            if body.kind == 'driver' and body.manager_shift_id:
                manager = lock_shift(c, body.manager_shift_id)
                if (manager and manager['kind'] == 'manager' and manager['status'] == 'open'
                        and manager['brand_id'] == brand
                        and (manager.get('delivery_window_snapshot') or {}).get('period') == 'morning'
                        and not body.branch_ids):
                    # Morning operates only a subset of the normal driver pool.
                    # Derive it server-side from two already-authorized scopes.
                    bids = [bid for bid in target.get('branch_ids', []) if bid in manager['branch_ids']]
            if not brand or not bids: fail(422,'يلزم براند وفروع فعلية للشفت')
            branch_rows=[]
            for bid in bids:
                b=c.execute('SELECT * FROM branches WHERE id=%s AND brand_id=%s',(bid,brand)).fetchone()
                if not b or (target['role']!='owner' and bid not in target.get('branch_ids',[])) or (user['role']!='owner' and bid not in user.get('branch_ids',[])): fail(403,'فرع غير مخول')
                branch_rows.append(b)
            if body.opening_halalas:
                roles(user,'owner')
                if not body.reason.strip(): fail(422,'العهدة الافتتاحية تحتاج سبب واعتماد المالك')
            if body.kind=='driver':
                if not body.manager_shift_id:
                    raise db.error(409,'MANAGER_SHIFT_REQUIRED','اختر شفت مدير التوصيل المفتوح. إذا لم يظهر، اطلب من المدير فتح شفت يغطي جميع فروعك المعتمدة.')
                manager=manager or lock_shift(c,body.manager_shift_id)
                if not manager or manager['kind']!='manager' or manager['status']!='open' or manager['brand_id']!=brand or not set(bids)<=set(manager['branch_ids']):
                    raise db.error(409,'MANAGER_SHIFT_SCOPE','شفت المدير المحدد مغلق أو لا يغطي جميع فروعك. مدير التوصيل هو من يعدّل نطاق التغطية.')
                manager_user=c.execute('SELECT * FROM users WHERE id=%s',(manager['user_id'],)).fetchone()
                if not manager_user or not manager_user['active'] or manager_user['role'] not in ('owner','delivery_manager') or (manager_user['role']!='owner' and not set(bids)<=set(manager_user['branch_ids'])):fail(409,'صلاحية مدير المناوبة غير سارية')
                if user['role']=='delivery_manager' and manager['user_id']!=user['id']: fail(403,'مدير الشفت غير مطابق')
            elif body.manager_shift_id: fail(422,'شفت المدير لا يتبع شفت آخر')
            period = body.delivery_period
            if body.kind == 'driver':
                inherited = (manager.get('delivery_window_snapshot') or {}).get('period', 'evening')
                if period and period != inherited:
                    fail(409, 'فترة السائق يجب أن تطابق مناوبة مديره.')
                period = inherited
            if body.receipt_trial:
                from . import driver_receipt_trial
                driver_receipt_trial.authorize_open(c, user, target, body, manager, branch_rows)
            operational_date, delivery_window = delivery_shift_window(
                c, brand, branch_rows, manager_preparation=body.kind == 'manager',
                delivery_period=period, principal=manager_user if body.kind == 'driver' else target)
            sid=uid()
            carryovers=c.execute('SELECT * FROM retained_carryovers WHERE user_id=%s AND to_shift_id IS NULL AND archived_test_at IS NULL FOR UPDATE',(target['id'],)).fetchall()
            if context and carryovers:
                raise db.error(403,'MORNING_CARRYOVER_NOT_ALLOWED','هذه المناوبة لا تنقل عهدًا مالية سابقة.')
            carried=sum(x['amount_halalas'] for x in carryovers)
            permanent_float=None
            if body.kind=='driver':
                from . import driver_float
                carried,permanent_float=driver_float.opening(c,target,carried)
                if permanent_float and body.opening_halalas:fail(422,'العهدة الدائمة يحددها اعتماد المحاسبة فقط')
            valid_cash_amount(body.opening_halalas+carried)
            c.execute('INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id,opening_halalas,delivery_operational_date,delivery_window_snapshot) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',(sid,body.kind,target['id'],brand,db.json(bids),body.manager_shift_id,body.opening_halalas+carried,operational_date,db.json(delivery_window) if delivery_window else None))
            if body.kind=='driver':
                c.execute('UPDATE shifts SET permanent_float=%s WHERE id=%s',(db.json(permanent_float) if permanent_float else None,sid))
                from .fuel_exceptions import consume
                if body.vehicle_inspection and body.vehicle_inspection.fuel_exception_id:
                    consume(c,user,c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone(),body.vehicle_inspection,'start',target.get('vehicle') or {})
                if body.receipt_trial:
                    vehicle_log=driver_receipt_trial.opening_log(c,user,body.reason)
                else:
                    vehicle_log=shift_vehicle.start(c,user,target,body.vehicle_inspection)
                    vehicle_log['awaiting_refuel']=body.vehicle_inspection.fuel_percent<100 and not body.vehicle_inspection.fuel_exception_id
                c.execute('UPDATE shifts SET vehicle_log=%s WHERE id=%s',(db.json(vehicle_log),sid))
            for carry in carryovers:c.execute('UPDATE retained_carryovers SET to_shift_id=%s,carried_at=now() WHERE id=%s',(sid,carry['id']))
            db.audit(c,user['id'],'shift.open','shift',sid,{**body.model_dump(),'carried_halalas':carried,'previous_carry_halalas':sum(x['amount_halalas'] for x in carryovers),'permanent_float':permanent_float,'carryover_ids':[x['id'] for x in carryovers], 'delivery_window':delivery_window})
            return {'shift':c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone()}
        return mutate(c,user,request,'shift.open',body.model_dump(),perform)

@router.get('/shifts')
@router.get('/shifts/current')
def shifts(user=Depends(auth.current_user), order_id: str | None = None, task_id: str | None = None,
           owner_view: Literal['current','review','history'] | None = None,
           search: str = '', start: date | None = None, end: date | None = None,
           offset: int = Query(0, ge=0)):
    shift_roles(user,'shifts_view','owner','delivery_manager','driver','delivery_staff')
    if owner_view:
        shift_roles(user,'shifts_view','owner')
        if order_id or task_id:fail(422,'لا يمكن الجمع بين عرض الشفتات واختيار سائق')
        if start and end and start>end:fail(422,'تاريخ البداية بعد النهاية')
    with db.tx() as c:
        page_meta={}
        context = user.get('_morning_manager_context')
        if context:
            if order_id or task_id:
                prior_task = None
                if task_id:
                    prior_task = c.execute('SELECT * FROM driver_tasks WHERE id=%s', (task_id,)).fetchone()
                    if not prior_task:fail(404,'المهمة غير موجودة')
                    shift_access(c,user,lock_shift(c,prior_task['shift_id']))
                    order_id = prior_task['order_id']
                order = c.execute('SELECT * FROM orders WHERE id=%s', (order_id,)).fetchone()
                order_access(c,user,order)
                rows = c.execute("SELECT s.id,s.shift_no,s.kind,s.user_id,s.brand_id,s.branch_ids,s.status,s.opened_at,s.delivery_window_snapshot,u.display_name "
                                 "FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.brand_id=%s AND s.manager_shift_id=%s "
                                 "AND s.kind='driver' AND s.status='open' AND s.delivery_window_snapshot->>'period'='morning' "
                                 "AND s.branch_ids <@ %s::jsonb ORDER BY s.opened_at,s.id",
                                 (user['brand_id'],context['shift_id'],db.json(user['branch_ids']))).fetchall()
                eligible=[]
                for row in rows:
                    if prior_task and row['id']==prior_task['shift_id']:continue
                    candidate=lock_shift(c,row['id'])
                    try:
                        shift_access(c,user,candidate)
                        eligible_driver_shift(c,candidate,order)
                    except HTTPException as error:
                        if error.status_code not in (403,404,409):raise
                        continue
                    row['active_tasks']=c.execute("SELECT count(*) n FROM driver_tasks WHERE shift_id=%s AND status NOT IN ('delivered','cancelled','transferred','reassigned')",(row['id'],)).fetchone()['n']
                    row['assignment_eligible']=True
                    eligible.append(row)
                return safe({'shifts':eligible})
            rows=c.execute("SELECT s.id,s.shift_no,s.kind,s.user_id,s.brand_id,s.branch_ids,s.status,s.opened_at,s.delivery_window_snapshot,u.display_name "
                           "FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.brand_id=%s AND s.id=%s AND s.user_id=%s "
                           "AND s.delivery_window_snapshot->>'period'='morning' AND s.branch_ids <@ %s::jsonb",
                           (user['brand_id'],context['shift_id'],user['id'],db.json(user['branch_ids']))).fetchall()
            # This read exists only for the manual-order form. No receivers,
            # driver roster, balances or closure contents belong to this role.
            return safe({'shifts': rows})
        elif owner_view:
            # Filter before pagination so old unclosed shifts cannot disappear
            # behind hundreds of newer historical records. Never change state.
            statuses={'current':['open'],'review':['closing','submitted'],'history':['closed']}[owner_view]
            where=['s.brand_id=%s','s.status=ANY(%s)'];params=[user['brand_id'],statuses]
            if user['role']!='owner':
                where.extend(["s.branch_ids <> '[]'::jsonb", 's.branch_ids <@ %s::jsonb'])
                params.append(db.json(user.get('branch_ids',[])))
            if search.strip():
                where.append('(strpos(lower(u.display_name),lower(%s))>0 OR strpos(lower(s.id),lower(%s))>0)')
                params.extend([search.strip(),search.strip()])
            day="COALESCE(s.delivery_operational_date, ((s.opened_at AT TIME ZONE 'Asia/Riyadh') - interval '16 hours')::date)"
            if start:where.append(day+'>=%s');params.append(start)
            if end:where.append(day+'<=%s');params.append(end)
            source=' FROM shifts s JOIN users u ON u.id=s.user_id WHERE '+' AND '.join(where)
            total=c.execute('SELECT count(*) AS n'+source,params).fetchone()['n']
            rows=c.execute('SELECT s.*,u.display_name'+source+' ORDER BY s.opened_at DESC,s.id LIMIT 50 OFFSET %s',params+[offset]).fetchall()
            page_meta={'total':total,'offset':offset,'next_offset':offset+50 if offset+50<total else None}
        elif user['role']=='owner': rows=c.execute('SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.brand_id=%s ORDER BY opened_at DESC LIMIT 200',(user['brand_id'],)).fetchall()
        elif user['role']=='admin': rows=c.execute("SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.brand_id=%s AND s.branch_ids <> '[]'::jsonb AND s.branch_ids <@ %s::jsonb ORDER BY opened_at DESC LIMIT 200",(user['brand_id'],db.json(user.get('branch_ids',[])))).fetchall()
        elif user['role']=='delivery_manager': rows=c.execute('SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.user_id=%s OR s.manager_shift_id IN (SELECT id FROM shifts WHERE user_id=%s) ORDER BY opened_at DESC LIMIT 200',(user['id'],user['id'])).fetchall()
        elif user['role']=='delivery_staff': rows=c.execute("SELECT s.id,s.shift_no,s.kind,s.user_id,s.branch_ids,s.status,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.brand_id=%s AND s.status='open' AND s.branch_ids ?| %s ORDER BY s.opened_at DESC LIMIT 100",(user['brand_id'],user.get('branch_ids',[]))).fetchall()
        else: rows=c.execute('SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.user_id=%s ORDER BY opened_at DESC LIMIT 100',(user['id'],)).fetchall()
        rows=[r for r in rows if r.get('brand_id',user['brand_id'])==user['brand_id'] and (user['role']=='owner' or set(r['branch_ids'])<=set(user.get('branch_ids',[])))]
        if user.get('_morning_manager_context'):
            from .morning_manager_context import shift_allowed
            rows = [r for r in rows if shift_allowed(user, r)]
            for row in rows:
                shift_access(c, user, row)
        if order_id or task_id:
            roles(user,'owner','delivery_manager','delivery_staff')
            task=None
            if task_id:
                roles(user,'owner','delivery_manager')
                task=c.execute('SELECT * FROM driver_tasks WHERE id=%s',(task_id,)).fetchone()
                if not task:fail(404,'المهمة غير موجودة')
                shift_access(c,user,lock_shift(c,task['shift_id']))
                order_id=task['order_id']
            order=c.execute('SELECT * FROM orders WHERE id=%s',(order_id,)).fetchone()
            order_access(c,user,order)
            eligible=[]
            for row in rows:
                if row['kind']!='driver' or task and row['id']==task['shift_id']:continue
                candidate=lock_shift(c,row['id'])
                try:
                    manager=eligible_driver_shift(c,candidate,order)
                except HTTPException as error:
                    if error.status_code not in (403,404,409):raise
                    continue
                if user['role']=='delivery_manager' and manager['user_id']!=user['id']:continue
                eligible.append(row)
            rows=eligible
        shift_order_counts.attach(c,rows,user)
        return safe(blind_closure.driver_view(user,{'shifts':rows,**people_data(c,user),**page_meta},shift_money=True))

def driver_cash_policy(c,brand_id):
    row=c.execute('SELECT settings FROM brands WHERE id=%s',(brand_id,)).fetchone()
    return (row['settings'] or {}).get('driver_cash_receipt_policy','delivery_manager')

def people_data(c,user):
    users=c.execute("SELECT id,display_name,role,brand_id,branch_ids FROM users WHERE active AND brand_id=%s AND role IN ('owner','delivery_manager','driver','branch_manager') ORDER BY display_name",(user['brand_id'],)).fetchall()
    users=[{'id':u['id'],'display_name':u['display_name'],'role':u['role'],'branch_ids':u['branch_ids']} for u in users if u['role']=='owner' or user['role']=='owner' or set(u['branch_ids'])&set(user.get('branch_ids',[]))]
    managers=c.execute("SELECT s.id,s.shift_no,s.user_id,s.branch_ids,s.status,s.delivery_window_snapshot,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.kind='manager' AND s.status IN ('open','closing') AND u.active AND s.brand_id=%s ORDER BY s.opened_at DESC",(user['brand_id'],)).fetchall()
    managers=[s for s in managers if user['role']=='owner' or set(s['branch_ids'])&set(user.get('branch_ids',[]))]
    if user['role']=='driver':
        managers=[s for s in managers if set(user.get('branch_ids',[]))<=set(s['branch_ids'])
                  or (s.get('delivery_window_snapshot') or {}).get('period')=='morning'
                  and bool(set(user.get('branch_ids',[]))&set(s['branch_ids']))]
    context = user.get('_morning_manager_context')
    if context:
        from .morning_manager_context import shift_allowed
        child_ids = {r['user_id'] for r in c.execute("SELECT user_id FROM shifts WHERE manager_shift_id=%s AND brand_id=%s AND delivery_window_snapshot->>'period'='morning'", (context['shift_id'], user['brand_id'])).fetchall()}
        users = [dict(u, branch_ids=[b for b in u['branch_ids'] if b in user['branch_ids']])
                 for u in users if u['id'] == user['id'] or u['role'] == 'driver' and (u['id'] in child_ids or set(u['branch_ids']) & set(user['branch_ids']))]
        managers = [s for s in managers if s['id'] == context['shift_id']]
    return {'authorized_receivers':users,'available_manager_shifts':[s for s in managers if s['status']=='open'],'closure_manager_shifts':managers,'driver_cash_receipt_policy':driver_cash_policy(c,user['brand_id'])}

@router.get('/operations/people')
def people(user=Depends(auth.current_user)):
    if not any(delegated_shift(user, cap) for cap in ('shifts_view','shifts_open','shifts_close','shifts_delegate_close','shifts_handover')):
        roles(user,'owner','delivery_manager','delivery_staff','driver')
    with db.tx() as c:
        data=people_data(c,user);return safe({'users':data['authorized_receivers'],**data})

def day_start(config):
    value=config.get('operational_day_start')
    if value is None:return 0,False
    try:
        parts=value.split(':')
        h,m=int(parts[0]),int(parts[1])
        if len(parts)!=2 or not 0<=h<=23 or not 0<=m<=59:raise ValueError()
        return h*60+m,True
    except (AttributeError,ValueError,TypeError,IndexError):fail(503,'بداية اليوم التشغيلي غير صحيحة')

class ExternalEnbishan(OperationBody):
    order_count: StrictInt = Field(ge=0, le=100000)
    cash_halalas: StrictInt = Field(ge=0, le=100000000000)
    card_halalas: StrictInt = Field(ge=0, le=100000000000)
    revision: StrictInt = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)


def external_enbishan_eligible(c, s):
    from .catalog import normalize_city
    if s['brand_id'] != 'kps' or s['kind'] != 'driver' or not s['branch_ids']:
        return False
    rows=c.execute('SELECT city FROM branches WHERE brand_id=%s AND id=ANY(%s)',(s['brand_id'],s['branch_ids'])).fetchall()
    return len(rows)==len(s['branch_ids']) and {normalize_city(r['city']) for r in rows}=={'حفر الباطن'}


@router.post('/shifts/{sid}/external-enbishan')
def save_external_enbishan(sid:str, body:ExternalEnbishan, request:Request, user=Depends(auth.current_user)):
    roles(user,'delivery_manager')
    with db.tx() as c:
        # Revalidate current scope even on an idempotent replay.
        shift_access(c,user,c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone(),manager=True)
        def perform():
            s=lock_shift(c,sid);shift_access(c,user,s,manager=True)
            if s['status'] not in ('open','closing'):fail(409,'أعد التقفيلة للتصحيح قبل تعديل طلبات عنبيشن')
            if not external_enbishan_eligible(c,s):fail(403,'طلبات عنبيشن المؤقتة لسواقين حفر الباطن فقط')
            before=s.get('external_enbishan') or {}
            if body.revision!=before.get('revision',0):fail(409,'تغير الملخص؛ حدّث الصفحة قبل الحفظ')
            total=body.cash_halalas+body.card_halalas
            if (body.order_count==0)!=(total==0):fail(422,'عدد الطلبات وقيمتها يجب أن يكونا متوافقين')
            delta=body.cash_halalas-before.get('cash_halalas',0)
            if available_balance(c,s)+delta<0:fail(409,'لا يمكن تخفيض النقد إلى أقل من المبالغ المسلمة أو المحجوزة')
            valid_cash_amount(balance(c,s)+delta)
            value={**body.model_dump(),'revision':body.revision+1,'total_halalas':total,'source':'sapaad',
                   'recorded_by':user['id'],'recorded_name':user.get('display_name') or user['id'],
                   'recorded_at':c.execute('SELECT now() n').fetchone()['n'].isoformat()}
            c.execute('UPDATE shifts SET external_enbishan=%s WHERE id=%s',(db.json(value),sid))
            if delta:event(c,sid,delta,'direct_receipt','external-enbishan:'+sid+':'+str(value['revision']),user['id'],'عنبيشن — ساباد / '+body.reason)
            db.audit(c,user['id'],'shift.external_enbishan','shift',sid,{'before':before,'after':value})
            return {'shift_id':sid,'external_enbishan':value}
        return mutate(c,user,request,'shift.external-enbishan:'+sid,body.model_dump(),perform)


@router.get('/shifts/{sid}')
def shift_detail(sid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone(); shift_access(c,user,s,capability='shifts_view')
        task_rows=c.execute('SELECT t.*,o.order_no,o.snapshot,o.total_halalas,o.preparation_status,o.payment_status FROM driver_tasks t JOIN orders o ON o.id=t.order_id WHERE shift_id=%s ORDER BY t.created_at',(sid,)).fetchall()
        if user['role']=='driver':
            task_rows=[task for task in task_rows if task['status'] not in TERMINAL]
        return safe(blind_closure.driver_view(user,{'shift':s,'external_enbishan_eligible':not user.get('_morning_manager_context') and external_enbishan_eligible(c,s),'vehicle_summary':shift_vehicle.summary(s.get('vehicle_log')),'expected_halalas':balance(c,s),'tasks':task_rows, 'collections':c.execute('SELECT * FROM collections WHERE shift_id=%s',(sid,)).fetchall(),'refunds':c.execute('SELECT * FROM refunds WHERE shift_id=%s',(sid,)).fetchall(),'custody_events':c.execute('SELECT * FROM custody_events WHERE shift_id=%s ORDER BY created_at',(sid,)).fetchall(),'transfers':c.execute('SELECT * FROM custody_transfers WHERE from_shift_id=%s OR to_shift_id=%s',(sid,sid)).fetchall(),'driver_shifts':c.execute('SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.manager_shift_id=%s ORDER BY s.opened_at',(sid,)).fetchall()},shift_money=True))

@router.post('/shifts/{sid}/refuel')
def refuel_shift(sid:str,body:shift_vehicle.Refuel,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        def perform():
            s=lock_shift(c,sid);shift_access(c,user,s)
            if s['kind']!='driver' or s['status'] not in ('open','closing'):fail(409,'تسجيل الوقود متاح لشفت سائق مفتوح أو تحت التقفيلة فقط')
            if user['role'] not in ('driver','owner','delivery_manager'):fail(403,'الصلاحية غير كافية')
            log=dict(s.get('vehicle_log') or {})
            floor=shift_vehicle.odometer_floor(log)
            if body.odometer_km<floor:fail(422,'قراءة العداد أقل من آخر قراءة مسجلة')
            if body.receipt_id:
                from .driver_evidence import validate
                validate(c,body.receipt_id,user,sid)
            entry={**shift_vehicle.stamp(c,user,body),'id':uid()}
            log['refuels']=[*log.get('refuels',[]),entry]
            if body.confirm_full and log.get('awaiting_refuel'):
                if s['status']!='open':raise db.error(409,'SHIFT_NOT_OPEN','الشفت ليس مفتوحًا.')
                if not body.receipt_id:raise db.error(422,'REFUEL_RECEIPT_REQUIRED','أرفق فاتورة التعبئة لتأكيد امتلاء الخزان.')
                log['fuel_readiness']={**entry,'fuel_percent':100}
                log['awaiting_refuel']=False
                db.audit(c,user['id'],'fuel.ready','shift',sid,log['fuel_readiness'])
            c.execute('UPDATE shifts SET vehicle_log=%s WHERE id=%s',(db.json(log),sid))
            db.audit(c,user['id'],'shift.refuel','shift',sid,entry)
            return {'shift_id':sid,'refuel':entry,'vehicle_summary':shift_vehicle.summary(log)}
        return mutate(c,user,request,'shift.refuel:'+sid,body.model_dump(mode='json'),perform)

@router.post('/shifts/{sid}/closing')
def request_close(sid:str,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        def perform():
            s=lock_shift(c,sid); shift_access(c,user,s,capability=close_capability(s))
            guard_accepted_awaiting_dispatch(c,s)
            from .driver_work_hours import guard_shift_completion
            guard_shift_completion(c,s)
            from .driver_breaks import guard as break_guard
            break_guard(c,sid)
            from .driver_closure import guard as closure_guard
            closure_guard(c,user,s)
            if s['status']=='open': c.execute("UPDATE shifts SET status='closing' WHERE id=%s",(sid,))
            elif s['status']!='closing': fail(409,'الشفت ليس مفتوحا')
            db.audit(c,user['id'],'shift.closing','shift',sid)
            return {'shift_id':sid,'status':'closing','unresolved_tasks':unresolved(c,sid),'expected_halalas':balance(c,s)}
        return mutate(c,user,request,'shift.closing:'+sid,{},perform)

class ClosureHandover(OperationBody):
    recipient_user_id: str
    to_shift_id: str|None=None
    receiving_branch_id: str|None=None
    reference: str=Field(min_length=1,max_length=200)

class ClosureSubmit(OperationBody):
    terminal_total_halalas: StrictInt|None=Field(None,ge=0,le=MAX_HALALAS)
    handover: ClosureHandover|None=None
    exception_reason: str=Field(default="",max_length=2000)
    vehicle_inspection: shift_vehicle.Inspection|None=None
    counted_halalas: StrictInt=Field(ge=0,le=MAX_HALALAS)
    handover_halalas: StrictInt=Field(ge=0,le=MAX_HALALAS)
    retained_halalas: StrictInt=Field(0,ge=0,le=MAX_HALALAS)

def submit_close_in_tx(c,user,sid,body):
    s=lock_shift(c,sid); shift_access(c,user,s,capability=close_capability(s))
    guard_accepted_awaiting_dispatch(c,s)
    from .driver_work_hours import guard_shift_completion
    guard_shift_completion(c,s)
    if s['status']!='closing': fail(409,'اطلب التقفيلة أولا')
    if unresolved(c,sid): fail(409,'توجد مهام معلقة')
    if c.execute("SELECT 1 FROM manager_handovers WHERE (from_shift_id=%s OR to_shift_id=%s) AND status='pending'",(sid,sid)).fetchone():fail(409,'تسليم مناوبة غير مقبول')
    if s['kind']=='manager' and c.execute("SELECT 1 FROM shifts WHERE manager_shift_id=%s AND status<>'closed'",(sid,)).fetchone(): fail(409,'توجد شفتات سائقين غير مقفلة، تشمل شفتات بلا طلبات')
    if c.execute("SELECT 1 FROM custody_transfers WHERE (from_shift_id=%s OR to_shift_id=%s) AND status='pending'",(sid,sid)).fetchone(): fail(409,'توجد تسليمات غير مؤكدة')
    if body.counted_halalas!=body.handover_halalas+body.retained_halalas: fail(422,'المعدود يجب أن يساوي التسليم مع المحتفظ به')
    delegated=s['kind']=='driver' and s['user_id']!=user['id']
    if delegated:
        shift_roles(user,'shifts_delegate_close','owner','delivery_manager')
        if not body.exception_reason.strip():fail(422,'اكتب سبب إقفال الشفت نيابة عن السائق؛ سيظهر للمالك')
    elif user['role']=='admin':
        if not body.exception_reason.strip():fail(422,'سجل سبب إرسال التقفيلة بالنيابة')
    elif body.exception_reason.strip():fail(403,'الإقفال الاستثنائي لمدير التوصيل فقط')
    if s['kind']=='driver':
        if body.terminal_total_halalas is None:fail(422,'أدخل مبلغ موازنة جهاز الشبكة الفعلي، أو صفر إذا لم توجد عمليات')
        from .driver_float import split
        if s.get('permanent_float') or user['role']=='driver':
            handover,retained=split(s,body.counted_halalas)
            if (body.handover_halalas,body.retained_halalas)!=(handover,retained):fail(422,'مبلغ التسليم يساوي الكاش المعدود بعد استبعاد العهدة الثابتة؛ لا يمكن تغيير العهدة')
        if user['role']=='driver' and body.handover is None:fail(422,'سجل تسليم النقد لمدير التوصيل مع التقفيلة')
        shift_vehicle.finish(c,user,s,body.vehicle_inspection)
        if delegated:
            c.execute("UPDATE shifts SET vehicle_log=jsonb_set(vehicle_log, '{end_exception}', %s) WHERE id=%s",(db.json({'reason':body.exception_reason.strip(),'recorded_by':user['id'],'recorded_at':c.execute('SELECT now() n').fetchone()['n'].isoformat()}),sid))
    values=body.model_dump(exclude={'vehicle_inspection','handover','exception_reason'});values['expected_halalas']=balance(c,s); values['difference_halalas']=body.counted_halalas-values['expected_halalas']
    if s['kind']=='driver':values.update(blind_closure.card_reconciliation(c,sid,body.terminal_total_halalas,(s.get('external_enbishan') or {}).get('card_halalas',0)))
    if s['kind']=='driver':values['payment_terminal']=c.execute('SELECT payment_terminal FROM users WHERE id=%s',(s['user_id'],)).fetchone()['payment_terminal']
    if delegated:values['delegated_closure']={'by':user['id'],'reason':body.exception_reason.strip()}
    c.execute("UPDATE shifts SET status='submitted',submission=%s,submitted_at=now() WHERE id=%s",(db.json(values),sid))
    if body.handover:
        create_custody(c,user,Custody(from_shift_id=sid,amount_halalas=body.handover_halalas,closure=True,**body.handover.model_dump()),capability=close_capability(s))
    db.audit(c,user['id'],'shift.submit','shift',sid,values)
    return {'shift_id':sid,'status':'submitted','submission':values}

@router.post('/shifts/{sid}/submit')
def submit_close(sid:str,body:ClosureSubmit,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        def perform():return submit_close_in_tx(c,user,sid,body)
        return mutate(c,user,request,'shift.submit:'+sid,body.model_dump(),perform)

class Review(OperationBody):
    exception_reason: str|None=Field(None,max_length=2000)
    reason: str=Field('',max_length=2000)

@router.post('/shifts/{sid}/return')
def return_close(sid:str,body:Review,request:Request,user=Depends(auth.current_user)):
    shift_roles(user,'shifts_approve','owner','delivery_manager')
    with db.tx() as c:
        def perform():
            s=lock_shift(c,sid);shift_access(c,user,s,manager=True,capability='shifts_approve')
            if s['status']!='submitted' or not body.reason.strip(): fail(422,'تحتاج تقفيلة مرسلة وسبب تصحيح')
            if c.execute("SELECT 1 FROM custody_transfers WHERE from_shift_id=%s AND closure AND status='confirmed'",(sid,)).fetchone(): fail(409,'تم تأكيد التسليم النهائي؛ يلزم قيد تصحيح مرتبط')
            c.execute("UPDATE custody_transfers SET status='rejected' WHERE from_shift_id=%s AND closure AND status='pending'",(sid,))
            c.execute("UPDATE shifts SET status='closing',submission=NULL,submitted_at=NULL WHERE id=%s",(sid,))
            db.audit(c,user['id'],'shift.return','shift',sid,{'reason':body.reason})
            return {'shift_id':sid,'status':'closing'}
        return mutate(c,user,request,'shift.return:'+sid,body.model_dump(),perform)

def approve_close_in_tx(c,user,sid,body):
    s=lock_shift(c,sid)
    own_manager=bool(s and s['kind']=='manager' and s['user_id']==user['id'] and user['role']=='delivery_manager')
    shift_access(c,user,s,manager=not own_manager,capability='shifts_approve')
    if s['kind']=='manager' and not own_manager:shift_roles(user,'shifts_approve','owner')
    guard_accepted_awaiting_dispatch(c,s)
    from .driver_work_hours import guard_shift_completion
    guard_shift_completion(c,s)
    if s['status']!='submitted': fail(409,'التقفيلة غير مرسلة')
    from .driver_incentives import require_decisions
    require_decisions(c,user,s)
    sub=s['submission']
    fixed_amount=int((s.get('permanent_float') or {}).get('amount',0))
    unusual_retained=sub['retained_halalas']!=min(sub.get('counted_halalas',0),fixed_amount) or (not s.get('permanent_float') and sub['retained_halalas'])
    if sub['difference_halalas'] or sub.get('card_difference_halalas') or unusual_retained or sub.get('counted_halalas',0)<fixed_amount:
        if not body.exception_reason or not body.exception_reason.strip(): fail(422,'يلزم سبب ظاهر لفرق النقد أو موازنة الشبكة أو العهدة المحتفظ بها')
    if unresolved(c,sid): fail(409,'مهام معلقة')
    if c.execute("SELECT 1 FROM manager_handovers WHERE (from_shift_id=%s OR to_shift_id=%s) AND status='pending'",(sid,sid)).fetchone():fail(409,'تسليم مناوبة غير مقبول')
    if s['kind']=='manager' and c.execute("SELECT 1 FROM shifts WHERE manager_shift_id=%s AND status<>'closed'",(sid,)).fetchone(): fail(409,'سائق غير مقفل')
    if c.execute("SELECT 1 FROM custody_transfers WHERE (from_shift_id=%s OR to_shift_id=%s) AND status='pending' AND NOT (%s AND from_shift_id=%s AND closure AND to_shift_id IS NULL)",(sid,sid,s['kind']=='manager',sid)).fetchone(): fail(409,'استلام غير مؤكد')
    receipts=c.execute("SELECT coalesce(sum(amount_halalas),0) n,count(*) nrows FROM custody_transfers WHERE from_shift_id=%s AND closure AND status='confirmed'",(sid,)).fetchone()
    if s['kind']=='driver' and (int(receipts['n'])!=sub['handover_halalas'] or not receipts['nrows']):fail(409,'يلزم تأكيد فعلي للتسليم النهائي، ولو كان المبلغ صفرا')
    if s['kind']=='manager':sub=dict(sub,operational_approved=True)
    if sub['difference_halalas']:event(c,sid,sub['difference_halalas'],'adjustment','closure-difference:'+sid,user['id'],body.exception_reason)
    if sub['retained_halalas']:c.execute('INSERT INTO retained_carryovers(id,from_shift_id,user_id,amount_halalas,approved_by,reason) VALUES(%s,%s,%s,%s,%s,%s)',(uid(),sid,s['user_id'],sub['retained_halalas'],user['id'],body.exception_reason or 'عهدة دائمة معتمدة من المحاسبة'))
    exceptional=s['kind']=='driver' and (sub.get('delegated_closure') or sub['difference_halalas'] or sub.get('card_difference_halalas') or unusual_retained or sub.get('counted_halalas',0)<fixed_amount)
    if exceptional:
        sub=dict(sub)
        sub['closure_exception']={'by':user['id'],'reason':body.exception_reason or sub.get('delegated_closure',{}).get('reason'),'delegated':bool(sub.get('delegated_closure'))}
    reason=body.exception_reason or sub.get('delegated_closure',{}).get('reason')
    c.execute("UPDATE shifts SET status='closed',closed_at=now(),approved_by=%s,exception_reason=%s,submission=%s WHERE id=%s",(user['id'],reason,db.json(sub),sid))
    from .reporting import create_snapshot
    rid=create_snapshot(c,sid,user['id'])
    db.audit(c,user['id'],'shift.approve','shift',sid,{'report_id':rid,'exception_reason':body.exception_reason})
    return {'shift_id':sid,'status':'closed','report_id':rid,'pdf_status':'pending'}

@router.post('/shifts/{sid}/approve')
def approve_close(sid:str,body:Review,request:Request,user=Depends(auth.current_user)):
    shift_roles(user,'shifts_approve','owner','delivery_manager')
    with db.tx() as c:
        def perform():return approve_close_in_tx(c,user,sid,body)
        result=mutate(c,user,request,'shift.approve:'+sid,body.model_dump(),perform)
    from .reporting import generate_files
    generate_files(result['report_id'])
    return result

def eligible_driver_shift(c,s,order):
    from .driver_receipt_trial import assignment_guard
    assignment_guard(s,order)
    from .fuel_exceptions import readiness_guard
    if s:readiness_guard(s)
    from .driver_breaks import guard as break_guard
    if s:break_guard(c,s['id'])
    if not s or s['kind']!='driver' or s['status']!='open' or not s.get('available',True) or s['brand_id']!=order['brand_id'] or order['branch_id'] not in s['branch_ids']:fail(409,'شفت السائق غير مؤهل')
    driver=c.execute('SELECT * FROM users WHERE id=%s',(s['user_id'],)).fetchone()
    if not driver or not driver['active'] or driver['role']!='driver' or driver['brand_id']!=order['brand_id'] or order['branch_id'] not in driver['branch_ids']:fail(409,'صلاحية السائق غير سارية')
    manager=lock_shift(c,s['manager_shift_id'])
    if not manager or manager['status']!='open' or manager['brand_id']!=order['brand_id'] or order['branch_id'] not in manager['branch_ids']:fail(409,'مناوبة المدير لا تستقبل مهام جديدة')
    principal=c.execute('SELECT * FROM users WHERE id=%s',(manager['user_id'],)).fetchone()
    if not principal or not principal['active'] or principal['role'] not in ('owner','delivery_manager') or (principal['role']!='owner' and order['branch_id'] not in principal['branch_ids']):fail(409,'مدير المناوبة غير مخول')
    if order.get('snapshot', {}).get('delivery_period') == 'morning':
        from . import morning_delivery
        morning_delivery.authorize_order(c, principal, order)
        if (manager.get('delivery_window_snapshot') or {}).get('period') != 'morning':
            fail(409, 'اختر سائقًا تابعًا لمناوبة صباحية.')
        from . import morning_dispatch
        if morning_dispatch.actual_morning(order):
            branch=c.execute('SELECT branch_no,city FROM branches WHERE id=%s AND brand_id=%s',(order['branch_id'],order['brand_id'])).fetchone()
            if branch and branch['branch_no']==2 and ''.join(branch['city'].split())=='حفرالباطن':
                if driver.get('invitation_status','active')!='active' or principal.get('invitation_status','active')!='active':
                    raise db.error(409,'MORNING_EMPLOYEE_INACTIVE','اعتماد حساب السائق أو مدير الصباح غير ساري.')
                if s['branch_ids'] != [order['branch_id']] or manager['branch_ids'] != [order['branch_id']]:
                    raise db.error(403,'MORNING_DRIVER_BRANCH_SCOPE','مناوبات الصباح الحالية محصورة بفرع 2 فقط.')
                load=c.execute("SELECT count(*) n FROM driver_tasks WHERE shift_id=%s AND status NOT IN ('delivered','cancelled','transferred','reassigned') AND (%s::text IS NULL OR order_id<>%s)", (s['id'],order.get('id'),order.get('id'))).fetchone()['n']
                if load>=morning_dispatch.CAPACITY:
                    raise db.error(409,'MORNING_DRIVER_CAPACITY','وصل السائق لسعة مهام الصباح الحالية.')
    return manager

class Assignment(OperationBody):
    driver_shift_id:str
    accept_order:bool=False

def assign_in_transaction(c,user,oid,body):
    o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone();order_access(c,user,o)
    from . import morning_delivery
    morning_delivery.authorize_order(c, user, o)
    if o.get('snapshot', {}).get('delivery_period') == 'morning' and o['preparation_status'] == 'new':
        fail(409, 'ينتظر الطلب الصباحي قبول محضّر الفرع قبل الإسناد.')
    if o['service']!='delivery' or o['preparation_status'] in ('cancelled','rejected','completed') or o.get('completed_at'):fail(409,'الطلب غير قابل للإسناد')
    from .order_amendments import guard
    guard(c,o,require_branch=False)
    s=lock_shift(c,body.driver_shift_id)
    if user.get('_morning_manager_context'):
        shift_access(c, user, s)
    if not s or s['kind']!='driver' or s['status']!='open' or s['brand_id']!=o['brand_id'] or o['branch_id'] not in s['branch_ids']:fail(409,'السائق غير مؤهل أو شفته غير مفتوح')
    driver=c.execute('SELECT * FROM users WHERE id=%s',(s['user_id'],)).fetchone()
    if not driver or not driver['active'] or driver['role']!='driver' or o['branch_id'] not in driver['branch_ids']:fail(409,'صلاحية السائق غير سارية')
    manager=lock_shift(c,s['manager_shift_id'])
    if not manager or manager['status']!='open':fail(409,'شفت المدير غير مفتوح')
    eligible_driver_shift(c,s,o)
    if user['role']=='delivery_manager' and manager['user_id']!=user['id']:fail(403,'الشفت تابع لمدير آخر')
    if c.execute("SELECT 1 FROM driver_tasks WHERE order_id=%s AND status NOT IN ('delivered','cancelled','transferred')",(oid,)).fetchone():fail(409,'الطلب مسند بالفعل؛ استخدم تسليم المهمة بقبول السائق الآخر')
    if body.accept_order and o['preparation_status']=='new':
        if o['customer'].get('requires_operational_confirmation') and not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='order.operational_confirmation' AND details->>'result'='reached'",(oid,)).fetchone():
            raise db.error(409,'OPERATIONAL_CONTACT_CONFIRMATION_REQUIRED','تتطلب سياسة الطلب اتصالًا تشغيليًا موثقًا قبل القبول والإسناد.')
        c.execute("UPDATE orders SET preparation_status='accepted' WHERE id=%s",(oid,))
        db.event(c,oid,user['id'],'preparation.accepted',{'previous':'new','reason':'Accepted with driver assignment'})
        db.audit(c,user['id'],'order.preparation','order',oid,{'from':'new','to':'accepted','reason':'Accepted with driver assignment'})
    tid=uid(); c.execute('INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,assigned_by) VALUES(%s,%s,%s,%s,%s)',(tid,oid,s['user_id'],s['id'],user['id']))
    c.execute("UPDATE orders SET delivery_status='assigned',version=version+1 WHERE id=%s",(oid,))
    db.event(c,oid,user['id'],'task.assigned',{'task_id':tid,'driver_id':s['user_id']})
    db.audit(c,user['id'],'task.assign','order',oid,{'task_id':tid,'shift_id':s['id']})
    return {'task':c.execute('SELECT * FROM driver_tasks WHERE id=%s',(tid,)).fetchone()}

@router.post('/orders/{oid}/assign')
def assign(oid:str,body:Assignment,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager','delivery_staff')
    if not auth.has_permission(user,'delivery_assign'):
        raise db.error(403,'STAFF_PERMISSION_FORBIDDEN','صلاحية إسناد الطلبات متوقفة لحسابك.')
    with db.tx() as c:
        from . import morning_delivery
        visible=c.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
        order_access(c,user,visible)
        morning_delivery.authorize_order(c,user,visible)
        if user.get('_morning_manager_context'):
            from .morning_dispatch import lock_operations
            lock_operations(c)
            target = lock_shift(c, body.driver_shift_id)
            shift_access(c, user, target)
            eligible_driver_shift(c, target, visible)
        def perform():return assign_in_transaction(c,user,oid,body)
        return mutate(c,user,request,'order.assign:'+oid,body.model_dump(),perform)

@router.get('/tasks')
def tasks(order_id: str | None=Query(None,max_length=100), user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager','delivery_staff','driver')
    with db.tx() as c:
        context = user.get('_morning_manager_context')
        if context:
            rows=c.execute("SELECT t.*,o.order_no,o.snapshot,o.customer,o.total_halalas,o.preparation_status,o.payment_status,o.branch_id,o.brand_id FROM driver_tasks t JOIN orders o ON o.id=t.order_id JOIN shifts s ON s.id=t.shift_id WHERE o.brand_id=%s AND o.branch_id=ANY(%s) AND o.service='delivery' AND o.snapshot->>'delivery_period'='morning' AND s.manager_shift_id=%s"+(' AND t.order_id=%s' if order_id else '')+' ORDER BY t.created_at DESC LIMIT 500', [user['brand_id'],user['branch_ids'],context['shift_id']]+([order_id] if order_id else [])).fetchall()
        elif user['role']=='driver':rows=c.execute("SELECT t.*,o.order_no,o.snapshot,o.customer,o.total_halalas,o.preparation_status,o.payment_status,o.branch_id FROM driver_tasks t JOIN orders o ON o.id=t.order_id WHERE o.brand_id=%s AND o.branch_id=ANY(%s) AND t.status NOT IN ('delivered','cancelled','transferred','reassigned') AND (t.driver_id=%s OR t.transfer_shift_id IN (SELECT id FROM shifts WHERE user_id=%s AND status='open')) ORDER BY t.created_at,t.id",(user['brand_id'],user.get('branch_ids',[]),user['id'],user['id'])).fetchall()
        else:
            rows=c.execute('SELECT t.*,o.order_no,o.snapshot,o.customer,o.total_halalas,o.preparation_status,o.payment_status,o.branch_id,o.brand_id FROM driver_tasks t JOIN orders o ON o.id=t.order_id WHERE o.brand_id=%s AND (%s OR o.branch_id=ANY(%s))'+(' AND t.order_id=%s' if order_id else '')+' ORDER BY t.created_at DESC LIMIT 500',
                           [user['brand_id'],user['role']=='owner',user.get('branch_ids',[])]+([order_id] if order_id else [])).fetchall()
        if user.get('_morning_manager_context'):
            from .morning_manager_context import order_allowed
            context = user['_morning_manager_context']
            rows = [r for r in rows if order_allowed(user, dict(r, id=r['order_id'], service='delivery'), c)]
        if order_id:
            rows=[r for r in rows if r['order_id']==order_id]
        if user['role']=='driver':
            rows=[r for r in rows if r['branch_id'] in user.get('branch_ids',[])]
            for r in rows:
                if r['status'] in TERMINAL or r['driver_id']!=user['id']:
                    r.pop('customer',None)
                    r['snapshot']=safe(r['snapshot']);r['snapshot'].pop('notes',None);r['snapshot'].pop('note',None)
                    for item in r['snapshot'].get('items',[]):item.pop('note',None)
        if user['role']=='driver':rows=[blind_closure.driver_view(user,r,shift_money=True) if r['status'] in TERMINAL or r['driver_id']!=user['id'] else r for r in rows]
        counts=None
        if user['role']=='driver':
            counts=c.execute("SELECT count(DISTINCT t.order_id) completed FROM driver_tasks t JOIN shifts s ON s.id=t.shift_id JOIN orders o ON o.id=t.order_id WHERE t.driver_id=%s AND s.id=(SELECT id FROM shifts WHERE user_id=%s AND brand_id=%s ORDER BY opened_at DESC,id DESC LIMIT 1) AND t.status='delivered' AND o.brand_id=%s AND o.branch_id=ANY(%s)",(user['id'],user['id'],user['brand_id'],user['brand_id'],user.get('branch_ids',[]))).fetchone()
        return safe({'tasks':rows, 'counts':counts})

class TaskTransition(OperationBody):
    photo_id:str|None=None
    status:str
    proof:str=Field('',max_length=2000)
    reason:str=Field('',max_length=2000)
    contact_method:Literal['call','message']|None=None


class DeliveryResolution(OperationBody):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    reason: str = Field(min_length=3, max_length=2000)
    evidence: str = Field(min_length=3, max_length=2000)
    delivered_at: datetime
    confirmed: StrictBool


def validate_delivery_resolution(body, task, order, now):
    if not body.confirmed or body.delivered_at.tzinfo is None:
        raise db.error(422, 'DELIVERY_VERIFICATION_REQUIRED', 'أكد التحقق من التسليم وحدد وقته الفعلي بمنطقة زمنية.')
    if (order['service'] != 'delivery' or order.get('completed_at') or
            order['preparation_status'] in ('cancelled', 'rejected', 'completed') or
            order['delivery_status'] in ('cancelled', 'delivered') or
            task['status'] not in ('picked_up', 'problem') or task.get('delivered_at')):
        raise db.error(409, 'DELIVERY_RESOLUTION_STATE', 'الطلب غير قابل لمعالجة تسليم متعثر.')
    pickup = task.get('picked_up_at')
    if pickup is None or not (order['created_at'] <= pickup <= body.delivered_at <= now):
        raise db.error(422, 'DELIVERY_ACTUAL_TIME_REQUIRED', 'يلزم استلام مسجل ووقت تسليم فعلي بعد الاستلام ولا يتجاوز الوقت الحالي.')
    from .complimentary import is_free
    if order['payment_status'] != 'paid' and not is_free(order):
        raise db.error(409, 'DELIVERY_PAYMENT_REQUIRED', 'يلزم استكمال التحصيل الفعلي قبل توثيق التسليم.')


@router.post('/tasks/{tid}/resolve-delivery')
def resolve_delivery(tid: str, body: DeliveryResolution, request: Request, user=Depends(auth.current_user)):
    roles(user, 'owner', 'delivery_manager')
    if not auth.has_permission(user, 'tasks_resolve_delivery'):
        raise db.error(403, 'STAFF_PERMISSION_FORBIDDEN', 'صلاحية معالجة الطلب المتعثر متوقفة لحسابك.')
    with db.tx() as c:
        # Recheck current order AND shift scope before idempotent replay.
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('operations:'+user['brand_id'],))
        task = c.execute('SELECT * FROM driver_tasks WHERE id=%s', (tid,)).fetchone()
        if not task: fail(404, 'المهمة غير موجودة')
        order = c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE', (task['order_id'],)).fetchone()
        order_access(c, user, order)
        task = c.execute('SELECT * FROM driver_tasks WHERE id=%s FOR UPDATE', (tid,)).fetchone()
        shift = lock_shift(c, task['shift_id'])
        shift_access(c, user, shift)
        def perform():
            if shift['status'] != 'open':
                raise db.error(409, 'DELIVERY_SHIFT_CLOSED', 'لا يمكن تعديل مهمة في شفت قيد الإقفال أو مقفل.')
            validate_delivery_resolution(body, task, order, datetime.now(timezone.utc))
            from .order_amendments import guard
            guard(c, order, driver_id=task['driver_id'], require_branch=True)
            c.execute("UPDATE driver_tasks SET status='delivered',delivered_at=%s,reason=%s,proof=%s WHERE id=%s",
                      (body.delivered_at, body.reason, body.evidence, tid))
            c.execute("UPDATE orders SET delivery_status='delivered',completed_at=%s,version=version+1 WHERE id=%s",
                      (body.delivered_at, order['id']))
            from .fiscal_issuance import issue_sale
            issue_sale(c, order['id'], user)
            details = {'task_id': tid, 'previous': task['status'], 'status': 'delivered',
                       'reason': body.reason, 'evidence': body.evidence,
                       'actual_delivered_at': body.delivered_at.isoformat(),
                       'branch_id': order['branch_id'], 'actor_name': user.get('display_name', ''),
                       'manager_decision': True, 'administrative_resolution': True}
            # Event/audit created_at records processing time, never replaces the
            # separately verified actual delivery time used by performance.
            db.event(c, order['id'], user['id'], 'task.delivered', details)
            db.audit(c, user['id'], 'task.delivery.resolved', 'task', tid, details)
            return {'task': c.execute('SELECT * FROM driver_tasks WHERE id=%s', (tid,)).fetchone()}
        return mutate(c, user, request, 'task.resolve_delivery:'+tid, body.model_dump(mode='json'), perform)

@router.post('/tasks/{tid}/transition')
def task_transition(tid:str,body:TaskTransition,request:Request,user=Depends(auth.current_user)):
    roles(user,'driver','owner','delivery_manager')
    if body.status == 'cancelled' and not auth.has_permission(user, 'orders_cancel'):
        raise db.error(403, 'STAFF_PERMISSION_FORBIDDEN', 'صلاحية إلغاء المهام متوقفة لحسابك.')
    with db.tx() as c:
        def perform():
            t=c.execute('SELECT * FROM driver_tasks WHERE id=%s',(tid,)).fetchone()
            if not t:fail(404,'المهمة غير موجودة')
            o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(t['order_id'],)).fetchone()
            t=c.execute('SELECT * FROM driver_tasks WHERE id=%s FOR UPDATE',(tid,)).fetchone()
            s=lock_shift(c,t['shift_id']);shift_access(c,user,s)
            from . import morning_dispatch
            if (body.status=='accepted' and morning_dispatch.launch_order(c,o)
                    and (user['role']!='driver' or t['driver_id']!=user['id'])):
                raise db.error(403,'MORNING_DRIVER_ACCEPTANCE_ONLY','قبول مهمة الصباح من السائق المكلف نفسه فقط.')
            from .driver_receipt_trial import physical_guard
            physical_guard(s,body.status)
            if body.status=='accepted':
                from .driver_receipt_trial import assignment_guard
                assignment_guard(s,o)
            if user['role']=='driver' and t['driver_id']!=user['id']:fail(403,'المهمة لسائق آخر')
            resolving_problem=t['status']=='problem' and body.status!='problem'
            if resolving_problem:
                roles(user,'owner','delivery_manager')
                if not body.reason.strip():
                    raise db.error(422,'PROBLEM_DECISION_REQUIRED','سجل قرار مدير التوصيل وسبب معالجة البلاغ.')
                if body.status=='accepted' and t.get('picked_up_at'):
                    raise db.error(409,'PROBLEM_PICKUP_ALREADY_CONFIRMED','الاستلام مسجل سابقًا؛ استأنف من مرحلة التوصيل دون إعادة المرحلة.')
            if body.status in ('accepted','picked_up','delivered'):
                from .order_amendments import guard
                guard(c,o,driver_id=t['driver_id'],require_branch=body.status!='accepted')
            allowed={'assigned':('accepted','problem'),'accepted':('picked_up','problem'),'picked_up':('delivered','problem'),'problem':('accepted','picked_up','cancelled')}
            if body.contact_method:
                if body.status!='problem':fail(422,'محاولة التواصل ترفع للمدير كبلاغ فقط')
                if t['status']=='problem':allowed['problem']=('problem',)
            if body.status not in allowed.get(t['status'],()):fail(409,'انتقال حالة غير صالح')
            if o['preparation_status'] in ('cancelled','rejected') and body.status in ('accepted','picked_up','delivered'):
                fail(409,'الطلب ملغى أو مرفوض')
            from . import morning_dispatch
            morning_pickup = morning_dispatch.launch_order(c,o)
            if body.status=='picked_up' and morning_pickup and o['preparation_status'] not in {'ready','handed_over'}:
                raise db.error(409,'MORNING_PREPARATION_REQUIRED','انتظر اكتمال تجهيز الفرع قبل الاستلام الفعلي.')
            if body.status=='picked_up' and o['preparation_status']!='handed_over':
                if o['snapshot'].get('delivery_workflow')!='manager_dispatch' and not morning_pickup:
                    fail(409,'يلزم تأكيد تسليم الفرع للسائق')
                if user['role']!='driver' or t['driver_id']!=user['id']:
                    fail(403,'تأكيد الاستلام الفعلي من السائق المكلف فقط')
                c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(o['id'],))
                db.event(c,o['id'],user['id'],'preparation.handed_over',
                         {'previous':o['preparation_status'],'source':'driver_confirmed_pickup','workflow':'manager_dispatch'})
            from .complimentary import is_free
            if body.status=='delivered' and o['payment_status']!='paid' and not is_free(o):fail(409,'يلزم تحصيل فعلي ودليل تسليم')
            if body.status in ('picked_up','delivered'):
                from .order_support import require_photo
                if resolving_problem and body.status=='picked_up':
                    # Manager restores only an already evidenced pickup, never certifies a new one.
                    if not t.get('picked_up_at') or not c.execute("SELECT 1 FROM delivery_photos WHERE task_id=%s AND stage='picked_up' AND used_at IS NOT NULL",(tid,)).fetchone():
                        raise db.error(409,'PROBLEM_PICKUP_EVIDENCE_REQUIRED','لا يوجد إثبات استلام سابق؛ راجع المهمة دون اختلاق وقت استلام.')
                else:
                    require_photo(c,t,user,body.status,body.photo_id)
            if body.status in ('problem','cancelled') and not body.reason.strip():fail(422,'يلزم سبب')
            if body.status=='cancelled':
                roles(user,'owner','delivery_manager')
                from .order_amendments import balance as paid_balance
                if o['payment_status'] in ('paid','partially_refunded') or paid_balance(c,o)>0:fail(409,'يلزم استرداد كامل قبل الإلغاء')
            if t['status']=='problem' and body.status!='problem':
                from .order_support import next_cleanup
                c.execute('UPDATE delivery_photos SET expires_at=greatest(expires_at,%s) WHERE order_id=%s AND data IS NOT NULL',(next_cleanup(),o['id']))
            timestamp={'accepted':'accepted_at','picked_up':'picked_up_at','delivered':'delivered_at'}.get(body.status)
            sql="UPDATE driver_tasks SET status=%s,proof=%s,reason=%s" + (f',{timestamp}=coalesce({timestamp},now())' if timestamp else '')+' WHERE id=%s'
            c.execute(sql,(body.status,body.proof,body.reason,tid))
            c.execute('UPDATE orders SET delivery_status=%s,completed_at=CASE WHEN %s=\'delivered\' THEN now() ELSE completed_at END,version=version+1 WHERE id=%s',(body.status,body.status,t['order_id']))
            if body.status=='delivered':
                from .fiscal_issuance import issue_sale
                issue_sale(c,t['order_id'],user)
            if body.status=='cancelled':
                from .fiscal_issuance import credit_on_cancellation
                credit_on_cancellation(c,o,user,body.reason)
            if body.status=='cancelled':c.execute("UPDATE orders SET preparation_status='cancelled' WHERE id=%s",(t['order_id'],))
            db.event(c,t['order_id'],user['id'],'task.'+body.status,{'task_id':tid,'previous':t['status'],'status':body.status,
                     'photo_id':body.photo_id,'reason':body.reason,'branch_id':o['branch_id'],
                     'actor_name':user.get('display_name',''),'manager_decision':resolving_problem})
            if body.contact_method:
                db.event(c,t['order_id'],user['id'],'customer.no_answer',{'task_id':tid,'method':body.contact_method,'reason':body.reason})
            db.audit(c,user['id'],'task.transition','task',tid,body.model_dump())
            return {'task':c.execute('SELECT * FROM driver_tasks WHERE id=%s',(tid,)).fetchone()}
        return mutate(c,user,request,'task.transition:'+tid,body.model_dump(),perform)

@router.get('/operations/alerts')
def operational_alerts(user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager','delivery_staff')
    with db.tx() as c:
        scope="o.brand_id=%s AND (%s OR o.branch_id=ANY(%s))"
        params=(user['brand_id'],user['role']=='owner',user.get('branch_ids',[]))
        rows=c.execute("SELECT o.id,o.order_no,o.snapshot->>'order_reference' AS order_reference,o.branch_id,o.created_at FROM orders o WHERE "+scope+" AND o.service='delivery' AND o.preparation_status NOT IN ('cancelled','rejected','completed') AND o.delivery_status NOT IN ('delivered','cancelled') AND NOT EXISTS (SELECT 1 FROM driver_tasks t WHERE t.order_id=o.id AND t.status NOT IN ('cancelled','transferred','reassigned')) ORDER BY o.created_at LIMIT 100",params).fetchall()
        problems=c.execute("SELECT o.id,o.order_no,o.snapshot->>'order_reference' AS order_reference,t.id AS task_id,t.reason FROM orders o JOIN driver_tasks t ON t.order_id=o.id WHERE "+scope+" AND t.status='problem' ORDER BY t.created_at LIMIT 100",params).fetchall()
        printing=[]
        if user['role'] in ('owner','delivery_manager'):
            printing=c.execute("SELECT p.id,p.order_id,p.status,p.branch_id,p.kind,p.created_at,p.reprint_reason,p.alert_resolution,o.order_no,o.snapshot->>'order_reference' AS order_reference,b.name AS branch_name,b.branch_no,d.label AS printer_label,d.printer_name FROM branch_print_jobs p LEFT JOIN orders o ON o.id=p.order_id AND o.brand_id=p.brand_id JOIN branches b ON b.id=p.branch_id AND b.brand_id=p.brand_id JOIN branch_print_devices d ON d.id=p.device_id AND d.brand_id=p.brand_id WHERE p.brand_id=%s AND (%s OR p.branch_id=ANY(%s)) AND coalesce(p.alert_resolution->>'action','') NOT IN ('confirmed','reprint') AND (p.alert_resolution->>'action'='escalated' OR p.status='uncertain' OR (p.status IN ('queued','claimed') AND (p.created_at<now()-interval '2 minutes' OR p.reprint_reason<>''))) ORDER BY p.created_at LIMIT 100",params).fetchall()
        exceptions=[]
        if user['role']=='owner':
            exceptions=c.execute("SELECT n.id,n.report_id FROM report_notifications n JOIN closure_reports r ON r.id=n.report_id WHERE n.recipient_user_id=%s AND n.brand_id=%s AND n.read_at IS NULL AND r.snapshot->'shift'->'submission'->'closure_exception' IS NOT NULL ORDER BY n.created_at DESC LIMIT 50",(user['id'],user['brand_id'])).fetchall()
        return safe({'unassigned':rows,'problems':problems,'printing':printing,'closure_exceptions':exceptions})

class TaskHandover(OperationBody): driver_shift_id:str; reason:str=Field(min_length=1,max_length=2000)

@router.post('/tasks/{tid}/handover')
def task_handover(tid:str,body:TaskHandover,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager')
    with db.tx() as c:
        if user.get('_morning_manager_context'):
            from .morning_dispatch import lock_operations
            lock_operations(c)
            prior = c.execute('SELECT * FROM driver_tasks WHERE id=%s', (tid,)).fetchone()
            if not prior:fail(404,'المهمة غير موجودة')
            shift_access(c,user,lock_shift(c,prior['shift_id']))
            order = c.execute('SELECT * FROM orders WHERE id=%s', (prior['order_id'],)).fetchone()
            order_access(c,user,order)
            from . import morning_delivery
            morning_delivery.authorize_order(c,user,order)
            target = lock_shift(c,body.driver_shift_id)
            shift_access(c,user,target)
            eligible_driver_shift(c,target,order)
            from .driver_receipt_trial import physical_guard
            physical_guard(target,prior['status'])
            if c.execute('SELECT 1 FROM collections WHERE order_id=%s',(prior['order_id'],)).fetchone():
                fail(409,'مهمة محصلة: يلزم إنهاؤها لدى صاحب عهدة التحصيل')
        def perform():
            t=c.execute('SELECT * FROM driver_tasks WHERE id=%s FOR UPDATE',(tid,)).fetchone()
            if not t:fail(404,'المهمة غير موجودة')
            shift_access(c,user,lock_shift(c,t['shift_id']))
            to=lock_shift(c,body.driver_shift_id);o=c.execute('SELECT * FROM orders WHERE id=%s',(t['order_id'],)).fetchone()
            if t['status'] in TERMINAL or not to or to['status']!='open' or to['kind']!='driver' or to['brand_id']!=o['brand_id'] or o['branch_id'] not in to['branch_ids'] or to['id']==t['shift_id']:fail(409,'شفت الاستلام غير مؤهل')
            eligible_driver_shift(c,to,o)
            from .driver_receipt_trial import physical_guard
            physical_guard(to,t['status'])
            if c.execute('SELECT 1 FROM collections WHERE order_id=%s',(t['order_id'],)).fetchone():fail(409,'مهمة محصلة: يلزم إنهاؤها لدى صاحب عهدة التحصيل')
            c.execute('UPDATE driver_tasks SET transfer_shift_id=%s,transfer_requested_by=%s,reason=%s WHERE id=%s',(to['id'],user['id'],body.reason,tid))
            db.audit(c,user['id'],'task.handover.request','task',tid,body.model_dump())
            return {'task_id':tid,'transfer_shift_id':to['id'],'acceptance_required':True}
        return mutate(c,user,request,'task.handover:'+tid,body.model_dump(),perform)

@router.post('/tasks/{tid}/accept-handover')
def accept_task_handover(tid:str,request:Request,user=Depends(auth.current_user)):
    roles(user,'driver')
    with db.tx() as c:
        def perform():
            prior=c.execute('SELECT * FROM driver_tasks WHERE id=%s',(tid,)).fetchone()
            if not prior:fail(404,'المهمة غير موجودة')
            c.execute('SELECT id FROM orders WHERE id=%s FOR UPDATE',(prior['order_id'],))
            t=c.execute('SELECT * FROM driver_tasks WHERE id=%s FOR UPDATE',(tid,)).fetchone()
            if not t or not t['transfer_shift_id'] or t['status'] in TERMINAL:fail(404,'لا يوجد تحويل معلق')
            if c.execute('SELECT 1 FROM collections WHERE order_id=%s',(t['order_id'],)).fetchone():fail(409,'تم تحصيل المهمة؛ يلزم إنهاؤها لدى صاحب عهدة التحصيل')
            to=lock_shift(c,t['transfer_shift_id']);shift_access(c,user,to)
            if to['user_id']!=user['id'] or to['status']!='open':fail(409,'الشفت غير متاح')
            order=c.execute('SELECT * FROM orders WHERE id=%s',(t['order_id'],)).fetchone();eligible_driver_shift(c,to,order)
            from .driver_receipt_trial import physical_guard
            physical_guard(to,t['status'])
            status='picked_up' if t['status']=='picked_up' else 'accepted'
            c.execute("UPDATE driver_tasks SET status='transferred' WHERE id=%s",(tid,));newid=uid()
            c.execute('INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,accepted_at,picked_up_at) VALUES(%s,%s,%s,%s,%s,%s,now(),%s)',(newid,t['order_id'],user['id'],to['id'],status,t['transfer_requested_by'],t['picked_up_at']))
            c.execute('UPDATE orders SET delivery_status=%s,version=version+1 WHERE id=%s',(status,t['order_id']))
            db.event(c,t['order_id'],user['id'],'task.handover.accepted',{'previous_task_id':tid,'task_id':newid,'driver_id':user['id'],'previous':t['status'],'status':status})
            db.audit(c,user['id'],'task.handover.accept','task',tid,{'new_task_id':newid})
            return {'task':c.execute('SELECT * FROM driver_tasks WHERE id=%s',(newid,)).fetchone()}
        return mutate(c,user,request,'task.handover.accept:'+tid,{},perform)

class Collection(OperationBody):
    method:str
    amount_halalas:StrictInt=Field(gt=0,le=MAX_HALALAS)
    tendered_halalas:StrictInt|None=Field(None,ge=0,le=MAX_HALALAS)
    reference:str=Field('',max_length=200)
    cash_halalas:StrictInt|None=Field(None,gt=0,le=MAX_HALALAS)
    card_halalas:StrictInt|None=Field(None,gt=0,le=MAX_HALALAS)

@router.post('/orders/{oid}/collect')
def collect(oid:str,body:Collection,request:Request,user=Depends(auth.current_user)):
    roles(user,'driver','branch_manager','owner','delivery_manager')
    with db.tx() as c:
        def perform():
            o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone();order_access(c,user,o)
            if o['snapshot'].get('complimentary'):
                raise db.error(409,'NO_COLLECTION_REQUIRED','طلب ضيافة أو تعويض؛ بدون تحصيل.')
            from .branch_payments import online_order
            if online_order(o):
                raise db.error(409,'ONLINE_PAYMENT_ONLY','هذا الطلب يتطلب دفعًا إلكترونيًا موثقًا؛ التحصيل اليدوي غير متاح.')
            if o['payment_status']!='unpaid' or o['preparation_status'] in ('cancelled','rejected'):fail(409,'الطلب غير قابل للتحصيل')
            from .order_amendments import guard,balance as paid_balance
            guard(c,o,driver_id=user['id'] if user['role']=='driver' else None)
            due=o['total_halalas']-paid_balance(c,o)
            if body.method not in ('cash','card','split') or due<=0 or body.amount_halalas!=due:fail(422,'وسيلة أو مبلغ التحصيل غير صحيح؛ حصّل المتبقي فقط')
            if body.method=='split':
                if body.cash_halalas is None or body.card_halalas is None or body.cash_halalas+body.card_halalas!=body.amount_halalas:
                    fail(422,'مجموع الكاش والشبكة يجب أن يساوي قيمة الطلب، وكل جزء أكبر من صفر')
                parts=[('cash',body.cash_halalas),('card',body.card_halalas)]
            else:
                if body.cash_halalas is not None or body.card_halalas is not None:fail(422,'مبالغ التقسيم مخصصة للكاش والشبكة معًا')
                parts=[(body.method,body.amount_halalas)]
            cash=next((amount for method,amount in parts if method=='cash'),0)
            if not (user['role']=='driver' and o['service']=='delivery') and any(method=='card' for method,_ in parts) and not body.reference.strip():fail(422,'يلزم مرجع شبكة فعلي')
            if cash and (body.tendered_halalas is None or body.tendered_halalas<cash):fail(422,'المبلغ النقدي المسلم غير كاف')
            sid=None
            if o['service']=='delivery':
                if user['role']!='driver':fail(403,'التحصيل من السائق المكلف فقط')
                t=c.execute("SELECT * FROM driver_tasks WHERE order_id=%s AND driver_id=%s AND status='picked_up' FOR UPDATE",(oid,user['id'])).fetchone()
                if not t:fail(409,'استلم المهمة من الفرع أولا')
                s=lock_shift(c,t['shift_id'])
                if s['status'] not in ('open','closing'):fail(409,'الشفت لا يسمح بالتحصيل')
                valid_cash_amount(balance(c,s)+cash)
                sid=s['id']
            else:roles(user,'branch_manager','owner')
            change=body.tendered_halalas-cash if cash else 0
            ids=[]
            for method,amount in parts:
                cid=uid();ids.append(cid)
                c.execute('INSERT INTO collections(id,order_id,shift_id,collector_id,method,amount_halalas,tendered_halalas,change_halalas,reference,amendment_revision) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(cid,oid,sid,user['id'],method,amount,body.tendered_halalas if method=='cash' else None,change if method=='cash' else 0,body.reference.strip() if method=='card' else '',o.get('amendment_revision',0)))
                if sid and method=='cash':event(c,sid,amount,'collection',cid,user['id'],'تحصيل نقدي صافي بعد الباقي')
            c.execute("UPDATE orders SET payment_status='paid',payment_method=%s,version=version+1 WHERE id=%s",(body.method,oid))
            db.audit(c,user['id'],'order.collect','order',oid,{'collection_ids':ids,'amount_halalas':body.amount_halalas,'method':body.method,'cash_halalas':cash,'card_halalas':body.amount_halalas-cash,'change_halalas':change})
            return {'collection_id':ids[0],'collection_ids':ids,'payment_status':'paid','amount_halalas':body.amount_halalas,'change_halalas':change}
        return mutate(c,user,request,'order.collect:'+oid,body.model_dump(),perform)

class Refund(OperationBody):
    amount_halalas:StrictInt=Field(gt=0,le=MAX_HALALAS)
    method:str
    reason:str=Field(min_length=1,max_length=2000)
    line_quantities:dict[str,StrictInt]=Field(default_factory=dict)
    shift_id:str|None=None
    amendment_id:str|None=None

@router.post('/orders/{oid}/refund')
def refund(oid:str,body:Refund,request:Request,user=Depends(auth.current_user)):
    # Check authority before idempotency lookup, including previously successful retries.
    roles(user,'owner','branch_manager')
    if user['role']=='branch_manager' and not body.amendment_id:fail(403,'الاسترداد الاعتيادي يتطلب المدير')
    with db.tx() as c:
        def perform():
            o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone();order_access(c,user,o)
            from .order_amendments import active,balance as paid_balance
            from .branch_payments import online_order
            if online_order(o):
                raise db.error(409,'PROVIDER_REFUND_REQUIRED','استرداد الدفع الإلكتروني يتطلب تأكيد مزود الدفع.')
            if active(c,oid):fail(409,'أكمل مراجعة التعديل قبل الاسترداد')
            if user['role']=='branch_manager' and o['service']=='delivery':fail(403,'تسوية التوصيل من مدير التوصيل')
            cols=c.execute('SELECT p.*,p.amount_halalas-coalesce((SELECT sum(r.amount_halalas) FROM refunds r WHERE r.collection_id=p.id),0) available FROM collections p WHERE p.order_id=%s AND p.method=%s ORDER BY p.created_at,p.id',(oid,body.method)).fetchall()
            cols=[p for p in cols if p['available']>0]
            col=cols[0] if cols else None
            prior=c.execute('SELECT coalesce(sum(amount_halalas),0) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']
            if not col or body.amount_halalas>sum(p['available'] for p in cols):fail(422,'مبلغ أو وسيلة الاسترداد غير صالح')
            if body.amendment_id:
                amendment=c.execute("SELECT 1 FROM order_revisions WHERE order_id=%s AND revision=%s AND amendment_id=%s",(oid,o['amendment_revision'],body.amendment_id)).fetchone()
                if not amendment or body.line_quantities or body.amount_halalas>paid_balance(c,o)-o['total_halalas']:fail(422,'سجل فرق التعديل المستحق فقط بدون كميات مرتجعة')
            elif paid_balance(c,o)>o['total_halalas']:fail(409,'سوّ فرق التعديل أولًا قبل تسجيل مرتجع جديد')
            lines={}
            for x in o['snapshot']['items']:lines[x['product_id']]=lines.get(x['product_id'],0)+x['quantity']
            for pid,q in body.line_quantities.items():
                previous=c.execute("SELECT coalesce(sum((line_quantities->>%s)::int),0) n FROM refunds WHERE order_id=%s",(pid,oid)).fetchone()['n']
                if q<0 or pid not in lines or q+previous>lines[pid]:fail(422,'كمية استرداد الصنف غير صالحة')
            sid=body.shift_id or col['shift_id']
            if sid:
                s=lock_shift(c,sid);shift_access(c,user,s)
                if s['status'] not in ('open','closing') or s['brand_id']!=o['brand_id']:fail(409,'الاسترداد يحتاج عهدة شفت مفتوح؛ لا يعدل تقفيلة تاريخية')
                if body.method=='cash' and available_balance(c,s)<body.amount_halalas:fail(409,'العهدة النقدية غير كافية')
            elif o['service']=='delivery' and body.method=='cash':fail(409,'يلزم شفت عهدة للاسترداد النقدي')
            remaining=body.amount_halalas;refund_ids=[]
            for source in cols:
                if not remaining:break
                amount=min(remaining,int(source['available']));rid=uid()
                from .fiscal_issuance import credit_before_refund
                credit_before_refund(c,o,user,rid,amount,body.reason,amendment_id=body.amendment_id)
                c.execute('INSERT INTO refunds(id,collection_id,order_id,shift_id,method,amount_halalas,line_quantities,reason,authorized_by,amendment_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(rid,source['id'],oid,sid,body.method,amount,db.json(body.line_quantities if not refund_ids else {}),body.reason,user['id'],body.amendment_id))
                refund_ids.append(rid);remaining-=amount
                if sid and body.method=='cash':event(c,sid,-amount,'refund',rid,user['id'],body.reason)
            net=paid_balance(c,o)
            status=('paid' if net==o['total_halalas'] else 'unpaid') if body.amendment_id else ('refunded' if net==0 else 'partially_refunded')
            c.execute('UPDATE orders SET payment_status=%s,version=version+1 WHERE id=%s',(status,oid))
            db.audit(c,user['id'],'order.refund','order',oid,{'refund_id':rid,**body.model_dump()})
            return {'refund_id':refund_ids[0],'refund_ids':refund_ids,'payment_status':status}
        return mutate(c,user,request,'order.refund:'+oid,body.model_dump(),perform)

class PaymentTerminal(OperationBody):
    provider:str=Field('',max_length=100)
    terminal_id:str=Field('',max_length=100)
    serial_number:str=Field('',max_length=100)
    model:str=Field('',max_length=100)
    notes:str=Field('',max_length=500)
    reason:str=Field(min_length=1,max_length=1000)


def terminal_driver(c,user,driver_id):
    row=c.execute("SELECT * FROM users WHERE id=%s AND role='driver' AND brand_id=%s",(driver_id,user['brand_id'])).fetchone()
    if not row:fail(404,'السائق غير موجود')
    if user['role']=='driver':
        if row['id']!=user['id']:fail(404,'السائق غير موجود')
    elif user['role']!='owner' and not set(row['branch_ids'])<=set(user.get('branch_ids',[])):fail(403,'السائق خارج نطاق المدير')
    return row


@router.get('/drivers/{driver_id}/payment-terminal')
def get_payment_terminal(driver_id:str,user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager','driver')
    with db.tx() as c:return {'payment_terminal':terminal_driver(c,user,driver_id)['payment_terminal']}


@router.put('/drivers/{driver_id}/payment-terminal')
def set_payment_terminal(driver_id:str,body:PaymentTerminal,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager')
    with db.tx() as c:
        def perform():
            row=terminal_driver(c,user,driver_id)
            if not body.reason.strip():fail(422,'اكتب سبب التغيير')
            value={k:v.strip() for k,v in body.model_dump(exclude={'reason'}).items()}
            if not value['terminal_id'] or not value['provider']:fail(422,'أدخل رقم الجهاز ومزود الشبكة')
            c.execute('UPDATE users SET payment_terminal=%s WHERE id=%s',(db.json(value),driver_id))
            db.audit(c,user['id'],'driver.payment_terminal','user',driver_id,{'before':row['payment_terminal'],'after':value,'reason':body.reason})
            return {'payment_terminal':value}
        # Recheck scope even for a replay after branch permissions change.
        terminal_driver(c,user,driver_id)
        return mutate(c,user,request,'driver.payment_terminal:'+driver_id,body.model_dump(),perform)


class Custody(OperationBody):
    from_shift_id:str
    to_shift_id:str|None=None
    recipient_user_id:str|None=None
    amount_halalas:StrictInt=Field(ge=0,le=MAX_HALALAS)
    reference:str=Field(min_length=1,max_length=200)
    closure:bool=False
    receiving_branch_id:str|None=None

def create_custody(c,user,body,capability=None):
    ids=sorted(set([body.from_shift_id]+([body.to_shift_id] if body.to_shift_id else [])))
    locked={i:lock_shift(c,i) for i in ids};src=locked[body.from_shift_id];shift_access(c,user,src,capability=capability)
    to=locked.get(body.to_shift_id)
    if src['status']=='closed' or (to and (to['status'] not in ('open','closing') or to['brand_id']!=src['brand_id'])):fail(409,'شفت عهدة غير متاح')
    recipient=(to['user_id'] if to else body.recipient_user_id)
    target=c.execute('SELECT * FROM users WHERE id=%s AND active',(recipient,)).fetchone()
    if not target or target['id']==src['user_id'] or target['role'] not in ('owner','delivery_manager','driver','branch_manager') or not same_scope(user,target):fail(422,'يلزم مستلم مخول مستقل يؤكد فعليا')
    if src['kind']=='driver' and driver_cash_policy(c,src['brand_id'])=='delivery_manager':
        if body.receiving_branch_id or not to or to['kind']!='manager' or to['id']!=src['manager_shift_id'] or target['role'] not in ('delivery_manager','owner'):
            fail(403,'نقد السائق يسلم لمدير التوصيل المسؤول عن شفته في جميع المدن')
        if body.recipient_user_id and body.recipient_user_id!=to['user_id']:
            fail(422,'المستلم لا يطابق مدير شفت السائق')
        if target['role']!='owner' and not set(src['branch_ids'])<=set(target.get('branch_ids',[])):
            fail(403,'مدير التوصيل غير مخول لجميع فروع الشفت')
    elif src['kind']=='driver':
        from .catalog import normalize_city
        branches=c.execute('SELECT id,city FROM branches WHERE brand_id=%s AND id=ANY(%s)',(src['brand_id'],src['branch_ids'])).fetchall()
        cities={normalize_city(b['city']) for b in branches}
        if 'حفر الباطن' in cities and len(cities)>1:fail(409,'نطاق السائق يجمع حفر الباطن ومدن أخرى؛ راجع نطاق الشفت قبل تسليم النقد')
        if cities=={'حفر الباطن'}:
            if body.receiving_branch_id or not to or to['id']!=src['manager_shift_id']:fail(403,'نقد سائق حفر الباطن يسلم إلى شفت مديره فقط')
        else:
            if not branches or to or not body.receiving_branch_id or body.receiving_branch_id not in src['branch_ids'] or target['role']!='branch_manager' or body.receiving_branch_id not in target.get('branch_ids',[]):fail(403,'نقد السائق خارج حفر الباطن يسلم لمسؤول فرع مخول ضمن نطاق الشفت')
    elif body.receiving_branch_id:fail(422,'تسليم الكيس للفرع مخصص لشفت السائق')
    if src['kind']=='manager' and to and to['kind']=='driver' and to['manager_shift_id']!=src['id']:fail(403,'السلفة لسائق تابع فقط')
    if not to and not body.receiving_branch_id and target['role']!='owner':fail(403,'المستلم النهائي المالك المخول')
    pending=c.execute("SELECT coalesce(sum(amount_halalas),0) n FROM custody_transfers WHERE from_shift_id=%s AND status='pending'",(src['id'],)).fetchone()['n']
    if body.closure:
        if src['status']!='submitted':fail(409,'يلزم إرسال التقفيلة أولا')
        existing=c.execute("SELECT 1 FROM custody_transfers WHERE from_shift_id=%s AND closure AND status<>'rejected'",(src['id'],)).fetchone()
        if existing or body.amount_halalas!=src['submission']['handover_halalas']:fail(409,'مبلغ تسليم التقفيلة غير مطابق أو مسجل سابقا')
    else:
        if src['status'] not in ('open','closing') or body.amount_halalas<=0 or body.amount_halalas+pending>balance(c,src):fail(409,'العهدة المتاحة غير كافية أو الشفت مرسل')
    cid=uid();c.execute('INSERT INTO custody_transfers(id,from_shift_id,to_shift_id,recipient_user_id,amount_halalas,reference,closure,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(cid,src['id'],body.to_shift_id,recipient,body.amount_halalas,body.reference,body.closure,user['id']))
    if body.receiving_branch_id:c.execute('UPDATE custody_transfers SET receiving_branch_id=%s WHERE id=%s',(body.receiving_branch_id,cid))
    db.audit(c,user['id'],'custody.request','custody',cid,body.model_dump())
    return {'transfer':c.execute('SELECT * FROM custody_transfers WHERE id=%s',(cid,)).fetchone()}

@router.post('/custody')
def custody_transfer(body:Custody,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','driver','delivery_manager')
    with db.tx() as c:
        def perform():return create_custody(c,user,body)
        return mutate(c,user,request,'custody.request',body.model_dump(),perform)

@router.get('/custody')
def custody_list(user=Depends(auth.current_user)):
    roles(user,'owner','driver','delivery_manager','branch_manager')
    with db.tx() as c:
        rows=c.execute('SELECT t.*,s.brand_id,s.branch_ids FROM custody_transfers t JOIN shifts s ON s.id=t.from_shift_id WHERE s.brand_id=%s AND (t.recipient_user_id=%s OR s.user_id=%s) ORDER BY t.created_at DESC LIMIT 200',(user['brand_id'],user['id'],user['id'])).fetchall()
        rows=[r for r in rows if user['role']=='owner' or (r.get('receiving_branch_id') in user.get('branch_ids',[]) if user['role']=='branch_manager' else set(r['branch_ids'])<=set(user.get('branch_ids',[])))]
        if user['role']=='driver':rows=[r for r in rows if r['status']=='pending' and r['recipient_user_id']==user['id']]
        return safe({'transfers':rows})

def confirm_custody_in_tx(c,user,cid):
    t=c.execute('SELECT * FROM custody_transfers WHERE id=%s FOR UPDATE',(cid,)).fetchone()
    if not t or t['recipient_user_id']!=user['id']:fail(404,'التسليم غير موجود أو أنت لست المستلم')
    if t['status']!='pending':fail(409,'التسليم غير معلق')
    source=c.execute('SELECT * FROM shifts WHERE id=%s',(t['from_shift_id'],)).fetchone()
    scope_ok=(t.get('receiving_branch_id') in user.get('branch_ids',[]) if user['role']=='branch_manager' else set(source['branch_ids'])<=set(user.get('branch_ids',[])))
    if not same_scope(user,source) or (user['role']!='owner' and not scope_ok):fail(403,'تم سحب صلاحية فروع التسليم')
    if t['to_shift_id']:shift_access(c,user,c.execute('SELECT * FROM shifts WHERE id=%s',(t['to_shift_id'],)).fetchone())
    ids=sorted(set([t['from_shift_id']]+([t['to_shift_id']] if t['to_shift_id'] else [])))
    ss={i:lock_shift(c,i) for i in ids}
    source_closed=ss[t['from_shift_id']]['status']=='closed'
    operational_receipt=ss[t['from_shift_id']]['kind']=='manager' and (ss[t['from_shift_id']].get('submission') or {}).get('operational_approved') and t['closure'] and not t['to_shift_id']
    if (source_closed and not operational_receipt) or (t['to_shift_id'] and ss[t['to_shift_id']]['status'] not in ('open','closing')):fail(409,'شفت مقفل')
    if t['to_shift_id']:valid_cash_amount(balance(c,ss[t['to_shift_id']])+t['amount_halalas'])
    c.execute("UPDATE custody_transfers SET status='confirmed',confirmed_by=%s,confirmed_at=now() WHERE id=%s",(user['id'],cid))
    event(c,t['from_shift_id'],-t['amount_halalas'],'transfer_out',cid,user['id'],t['reference'])
    if t['to_shift_id']:event(c,t['to_shift_id'],t['amount_halalas'],'transfer_in',cid,user['id'],t['reference'])
    db.audit(c,user['id'],'custody.confirm','custody',cid,{'actual_recipient':user['id']})
    return {'transfer_id':cid,'status':'confirmed'}

@router.post('/custody/{cid}/confirm')
def confirm_custody(cid:str,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','driver','delivery_manager','branch_manager')
    with db.tx() as c:
        def perform():return confirm_custody_in_tx(c,user,cid)
        return mutate(c,user,request,'custody.confirm:'+cid,{},perform)

class DriverClose(OperationBody):
    vehicle_inspection: shift_vehicle.Inspection|None=None
    terminal_total_halalas: StrictInt|None=Field(None,ge=0,le=MAX_HALALAS)
    received_halalas: StrictInt=Field(ge=0,le=MAX_HALALAS)
    counted_halalas: StrictInt|None=Field(None,ge=0,le=MAX_HALALAS)
    confirmed: StrictBool
    exception_reason: str=Field(default='',max_length=2000)


class DriverAmountReview(OperationBody):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    counted_halalas: StrictInt=Field(ge=0,le=MAX_HALALAS)
    terminal_total_halalas: StrictInt=Field(ge=0,le=MAX_HALALAS)
    reason: str=Field(min_length=3,max_length=2000)


@router.post('/shifts/{sid}/review-amounts')
def review_driver_amounts(sid:str,body:DriverAmountReview,request:Request,user=Depends(auth.current_user)):
    """Manager correction before approval; original submission remains in audit."""
    roles(user,'delivery_manager')
    if not auth.has_permission(user,'shifts_approve'):fail(403,'صلاحية مراجعة التقفيلة غير متاحة')
    with db.tx() as c:
        def perform():
            s=lock_shift(c,sid);shift_access(c,user,s,manager=True,capability='shifts_approve')
            if s['kind']!='driver' or s['status']!='submitted':fail(409,'التعديل لتقفيلة سائق مرسلة وقبل اعتمادها فقط')
            manager=lock_shift(c,s['manager_shift_id'])
            if not manager or manager['user_id']!=user['id'] or manager['status']!='open':fail(403,'المراجعة لمدير الشفت المسؤول فقط')
            transfers=c.execute("SELECT * FROM custody_transfers WHERE from_shift_id=%s AND closure AND status<>'rejected' FOR UPDATE",(sid,)).fetchall()
            if any(t['status']=='confirmed' for t in transfers):fail(409,'الاستلام مؤكد؛ التصحيح اللاحق من خلال تسوية المحاسبة')
            if len(transfers)>1:fail(409,'توجد تسليمات متعددة؛ أعد التقفيلة للتصحيح والمراجعة')
            from .driver_float import split
            handover,retained=split(s,body.counted_halalas)
            original=dict(s['submission']);updated=dict(original)
            updated.update(counted_halalas=body.counted_halalas,handover_halalas=handover,retained_halalas=retained,
                           terminal_total_halalas=body.terminal_total_halalas,
                           difference_halalas=body.counted_halalas-original['expected_halalas'])
            updated.update(blind_closure.card_reconciliation(c,sid,body.terminal_total_halalas,(s.get('external_enbishan') or {}).get('card_halalas',0)))
            updated['driver_original_submission']=original.get('driver_original_submission') or {k:v for k,v in original.items() if k!='driver_original_submission'}
            c.execute('UPDATE shifts SET submission=%s WHERE id=%s',(db.json(updated),sid))
            for transfer in transfers:
                c.execute('UPDATE custody_transfers SET amount_halalas=%s WHERE id=%s',(handover,transfer['id']))
            db.audit(c,user['id'],'shift.manager_amount_review','shift',sid,
                     {'before':original,'after':updated,'reason':body.reason,'fields':['counted_halalas','terminal_total_halalas']})
            return {'shift_id':sid,'submission':updated}
        return mutate(c,user,request,'shift.manager_amount_review:'+sid,body.model_dump(),perform)

@router.post('/shifts/{sid}/driver-close')
def simple_driver_close(sid:str,body:DriverClose,request:Request,user=Depends(auth.current_user)):
    """One explicit receipt and closure confirmation; existing financial guards apply.

    Submission, receipt and approval share one transaction and idempotency key.
    Nothing is committed when a task, transfer, scope or amount prevents closure.
    """
    roles(user,'owner','delivery_manager')
    with db.tx() as c:
        def perform():
            s=lock_shift(c,sid);shift_access(c,user,s,manager=True)
            if user['role']!='delivery_manager':
                raise HTTPException(403,{'code':'DRIVER_CLOSE_MANAGER_ONLY'})
            if s['kind']!='driver' or s['status'] not in ('open','closing','submitted'):
                fail(409,'التقفيلة مقفلة أو غير متاحة')
            from .driver_work_hours import guard_shift_completion
            guard_shift_completion(c,s)
            if not body.confirmed:fail(422,'أكد استلام النقد الفعلي ومراجعة التقفيلة')
            if s['status'] in ('open','closing'):
                if body.counted_halalas is None:fail(422,'أدخل النقد المعدود فعليا')
                if body.received_halalas>body.counted_halalas:fail(422,'المستلم لا يتجاوز المعدود')
                if not body.exception_reason.strip():fail(422,'اكتب سبب الإقفال نيابة عن السائق')
                if s['user_id']==user['id']:fail(403,'استخدم إنهاء ورديتك من حساب السائق')
                if s['status']=='open':
                    c.execute("UPDATE shifts SET status='closing' WHERE id=%s",(sid,))
                    db.audit(c,user['id'],'shift.closing','shift',sid)
                submit_close_in_tx(c,user,sid,ClosureSubmit(
                    terminal_total_halalas=body.terminal_total_halalas,counted_halalas=body.counted_halalas,handover_halalas=body.received_halalas,
                    retained_halalas=body.counted_halalas-body.received_halalas,
                    vehicle_inspection=body.vehicle_inspection,exception_reason=body.exception_reason))
                s=lock_shift(c,sid)
            elif body.counted_halalas is not None:
                fail(422,'المعدود المرسل محفوظ؛ أعد التقفيلة للتصحيح عند الحاجة')
            sub=s['submission']
            if body.received_halalas!=sub['handover_halalas']:
                fail(422,'المستلم لا يطابق التسليم المسجل؛ أعد التقفيلة للتصحيح')
            transfers=c.execute("SELECT * FROM custody_transfers WHERE from_shift_id=%s AND closure AND status<>'rejected' FOR UPDATE",(sid,)).fetchall()
            if not transfers:
                manager=lock_shift(c,s['manager_shift_id'])
                if not manager or manager['user_id']!=user['id']:
                    fail(403,'مدير شفت السائق هو من يؤكد الاستلام الفعلي')
                transfer=create_custody(c,user,Custody(from_shift_id=sid,to_shift_id=manager['id'],
                    recipient_user_id=user['id'],amount_halalas=body.received_halalas,
                    reference='Driver closure '+str(s.get('shift_no') or sid),closure=True))['transfer']
                transfers=[transfer]
            for transfer in transfers:
                if transfer['status']=='pending':
                    if transfer['recipient_user_id']!=user['id']:
                        raise HTTPException(409,{'code':'DRIVER_RECEIPT_OTHER_RECIPIENT'})
                    confirm_custody_in_tx(c,user,transfer['id'])
            return approve_close_in_tx(c,user,sid,Review(exception_reason=body.exception_reason or None))
        result=mutate(c,user,request,'shift.driver-close:'+sid,body.model_dump(),perform)
    from .reporting import generate_files
    generate_files(result['report_id'])
    return result

class AuthorizedCash(OperationBody):
    corrects_report_id:str|None=None
    kind:str
    amount_halalas:StrictInt=Field(ge=-MAX_HALALAS-1,le=MAX_HALALAS)
    reason:str=Field(min_length=1,max_length=2000)
    reference:str=Field(min_length=1,max_length=200)

@router.post('/shifts/{sid}/cash-event')
def authorized_cash(sid:str,body:AuthorizedCash,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner')
    with db.tx() as c:
        def perform():
            s=lock_shift(c,sid);shift_access(c,user,s)
            if s['status'] not in ('open','closing'):fail(409,'الشفت لا يقبل تعديل عهدة')
            if body.kind not in ('expense','direct_receipt','adjustment') or not body.amount_halalas:fail(422,'نوع القيد غير صالح')
            if body.kind=='expense' and body.amount_halalas>=0 or body.kind=='direct_receipt' and body.amount_halalas<=0:fail(422,'إشارة المبلغ غير صحيحة')
            if available_balance(c,s)+body.amount_halalas<0:fail(409,'العهدة غير كافية')
            valid_cash_amount(balance(c,s)+body.amount_halalas)
            eid=uid();event(c,sid,body.amount_halalas,body.kind,eid,user['id'],body.reason+' / '+body.reference)
            if body.corrects_report_id:
                from .reporting import report
                original=report(c,user,body.corrects_report_id)
                version=c.execute('SELECT coalesce(max(correction_version),1)+1 n FROM report_corrections WHERE report_id=%s',(original['id'],)).fetchone()['n']
                c.execute('INSERT INTO report_corrections(id,report_id,correction_version,shift_id,event_reference_id,amount_halalas,reason,authorized_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(uid(),original['id'],version,sid,eid,body.amount_halalas,body.reason,user['id']))
            db.audit(c,user['id'],'custody.authorized_event','shift',sid,body.model_dump())
            return {'event_id':eid,'expected_halalas':balance(c,s)}
        return mutate(c,user,request,'cash.event:'+sid,body.model_dump(),perform)

@router.get('/dashboard')
def dashboard(period:str='today',start:str|None=None,end:str|None=None,brand_id:str|None=None,branch_id:str|None=None,service:str|None=None,user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager','delivery_staff','branch_manager')
    with db.tx() as c:
        brand=brand_id or user['brand_id']
        if brand!=user['brand_id']:fail(403,'براند غير مخول')
        cfg=c.execute('SELECT settings FROM brands WHERE id=%s',(brand,)).fetchone()
        if not cfg:fail(404,'البراند غير موجود')
        setting=c.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(brand,)).fetchone()
        config={**cfg['settings'],**(setting['settings'] if setting else {})}
        minutes,configured=day_start(config)
        # Delivery's day is independent of pickup/car reporting settings.
        if service=='delivery':minutes,configured=service_hours.DELIVERY_DAY_START_MINUTES,True
        now=datetime.now(RIYADH);day=(now-timedelta(minutes=minutes)).date()
        if period=='yesterday':day-=timedelta(days=1)
        try:
            lo=datetime.fromisoformat(start) if start else datetime.combine(day,datetime.min.time(),RIYADH)+timedelta(minutes=minutes)
            lo=lo.replace(tzinfo=RIYADH) if lo.tzinfo is None else lo.astimezone(RIYADH)
            hi=datetime.fromisoformat(end) if end else lo+timedelta(days=1)
            hi=hi.replace(tzinfo=RIYADH) if hi.tzinfo is None else hi.astimezone(RIYADH)
        except ValueError:fail(422,'تاريخ غير صالح')
        if hi<=lo or hi-lo>timedelta(days=93):fail(422,'الفترة يجب أن تكون بين يوم و93 يوما')
        clauses=['o.brand_id=%s','o.created_at>=%s','o.created_at<%s'];params=[brand,lo,hi]
        if user['role']!='owner':clauses.append('o.branch_id=ANY(%s)');params.append(user.get('branch_ids',[]))
        if branch_id:
            if user['role']!='owner' and branch_id not in user.get('branch_ids',[]):fail(403,'فرع غير مخول')
            clauses.append('o.branch_id=%s');params.append(branch_id)
        if service:
            if service not in ('delivery','pickup','car'):fail(422,'نوع طلب غير صالح')
            clauses.append('o.service=%s');params.append(service)
        where=' AND '.join(clauses)
        summary=c.execute("SELECT count(*) orders,count(*) FILTER(WHERE preparation_status='new') new,count(*) FILTER(WHERE completed_at IS NULL AND preparation_status NOT IN ('cancelled','rejected','new')) active,count(*) FILTER(WHERE completed_at IS NOT NULL) completed,count(*) FILTER(WHERE preparation_status IN ('cancelled','rejected')) cancelled,coalesce(sum(total_halalas) FILTER(WHERE completed_at IS NOT NULL),0) gross_completed_halalas,coalesce(sum(delivery_fee_halalas) FILTER(WHERE completed_at IS NOT NULL),0) delivery_fee_halalas FROM orders o WHERE "+where,params).fetchone()
        refs=c.execute('SELECT coalesce(sum(r.amount_halalas),0) n FROM refunds r JOIN orders o ON o.id=r.order_id WHERE r.amendment_id IS NULL AND o.completed_at IS NOT NULL AND '+where,params).fetchone()['n']
        late=config.get('late_after_minutes',60)
        if not isinstance(late,int) or not 1<=late<=1440:fail(503,'حد التأخير غير صحيح')
        summary['live_overdue']=c.execute("SELECT count(*) n FROM orders o WHERE o.service='delivery' AND o.completed_at IS NULL AND o.preparation_status NOT IN ('cancelled','rejected') AND o.created_at<now()-(%s*interval '1 minute') AND "+where,[late,*params]).fetchone()['n']
        summary['refund_halalas']=int(refs);summary['net_completed_halalas']=int(summary['gross_completed_halalas'])-int(refs)
        items=c.execute("WITH lines AS (SELECT o.id order_id,o.total_halalas,item->>'product_id' product_id,max(item->>'name') name,sum((item->>'quantity')::int) quantity,sum((item->>'line_total_halalas')::bigint) gross_halalas FROM orders o CROSS JOIN LATERAL jsonb_array_elements(o.snapshot->'items') item WHERE o.completed_at IS NOT NULL AND "+where+" GROUP BY o.id,o.total_halalas,item->>'product_id') SELECT product_id,max(name) name,sum(greatest(quantity-coalesce((SELECT sum((r.line_quantities->>lines.product_id)::int) FROM refunds r WHERE r.amendment_id IS NULL AND r.order_id=lines.order_id),0),0)) quantity,sum(gross_halalas) gross_halalas,round(sum(gross_halalas::numeric*greatest(total_halalas-coalesce((SELECT sum(r.amount_halalas) FROM refunds r WHERE r.amendment_id IS NULL AND r.order_id=lines.order_id),0),0)/nullif(total_halalas,0))) net_halalas FROM lines GROUP BY product_id ORDER BY quantity DESC LIMIT 10",params).fetchall()
        branch_top=c.execute("SELECT o.branch_id,b.branch_no,b.name,count(*) completed,coalesce(sum(o.total_halalas-coalesce((SELECT sum(r.amount_halalas) FROM refunds r WHERE r.amendment_id IS NULL AND r.order_id=o.id),0)),0) net_halalas FROM orders o JOIN branches b ON b.id=o.branch_id WHERE o.completed_at IS NOT NULL AND "+where+' GROUP BY o.branch_id,b.branch_no,b.name ORDER BY completed DESC LIMIT 20',params).fetchall()
        timing=c.execute("SELECT count(*) FILTER(WHERE t.picked_up_at IS NOT NULL AND t.delivered_at>=t.picked_up_at) valid,count(*) FILTER(WHERE t.picked_up_at IS NULL OR t.delivered_at<t.picked_up_at) excluded,avg(extract(epoch FROM(t.delivered_at-t.picked_up_at))) FILTER(WHERE t.picked_up_at IS NOT NULL AND t.delivered_at>=t.picked_up_at) average_seconds,count(*) FILTER(WHERE t.delivered_at-t.picked_up_at>(%s*interval '1 minute')) delayed FROM driver_tasks t JOIN orders o ON o.id=t.order_id WHERE t.status='delivered' AND "+where,[config.get('late_after_minutes',60),*params]).fetchone()
        timing['threshold_minutes']=late;timing['threshold_configured']='late_after_minutes' in config
        from .performance import delivery_summary
        frozen_performance=delivery_summary(c,where,params)
        timing['legacy_delayed']=timing['delayed']
        timing.update(valid=frozen_performance['valid'],excluded=frozen_performance['excluded'],
                      average_seconds=frozen_performance['average_seconds'],delayed=frozen_performance['completed_late'])
        timing['classification_basis']='frozen_province_policy'
        # Collection events use their own timestamps, independently of order cohorts and custody transfers.
        cash_where=where.replace('o.created_at>=','p.created_at>=').replace('o.created_at<','p.created_at<')
        collected=c.execute('SELECT p.method,coalesce(sum(p.amount_halalas),0) amount_halalas FROM collections p JOIN orders o ON o.id=p.order_id WHERE '+cash_where+' GROUP BY p.method',params).fetchall()
        shift_rows=c.execute("SELECT status,count(*) count,coalesce(sum((submission->>'difference_halalas')::bigint),0) difference_halalas FROM shifts WHERE brand_id=%s AND opened_at<%s AND coalesce(closed_at,%s)>=%s AND (%s OR branch_ids ?| %s) GROUP BY status",(brand,hi,hi,lo,user['role']=='owner',[branch_id] if branch_id else user.get('branch_ids',[]))).fetchall()
        return safe({'period':{'start':lo,'end':hi,'timezone':'Asia/Riyadh','operational_day_start_minutes':minutes,'operational_day_configured':configured,'warning':None if configured else 'لم يحدد بدء اليوم التشغيلي؛ الفترة الحالية يوم تقويمي بتوقيت الرياض'},'summary':summary,'top_items':items,'top_branches':branch_top,'delivery_time':timing,'delivery_performance':frozen_performance,'collections':collected,'shifts':shift_rows,'definitions_url':'/docs/metrics'})

class ManagerHandover(OperationBody):
    target_manager_shift_id:str
    reason:str=Field(min_length=1,max_length=2000)

def morning_handover_scope(c, source, target):
    if (source.get('delivery_window_snapshot') or {}).get('period') != 'morning':
        return
    if (target.get('delivery_window_snapshot') or {}).get('period') != 'morning':
        fail(409, 'المناوبة البديلة للطلبات الصباحية يجب أن تكون صباحية معتمدة.')
    from . import morning_delivery
    principal=c.execute('SELECT * FROM users WHERE id=%s', (target['user_id'],)).fetchone()
    for bid in source['branch_ids']:
        morning_delivery.authorize_order(c, principal, {'brand_id':source['brand_id'], 'branch_id':bid,
                                                       'snapshot':{'delivery_period':'morning'}})

@router.post('/shifts/{sid}/handover-manager')
def manager_handover(sid:str,body:ManagerHandover,request:Request,user=Depends(auth.current_user)):
    shift_roles(user,'shifts_handover','owner','delivery_manager')
    with db.tx() as c:
        def perform():
            ids=sorted(set([sid,body.target_manager_shift_id]));ss={i:lock_shift(c,i) for i in ids};src=ss[sid];target=ss[body.target_manager_shift_id]
            shift_access(c,user,src,capability='shifts_handover')
            if user['role']=='admin':shift_access(c,user,target,capability='shifts_handover')
            if not target or src['kind']!='manager' or target['kind']!='manager' or src['id']==target['id'] or src['status'] not in ('open','closing') or target['status']!='open' or src['brand_id']!=target['brand_id'] or not set(src['branch_ids'])<=set(target['branch_ids']):fail(409,'المناوبة البديلة غير مؤهلة')
            morning_handover_scope(c, src, target)
            if c.execute("SELECT 1 FROM manager_handovers WHERE from_shift_id=%s AND status='pending'",(sid,)).fetchone():fail(409,'يوجد تسليم مناوبة معلق')
            children=c.execute("SELECT id FROM shifts WHERE manager_shift_id=%s AND status<>'closed' ORDER BY id",(sid,)).fetchall()
            hid=uid();c.execute('INSERT INTO manager_handovers(id,from_shift_id,to_shift_id,branch_ids,driver_shift_ids,reason,requested_by) VALUES(%s,%s,%s,%s,%s,%s,%s)',(hid,sid,target['id'],db.json(src['branch_ids']),db.json([x['id'] for x in children]),body.reason,user['id']))
            c.execute("UPDATE shifts SET status='closing' WHERE id=%s",(sid,))
            db.audit(c,user['id'],'manager.handover.request','shift',sid,{'handover_id':hid,**body.model_dump()})
            return {'handover_id':hid,'status':'pending','driver_shift_ids':[x['id'] for x in children],'cash_handover_requires_separate_confirmation':True}
        return mutate(c,user,request,'manager.handover:'+sid,body.model_dump(),perform)

@router.get('/manager-handovers')
def manager_handover_list(user=Depends(auth.current_user)):
    shift_roles(user,'shifts_view','owner','delivery_manager')
    with db.tx() as c:
        rows=c.execute('SELECT h.* FROM manager_handovers h JOIN shifts s ON s.id=h.from_shift_id JOIN shifts t ON t.id=h.to_shift_id WHERE s.brand_id=%s AND (s.user_id=%s OR t.user_id=%s OR %s) ORDER BY h.created_at DESC LIMIT 100',(user['brand_id'],user['id'],user['id'],user['role']=='owner' or delegated_shift(user,'shifts_view'))).fetchall();rows=[r for r in rows if user['role']=='owner' or set(r['branch_ids'])<=set(user.get('branch_ids',[]))];return safe({'handovers':rows})

@router.post('/manager-handovers/{hid}/accept')
def accept_manager_handover(hid:str,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager')
    with db.tx() as c:
        def perform():
            h=c.execute('SELECT * FROM manager_handovers WHERE id=%s FOR UPDATE',(hid,)).fetchone()
            if not h or h['status']!='pending':fail(404,'تسليم المناوبة غير معلق')
            ids=sorted([h['from_shift_id'],h['to_shift_id']]);ss={i:lock_shift(c,i) for i in ids};src=ss[h['from_shift_id']];target=ss[h['to_shift_id']]
            if target['user_id']!=user['id'] or target['brand_id']!=user['brand_id']:fail(403,'المستلم الحقيقي فقط يقبل المناوبة')
            shift_access(c,user,target)
            if target['status']!='open' or src['status']!='closing':fail(409,'حالة الشفت غير صالحة')
            morning_handover_scope(c, src, target)
            c.execute("UPDATE shifts SET manager_shift_id=%s WHERE id=ANY(%s) AND manager_shift_id=%s AND status<>'closed'",(target['id'],h['driver_shift_ids'],src['id']))
            c.execute("UPDATE manager_handovers SET status='accepted',accepted_by=%s,accepted_at=now() WHERE id=%s",(user['id'],hid))
            db.audit(c,user['id'],'manager.handover.accept','shift',target['id'],{'handover_id':hid,'driver_shift_ids':h['driver_shift_ids'],'cash_transferred':False})
            return {'handover_id':hid,'status':'accepted','cash_transferred':False}
        return mutate(c,user,request,'manager.handover.accept:'+hid,{},perform)


@router.get('/operations/drivers')
def driver_roster(user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager','delivery_staff')
    with db.tx() as c:
        rows=c.execute("SELECT u.id,u.display_name,u.branch_ids,u.active,u.email,u.phone,u.invitation_status,"
                       "s.id shift_id,s.status shift_status,s.available shift_available,s.manager_shift_id,"
                       "(SELECT count(*) FROM driver_tasks t WHERE t.driver_id=u.id AND t.status NOT IN ('delivered','cancelled','transferred')) active_tasks "
                       "FROM users u LEFT JOIN shifts s ON s.user_id=u.id AND s.status<>'closed' "
                       "WHERE u.brand_id=%s AND u.role='driver' ORDER BY u.display_name,u.id",(user['brand_id'],)).fetchall()
        visible=[]
        for row in rows:
            if user['role']!='owner':
                row['branch_ids']=[bid for bid in row['branch_ids'] if bid in user['branch_ids']]
                if not row['branch_ids']:continue
            context = user.get('_morning_manager_context')
            if context:
                if row.get('shift_id') and row.get('manager_shift_id') != context['shift_id']:
                    continue
                row['active_tasks'] = c.execute("SELECT count(*) AS n FROM driver_tasks t JOIN shifts s ON s.id=t.shift_id JOIN orders o ON o.id=t.order_id WHERE t.driver_id=%s AND s.manager_shift_id=%s AND o.brand_id=%s AND o.branch_id=ANY(%s) AND o.snapshot->>'delivery_period'='morning' AND t.status NOT IN ('delivered','cancelled','transferred','reassigned')", (row['id'], context['shift_id'], user['brand_id'], user['branch_ids'])).fetchone()['n']
            from .driver_readiness import describe
            row['availability'],row['readiness_action'],row['eligible_branch_ids']=describe(c,row)
            row['assignment_eligible']=row['availability']=='available'
            visible.append(row)
        return safe({'drivers':visible})


class ClosureDraft(OperationBody):
    terminal_total_halalas:StrictInt|None=Field(None,ge=0,le=MAX_HALALAS)
    model_config=ConfigDict(extra='forbid')
    counted_halalas:StrictInt=Field(ge=0,le=MAX_HALALAS)
    handover_halalas:StrictInt=Field(ge=0,le=MAX_HALALAS)
    retained_halalas:StrictInt|None=Field(None,ge=0,le=MAX_HALALAS)
    notes:str=Field('',max_length=1000)
    reference:str=Field('',max_length=200)
    recipient_user_id:str|None=Field(None,max_length=80)
    revision:StrictInt|None=Field(None,ge=1)


@router.get('/shifts/{sid}/draft')
def closure_draft(sid:str,user=Depends(auth.current_user)):
    with db.tx() as c:
        shift=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone();shift_access(c,user,shift,capability='shifts_view')
        row=c.execute('SELECT * FROM closure_drafts WHERE shift_id=%s',(sid,)).fetchone()
        return safe(blind_closure.driver_view(user,{'draft':dict(row['draft'],revision=row['revision'],updated_at=row['updated_at']) if row else None,
                     'expected_halalas':balance(c,shift),'status':shift['status']},shift_money=shift['status'] in ('submitted','closed')))


@router.post('/shifts/{sid}/draft')
def save_closure_draft(sid:str,body:ClosureDraft,request:Request,user=Depends(auth.current_user)):
    with db.tx() as c:
        shift=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone();shift_access(c,user,shift)
        if shift['user_id']!=user['id']:fail(403,'حفظ المسودة لصاحب الشفت فقط')
        if shift['status'] not in ('open','closing'):fail(409,'لا تعدل مسودة تقفيلة مرسلة أو مقفلة')
        def perform():
            current=lock_shift(c,sid);shift_access(c,user,current)
            if current['user_id']!=user['id'] or current['status'] not in ('open','closing'):fail(409,'لا تعدل مسودة تقفيلة مرسلة أو مقفلة')
            existing=c.execute('SELECT * FROM closure_drafts WHERE shift_id=%s FOR UPDATE',(sid,)).fetchone()
            if existing and body.revision!=existing['revision']:fail(409,'تغيرت مسودة التقفيلة؛ حدّثها قبل الحفظ')
            retained=body.counted_halalas-body.handover_halalas
            if retained<0 or (body.retained_halalas is not None and body.retained_halalas!=retained):fail(422,'التسليم لا يتجاوز المعدود والمحتفظ به هو الفرق بينهما')
            if current['kind']=='driver':
                from .driver_float import split
                if (body.handover_halalas,retained)!=split(current,body.counted_halalas):
                    fail(422,'التسليم يحسب بعد استبعاد العهدة الثابتة؛ لا يمكن تغييرها')
            if body.recipient_user_id:
                target=c.execute('SELECT * FROM users WHERE id=%s AND brand_id=%s AND active',(body.recipient_user_id,user['brand_id'])).fetchone()
                if not target or target['role'] not in ('owner','delivery_manager','driver','branch_manager') or target['id']==user['id']:fail(422,'مستلم غير مخول')
            draft=body.model_dump(exclude={'revision'})|{'retained_halalas':retained,'expected_halalas':balance(c,shift),
                                                         'difference_halalas':body.counted_halalas-balance(c,shift)}
            row=c.execute('INSERT INTO closure_drafts(shift_id,user_id,draft) VALUES(%s,%s,%s) '
                          'ON CONFLICT(shift_id) DO UPDATE SET draft=excluded.draft,revision=closure_drafts.revision+1,updated_at=now() '
                          'RETURNING revision,updated_at',(sid,user['id'],db.json(draft))).fetchone()
            db.audit(c,user['id'],'shift.closure_draft.saved','shift',sid,{'revision':row['revision']})
            return {'draft':dict(draft,**row),'expected_halalas':draft['expected_halalas'],'status':shift['status']}
        return mutate(c,user,request,'shift.draft:'+sid,body.model_dump(),perform)


class AvailabilityUpdate(OperationBody):
    model_config=ConfigDict(extra='forbid')
    available:bool


@router.post('/shifts/{sid}/availability')
def set_shift_availability(sid:str,body:AvailabilityUpdate,request:Request,user=Depends(auth.current_user)):
    roles(user,'owner','delivery_manager')
    with db.tx() as c:
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(sid,)).fetchone();shift_access(c,user,s)
        if s['kind']!='driver' or s['status']!='open':fail(409,'التوفر يخص شفت سائق مفتوح فقط')
        def perform():
            current=lock_shift(c,sid);shift_access(c,user,current)
            if current['kind']!='driver' or current['status']!='open':fail(409,'التوفر يخص شفت سائق مفتوح فقط')
            from .fuel_exceptions import readiness_guard
            if body.available:readiness_guard(current)
            from .driver_breaks import guard as break_guard
            break_guard(c,sid)
            c.execute('UPDATE shifts SET available=%s WHERE id=%s',(body.available,sid))
            db.audit(c,user['id'],'shift.availability','shift',sid,{'available':body.available})
            return {'shift_id':sid,'available':body.available}
        return mutate(c,user,request,'shift.availability:'+sid,body.model_dump(),perform)
