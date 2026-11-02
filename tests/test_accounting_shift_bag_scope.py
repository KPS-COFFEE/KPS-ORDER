"""Current accounting card metadata; prior images keep their old read contract."""
import pytest
from test_enbishan_dual_brand import dual
from test_central_workdays import central
from test_aggregate_driver_closure import declare_separate_bag

@pytest.mark.postgres
@pytest.mark.parametrize('city', ['النعيرية', 'القيصومة', 'حفر الباطن'])
def test_bag_metadata_links_exact_authorized_manager_periods(central, city):
    _, actor, _, _, _ = central
    _, driver, _, declared = declare_separate_bag(central, city)
    response = actor('accountant').get('/api/cash-bags')
    assert response.status_code == 200
    actual = next(b for b in response.json()['bags'] if b['id'] == declared['cash_bag']['id'])
    assert set(actual['shift_ids']) == {leg['id'] for leg in driver['shifts']}
    assert set(actual['manager_shift_ids']) == {leg['manager_shift_id'] for leg in driver['shifts']}
