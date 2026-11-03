"""Readable download names from the report date, never the download clock."""
from datetime import date
import re
from urllib.parse import quote

WEEKDAYS = ('الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد')
ASCII_WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')


def dated_filename(day, *, kind='تشغيلي', extension='pdf'):
    try:
        value = date.fromisoformat(str(day)[:10])
    except (TypeError, ValueError):
        # A legacy record with no date must never acquire today's date.
        return f'{kind} تاريخ غير مسجل.{extension}'
    return f'{kind} {WEEKDAYS[value.weekday()]} {value.day}-{value.month}-{value.year}.{extension}'


def closure_filename(snapshot, extension='pdf'):
    shift = snapshot.get('shift') or {}
    day = (snapshot.get('operational_date') or shift.get('reporting_operational_date')
           or shift.get('delivery_operational_date'))
    kind = 'تقفيلة السائق' if (snapshot.get('kind') or shift.get('kind')) == 'driver' else 'تشغيلي'
    return dated_filename(day, kind=kind, extension=extension)


def disposition(filename):
    # ASCII fallback plus RFC 5987 UTF-8 works across browser download/share UI.
    extension = 'csv' if filename.endswith('.csv') else 'pdf'
    fallback = 'driver-closure' if filename.startswith('تقفيلة السائق') else 'operational'
    stamp = re.search(r'(\d{1,2})-(\d{1,2})-(\d{4})\.', filename)
    if stamp:
        day, month, year = map(int, stamp.groups())
        value = date(year, month, day)
        fallback += f'-{ASCII_WEEKDAYS[value.weekday()]}-{day}-{month}-{year}'
    else:
        fallback += '-date-unrecorded'
    return f'attachment; filename="{fallback}.{extension}"; filename*=UTF-8\'\'' + quote(filename, safe='')
