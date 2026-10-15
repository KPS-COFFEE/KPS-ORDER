"""Accountant-certified standing cash: immutable decisions, frozen per shift.

Approval certifies actual cash held, not a payment instruction. It is allowed
between shifts only. Reusing an approval consumes the existing carried cash,
never issues the float again. A shortage never reduces the approved entitlement.
"""
from fastapi import APIRouter, Depends, Request
from pydantic import ConfigDict, Field, StrictBool, StrictInt
from . import auth, db
from .operations import OperationBody, safe, uid, MAX_HALALAS, fail

router = APIRouter(prefix='/api/driver-floats', tags=['driver-floats'])


def driver(c, user, driver_id):
    row = c.execute("SELECT * FROM users WHERE id=%s AND brand_id=%s AND role='driver'", (driver_id,user['brand_id'])).fetchone()
    if not row or (user['role']!='owner' and not set(row['branch_ids'])<=set(user.get('branch_ids',[]))):
        fail(404,'السائق خارج نطاقك')
    return row


def current(c, driver_id):
    return c.execute('SELECT r.* FROM driver_float_requests r JOIN driver_float_decisions d ON d.request_id=r.id WHERE r.driver_id=%s AND d.approved ORDER BY d.created_at DESC,r.id DESC LIMIT 1',(driver_id,)).fetchone()


def opening(c, target, carried):
    row = current(c,target['id'])
    if not row:return carried,None
    used=c.execute("SELECT 1 FROM shifts WHERE user_id=%s AND permanent_float->>'request_id'=%s",(target['id'],row['id'])).fetchone()
    return (carried if used else row['amount_halalas']), {'request_id':row['id'],'amount':row['amount_halalas']}


def split(shift, counted):
    approved=int((shift.get('permanent_float') or {}).get('amount',0))
    retained=min(approved,counted)
    return counted-retained,retained


def require_closed(c, driver_id):
    if c.execute("SELECT 1 FROM shifts WHERE user_id=%s AND status<>'closed'",(driver_id,)).fetchone():
        fail(409,'تسجيل أو تغيير العهدة الدائمة يكون بعد إقفال شفت السائق؛ التقفيلات السابقة لا تتغير')


class FloatRequest(OperationBody):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    driver_id:str
    amount_halalas:StrictInt=Field(ge=0,le=MAX_HALALAS)
    base_request_id:str|None=None
    reference:str=Field(min_length=1,max_length=200)
    reason:str=Field(min_length=1,max_length=1000)


class Decision(OperationBody):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    approved:StrictBool
    confirmed:StrictBool
    reference:str=Field(min_length=1,max_length=200)


@router.get('')
def listing(user=Depends(auth.current_user)):
    if user['role'] not in ('owner','delivery_manager') and not auth.has_permission(user,'accounting_review'):
        fail(403,'العهدة الدائمة لمدير التوصيل والمحاسبة')
    with db.tx() as c:
        drivers=c.execute("SELECT id,display_name,branch_ids FROM users WHERE brand_id=%s AND role='driver' AND active ORDER BY display_name",(user['brand_id'],)).fetchall()
        drivers=[d for d in drivers if user['role']=='owner' or set(d['branch_ids'])<=set(user.get('branch_ids',[]))]
        rows=c.execute('SELECT r.*,d.approved,d.reviewed_by,d.created_at reviewed_at FROM driver_float_requests r LEFT JOIN driver_float_decisions d ON d.request_id=r.id WHERE r.brand_id=%s ORDER BY r.created_at DESC',(user['brand_id'],)).fetchall()
        ids={d['id'] for d in drivers}
        rows=[r for r in rows if r['driver_id'] in ids and (user['role']=='owner' or set(r['branch_ids'])<=set(user.get('branch_ids',[])))]
        for d in drivers:
            approved=current(c,d['id']);d['current']= {'request_id':approved['id'],'amount':approved['amount_halalas']} if approved else None
        return safe({'drivers':drivers,'requests':rows})


@router.post('')
def propose(body:FloatRequest,request:Request,user=Depends(auth.current_user)):
    if user['role']!='delivery_manager':fail(403,'مدير التوصيل يسجل العهدة والمحاسب يعتمدها')
    with db.tx() as c:
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('operations:'+user['brand_id'],))
        target=driver(c,user,body.driver_id)
        def perform():
            require_closed(c,target['id'])
            latest=current(c,target['id'])
            if body.base_request_id!=(latest['id'] if latest else None):fail(409,'تغيرت العهدة؛ حدّث الشاشة')
            if c.execute('SELECT 1 FROM driver_float_requests r LEFT JOIN driver_float_decisions d ON d.request_id=r.id WHERE r.driver_id=%s AND d.request_id IS NULL',(target['id'],)).fetchone():fail(409,'توجد عهدة بانتظار قرار المحاسب')
            identifier=uid()
            c.execute('INSERT INTO driver_float_requests(id,brand_id,driver_id,branch_ids,base_request_id,amount_halalas,reference,reason,requested_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',(identifier,user['brand_id'],target['id'],db.json(target['branch_ids']),body.base_request_id,body.amount_halalas,body.reference,body.reason,user['id']))
            db.audit(c,user['id'],'driver_float.request','user',target['id'],{'request_id':identifier,**body.model_dump()})
            return {'id':identifier,'status':'pending'}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),'driver_float.request',body.model_dump(),perform)


@router.post('/{identifier}/decision')
def decide(identifier:str,body:Decision,request:Request,user=Depends(auth.current_user)):
    if user['role'] not in ('admin','owner') or not auth.has_permission(user,'accounting_review'):fail(403,'اعتماد العهدة من صلاحية المحاسبة')
    if not body.confirmed:fail(422,'أكد مراجعة سند العهدة والمبلغ الفعلي لدى السائق')
    with db.tx() as c:
        c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('operations:'+user['brand_id'],))
        row=c.execute('SELECT * FROM driver_float_requests WHERE id=%s AND brand_id=%s',(identifier,user['brand_id'])).fetchone()
        if not row:fail(404,'العهدة غير موجودة')
        driver(c,user,row['driver_id'])
        if user['role']!='owner' and not set(row['branch_ids'])<=set(user.get('branch_ids',[])):fail(403,'العهدة خارج نطاقك')
        if row['requested_by']==user['id']:fail(403,'لا يعتمد مسجل العهدة طلبه بنفسه')
        def perform():
            if c.execute('SELECT 1 FROM driver_float_decisions WHERE request_id=%s',(identifier,)).fetchone():fail(409,'تم اتخاذ قرار لهذه العهدة')
            if body.approved:
                require_closed(c,row['driver_id'])
                latest=current(c,row['driver_id'])
                if row['base_request_id']!=(latest['id'] if latest else None):fail(409,'تغيرت العهدة؛ يلزم طلب جديد')
            c.execute('INSERT INTO driver_float_decisions(request_id,brand_id,approved,reviewed_by,reference) VALUES(%s,%s,%s,%s,%s)',(identifier,user['brand_id'],body.approved,user['id'],body.reference))
            db.audit(c,user['id'],'driver_float.decision','user',row['driver_id'],{'request_id':identifier,**body.model_dump()})
            return {'id':identifier,'approved':body.approved}
        return db.idempotent(c,user['id'],request.headers.get('Idempotency-Key',''),'driver_float.decision:'+identifier,body.model_dump(),perform)
