"""Pure metric/report contracts. Database and browser acceptance is separate."""
from datetime import date,datetime,timezone
from decimal import Decimal
import io,zipfile
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app import fleet,accounting_export


def row(km=210,liters=21,efficiency=10,color='green',vehicle='one',faults=0,exception=False):
    return dict(date='2026-10-12',operating_day='2026-10-12',vehicle_id=vehicle,plate='TEST 123',driver_name='TEST Driver',branches='TEST Branch',
      received_at=datetime(2026,10,12,16,tzinfo=timezone.utc),returned_at=datetime(2026,10,12,20,tzinfo=timezone.utc),
      start_odometer=1000,end_odometer=1210,actual_distance=km,gps_distance=None,expected_distance=None,distance_variance=None,
      liters=liters,efficiency=efficiency,full_start=efficiency is not None,full_end=True,fuel_status=color,
      orders=5,faults=faults,exception=exception,notes='TEST ONLY notes',record_status='exception' if exception else 'closed')


def test_average_uses_eligible_distance_over_fuel_not_mean_ratios():
    rows=[row(100,10,10),row(200,40,5,'red',vehicle='two',faults=2,exception=True),row(500,100,None,'grey',vehicle='two')]
    s=fleet.summary(rows)
    assert s['vehicles']==2 and s['actual_distance']==800 and s['liters']==150
    assert s['efficiency']==6 and s['efficiency']!=(10+5)/2
    assert s['green']==1 and s['red']==1 and s['grey']==1 and s['exceptions']==1 and s['faults']==2
    assert fleet.summary([])['efficiency'] is None


@pytest.mark.parametrize('start,end',[('2026-10-12','2026-10-01'),('2024-01-01','2026-10-12')])
def test_bad_period_fails_before_data_read(start,end):
    with pytest.raises(HTTPException) as e:fleet.filters({'brand_id':'test','role':'owner'},start=date.fromisoformat(start),end=date.fromisoformat(end))
    assert e.value.status_code==422


def test_summary_color_filter_keeps_existing_branch_driver_and_fault_filters():
    lo,hi,clauses,args,outer=fleet.filters({'brand_id':'test','role':'admin','branch_ids':['b']},start=date(2026,10,1),end=date(2026,10,12),driver_id='d',branch_id='b',fuel_status='green',metric='red',faults_only=True)
    assert args[-4:]==['d','b','green','red'] and outer==['faults>0']
    assert any('branch_ids <@' in c for c in clauses)


@pytest.mark.parametrize('change',[{'revision':0},{'start_odometer':1.5},{'liters':'NaN'},{'liters':-1},{'reason':' '},{'notes':'bad\x00value'},{'invented':'no'}])
def test_correction_validation_rejects_bad_receipts(change):
    body=dict(revision=1,reason='Test receipt',start_odometer=1000,end_odometer=1100,liters='10.25',full_start=True,full_end=True)
    with pytest.raises(ValidationError):fleet.Correction(**{**body,**change})


def test_xlsx_cells_keep_user_formula_text_inert_and_all_columns():
    keys=[c[0] for c in fleet.COLUMNS]
    headers=[c[2] for c in fleet.COLUMNS]
    r=row();r['notes']='=HYPERLINK("https://example.invalid","test")'
    data=accounting_export.xlsx([headers,[str(r.get(k,'—')) for k in keys]],keys,['TEST ONLY'], 'en')
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        sheet=z.read('xl/worksheets/sheet1.xml').decode()
        assert '<f>' not in sheet and 'HYPERLINK' in sheet and 'W2' in sheet
        assert all(label in sheet for label in ['Expected order KM','Full at start','Fuel status'])


@pytest.mark.parametrize('lang',['ar','en'])
def test_pdf_readable_two_sections_and_ryadh_times(lang):
    data=fleet.make_pdf([row()],lang,'TEST ONLY Fleet','2026-10-12 — 2026-10-12',fleet.DEFINITIONS)
    assert data.startswith(b'%PDF')
    from pypdf import PdfReader
    pages=PdfReader(io.BytesIO(data)).pages
    assert len(pages)==2
    if lang=='en':
        assert 'Movement and distances' in pages[0].extract_text()
        assert 'Fuel, faults and exceptions' in pages[1].extract_text()
        assert '19:00' in pages[0].extract_text() and '23:00' in pages[0].extract_text()
