"""Scope-preserving working-hours presentation and exports; no payroll writes."""
from __future__ import annotations

import io
from datetime import date
from html import escape
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse

from . import auth, db

router = APIRouter(tags=['driver-hours'])

# Shared column order keeps the human exports and API totals reconcilable.
COLUMNS = (
    ('name', 'السائق', 'Driver'), ('order_count', 'الطلبات', 'Orders'),
    ('trip_count', 'الرحلات', 'Trips'), ('shift_seconds', 'ساعات الشفت', 'Shift hours'),
    ('actual_seconds', 'التوصيل الفعلي من القبول إلى العودة', 'Accepted-to-return work hours'),
    ('delivery_seconds', 'رحلات الفرع إلى الفرع', 'Branch-to-branch hours'),
    ('waiting_seconds', 'ساعات الانتظار', 'Waiting hours'),
    ('break_seconds', 'ساعات الاستراحات', 'Break hours'),
    ('other_work_seconds', 'أعمال أخرى', 'Other work hours'),
    ('unclassified_seconds', 'وقت غير مصنف', 'Unclassified hours'),
    ('order_activity_seconds', 'توصيل الطلبات المكتملة', 'Completed order delivery hours'),
    ('active_delivery_seconds', 'طلبات جارٍ توصيلها', 'Active delivery hours'),
    ('non_delivery_seconds', 'باقي الدوام خارج التوصيل الجاري والمكتمل', 'Other duty time'),
    ('duty_seconds', 'مدة الدوام للمقارنة', 'Duty duration'),
    ('active_work_seconds', 'عمل جارٍ حتى العودة', 'Active accepted-to-return hours'),
    ('work_utilization_percent', 'نسبة التوصيل من الدوام %', 'Completed work / duty %'),
    ('work_clock_status', 'حالة توثيق العمل', 'Work timing evidence'),
)


def export_value(row, key, lang):
    unavailable='غير متاح' if lang=='ar' else 'Unavailable'
    value=row.get(key)
    if key=='name':return row.get('name',row.get('driver_name',''))
    if key=='work_clock_status':
        return {'recorded':('موثق','Recorded'),'in_progress':('جارٍ — غير نهائي','In progress — provisional'),
                'needs_review':('ناقص — يحتاج مراجعة','Incomplete — needs review')}.get(value,('غير متاح','Unavailable'))[lang=='en']
    if value is None or (key=='actual_seconds' and not value and row.get('work_review_required')):
        return unavailable
    return round(float(value)/3600,6) if key.endswith('_seconds') else value


def export_rows(report, lang='ar'):
    labels = [c[2] if lang == 'en' else c[1] for c in COLUMNS]
    rows = [labels]
    for driver in report['drivers']:
        rows.append([export_value(driver,k,lang) for k,_,_ in COLUMNS])
    total = dict(report['totals'])
    total['work_review_required']=bool(total.get('drivers_needing_review'))
    total['work_clock_status']='needs_review' if total['work_review_required'] else 'in_progress' if any(d.get('work_clock_status')=='in_progress' for d in report['drivers']) else 'recorded'
    rows.append(['Total' if lang == 'en' else 'الإجمالي', *[export_value(total,k,lang) for k,_,_ in COLUMNS[1:]]])
    return rows


def make_xlsx(report, lang='ar'):
    """OOXML inline strings avoid formula injection from staff display names."""
    rows = export_rows(report, lang)
    sheet_rows = []
    for index, row in enumerate(rows, 1):
        cells = []
        for col, value in enumerate(row):
            ref = f'{chr(65 + col)}{index}'
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        sheet_rows.append(f'<row r="{index}">'+''.join(cells)+'</row>')
    out = io.BytesIO()
    with ZipFile(out, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Driver hours" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0" rightToLeft="'+('1' if lang=='ar' else '0')+'"/></sheetViews><cols><col min="1" max="17" width="24" customWidth="1"/></cols><sheetData>'+''.join(sheet_rows)+'</sheetData></worksheet>')
    return out.getvalue()


def make_pdf(report, lang='ar'):
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A3, landscape
    import arabic_reshaper
    from bidi.algorithm import get_display
    font = Path(__file__).resolve().parents[1] / 'public/assets/fonts/NotoKufiArabic-Regular.ttf'
    if 'KPSHours' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('KPSHours', str(font)))
    visual = lambda value: get_display(arabic_reshaper.reshape(str(value)))
    out = io.BytesIO()
    width, height = landscape(A3)
    cv = canvas.Canvas(out, pagesize=(width, height))
    cv.setTitle('KPS ORDER - Driver work hours')
    rows = export_rows(report, lang)
    y = height - 35
    column_width = (width-50)/len(COLUMNS)
    def cell_lines(value):
        # Preserve every character, including long names without word breaks.
        # Measure the shaped line because Arabic glyph widths change by context.
        lines = []
        for paragraph in str(value).split('\n'):
            line = ''
            for word in paragraph.split():
                candidate = (line + ' ' + word) if line else word
                if line and pdfmetrics.stringWidth(visual(candidate), 'KPSHours', 8) > column_width-6:
                    lines.append(line)
                    line = word
                else:
                    line = candidate
                # A pathological unbroken identifier still cannot escape its cell.
                if pdfmetrics.stringWidth(visual(line), 'KPSHours', 8) > column_width-6:
                    chunk = ''
                    for character in line:
                        if chunk and pdfmetrics.stringWidth(visual(chunk+character), 'KPSHours', 8) > column_width-6:
                            lines.append(chunk)
                            chunk = ''
                        chunk += character
                    line = chunk
            lines.append(line)
        return lines
    def header():
        nonlocal y
        cv.setFont('KPSHours', 12)
        cv.drawRightString(width-25, y, visual('ساعات السائقين — KPS ORDER' if lang=='ar' else 'KPS ORDER — Driver work hours'))
        y -= 24
        cv.setFont('KPSHours', 8)
        cv.drawRightString(width-25, y, visual(f"{report['start']} → {report['end']} · Asia/Riyadh · {report.get('operational_day_start', '')}"))
        y -= 28
    def draw_lines(cells, first, count):
        nonlocal y
        cv.setFont('KPSHours', 8)
        for line_index in range(first, first+count):
            for index, lines in enumerate(cells):
                if line_index < len(lines):
                    cv.drawRightString(width-25-index*column_width, y, visual(lines[line_index]))
            y -= 13
        y -= 9
    header()
    column_labels = [cell_lines(value) for value in rows[0]]
    draw_lines(column_labels, 0, max(map(len, column_labels)))
    for row in rows[1:]:
        cells = [cell_lines(value) for value in row]
        line_count = max(map(len, cells))
        first = 0
        while first < line_count:
            remaining = int((y-40)//13)
            if remaining < 1 or (first == 0 and line_count <= 25 and remaining < line_count):
                cv.showPage(); y = height-35; header()
                draw_lines(column_labels, 0, max(map(len, column_labels)))
                remaining = int((y-40)//13)
            count = min(remaining, line_count-first)
            draw_lines(cells, first, count)
            first += count
    y -= 12
    note = ('العمل من قبول الطلب إلى العودة المؤكدة، دون مضاعفة التداخل. الجاري منفصل والنقص للمراجعة. لا أثر تلقائي على الرواتب.' if lang=='ar'
            else 'Work runs from acceptance to confirmed return. Overlap counts once; active work is separate. Missing timing needs review. No automatic payroll impact.')
    if y<35:
        cv.showPage(); y=height-35; header()
        draw_lines(column_labels, 0, max(map(len, column_labels)))
    cv.drawRightString(width-25, y, visual(note))
    cv.save()
    return out.getvalue()


def _report(user, start, end, driver_id, branch_id):
    from .driver_work_hours import build_report
    with db.tx() as c:
        return build_report(c, user, start.isoformat(), end.isoformat(), driver_id=driver_id, branch_id=branch_id)


@router.get('/driver-hours')
def dashboard(request: Request):
    # A normal browser navigation has no X-KPS-Context header. Authentication
    # still resolves only the work cookie; anonymous visitors see the login UI.
    try:
        user = auth.current_user(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse('/ops', status_code=303, headers={'Cache-Control':'private, no-store'})
        raise
    from .driver_work_hours import require_report_access
    require_report_access(user)
    return FileResponse(db.ROOT / 'public/driver-hours.html', headers={'Cache-Control':'private, no-store'})


@router.get('/api/driver-work-hours/export.xlsx')
def export_xlsx(start: date, end: date, driver_id: str | None = None,
                branch_id: str | None = None, lang: str = Query('ar', pattern='^(ar|en)$'),
                user=Depends(auth.current_user)):
    report = _report(user, start, end, driver_id, branch_id)
    return Response(make_xlsx(report, lang), media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':'attachment; filename="KPS-driver-hours.xlsx"', 'Cache-Control':'private, no-store'})


@router.get('/api/driver-work-hours/export.pdf')
def export_pdf(start: date, end: date, driver_id: str | None = None,
               branch_id: str | None = None, lang: str = Query('ar', pattern='^(ar|en)$'),
               user=Depends(auth.current_user)):
    report = _report(user, start, end, driver_id, branch_id)
    return Response(make_pdf(report, lang), media_type='application/pdf',
                    headers={'Content-Disposition':'attachment; filename="KPS-driver-hours.pdf"', 'Cache-Control':'private, no-store'})

