"""Download/share names identify the frozen day and preserve saved PDF bytes."""
from contextlib import nullcontext
from copy import deepcopy
from urllib.parse import unquote

import pytest

from app import operating_reports, reporting
from app.report_filenames import closure_filename, dated_filename


def test_exact_requested_arabic_name_and_no_export_clock():
    assert dated_filename('2026-11-03') == 'تشغيلي الثلاثاء 3-11-2026.pdf'
    assert operating_reports.filename('2026-11-03', 'shift') == 'تشغيلي الثلاثاء 3-11-2026.pdf'
    assert dated_filename(None) == 'تشغيلي تاريخ غير مسجل.pdf'


@pytest.mark.parametrize('variant', ['morning', 'evening'])
@pytest.mark.parametrize('route', ['saved', 'original', 'formatted', 'v5'])
def test_every_closure_download_names_frozen_day_without_uuid_or_mutating_original(monkeypatch, variant, route):
    snapshot = dict(kind='manager', operational_date='2026-11-03',
                    shift=dict(kind='manager', delivery_window_snapshot={'period': variant}))
    frozen = deepcopy(snapshot)
    report = dict(snapshot=snapshot, kind='manager', pdf_status='ready', pdf_data=b'%PDF-frozen-original')
    monkeypatch.setattr(reporting.db, 'tx', lambda: nullcontext(None))
    monkeypatch.setattr(reporting, 'report', lambda *a: report)
    monkeypatch.setattr(reporting, 'corrected_snapshot', lambda c, snap: snap)
    monkeypatch.setattr(reporting, 'saved_rendering', lambda *a: (report, {'status':'ready','pdf_data':b'%PDF-frozen-v5'}, 'owner'))
    monkeypatch.setattr(reporting, 'make_v5_pdf', lambda *a: b'%PDF-formatted')
    if route == 'saved':
        response = reporting.download_pdf('opaque-report-uuid', {'role':'owner'})
        assert response.body == b'%PDF-frozen-original'
    elif route == 'original':
        response = reporting.original_pdf('opaque-report-uuid', {'role':'owner'})
        assert response.body == b'%PDF-frozen-original'
    elif route == 'formatted':
        response = reporting.formatted_download('opaque-report-uuid', 'pdf', user={'role':'owner'})
    else:
        response = reporting.v5_download('opaque-report-uuid', 'pdf', {'role':'owner'})
        assert response.body == b'%PDF-frozen-v5'
    header = response.headers['content-disposition']
    header.encode('ascii')
    assert 'filename="operational-Tuesday-3-11-2026.pdf"' in header
    assert unquote(header.split("UTF-8''")[1]) == 'تشغيلي الثلاثاء 3-11-2026.pdf'
    assert 'opaque-report-uuid' not in header
    assert snapshot == frozen


def test_driver_closure_remains_distinct_from_manager_operational_report():
    assert closure_filename({'kind':'driver','operational_date':'2026-11-03'}) == 'تقفيلة السائق الثلاثاء 3-11-2026.pdf'


def test_share_and_download_use_the_same_server_operational_filename():
    from pathlib import Path
    script = (Path(__file__).resolve().parents[1]/'public/assets/operating-reports.js').read_text()
    assert 'new File([blob],exported.filename' in script
    assert 'a.download=exported.filename' in script
