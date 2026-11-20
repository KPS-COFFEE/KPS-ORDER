from datetime import datetime, timedelta, timezone
import pytest
from pydantic import ValidationError
from app.photo_location import CaptureLocation, classify_capture

NOW = datetime(2026, 10, 30, 8, tzinfo=timezone.utc)


def payload(**extra):
    return dict(captured_at=NOW, source='browser_camera', lat=28.4, lng=45.9,
                accuracy_m=200, location_at=NOW-timedelta(seconds=2), **extra)


@pytest.mark.parametrize('accuracy,status', [(199,'acceptable'),(200,'acceptable'),(201,'weak'),(3000,'weak')])
def test_accuracy_is_server_classified(accuracy,status):
    data=payload();data['accuracy_m']=accuracy
    assert classify_capture(CaptureLocation(**data),NOW)['status']==status


@pytest.mark.parametrize('age,status', [(15,'acceptable'),(15.01,'stale'),(-1,'stale')])
def test_fix_time_is_compared_to_capture_not_upload(age,status):
    data=payload();data['location_at']=NOW-timedelta(seconds=age)
    result=classify_capture(CaptureLocation(**data),NOW+timedelta(seconds=120))
    assert result['status']==status


def test_missing_gps_and_old_clients_do_not_block_or_invent_a_position():
    assert classify_capture(None,NOW)['status']=='missing'
    result=classify_capture(CaptureLocation(captured_at=NOW,source='browser_camera'),NOW)
    assert result['status']=='missing' and result['lat'] is None


def test_native_camera_return_cannot_be_claimed_as_shutter_position():
    data=payload();data['source']='device_camera_return'
    assert classify_capture(CaptureLocation(**data),NOW)['status']=='capture_time_unverified'


@pytest.mark.parametrize('stamp',[NOW.replace(tzinfo=None),NOW+timedelta(minutes=5),NOW-timedelta(minutes=11)])
def test_bad_device_clock_is_labelled_without_blocking_delivery(stamp):
    data=payload();data['captured_at']=stamp
    assert classify_capture(CaptureLocation(**data),NOW)['status']=='invalid_clock'


@pytest.mark.parametrize('key,value',[('lat',float('nan')),('lng',181),('accuracy_m',-1),('status','acceptable')])
def test_invalid_coordinates_and_client_classifications_are_rejected(key,value):
    data=payload();data[key]=value
    with pytest.raises(ValidationError):CaptureLocation(**data)


def test_photo_metadata_has_a_printable_snapshot_for_both_report_views():
    from app.reporting import v5_tables
    snap={'shift':{},'summary':{},'orders':[], 'photo_capture_evidence':[
        {'order_id':'order1','stage':'picked_up','driver_name':'Driver',
         'created_at':NOW.isoformat(),'capture_location':classify_capture(CaptureLocation(**payload()),NOW)}]}
    for view in ('owner','compact'):
        table=next(x for x in v5_tables(snap,view) if x['key']=='photo_capture_evidence')
        assert len(table['columns'])==len(table['rows'][0])==8
        assert table['rows'][0][5]=='دقة مقبولة'
