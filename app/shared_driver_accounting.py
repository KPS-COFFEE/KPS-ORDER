"""Settle both seller reports from the same independently witnessed bag."""
from . import auth, db
from .accounting import (DriverBalanceApproval, DriverCashReceipt, TZ,
                         driver_settlement_scope, driver_settlement_values, save_driver_settlement)


def finalize(c, user, day, legs, declaration, receipt, allocation, count_id, *, for_review=False):
    from .cash_bags import bag_view
    bag = bag_view(c, day['id'])
    if not bag or bag['count_id'] != count_id or bag['declaration_id'] != declaration['id']:
        raise db.error(409, 'BAG_CHANGED', 'حدّث البطاقة وتحقق من أصل الجرد.')
    if user['id'] in (day['user_id'], receipt['received_by']):
        raise db.error(403, 'INDEPENDENT_REVIEW', 'يلزم معتمد مستقل عن صاحب العهدة ومستلمها.')
    if (bag['received_cash_halalas'] != receipt['received_halalas']
            or receipt['received_halalas'] != declaration['cash_halalas'] - declaration['retained_halalas']
            or bag['card_halalas'] != declaration['card_halalas']):
        raise db.error(409, 'BAG_VARIANCE', 'راجع فرق الكيس والاستلام قبل الاعتماد.')
    attributed = {r['shift_id']:r for r in allocation['legs']}
    for principal, shift in legs:
        if not all(auth.has_permission(principal, perm) for perm in ('accounting_review','accounting_reports')):
            raise db.error(403, 'REVIEW_REQUIRED', 'يلزم اعتماد المحاسبة لكل براند.')
        report = c.execute("SELECT id FROM closure_reports WHERE shift_id=%s AND kind='driver' ORDER BY version DESC LIMIT 1", (shift['id'],)).fetchone()
        if not report:
            raise db.error(409, 'REPORT_PENDING', 'تقرير السائق بانتظار الاكتمال.')
        rid = report['id']
        snapshot = driver_settlement_scope(c, principal, rid)['snapshot']
        if not for_review:
            from .order_financial_cases import guard_financial_closure
            guard_financial_closure(c, shift_id=shift['id'], brand_id=shift['brand_id'])
        entry = attributed[shift['id']]
        previous = c.execute('SELECT * FROM driver_accounting_settlements WHERE report_id=%s ORDER BY revision DESC LIMIT 1',(rid,)).fetchone()
        values = driver_settlement_values(snapshot, previous)
        cash, card = entry['received_halalas'], entry['terminal_total_halalas']
        if (values['shared_declaration_id'] != declaration['id'] or values['shared_receipt_id'] != receipt['id']
                or not values['financial_approval_allowed'] or entry.get('receipt_difference_halalas')
                or (not for_review and (cash != values['system_cash_halalas'] or card != values['system_bank_halalas']))
                or (values['balance_approved'] and (values['bank_halalas'] != card or values['cash_due_halalas'] != cash))
                or (values['cash_recorded'] and values['cash_received_halalas'] != cash)):
            raise db.error(409, 'CHANNEL_REVIEW_REQUIRED', 'راجع فرق الكاش أو الشبكة والمستندات في سجل البراند.')
        revision = previous['revision'] if previous else 0
        note = 'اعتماد تقفيلة السائق من جرد الكيس ' + bag['reference']
        if not values['balance_approved']:
            saved = save_driver_settlement(c, principal, rid, DriverBalanceApproval(
                expected_revision=revision, bank_halalas=card, cash_due_halalas=cash, note=note))
            revision = saved['revision']
        if not values['cash_recorded']:
            save_driver_settlement(c, principal, rid, DriverCashReceipt(expected_revision=revision,
                cash_received_halalas=cash, received_on=bag['counted_at'].astimezone(TZ).date(),
                receipt_reference=bag['count_reference'], note=note), receipt=True)
