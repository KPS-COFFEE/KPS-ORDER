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
