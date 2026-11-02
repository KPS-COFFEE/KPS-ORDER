"""Fuel amounts are recorded facts, never inferred from liters or missing data."""
from app.driver_fuel_report import fuel_values, segment_log


def test_segment_refuels_remain_with_actual_vehicle_after_handover():
    original={'refuels':[{'liters':'5','amount_halalas':1000}]}
    current={'refuels':[{'liters':'7','amount_halalas':1500}], 'previous_segments':[original]}
    assert fuel_values(segment_log(current,0))['fuel_amount_halalas']==1000
    assert fuel_values(segment_log(current,1))['fuel_amount_halalas']==1500
    assert segment_log(current,2)=={}


def test_missing_amount_is_not_zero_and_retains_known_subtotal():
    data=fuel_values({'refuels':[{'amount_halalas':1200},{'liters':'3'}]})
    assert data['fuel_amount_halalas'] is None
    assert data['fuel_amount_known_halalas']==1200
    assert data['fuel_amount_missing_count']==1
    assert data['fuel_record_missing'] is False
    data=fuel_values({})
    assert data['fuel_record_missing'] is True
    assert data['fuel_amount_halalas'] is None
    assert fuel_values({'refuels':[{'amount_halalas':0}]})['fuel_amount_halalas']==0


def test_pdf_paginates_long_names_and_preserves_missing_values():
    import io
    from pypdf import PdfReader
    from app.driver_fuel_pdf import render
    rows = [dict(operating_day='2026-11-01', driver_name='Very long driver name '+str(i),
                 plate='TEST '+str(i), cities=['Test city'], start_odometer=1000,
                 end_odometer=1210, actual_distance=210, liters=21,
                 fuel_record_missing=False, fuel_amount_halalas=4200, record_status='closed')
            for i in range(51)]
    rows[0].update(driver_name='أحمد عبدالناصر', cities=['حفر الباطن'])
    rows[-1].update(actual_distance=None, end_odometer=None, fuel_record_missing=True,
                    fuel_amount_halalas=None, driver_name='LAST-RECORD')
    data = {'rows': rows, 'filters': {'driver': '', 'vehicle': '', 'driver_selected': False,
            'vehicle_selected': False, 'city': '', 'brand': 'test'},
            'period': {'start': '2026-10-01', 'end': '2026-11-01'},
            'summary': {'distance_km': 10500, 'liters': 1050, 'records': 51, 'drivers': 51,
                        'fuel_amount_known_halalas': 210000, 'fuel_amount_halalas': None,
                        'distance_missing_records': 1},
            'note': 'Each row is one vehicle interval. Missing is not zero or a driver charge.'}
    result = render(data, 'en')
    pdf = PdfReader(io.BytesIO(result))
    assert len(pdf.pages) > 1
    text = '\n'.join(page.extract_text() for page in pdf.pages)
    assert 'LAST-RECORD' in text and 'TEST 50' in text
    assert 'All drivers' in text and 'All vehicles' in text
    assert 'Totals are incomplete' in text
    assert '2,100.00' in text
    assert 'Driver fuel and distance' in text
    # Arabic identity remains Arabic even in an English-language export.
    assert any('\ufb50' <= ch <= '\ufeff' for ch in text)
    assert float(pdf.pages[0].mediabox.width) > float(pdf.pages[0].mediabox.height)
