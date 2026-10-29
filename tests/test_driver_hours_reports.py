"""Focused evidence for scoped exports and responsive four-card view."""
import io
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree
from app.driver_hours_reports import make_xlsx, make_pdf, export_rows, COLUMNS


def fixture():
    metrics = dict(shift_seconds=28800, actual_seconds=25200, delivery_seconds=7200,
                   waiting_seconds=14400, break_seconds=3600, other_work_seconds=0,
                   unclassified_seconds=3600, order_activity_seconds=21600, active_delivery_seconds=0, remaining_shift_seconds=7200, trip_count=2, order_count=5,
                   non_delivery_seconds=3600,duty_seconds=28800,active_work_seconds=0,work_utilization_percent=87.5,work_clock_status='recorded')
    return dict(start='2026-10-18', end='2026-10-18', operational_day_start='05:30',
                totals=metrics, drivers=[dict(name='=HYPERLINK("bad")', **metrics)])


def test_xlsx_is_parseable_and_names_never_formulas():
    with ZipFile(io.BytesIO(make_xlsx(fixture()))) as archive:
        for name in archive.namelist():
            ElementTree.fromstring(archive.read(name))
        sheet = archive.read('xl/worksheets/sheet1.xml').decode()
        assert '<f>' not in sheet
        assert 'HYPERLINK' in sheet and 'inlineStr' in sheet
        assert '<v>8.0</v>' in sheet
        assert 'rightToLeft="1"' in sheet


def test_export_hours_reconcile_and_english_labels():
    rows = export_rows(fixture(), 'en')
    assert rows[0][0] == 'Driver'
    assert rows[1][3:8] == [8, 7, 2, 4, 1]
    assert rows[-1][1:-1] == rows[1][1:-1]
    assert rows[1][9:13] == [1,6,0,1]


def test_accepted_return_exports_match_duty_comparison_and_keep_missing_evidence_visible():
    report=fixture()
    for row in [report['totals'],report['drivers'][0]]:
        row.update(actual_seconds=27000,duty_seconds=43200,active_work_seconds=3600,non_delivery_seconds=12600,work_utilization_percent=62.5)
    rows=export_rows(report,'en')
    assert rows[1][4]==7.5 and rows[1][12:16]==[3.5,12,1,62.5]
    row=report['drivers'][0]
    row.update(actual_seconds=0,work_utilization_percent=None,work_clock_status='needs_review',work_review_required=True)
    report['totals'].update(actual_seconds=0,work_utilization_percent=None,drivers_needing_review=1)
    rows=export_rows(report,'en')
    assert rows[1][4]==rows[1][15]==rows[-1][4]=='Unavailable'
    assert rows[1][-1]==rows[-1][-1]=='Incomplete — needs review'
    # Even an older saved report with a numeric partial ratio is not a grade.
    row['work_utilization_percent']=0
    report['totals']['work_utilization_percent']=62.5
    rows=export_rows(report,'en')
    assert rows[1][15]==rows[-1][15]=='Unavailable'


def test_arabic_pdf_has_embedded_font_and_real_pdf():
    value=make_pdf(fixture())
    assert value.startswith(b'%PDF-')
    assert b'/FontFile2' in value


def test_pdf_wraps_complete_driver_names_and_repeats_table_headers():
    from pypdf import PdfReader
    report = fixture()
    name = 'Mohammed Abdulrahman Alharbi Complete Driver Name Preserved'
    report['drivers'] = [dict(report['drivers'][0], name=name) for _ in range(45)]
    document = PdfReader(io.BytesIO(make_pdf(report, 'en')))
    assert len(document.pages) > 1
    text = ''.join(page.extract_text() for page in document.pages)
    # Line wrapping can split even a single long word but may never remove it.
    name_fragments = []
    for page in document.pages:
        # Read the name column separately from adjacent numeric cells.
        page.extract_text(visitor_text=lambda value, cm, tm, font, size:
                          name_fragments.append(value) if tm[4] > float(page.mediabox.width)-25-(float(page.mediabox.width)-50)/len(COLUMNS)+1 else None)
    compact = ''.join(''.join(name_fragments).split())
    assert ''.join(name.split()) in compact
    assert '…' not in text
    for page in document.pages:
        assert 'Driver' in page.extract_text() and 'Orders' in page.extract_text()


def test_four_cards_match_ops_identity_and_responsive_contract():
    root=Path(__file__).resolve().parents[1]
    js=(root/'public/assets/driver-hours.js').read_text()
    css=(root/'public/assets/driver-hours.css').read_text()
    html=(root/'public/driver-hours.html').read_text()
    assert "[['actual','actual_seconds'],['duty','duty_seconds'],['remaining','non_delivery_seconds'],['utilization','work_utilization_percent']]" in js
    assert 'repeat(4,minmax(0,1fr))' in css
    assert 'repeat(2,minmax(0,1fr))' in css
    assert '/ops.css' in html and 'unclassified_seconds' in js


import pytest


@pytest.mark.postgres
def test_real_sms_delegated_exports_scope_and_immediate_revocation(client,monkeypatch):
    from app import db, employee_permissions as policy
    from tests.test_core import _make_user
    from tests.sms_policy_helpers import sms_login, sms_step_up
    owner=_make_user('owner')
    driver=_make_user('driver',branches=['kps-branch-1'])
    outside=_make_user('driver',branches=['kps-branch-2'])
    unscoped=_make_user('driver')
    with db.tx() as c:
        for name,person,branches in [('inside',driver,['kps-branch-1']),('outside',outside,['kps-branch-2']),('unscoped',unscoped,[])]:
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,opened_at) VALUES(%s,'driver',%s,'kps',%s,now()-interval '1 minute')",('report-auth-'+name,person['id'],db.json(branches)))
    sms_login(client,driver,monkeypatch)
    driver_cookies=dict(client.cookies);driver_csrf=client.headers['X-CSRF-Token']
    paths=['/driver-hours','/api/driver-work-hours/report','/api/driver-work-hours/export.xlsx?start=2026-10-18&end=2026-10-18','/api/driver-work-hours/export.pdf?start=2026-10-18&end=2026-10-18']
    for path in paths:
        assert client.get(path).status_code==403
    sms_login(client,owner,monkeypatch);sms_step_up(client,owner,monkeypatch)
    baseline=policy.effective(driver)
    def save(target,permissions):
        return client.put('/api/identity/staff/'+driver['id']+'/permissions',json={'enabled_permissions':sorted(permissions),'expected_permissions':sorted(policy.effective(target)),'reason':'Explicit owner review of scoped hours report access'})
    grant=save(driver,baseline|{'driver_hours_reports'})
    assert grant.status_code==200,grant.text
    owner_cookies=dict(client.cookies);owner_csrf=client.headers['X-CSRF-Token']
    client.cookies.clear();client.cookies.update(driver_cookies);client.headers['X-CSRF-Token']=driver_csrf
    result=client.get('/api/driver-work-hours/report')
    assert result.status_code==200,result.text
    report=result.json()
    assert [r['driver_id'] for r in report['drivers']]==[driver['id']]
    assert [r['id'] for r in report['options']['drivers']]==[driver['id']]
    assert [r['id'] for r in report['branches']]==['kps-branch-1']
    assert client.get('/api/driver-work-hours/report?branch_id=kps-branch-2').status_code==403
    assert client.get('/api/driver-work-hours/report?driver_id='+outside['id']).status_code==404
    query='?start='+report['start']+'&end='+report['end']
    for kind in ('xlsx','pdf'):
        response=client.get('/api/driver-work-hours/export.'+kind+query)
        assert response.status_code==200,response.text[:200] if response.status_code!=200 else ''
        assert response.headers['cache-control']=='private, no-store'
        assert client.get('/api/driver-work-hours/export.'+kind+query+'&branch_id=kps-branch-2').status_code==403
    # A live branch withdrawal must not make empty historical scopes visible.
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]'::jsonb WHERE id=%s",(driver['id'],))
    empty=client.get('/api/driver-work-hours/report')
    assert empty.status_code==200,empty.text
    assert empty.json()['drivers']==[] and empty.json()['options']['drivers']==[]
    client.cookies.clear();client.cookies.update(owner_cookies);client.headers['X-CSRF-Token']=owner_csrf
    assert save(grant.json()['user'],baseline).status_code==200
    client.cookies.clear();client.cookies.update(driver_cookies);client.headers['X-CSRF-Token']=driver_csrf
    for path in paths:
        assert client.get(path).status_code==403

