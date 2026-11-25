"""Owner-reviewed internal allocation ledger; never pays/refunds customer funds."""
from typing import Literal
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from . import auth, db
from .orders import scoped_order
from .channel_commercial import MODEL

router = APIRouter(prefix='/api/channel-settlements', tags=['internal-settlement'])
owner = auth.require_roles('owner')


def channel_order(c, oid, user, lock=False):
    order = scoped_order(c, oid, user, lock=lock)
    commercial = order['snapshot'].get('channel_commercial', {})
    if order['brand_id'] != 'kps' or commercial.get('model') != MODEL:
        raise db.error(409, 'HISTORICAL_SETTLEMENT_REVIEW', 'لا توجد سياسة قناة محفوظة لهذا الطلب؛ لا تستنتج تسوية تاريخية.')
    return order, commercial


def view(c, oid, commercial):
    rows = c.execute('SELECT * FROM channel_settlement_allocations WHERE order_id=%s ORDER BY created_at,id', (oid,)).fetchall()
    all_reversals = c.execute('SELECT id,amount_halalas,record_kind FROM refunds WHERE order_id=%s ORDER BY created_at,id', (oid,)).fetchall()
    test_void=any(r['record_kind']=='test_void' for r in all_reversals)
    refunds=[r for r in all_reversals if r['record_kind']!='test_void']
    allocated = sum(r['amount_halalas'] for r in rows if r['kind']=='refund')
    return {'commercial': commercial, 'allocations': rows, 'refunds': refunds,
        'unallocated_refund_halalas': sum(r['amount_halalas'] for r in refunds)-allocated,
        'branch_settlement_halalas': 0 if test_void else commercial['settlement_amount_halalas']-sum(r['amount_halalas'] for r in rows if r['cause']=='branch'),
        'delivery_channel_effect_halalas': -sum(r['amount_halalas'] for r in rows if r['cause']=='delivery_channel'),
        'payout_enabled': False, 'test_void_no_cash_disbursed':test_void,
        'status': 'test_void_no_cash_disbursed' if test_void else 'accounting_review_required'}


@router.get('/orders/{oid}')
def detail(oid: str, user=Depends(owner)):
    with db.tx() as c:
        order, commercial = channel_order(c, oid, user)
        return view(c, oid, commercial)


class Allocation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['refund','compensation']
    refund_id: str | None = None
    cause: Literal['branch','delivery_channel']
    amount_halalas: StrictInt = Field(gt=0, le=100_000_000)
    reason: str = Field(min_length=3, max_length=1000)


@router.post('/orders/{oid}/allocations')
def allocate(oid: str, body: Allocation, user=Depends(owner), idem: str | None=Header(None, alias='Idempotency-Key')):
    if not body.reason.strip() or (body.kind=='refund') != bool(body.refund_id):
        raise db.error(422, 'ALLOCATION_INVALID', 'حدد سبب المراجعة ومرجع الاسترداد الصحيح.')
    with db.tx() as c:
        channel_order(c, oid, user)
        def perform():
            order, commercial = channel_order(c, oid, user, lock=True)
            current = view(c, oid, commercial)
            if current['test_void_no_cash_disbursed']:
                raise db.error(409,'TEST_VOID_SETTLEMENT','الفاتورة ملغاة تجريبيًا ولا تنشئ استحقاقًا أو صرفًا.')
            if body.kind=='refund':
                refund = next((r for r in current['refunds'] if r['id']==body.refund_id), None)
                used = sum(r['amount_halalas'] for r in current['allocations'] if r['refund_id']==body.refund_id)
                if not refund or used+body.amount_halalas>refund['amount_halalas']:
                    raise db.error(409, 'REFUND_ALLOCATION_LIMIT', 'مرجع الاسترداد أو الرصيد المتاح غير صحيح.')
            else:
                used = sum(r['amount_halalas'] for r in current['allocations'] if r['kind']=='compensation')
                if used+body.amount_halalas>commercial['total_halalas']:
                    raise db.error(409, 'COMPENSATION_ALLOCATION_LIMIT', 'التعويض الداخلي يتجاوز قيمة الطلب.')
            beneficiary = commercial['settlement_beneficiary'] if body.cause=='branch' else 'reem'
            aid = db.new_id('allocation_')
            c.execute('INSERT INTO channel_settlement_allocations(id,order_id,refund_id,kind,cause,beneficiary,amount_halalas,reason,authorized_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (aid,oid,body.refund_id,body.kind,body.cause,beneficiary,body.amount_halalas,body.reason.strip(),user['id']))
            db.audit(c,user['id'],'channel_settlement.allocated','order',oid,{'allocation_id':aid,'beneficiary':beneficiary,**body.model_dump()})
            return {'allocation_id':aid,'beneficiary':beneficiary,'payout_enabled':False}
        return db.idempotent(c,user['id'],idem,'channel_settlement.allocate:'+oid,body.model_dump(),perform)
