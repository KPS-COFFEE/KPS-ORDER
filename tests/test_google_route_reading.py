"""Input/source validation; real SQL metric boundaries are certified separately."""
import pytest
from pydantic import ValidationError
from app.fleet import GoogleRouteReading, summary


def reading(**changes):
    return GoogleRouteReading(**dict(revision=1,expected_km='210.5',source_url='https://www.google.com/maps/dir/branch/customer/branch',
        itinerary='Branch - customer 1 - customer 2 - branch',reason='Reviewed all trips',complete_itinerary=True,**changes))


def test_google_reading_explicit_manual_source():
    r=reading()
    assert float(r.expected_km)==210.5 and r.complete_itinerary


@pytest.mark.parametrize('field,value',[
    ('source_url','https://www.google.com.evil.test/maps/dir'),('source_url','https://evil.test/maps'),
    ('source_url','https://google.com/search?q=maps'),('source_url','http://maps.google.com'),
    ('source_url','https://user:pass@maps.google.com'),('source_url','javascript:alert(1)'),
    ('expected_km',0),('expected_km',-1),('expected_km','NaN'),('expected_km','Infinity'),
    ('complete_itinerary',False),('revision',0),('itinerary','x')])
def test_google_reading_invalid_values_rejected(field,value):
    args=reading().model_dump();args[field]=value
    with pytest.raises(ValidationError):GoogleRouteReading(**args)


def test_excluded_odometer_does_not_inflate_distance_or_efficiency():
    row={'vehicle_id':'one','actual_distance':211432,'liters':10,'efficiency':None,
         'fuel_status':'grey','distance_eligible':False,'faults':0,'exception':False}
    result=summary([row])
    assert result['actual_distance']==0 and result['efficiency'] is None and result['grey']==1
