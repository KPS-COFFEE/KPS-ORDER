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
    return f"تقرير تشغيلي - {'اليوم الكامل' if scope == 'day' else 'شفتي'} - {DAYS[day.weekday()]} - {day:%d-%m-%Y}.pdf"


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
    return dict(shift=shift, orders=orders, refunds=refunds, external_enbishan=[shift['external_enbishan']] if shift.get('external_enbishan') else []), None


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


def build(c, user, day, scope):
    ops.roles(user, 'owner', 'delivery_manager')
    if not auth.has_permission(user, 'reports_view'):
        raise db.error(403, 'OPERATING_REPORT_FORBIDDEN', 'صلاحية التقارير غير متاحة.')
    if scope == 'day' and user.get('_morning_manager_context'):
        raise db.error(403, 'OPERATING_REPORT_MORNING', 'تقرير الصباح يخص مناوبتك فقط.')
    principals = [user]
    if multibrand.enabled() and not user.get('_morning_manager_context'):
        principals = [p for b in multibrand.available(c, user) if (p := multibrand.project(c, user, b['id'])) and auth.has_permission(p, 'reports_view')]
    managers, driver_rows, cases, entries, approvals = [], {}, [], [], []
    day_allowed = user['role'] == 'owner'
    pending = 0
    for principal in principals:
        candidates = c.execute("SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.kind='manager' AND s.brand_id=%s "
                               "AND COALESCE(s.reporting_operational_date,s.delivery_operational_date,(s.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '4 hours')::date)=%s ORDER BY s.opened_at,s.id",
                               (principal['brand_id'], day)).fetchall()
        visible = [s for s in candidates if allowed_manager(principal, s, scope)]
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
                row = driver_rows.setdefault(key, dict(name=shift['display_name'], period=period, brands={k: dict(count=0, amount=0) for k in BRANDS}, net=0, closed=True,evidence_missing=False))
                for brand, item in data['brands'].items():
                    for field in ('count', 'amount'):
                        row['brands'][brand][field] += item[field]
                row['net'] += data['net']
                row['closed'] &= shift['status'] == 'closed' and rid is not None
                row['evidence_missing'] |= bool(snap.get('evidence_missing'))
                for case in data['cases']:
                    cases.append(dict(case, driver=shift['display_name']))
                if not rid:
                    entries.append({})
            if manager['status'] != 'closed':
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
    if scope == 'day' and not day_allowed:
        raise db.error(403, 'OPERATING_REPORT_DAY', 'اليوم الكامل لمدير المساء صاحب المناوبة والمالك.')
    rows = sorted(driver_rows.values(), key=lambda r: (r['period'] != 'morning', r['name']))
    accounting = progress(entries, approvals, [m['id'] for m in managers], pending)
    return ops.safe(dict(day=day, scope=scope, can_day=day_allowed and not bool(user.get('_morning_manager_context')), filename=filename(day, scope), manager=user['display_name'],
                         generated_at=datetime.now(TZ), rows=rows, cases=cases, accounting=accounting,
                         operational_complete=bool(managers) and not pending and all(r['closed'] for r in rows),evidence_missing=any(r['evidence_missing'] for r in rows),
                         count=sum(v['count'] for r in rows for v in r['brands'].values()), net=sum(r['net'] for r in rows)))


@router.get('')
def listing(day: date | None = None, scope: Literal['shift', 'day'] = 'shift', user=Depends(auth.current_user)):
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        return build(c, user, day or service_hours.delivery_operational_date(), scope)


def money(value):
    return 'غير موثق' if value is None else f'{value / 100:,.2f}'.rstrip('0').rstrip('.')


def report_tables(report):
    headers = ['السائق', 'الوردية', 'كبز: عدد', 'كبز: قيمة', 'عنبيشن: عدد', 'عنبيشن: قيمة', 'الصافي', 'التقفيلة التشغيلية']
    rows = [[r['name'], 'صباحي' if r['period'] == 'morning' else 'مسائي', r['brands']['kps']['count'], money(r['brands']['kps']['amount']),
             r['brands']['enbishan']['count'], money(r['brands']['enbishan']['amount']), money(r['net']), 'لقطة مفقودة' if r.get('evidence_missing') else 'مكتملة' if r['closed'] else 'معلّقة'] for r in report['rows']]
    totals = ['الإجمالي', '', *[sum(r['brands'][b]['count'] for r in report['rows']) if k == 'count' else money(sum(r['brands'][b]['amount'] for r in report['rows'])) for b in BRANDS for k in ('count', 'amount')], money(report['net']), '']
    cases = [[r['driver'], BRANDS.get(r['brand'], r['brand']), r['kind'], money(r['amount']), r['reference'], r['reason'], r['approval']] for r in report['cases']]
    return headers, rows, totals, cases


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
    body = '<h1>'+('تقرير اليوم التشغيلي' if report['scope'] == 'day' else 'تقرير شفتي')+'</h1><p>'+e(report['day'])+' · '+e(report['manager'])+'</p>'
    body += '<p>'+str(report['count'])+' طلب · صافي المبيعات '+money(report['net'])+' ر.س</p>'+table(headers, rows, totals)
    if cases:
        body += '<h2>الضيافة والتعويضات والحالات</h2>'+table(['السائق', 'البراند', 'النوع', 'القيمة المرجعية', 'المرجع', 'السبب', 'الحالة'], cases)
    else:
        body += '<p>لا توجد ضيافة أو تعويضات أو حالات مسجلة.</p>'
    body += '<section class="accounting '+('approved' if report['accounting']['approved'] else '')+'"><h2>متابعة المراجعة المحاسبية</h2><progress max="100" value="'+str(report['accounting']['percent'])+'"></progress>'+''.join('<p>'+e(line)+'</p>' for line in accounting_lines(report))+'</section>'
    body += '<p class="note">تقرير تشغيلي. قيم الضيافة والتعويضات مرجعية ولا تخصم مرة أخرى من التحصيل. لا يشمل تأكيد إيداع البنك إلا وفق سجل المحاسبة.</p>'
    return '<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+e(report['filename'][:-4])+'</title><link rel="stylesheet" href="/assets/operating-reports.css"><script src="/report.js" defer></script><main><nav class="screen-only"><button data-print-report>طباعة</button></nav>'+body+'</main></html>'


def make_pdf(report, paper='A4'):
    """One readable landscape page; raise rather than silently drop any row.

For high-volume days choose A3 before shrinking text; no ellipsis/hidden cases.
"""
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
    size = landscape(A4 if paper == 'A4' else A3)
    width, height = size
    out = BytesIO()
    cv = canvas.Canvas(out, pagesize=size, invariant=1)
    cv.setTitle(report['filename'][:-4])
    margin = 22
    usable = width - 2 * margin
    y = height - margin
    def text(value, right, baseline, pts=9, color='#102e4d'):
        cv.setFillColor(colors.HexColor(color)); cv.setFont(font, pts)
        cv.drawRightString(right, baseline, visual(value))
    text('تقرير اليوم التشغيلي' if report['scope'] == 'day' else 'تقرير شفتي', width-margin, y-12, 17)
    y -= 34
    text(str(report['day'])+' · '+report['manager'], width-margin, y, 10)
    y -= 22
    text(f"{report['count']} طلب | صافي المبيعات {money(report['net'])} ر.س", width-margin, y, 11)
    y -= 15
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
    widths = [usable*x for x in (.18,.08,.08,.13,.08,.13,.14,.18)]
    case_widths = [usable*x for x in (.15,.07,.12,.10,.12,.29,.15)]
    pts = 9
    while True:
        main_layout = layout([head, *rows, total], widths, pts)
        case_layout = layout([['السائق','البراند','النوع','القيمة المرجعية','المرجع','السبب','الحالة'], *cases], case_widths, pts) if cases else []
        needed = sum(h for _,h in main_layout+case_layout) + (102 if cases else 96) + len(lines)*17 + (25 if report['accounting']['approved'] else 0)
        if needed <= y - margin:
            break
        if pts <= 8:
            if paper == 'A4':
                return make_pdf(report,paper='A3')
            raise db.error(422, 'OPERATING_REPORT_PRINT_CAPACITY', 'تفاصيل اليوم تتجاوز ورقة واضحة واحدة؛ يلزم تقليل تفاصيل الحالات في الملخص.')
        pts -= .5
    def draw_table(data, cols, total_row=False):
        nonlocal y
        for index,(cells, rh) in enumerate(data):
            dark = index == 0 or total_row and index == len(data)-1
            cv.setFillColor(colors.HexColor('#102e4d' if dark else '#f4f7f9' if index%2 == 0 else '#ffffff'))
            cv.rect(margin, y-rh, usable, rh, stroke=0, fill=1)
            x = width-margin
            for col, cell in zip(cols, cells):
                for j,line in enumerate(cell):
                    text(line, x-4, y-pts-4-j*(pts+3), pts, '#ffffff' if dark else '#102e4d')
                cv.setStrokeColor(colors.HexColor('#cfd6de'));cv.rect(x-col,y-rh,col,rh,stroke=1,fill=0)
                x -= col
            y -= rh
        y -= 12
    # Grouped brand headings preserve the two-brand comparison on paper.
    x=width-margin
    for label,span,fill in [('السائق',widths[0],'#edf2f6'),('الوردية',widths[1],'#edf2f6'),
                             ('كبز',sum(widths[2:4]),'#fff0e3'),('عنبيشن',sum(widths[4:6]),'#e6f7f7'),
                             ('الصافي',widths[6],'#edf2f6'),('التقفيلة التشغيلية',widths[7],'#edf2f6')]:
        cv.setFillColor(colors.HexColor(fill));cv.rect(x-span,y-21,span,21,stroke=0,fill=1)
        text(label,x-5,y-14,pts);x-=span
    y-=21
    main_layout[0]=layout([['','','عدد','قيمة','عدد','قيمة','','']],widths,pts)[0]
    draw_table(main_layout,widths,True)
    if cases:
        text('الضيافة والتعويضات والحالات',width-margin,y,10);y-=14
        draw_table(case_layout,case_widths)
    else:
        text('لا توجد ضيافة أو تعويضات أو حالات مسجلة.',width-margin,y,9);y-=20
    approved = report['accounting']['approved']
    cv.setFillColor(colors.HexColor('#e4e9ed'));cv.roundRect(margin,y-8,usable,7,3,stroke=0,fill=1)
    if report['accounting']['percent']:
        cv.setFillColor(colors.HexColor('#08733a' if approved else '#e78216'))
        cv.roundRect(width-margin-usable*report['accounting']['percent']/100,y-8,usable*report['accounting']['percent']/100,7,3,stroke=0,fill=1)
    y-=23
    normal_lines=[line for line in lines if not line.startswith('معتمد محاسبيًا')]
    approval_lines=[line for line in lines if line.startswith('معتمد محاسبيًا')]
    for line in normal_lines:
        text(line,width-margin,y,9,'#08733a' if approved else '#9b4d00');y-=17
    if approved:
        stamp_height=30+len(approval_lines)*17
        cv.setStrokeColor(colors.HexColor('#08733a'));cv.setLineWidth(1.5)
        cv.roundRect(margin,y-stamp_height+12,usable,stamp_height,5,stroke=1,fill=0)
        text('معتمد محاسبيًا',width-margin-9,y-5,12,'#08733a');y-=25
        for line in approval_lines:
            text(line.removeprefix('معتمد محاسبيًا | '),width-margin-9,y,9,'#08733a');y-=17
    text('تقرير تشغيلي · قيم الحالات مرجعية · جميع المبالغ بالريال السعودي',width-margin,margin+10,8)
    text('صفحة 1 من 1',margin+85,margin+10,8)
    cv.showPage();cv.save()
    return out.getvalue()


@router.get('/export/{format}')
def export(format: Literal['pdf', 'print'], day: date | None = None, scope: Literal['shift', 'day'] = 'shift', user=Depends(auth.current_user)):
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        report = build(c,user,day or service_hours.delivery_operational_date(),scope)
    headers = {'Cache-Control': 'private, no-store'}
    if format == 'pdf':
        headers['Content-Disposition'] = disposition(report['filename'])
    return Response(make_pdf(report) if format == 'pdf' else make_html(report),
                    media_type='application/pdf' if format == 'pdf' else 'text/html', headers=headers)
