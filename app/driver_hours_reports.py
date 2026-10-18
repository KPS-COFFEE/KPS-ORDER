"""Scope-preserving working-hours presentation and exports; no payroll writes."""
from __future__ import annotations

import io
from datetime import date
from html import escape
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse

from . import auth, db

router = APIRouter(tags=['driver-hours'])

# Shared column order keeps the human exports and API totals reconcilable.
COLUMNS = (
    ('name', 'السائق', 'Driver'), ('order_count', 'الطلبات', 'Orders'),
    ('trip_count', 'الرحلات', 'Trips'), ('shift_seconds', 'ساعات الشفت', 'Shift hours'),
    ('actual_seconds', 'ساعات العمل الفعلي', 'Actual hours'),
    ('delivery_seconds', 'ساعات التوصيل', 'Delivery hours'),
    ('waiting_seconds', 'ساعات الانتظار', 'Waiting hours'),
    ('break_seconds', 'ساعات الاستراحات', 'Break hours'),
    ('other_work_seconds', 'أعمال أخرى', 'Other work hours'),
    ('unclassified_seconds', 'وقت غير مصنف', 'Unclassified hours'),
)


def export_rows(report, lang='ar'):
    labels = [c[2] if lang == 'en' else c[1] for c in COLUMNS]
    rows = [labels]
    for driver in report['drivers']:
        rows.append([driver.get('name', driver.get('driver_name', '')) if k == 'name'
                     else round(float(driver.get(k, 0) or 0) / 3600, 6) if k.endswith('_seconds')
                     else driver.get(k, 0) for k, _, _ in COLUMNS])
    total = report['totals']
    rows.append(['Total' if lang == 'en' else 'الإجمالي', *[
        round(float(total.get(k, 0) or 0) / 3600, 6) if k.endswith('_seconds')
        else total.get(k, 0) for k, _, _ in COLUMNS[1:]]])
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
        archive.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0" rightToLeft="'+('1' if lang=='ar' else '0')+'"/></sheetViews><cols><col min="1" max="10" width="24" customWidth="1"/></cols><sheetData>'+''.join(sheet_rows)+'</sheetData></worksheet>')
    return out.getvalue()


def make_pdf(report, lang='ar'):
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A4, landscape
    import arabic_reshaper
    from bidi.algorithm import get_display
    font = Path(__file__).resolve().parents[1] / 'public/assets/fonts/NotoKufiArabic-Regular.ttf'
    if 'KPSHours' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('KPSHours', str(font)))
    visual = lambda value: get_display(arabic_reshaper.reshape(str(value)))
    out = io.BytesIO()
    width, height = landscape(A4)
    cv = canvas.Canvas(out, pagesize=(width, height))
    cv.setTitle('KPS ORDER - Driver work hours')
    rows = export_rows(report, lang)
    y = height - 35
    def header():
        nonlocal y
        cv.setFont('KPSHours', 12)
        cv.drawRightString(width-25, y, visual('ساعات السائقين — KPS ORDER' if lang=='ar' else 'KPS ORDER — Driver work hours'))
        y -= 24
        cv.setFont('KPSHours', 8)
        cv.drawRightString(width-25, y, visual(f"{report['start']} → {report['end']} · Asia/Riyadh · {report.get('operational_day_start', '')}"))
        y -= 28
    header()
    for row in rows:
        if y < 40:
            cv.showPage(); y = height-35; header()
        for index, value in enumerate(row):
            value = str(value)
            available = (width-50)/len(COLUMNS)-6
            while pdfmetrics.stringWidth(visual(value), 'KPSHours', 8) > available and len(value)>1:
                value = value[:-2]+'…'
            cv.drawRightString(width-25-index*(width-50)/len(COLUMNS), y, visual(value))
        y -= 22
    y -= 12
    note = ('الرحلات غير المؤكدة العودة مستبعدة من التوصيل المعتمد. لا أثر تلقائي على الرواتب.' if lang=='ar'
            else 'Trips awaiting return confirmation are excluded from approved delivery totals. No automatic payroll impact.')
    if y<35: cv.showPage(); y=height-35
    cv.drawRightString(width-25, y, visual(note))
    cv.save()
    return out.getvalue()


def _report(user, start, end, driver_id, branch_id):
    from .driver_work_hours import build_report
    with db.tx() as c:
        return build_report(c, user, start.isoformat(), end.isoformat(), driver_id=driver_id, branch_id=branch_id)


@router.get('/driver-hours')
def dashboard(user=Depends(auth.current_user)):
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
