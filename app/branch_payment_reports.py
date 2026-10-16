"""Branch-scoped online money movements, separate from Delivery custody.

Receipts use their confirmation time, refunds their verification time. These
cash-flow totals are not labelled fiscal sales, invoices or bank settlement.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends
from typing import Literal
from . import auth, db

router = APIRouter(prefix='/api/branch-payments')
RIYADH = ZoneInfo('Asia/Riyadh')


def access(user=Depends(auth.current_user)):
    if user['role'] not in {'owner', 'branch_manager'} and not (
            user['role'] == 'admin' and auth.has_permission(user, 'accounting_reports')):
        raise db.error(403, 'PAYMENT_REPORT_FORBIDDEN', 'صلاحية التقرير غير متاحة.')
    return user


def period(start: date | None, end: date | None):
    today = datetime.now(RIYADH).date()
    start, end = start or today, end or today
    if start > end or (end-start).days > 365:
        raise db.error(422, 'PAYMENT_REPORT_PERIOD_INVALID', 'حدد فترة صحيحة لا تتجاوز سنة.')
    return (datetime.combine(start, time.min, RIYADH),
            datetime.combine(end+timedelta(days=1), time.min, RIYADH))


@router.get('/report')
def report(branch_id: str, start: date | None=None, end: date | None=None,
           user=Depends(access), mode: Literal['test','live']='test'):
    begin, until = period(start, end)
    with db.tx() as c:
        branch = c.execute('SELECT id,name FROM branches WHERE id=%s AND brand_id=%s', (branch_id, user['brand_id'])).fetchone()
        if not branch or (user['role'] != 'owner' and branch_id not in user.get('branch_ids', [])):
            raise db.error(404, 'BRANCH_NOT_FOUND', 'الفرع خارج نطاق صلاحياتك.')
        params = (user['brand_id'], branch_id, begin, until, mode)
        receipts = c.execute("""SELECT o.service,r.seller_id,count(*) payment_count,
            sum(r.amount_halalas) received_halalas,
            coalesce(sum(r.amount_halalas) FILTER(WHERE r.disposition='review_required'),0) review_halalas
            FROM branch_online_receipts r JOIN orders o ON o.id=r.order_id
            WHERE r.brand_id=%s AND r.branch_id=%s AND r.created_at>=%s AND r.created_at<%s
            AND r.mode=%s AND o.service IN ('pickup','car') GROUP BY o.service,r.seller_id""", params).fetchall()
        refunds = c.execute("""SELECT o.service,a.seller_id,count(*) refund_count,
            sum(r.amount_halalas) refunded_halalas
            FROM branch_online_refunds r JOIN orders o ON o.id=r.order_id
            JOIN branch_payment_attempts a ON a.id=r.attempt_id
            WHERE r.brand_id=%s AND r.branch_id=%s AND r.verified_at>=%s AND r.verified_at<%s
            AND a.mode=%s AND r.status='verified' AND o.service IN ('pickup','car') GROUP BY o.service,a.seller_id""", params).fetchall()
        pending = c.execute("""SELECT o.service,a.seller_id,count(*) pending_refund_count,
            sum(r.amount_halalas) pending_refund_halalas
            FROM branch_online_refunds r JOIN orders o ON o.id=r.order_id
            JOIN branch_payment_attempts a ON a.id=r.attempt_id
            WHERE r.brand_id=%s AND r.branch_id=%s AND r.created_at>=%s AND r.created_at<%s
            AND a.mode=%s AND r.status<>'verified' AND o.service IN ('pickup','car') GROUP BY o.service,a.seller_id""", params).fetchall()
        fields = ('payment_count','received_halalas','review_halalas','refund_count','refunded_halalas',
                  'pending_refund_count','pending_refund_halalas')
        groups = {}
        for source in (receipts, refunds, pending):
            for row in source:
                key = (row['service'], row['seller_id'])
                entry = groups.setdefault(key, {'service': key[0], 'seller_id': key[1], **dict.fromkeys(fields, 0)})
                entry.update({k: int(row[k]) for k in fields if k in row})
        rows = [groups[key] for key in sorted(groups)]
        for row in rows:
            row['net_movement_halalas'] = row['received_halalas']-row['refunded_halalas']
        totals = {k: sum(row[k] for row in rows) for k in (*fields, 'net_movement_halalas')}
        return {'branch': dict(branch), 'start': begin.date().isoformat(),
                'end': (until-timedelta(days=1)).date().isoformat(), 'timezone': 'Asia/Riyadh',
                'currency': 'SAR', 'mode': mode, 'basis': 'verified_online_money_movements',
                'fiscal_issuance': 'not_certified', 'rows': rows, 'totals': totals}
