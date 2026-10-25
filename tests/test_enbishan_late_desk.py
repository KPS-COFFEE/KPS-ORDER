"""One-night delivery desk admission expires without changing public hours."""
from copy import deepcopy
from datetime import datetime

import pytest
from app import catalog
from test_branch_opening_hours import configured_branch


def branch():
    value = configured_branch()
    value.update(brand_id='enbishan', branch_no=1)
    for key in ('opening_hours', 'delivery_hours'):
        value['settings'][key] = [{'days': list(range(7)), 'start': '16:00', 'end': '03:00'}]
    return value


@pytest.mark.parametrize('time,expected', [
    ('2026-10-24T04:00:00+03:00', False),
    ('2026-10-25T03:00:00+03:00', True),
    ('2026-10-25T04:29:59+03:00', True),
    ('2026-10-25T01:29:59+00:00', True),
    ('2026-10-25T04:30:00+03:00', False),
    ('2026-10-26T04:00:00+03:00', False),
])
def test_exact_one_night_window(time, expected):
    value = branch()
    before = deepcopy(value)
    now = datetime.fromisoformat(time)
    assert catalog.branch_service(value, 'delivery', now, delivery_center=True)[0] is expected
    assert catalog.branch_service(value, 'delivery', now)[0] is False
    assert value == before


@pytest.mark.parametrize('brand,number,service', [
    ('kps', 1, 'delivery'), ('enbishan', 3, 'delivery'),
    ('enbishan', 1, 'pickup'), ('enbishan', 1, 'car'),
])
def test_does_not_expand_other_brands_branches_or_services(brand, number, service):
    value = branch()
    value.update(brand_id=brand, branch_no=number)
    assert not catalog.branch_service(value, service, datetime.fromisoformat('2026-10-25T04:00:00+03:00'), delivery_center=True)[0]


@pytest.mark.parametrize('field,value', [
    ('active', False), ('delivery_enabled', False),
    ('operational_data_verified', False), ('payment_methods', []),
    ('opening_hours', []), ('delivery_hours', []), ('coverage', None),
    ('location_verified', False), ('delivery_fee_halalas', None),
])
def test_retains_all_other_admission_checks(field, value):
    item = branch()
    item['settings'][field] = value
    assert not catalog.branch_service(item, 'delivery', datetime.fromisoformat('2026-10-25T04:00:00+03:00'), delivery_center=True)[0]
