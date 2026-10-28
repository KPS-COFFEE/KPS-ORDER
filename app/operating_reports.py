"""Read-only operational summaries; accounting status comes from its canonical ledger.

These are presentations, never new closure snapshots or accounting approvals.
"""
from collections import defaultdict
from datetime import date, datetime
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Response
from . import auth, db, multibrand, operations as ops, service_hours

router = APIRouter(prefix='/api/operating-reports', tags=['operating-reports'])
TZ = ZoneInfo('Asia/Riyadh')
DAYS = ('الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد')
BRANDS = {'kps': 'كبز', 'enbishan': 'عنبيشن'}


def filename(day, scope):
    day = date.fromisoformat(str(day))
    return f"تشغيلي {DAYS[day.weekday()]} {day:%d-%m-%Y}.pdf"


def disposition(name):
    return 'attachment; filename="operating-report.pdf"; filename*=UTF-8\'\'' + quote(name, safe='')


def allowed_manager(user, shift, scope):
    if shift['brand_id'] != user['brand_id']:
        return False
    if not ops.unrestricted_owner(user) and (not shift['branch_ids'] or not set(shift['branch_ids']) <= set(user.get('branch_ids', []))):
        return False
    if scope == 'shift' and user['role'] != 'owner' and shift['user_id'] != user['id']:
        return False
    from .morning_manager_context import shift_allowed
    return shift_allowed(user, shift)


def progress(entries, approvals, managers, pending_managers=0):
    """Balance + cash + explicit variance review; final day approval is separate.

100% means the actual day is approved, not simply that the papers were reviewed.
"""
    steps = done = overdue = late = 0
    for entry in entries:
        variance = bool(entry.get('reconciliation_complete') and entry.get('net_difference_halalas'))
        steps += 2 + int(variance)
        done += int(bool(entry.get('balance_approved'))) + int(bool(entry.get('cash_recorded')))
        done += int(variance and bool(entry.get('variance_confirmation')))
        complete = entry.get('reconciliation_complete') and not entry.get('variance_pending')
        if entry.get('overdue'):
            if complete:
                late += 1
            else:
                overdue += 1
    steps += len(managers)
    approved_ids = {a['manager_shift_id'] for a in approvals}
    done += len(set(managers) & approved_ids)
    complete = bool(managers) and not pending_managers and set(managers) <= approved_ids and done == steps
    return dict(percent=100 if complete else min(99, int(done * 100 / steps)) if steps else 0,
                completed_steps=done, total_steps=steps, overdue=overdue, late=late,
                approved=complete, approvals=approvals if complete else [])


def snapshot(c, shift):
    saved = c.execute('SELECT id,snapshot FROM closure_reports WHERE shift_id=%s AND brand_id=%s ORDER BY version DESC,created_at DESC LIMIT 1',
                      (shift['id'], shift['brand_id'])).fetchone()
    if saved:
        return saved['snapshot'], saved['id']
    if shift['status'] == 'closed':
        # A missing immutable snapshot is missing evidence, never permission to
        # reconstruct historical sales from today's mutable orders.
        return dict(shift=shift,orders=[],refunds=[],external_enbishan=[],evidence_missing=True), None
    # Active/submitted shifts use the same effective attribution views. A GET
    # never creates a closure and never writes a provisional accounting record.
    orders = c.execute('SELECT DISTINCT ON (o.id) o.*,t.status task_status,t.delivered_at task_delivered_at FROM effective_driver_tasks t JOIN orders o ON o.id=t.order_id '
                       "WHERE t.shift_id=%s AND t.status NOT IN ('transferred','reassigned') ORDER BY o.id,t.created_at DESC,t.id DESC",
                       (shift['id'],)).fetchall()
    attested = {r['order_id'] for r in c.execute('SELECT order_id FROM legacy_delivery_attestations WHERE shift_id=%s UNION SELECT order_id FROM legacy_invoice_imports WHERE ledger_shift_id=%s', (shift['id'],shift['id'])).fetchall()}
    for order in orders:
        order['fulfilled_in_report'] = order['task_status'] == 'delivered' and bool(order.get('task_delivered_at') or order['id'] in attested) and not order.get('test_void')
    refunds = c.execute('SELECT * FROM refunds WHERE shift_id=%s', (shift['id'],)).fetchall()
    collections = c.execute('SELECT * FROM effective_collections WHERE shift_id=%s', (shift['id'],)).fetchall()
    return dict(shift=shift, orders=orders, collections=collections, refunds=refunds, external_enbishan=[shift['external_enbishan']] if shift.get('external_enbishan') else []), None


def project_driver(shift, snap):
    """No repricing, no double deduction of complimentary goods or refunds."""
    brand = 'enbishan' if shift['brand_id'] == 'enbishan' else 'kps'
    goods = {key: dict(count=0, amount=0) for key in BRANDS}
    cases = []
    refunds = [r for r in snap.get('refunds', []) if r.get('record_kind') != 'test_void']
    seen = set()
    for order in snap.get('orders', []):
        if order['id'] in seen:
            continue
        seen.add(order['id'])
        free = (order.get('snapshot') or {}).get('complimentary') or {}
        complete = order.get('fulfilled_in_report', False)
        if complete:
            goods[brand]['count'] += 1
            goods[brand]['amount'] += order.get('total_halalas', 0)
        if complete and free:
            from .complimentary import accounting_values
            values = accounting_values(order)
            cases.append(dict(brand=brand, kind=values['kind'], amount=values['amount'] if type(values['amount']) is int else None,
                              reference=str(order.get('order_no') or order['id']), reason=values['reason'],
                              approval='معتمدة تشغيليًا' if values.get('authorized_by') else 'اعتماد غير موثق'))
        elif order.get('delivery_status') == 'cancelled' or order.get('preparation_status') in ('cancelled', 'rejected'):
            cases.append(dict(brand=brand, kind='إلغاء', amount=order.get('total_halalas'),
                              reference=str(order.get('order_no') or order['id']), reason='راجع سجل الطلب', approval='مسجل'))
    for refund in refunds:
        cases.append(dict(brand=brand, kind='مرتجع', amount=refund['amount_halalas'],
                          reference=str(refund.get('order_id') or ''), reason=refund.get('reason') or 'غير مسجل', approval='مسجل'))
    for external in snap.get('external_enbishan', []):
        goods['enbishan']['count'] += external.get('order_count', 0)
        goods['enbishan']['amount'] += external.get('total_halalas', 0)
    net = sum(v['amount'] for v in goods.values()) - sum(r['amount_halalas'] for r in refunds)
    return dict(brands=goods, net=net, cases=cases)


def collection_channels(snap):
    """Recorded order collections only; never cash handover or accountant receipts."""
    if snap.get('evidence_missing') or 'collections' not in snap:
        return None, None
    eligible = {o['id'] for o in snap.get('orders', []) if o.get('fulfilled_in_report') and not o.get('test_void')}
    channels = {'card': 0, 'cash': 0}
    seen = set()
    for record in snap['collections']:
        if record.get('order_id') not in eligible or record.get('record_kind') == 'test_void':
            continue
        identity = record.get('id')
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        if record.get('method') in channels:
            channels[record['method']] += record['amount_halalas']
    for record in snap.get('refunds', []):
        if record.get('record_kind') != 'test_void' and record.get('method') in channels:
            channels[record['method']] -= record['amount_halalas']
    for external in snap.get('external_enbishan', []):
        if not external or not external.get('total_halalas'):
            continue
        if any(external.get(k) is None for k in ('card_halalas', 'cash_halalas')):
            return None, None
        channels['card'] += external['card_halalas']
        channels['cash'] += external['cash_halalas']
    return channels['card'], channels['cash']


def matching(row):
    if row.get('network') is None or row.get('cash') is None:
        return dict(shortage=None, surplus=None, status='بانتظار البيانات')
    delta = row['network'] + row['cash'] - row['net']
    return dict(shortage=max(0, -delta), surplus=max(0, delta),
                status='عجز' if delta < 0 else 'فائض' if delta > 0 else 'متوافق')


def complete_sum(rows, field):
    return sum(r[field] for r in rows) if rows and all(r.get(field) is not None for r in rows) else None


def build(c, user, day, scope, manager_shift_id=None):
    ops.roles(user, 'owner', 'delivery_manager')
    if not auth.has_permission(user, 'reports_view'):
        raise db.error(403, 'OPERATING_REPORT_FORBIDDEN', 'صلاحية التقارير غير متاحة.')
    if scope == 'day' and user.get('_morning_manager_context'):
        raise db.error(403, 'OPERATING_REPORT_MORNING', 'تقرير الصباح يخص مناوبتك فقط.')
    principals = [user]
    if multibrand.enabled() and not user.get('_morning_manager_context'):
        principals = [p for b in multibrand.available(c, user) if (p := multibrand.project(c, user, b['id'])) and auth.has_permission(p, 'reports_view')]
    selected_manager_ids = {manager_shift_id} if scope == 'shift' and manager_shift_id else None
    if selected_manager_ids and multibrand.enabled():
        # A selected component identifies the entire physical manager duty.
        # This lookup supplies identities only; every leg still passes the
        # report's date, brand, owner and current branch authorization below.
        legs = c.execute('SELECT l.shift_id FROM central_workday_legs l '
                         'JOIN central_workday_legs anchor ON anchor.workday_id=l.workday_id '
                         'WHERE anchor.shift_id=%s', (manager_shift_id,)).fetchall()
        if legs:
            selected_manager_ids = {leg['shift_id'] for leg in legs}
    managers, driver_rows, cases, entries, approvals = [], {}, [], [], []
    day_allowed = user['role'] == 'owner'
    pending = 0
    for principal in principals:
        locations = {b['id']: b for b in c.execute('SELECT id,name,city FROM branches WHERE brand_id=%s', (principal['brand_id'],)).fetchall()}
        candidates = c.execute("SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.kind='manager' AND s.brand_id=%s "
                               "AND COALESCE(s.reporting_operational_date,s.delivery_operational_date,(s.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '4 hours')::date)=%s ORDER BY s.opened_at,s.id",
                               (principal['brand_id'], day)).fetchall()
        visible = [s for s in candidates if allowed_manager(principal, s, scope)
                   and (selected_manager_ids is None or s['id'] in selected_manager_ids)]
        day_allowed |= any(s['user_id'] == user['id'] and (s.get('delivery_window_snapshot') or {}).get('period', 'evening') == 'evening' for s in visible)
        for manager in visible:
            if principal.get('_morning_manager_context'):
                ops.shift_access(c,principal,manager,capability='reports_view')
            managers.append(manager)
            drivers = c.execute("SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.manager_shift_id=%s AND s.kind='driver' ORDER BY s.opened_at,s.id", (manager['id'],)).fetchall()
            # Never emit a partially visible physical duty as a complete report.
            if any(s['brand_id'] != principal['brand_id'] or (not ops.unrestricted_owner(principal) and not set(s['branch_ids']) <= set(principal.get('branch_ids', []))) for s in drivers):
                raise db.error(403, 'OPERATING_REPORT_SCOPE', 'توجد تقفيلة خارج نطاق التقرير المصرح.')
            for shift in drivers:
                snap, rid = snapshot(c, shift)
                data = project_driver(shift, snap)
                period = (manager.get('delivery_window_snapshot') or {}).get('period', 'evening')
                key = (shift['user_id'], period)
                row = driver_rows.setdefault(key, dict(name=shift['display_name'], period=period, brands={k: dict(count=0, amount=0) for k in BRANDS}, net=0, closed=True,evidence_missing=False,network=0,cash=0,cities=set(),branches=set()))
                for brand, item in data['brands'].items():
                    for field in ('count', 'amount'):
                        row['brands'][brand][field] += item[field]
                row['net'] += data['net']
                network, cash = collection_channels(snap)
                row['network'] = row['network'] + network if row['network'] is not None and network is not None else None
                row['cash'] = row['cash'] + cash if row['cash'] is not None and cash is not None else None
                branch_ids = {o['branch_id'] for o in snap.get('orders', []) if o.get('fulfilled_in_report') and o.get('branch_id')}
                if not branch_ids:
                    branch_ids = set(shift.get('branch_ids', []))
                for bid in branch_ids:
                    if bid in locations:
                        row['cities'].add(locations[bid]['city'])
                        row['branches'].add(locations[bid]['name'])
                # Physical staffing can end before accounting closes the
                # component ledger (vault bags and approved remote duties).
                # The guarded marker is operational evidence, never approval.
                row['closed'] &= not snap.get('evidence_missing') and (
                    shift.get('staffing_ended_at') is not None
                    or shift['status'] == 'closed' and rid is not None)
                row['evidence_missing'] |= bool(snap.get('evidence_missing'))
                for case in data['cases']:
                    cases.append(dict(case, driver=shift['display_name']))
                if not rid:
                    entries.append({})
            if manager['status'] != 'closed' and manager.get('staffing_ended_at') is None:
                pending += 1
        if visible:
            # Reuse canonical accounting deadlines, extensions and completion;
            # expose status only, not the underlying accounting money/notes.
            from .accounting_statements import manager_cards
            bids = sorted({b for m in visible for b in m['branch_ids']})
            ids = {m['id'] for m in visible}
            cards = manager_cards(c, principal, bids, manager_shift_ids=list(ids))['cards']
            for card in cards:
                if card['id'] in ids:
                    entries.extend(card['drivers'])
            approvals.extend(c.execute('SELECT a.id,a.manager_shift_id,a.created_at,u.display_name approved_name FROM accounting_daily_closures a JOIN users u ON u.id=a.approved_by WHERE a.manager_shift_id=ANY(%s) AND a.brand_id=%s ORDER BY a.created_at,a.id', (list(ids), principal['brand_id'])).fetchall())
            # Closed driver reports whose manager is still open have not yet
            # entered accounting's canonical queue. They remain pending.
            card_ids = {r['id'] for g in cards if g['id'] in ids for r in g['drivers']}
            for manager in visible:
                for r in c.execute("SELECT r.id FROM closure_reports r JOIN shifts s ON s.id=r.shift_id WHERE s.manager_shift_id=%s AND r.kind='driver'", (manager['id'],)).fetchall():
                    if r['id'] not in card_ids:
                        entries.append({})
    if selected_manager_ids is not None and ({m['id'] for m in managers} != selected_manager_ids
                                             or manager_shift_id not in selected_manager_ids):
        raise db.error(404, 'OPERATING_REPORT_SHIFT', 'المناوبة غير متاحة ضمن نطاق التقرير المصرح.')
    if scope == 'day' and not day_allowed:
        raise db.error(403, 'OPERATING_REPORT_DAY', 'اليوم الكامل لمدير المساء صاحب المناوبة والمالك.')
    rows = sorted(driver_rows.values(), key=lambda r: (r['period'] != 'morning', r['name']))
    for row in rows:
        row['city'] = ' / '.join(sorted(row.pop('cities'))) or 'غير مسجل'
        row['branch'] = ' / '.join(sorted(row.pop('branches'))) or 'غير مسجل'
        row.update(matching(row))
    accounting = progress(entries, approvals, [m['id'] for m in managers], pending)
    return ops.safe(dict(day=day, scope=scope, can_day=day_allowed and not bool(user.get('_morning_manager_context')), filename=filename(day, scope), manager=user['display_name'],
                         generated_at=datetime.now(TZ), rows=rows, cases=cases, accounting=accounting,
                         operational_complete=bool(managers) and not pending and all(r['closed'] for r in rows),evidence_missing=any(r['evidence_missing'] for r in rows),
                         count=sum(v['count'] for r in rows for v in r['brands'].values()), net=sum(r['net'] for r in rows)))


@router.get('')
def listing(day: date | None = None, scope: Literal['shift', 'day'] = 'shift', manager_shift_id: str | None = None, user=Depends(auth.current_user)):
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        return build(c, user, day or service_hours.delivery_operational_date(), scope, manager_shift_id)


def money(value):
    return '—' if value is None else f'{value / 100:,.2f}'.rstrip('0').rstrip('.')


def report_tables(report):
    headers = ['السائق', 'الوردية', 'المدينة / المحافظة', 'الفرع', 'كبز: عدد', 'كبز: قيمة', 'عنبيشن: عدد', 'عنبيشن: قيمة', 'قيمة الطلبات', 'شبكة', 'كاش', 'العجز', 'الفائض', 'المطابقة', 'التقفيلة التشغيلية']
    rows = [[r['name'], 'صباحي' if r['period'] == 'morning' else 'مسائي', r.get('city') or 'غير مسجل', r.get('branch') or 'غير مسجل',
             r['brands']['kps']['count'], money(r['brands']['kps']['amount']), r['brands']['enbishan']['count'], money(r['brands']['enbishan']['amount']), money(r['net']),
             money(r.get('network')), money(r.get('cash')), money(matching(r)['shortage']), money(matching(r)['surplus']), matching(r)['status'],
             'لقطة مفقودة' if r.get('evidence_missing') else 'مكتملة' if r['closed'] else 'معلّقة'] for r in report['rows']]
    projected = [dict(r, **matching(r)) for r in report['rows']]
    totals = ['الإجمالي', '', '', '', *[sum(r['brands'][b]['count'] for r in report['rows']) if k == 'count' else money(sum(r['brands'][b]['amount'] for r in report['rows'])) for b in BRANDS for k in ('count', 'amount')], money(report['net']),
              *[money(complete_sum(projected, k)) for k in ('network', 'cash', 'shortage', 'surplus')], '', '']
    cases = [[r['driver'], BRANDS.get(r['brand'], r['brand']), r['kind'], money(r['amount']), r['reference'], r['reason'], r['approval']] for r in report['cases']]
    return headers, rows, totals, cases


def city_tables(report):
    groups = defaultdict(list)
    for row in report['rows']:
        groups[row.get('city') or 'غير مسجل'].append(dict(row, **matching(row)))
    headers = ['المدينة / المحافظة', 'الطلبات', 'قيمة الطلبات', 'شبكة', 'كاش', 'العجز', 'الفائض']
    rows = [[city, sum(v['count'] for r in items for v in r['brands'].values()), money(sum(r['net'] for r in items)),
             *[money(complete_sum(items, field)) for field in ('network', 'cash', 'shortage', 'surplus')]] for city,items in sorted(groups.items())]
    return headers, rows


def accounting_lines(report):
    a = report['accounting']
    lines = [f"اكتمال المراجعة المحاسبية: {a['percent']}٪ ({a['completed_steps']} من {a['total_steps']} خطوة)"]
    if a['overdue']:
        lines.append(f"متأخر: {a['overdue']} تقفيلة تحتاج متابعة")
    if a['late']:
        lines.append(f"سجل التأخير: {a['late']} تقفيلة أُنجزت بعد الموعد")
    if a['approved']:
        for approval in a['approvals']:
            at = datetime.fromisoformat(approval['created_at']).astimezone(TZ)
            lines.append(f"معتمد محاسبيًا | المعتمد: {approval['approved_name']} | {at:%d-%m-%Y %H:%M} | {approval['id']}")
    else:
        lines.append('قيد المراجعة المحاسبية — الاعتماد النهائي لم يكتمل')
    return lines


def make_html(report):
    e = lambda value: escape(str(value))
    headers, rows, totals, cases = report_tables(report)
    def table(head, data, total=None):
        return '<table><thead><tr>'+''.join('<th>'+e(x)+'</th>' for x in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+e(x)+'</td>' for x in r)+'</tr>' for r in data)+'</tbody>'+('<tfoot><tr>'+''.join('<td>'+e(x)+'</td>' for x in total)+'</tr></tfoot>' if total else '')+'</table>'
    body = '<div class="or-print-logos"><img src="/assets/operating-report/kps.jpeg" alt="كبز"><img src="/assets/operating-report/enbishan.jpeg" alt="عنبيشن"></div><h1>'+('تقرير اليوم التشغيلي' if report['scope'] == 'day' else 'تقرير شفتي')+'</h1><p>'+e(report['day'])+' · '+e(report['manager'])+'</p>'
    body += '<p>'+str(report['count'])+' طلب · صافي المبيعات '+money(report['net'])+' ر.س</p>'+table(headers, rows, totals)
    city_head, city_rows = city_tables(report)
    body += '<h2>ملخص المدن / المحافظات</h2>'+table(city_head, city_rows)
    body += '<p>المطابقة: قيمة الطلبات = شبكة + كاش. النقص عجز والزيادة فائض. — بيانات غير متاحة.</p>'
    if cases:
        body += '<h2>الضيافة والتعويضات والحالات</h2>'+table(['السائق', 'البراند', 'النوع', 'القيمة المرجعية', 'المرجع', 'السبب', 'الحالة'], cases)
    else:
        body += '<p>لا توجد ضيافة أو تعويضات أو حالات مسجلة.</p>'
    body += '<section class="accounting '+('approved' if report['accounting']['approved'] else '')+'"><h2>متابعة المراجعة المحاسبية</h2><progress max="100" value="'+str(report['accounting']['percent'])+'"></progress>'+''.join('<p>'+e(line)+'</p>' for line in accounting_lines(report))+'</section>'
    body += '<p class="note">تقرير تشغيلي. قيم الضيافة والتعويضات مرجعية ولا تخصم مرة أخرى من التحصيل. لا يشمل تأكيد إيداع البنك إلا وفق سجل المحاسبة.</p>'
    return '<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+e(report['filename'][:-4])+'</title><link rel="stylesheet" href="/assets/operating-reports.css"><script src="/report.js" defer></script><main><nav class="screen-only"><button data-print-report>طباعة</button></nav>'+body+'</main></html>'


def make_pdf(report, paper='A3'):
    """Readable landscape report, paginating all evidence at a fixed font size."""
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A4, A3, landscape
    from reportlab.lib import colors
    import arabic_reshaper
    from bidi.algorithm import get_display
    font = 'OperatingKufi'
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font, str(Path(__file__).resolve().parents[1] / 'public/assets/fonts/NotoKufiArabic-Regular.ttf')))
    def visual(text):
        return get_display(arabic_reshaper.reshape(str(text)))
    head, rows, total, cases = report_tables(report)
    lines = accounting_lines(report)
    city_head, city_rows = city_tables(report)
    size = landscape(A4 if paper == 'A4' else A3)
    width, height = size
    out = BytesIO()
    class NumberedCanvas(canvas.Canvas):
        # Replay finished pages after their total is known, so every footer is
        # accurate without estimating content height or rebuilding the report.
        def showPage(self):
            self.saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            pages = self.saved_pages
            for state in pages:
                self.__dict__.update(state)
                text('تقرير تشغيلي · قيم الحالات مرجعية · جميع المبالغ بالريال السعودي', width-margin, margin+10, 8)
                text(report['filename'], width-margin, margin-2, 7)
                text(f'صفحة {self._pageNumber} من {len(pages)}', margin+100, margin+10, 8)
                super().showPage()
            super().save()

    cv = NumberedCanvas(out, pagesize=size, invariant=1)
    cv.saved_pages = []
    cv.setTitle(report['filename'][:-4])
    margin = 22
    usable = width - 2 * margin
    y = height - margin
    def text(value, right, baseline, pts=9, color='#232d34'):
        cv.setFillColor(colors.HexColor(color)); cv.setFont(font, pts)
        cv.drawRightString(right, baseline, visual(value))
    # Embed the owner-supplied files without tracing, recolouring or stretching.
    logo_root = Path(__file__).resolve().parents[1] / 'public/assets/operating-report'
    for i, brand in enumerate(BRANDS):
        cv.drawImage(str(logo_root / (brand + '.jpeg')), margin + i*49, y-44,
                     width=43, height=44, preserveAspectRatio=True, anchor='c')
    text('التقرير التشغيلي', width-margin, y-12, 17)
    y -= 34
    operating_day = date.fromisoformat(str(report['day']))
    text(f"{DAYS[operating_day.weekday()]}، {operating_day:%d-%m-%Y} · {report['manager']}", width-margin, y, 10)
    text("اليوم التشغيلي · الصباحي والمسائي" if report["scope"] == "day" else "شفتي", width-margin, y-14, 8, "#687078")
    y -= 32
    for i, (value, label) in enumerate([(str(report['count']), 'طلب'),
                                      (str(len(report['rows'])), 'عدد السائقين'),
                                      (money(report['net']) + ' ر.س', 'صافي الطلبات')]):
        cell = usable / 3
        right = width-margin-i*cell
        cv.setFillColor(colors.HexColor('#fafafa'))
        cv.setStrokeColor(colors.HexColor('#e0e3e6'))
        cv.rect(right-cell, y-43, cell, 43, stroke=1, fill=1)
        text(value, right-12, y-18, 13)
        text(label, right-12, y-34, 8, '#687078')
    y -= 58
    # Wrap logical Arabic before shaping. Measure all rows before drawing.
    def wrapped(value, max_width, pts):
        result, line = [], ''
        for word in str(value).split():
            if pdfmetrics.stringWidth(visual((line+' '+word).strip()), font, pts) > max_width and line:
                result.append(line); line = ''
            for char in word:
                candidate = line + char
                if pdfmetrics.stringWidth(visual(candidate), font, pts) > max_width and line:
                    result.append(line); line = ''
                line += char
            line += ' '
        return result + [line.strip()]
    def layout(data, widths, pts):
        return [(cells, max(len(x) for x in cells)*(pts+3)+8) for cells in [[wrapped(v, w-8, pts) for v,w in zip(row,widths)] for row in data]]
    weights = [100,51,89,70,35,62,35,62,77,63,63,51,51,86,83]
    widths = [usable*x/sum(weights) for x in weights]
    case_widths = [usable*x for x in (.15,.07,.12,.10,.12,.29,.15)]
    city_widths = [usable*x for x in (.25,.10,.17,.12,.12,.12,.12)]
    pts = 10
    leading = pts + 3
    bottom = margin + 34  # Reserve the footer on every page.
    main_layout = layout([head, *rows, total], widths, pts)
    case_layout = layout([['السائق','البراند','النوع','القيمة المرجعية','المرجع','السبب','الحالة'], *cases], case_widths, pts) if cases else []
    city_layout = layout([city_head, *city_rows], city_widths, pts)

    def new_page():
        nonlocal y
        cv.showPage()
        y = height - margin
        text('التقرير التشغيلي · ' + str(report['day']), width-margin, y-12, 11)
        y -= 32

    def ensure(space):
        if y - space < bottom:
            new_page()

    def draw_row(cells, cols, dark=False, shaded=False):
        nonlocal y
        rh = max(len(cell) for cell in cells)*leading+8
        cv.setFillColor(colors.HexColor('#232d34' if dark else '#f4f7f9' if shaded else '#ffffff'))
        cv.rect(margin, y-rh, usable, rh, stroke=0, fill=1)
        x = width-margin
        for col, cell in zip(cols, cells):
            for j,line in enumerate(cell):
                text(line, x-4, y-pts-4-j*leading, pts, '#ffffff' if dark else '#232d34')
            cv.setStrokeColor(colors.HexColor('#cfd6de'))
            cv.rect(x-col,y-rh,col,rh,stroke=1,fill=0)
            x -= col
        y -= rh

    def draw_table(data, cols, total_row=False, heading=None, grouped=False):
        nonlocal y
        header, header_height = data[0]
        def table_header():
            nonlocal y
            if heading:
                text(heading,width-margin,y-11,10)
                y -= 25
            if grouped:
                draw_groups()
            draw_row(header, cols, dark=True)
        header_space = header_height + (25 if heading else 0) + (group_height if grouped else 0)
        # Keep a section title and headings with at least one body line.
        ensure(header_space + leading + 8)
        table_header()
        for index,(cells, rh) in enumerate(data[1:], 1):
            remaining = [list(cell) for cell in cells]
            # Prefer an intact row. Only rows taller than a whole fresh page
            # are split; every cell's wrapped lines are retained in sequence.
            fresh_capacity = height-margin-32-bottom-header_space
            if y-rh < bottom and rh <= fresh_capacity:
                new_page()
                table_header()
            while any(remaining):
                capacity = int((y-bottom-8)//leading)
                if capacity < 1:
                    new_page()
                    table_header()
                    continue
                fragment = [cell[:capacity] for cell in remaining]
                draw_row(fragment, cols, dark=total_row and index == len(data)-1, shaded=index%2 == 0)
                remaining = [cell[capacity:] for cell in remaining]
                if any(remaining):
                    new_page()
                    table_header()
        y -= 12
    # Grouped brand headings preserve the two-brand comparison on paper.
    x=width-margin
    groups = [(head[i], widths[i], '#edf2f6') for i in range(4)]
    groups += [('كبز', sum(widths[4:6]), '#fff0e3'), ('عنبيشن', sum(widths[6:8]), '#e6f7f7')]
    groups += [(head[i], widths[i], '#edf2f6') for i in range(8, len(head))]
    group_height = max(len(wrapped(label, span-8, pts)) for label,span,_ in groups)*(pts+3)+8
    def draw_groups():
        nonlocal y
        x = width-margin
        for label,span,fill in groups:
            cv.setFillColor(colors.HexColor(fill))
            cv.rect(x-span,y-group_height,span,group_height,stroke=0,fill=1)
            for j,line in enumerate(wrapped(label,span-8,pts)):
                text(line,x-4,y-pts-4-j*leading,pts)
            x-=span
        y-=group_height
    main_layout[0]=layout([['','','','','عدد','قيمة','عدد','قيمة','','','','','','','']],widths,pts)[0]
    draw_table(main_layout,widths,True,grouped=True)
    draw_table(city_layout,city_widths,heading='ملخص المدن / المحافظات')
    if cases:
        draw_table(case_layout,case_widths,heading='حالات التشغيل')
    else:
        ensure(20)
        text('لا توجد ضيافة أو تعويضات أو حالات مسجلة.',width-margin,y-10,9);y-=20
    approved = report['accounting']['approved']
    normal_lines=[line for line in lines if not line.startswith('معتمد محاسبيًا')]
    approval_lines=[line for line in lines if line.startswith('معتمد محاسبيًا')]
    approval_parts = [part for line in approval_lines for part in wrapped(line.removeprefix('معتمد محاسبيًا | '),usable-18,9)]
    accounting_height = 40 + sum(len(wrapped(line,usable-18,9))*17 for line in normal_lines)
    if approved:
        accounting_height += 48 + len(approval_parts)*17
    # Keep ordinary review details and their stamp together. Exceptionally
    # large approval evidence is safely split below.
    ensure(min(accounting_height,height-margin-32-bottom))
    cv.setFillColor(colors.HexColor('#e4e9ed'));cv.roundRect(margin,y-8,usable,7,3,stroke=0,fill=1)
    if report['accounting']['percent']:
        cv.setFillColor(colors.HexColor('#08733a' if approved else '#e78216'))
        cv.roundRect(width-margin-usable*report['accounting']['percent']/100,y-8,usable*report['accounting']['percent']/100,7,3,stroke=0,fill=1)
    y-=23
    text('المطابقة: قيمة الطلبات = شبكة + كاش · النقص عجز والزيادة فائض · — بيانات غير متاحة',width-margin,y,9);y-=17
    for line in normal_lines:
        for part in wrapped(line,usable-18,9):
            ensure(17)
            text(part,width-margin,y-10,9,'#08733a' if approved else '#9b4d00');y-=17
    if approved:
        # Long approver names/IDs and many approvals can span pages. Each
        # chunk retains an approval border/title and never covers the footer.
        while approval_parts:
            ensure(64)
            capacity = max(1,int((y-bottom-42)//17))
            chunk, approval_parts = approval_parts[:capacity], approval_parts[capacity:]
            stamp_height = 36+len(chunk)*17
            cv.setStrokeColor(colors.HexColor('#08733a'));cv.setLineWidth(1.5)
            cv.roundRect(margin,y-stamp_height,usable,stamp_height,5,stroke=1,fill=0)
            text('معتمد محاسبيًا',width-margin-9,y-18,12,'#08733a')
            for i,part in enumerate(chunk):
                text(part,width-margin-9,y-36-i*17,9,'#08733a')
            y-=stamp_height+12
            if approval_parts:
                new_page()
    cv.showPage();cv.save()
    return out.getvalue()


@router.get('/export/{format}')
def export(format: Literal['pdf', 'print'], day: date | None = None, scope: Literal['shift', 'day'] = 'shift', manager_shift_id: str | None = None, user=Depends(auth.current_user)):
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        report = build(c,user,day or service_hours.delivery_operational_date(),scope,manager_shift_id)
    headers = {'Cache-Control': 'private, no-store'}
    if format == 'pdf':
        headers['Content-Disposition'] = disposition(report['filename'])
    return Response(make_pdf(report) if format == 'pdf' else make_html(report),
                    media_type='application/pdf' if format == 'pdf' else 'text/html', headers=headers)
