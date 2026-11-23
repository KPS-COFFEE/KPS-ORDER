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


@pytest.mark.postgres
def test_count_evidence_on_both_manager_cards_during_concurrent_shift_update(central):
    from app import db

    _, actor, post, _, _ = central
    manager, driver, path, declared = declare_separate_bag(central, 'النعيرية')
    bag_id = declared['cash_bag']['id']
    legs = driver['shifts']
    post('manager', path+'/physical-approval', {
        'declaration_id': declared['declaration_id'], 'confirmed': True, 'reason': 'Reviewed driver closure',
    })
    post('manager', '/api/cash-bags/workdays/'+manager['shift']['id']+'/vault-close', {'confirmed': True})

    def card_for(leg):
        response = actor('accountant', leg['brand_id']).get('/api/accounting/manager-cards')
        assert response.status_code == 200, response.text
        return next(card for card in response.json()['cards'] if card['id'] == leg['manager_shift_id'])

    assert card_for(legs[0])['cash_bags'][0]['count_id'] is None
    receipt = post('accountant', '/api/cash-bags/'+bag_id+'/count', {
        'received_cash_halalas': 0, 'card_halalas': 900, 'confirmed': True,
        'reference': 'Synthetic witnessed zero cash',
    })

    with db.tx() as writer:
        writer.execute('SELECT id FROM shifts WHERE id=ANY(%s) ORDER BY id FOR UPDATE',
                       ([leg['id'] for leg in legs],))
        for leg in legs:
            card = card_for(leg)
            assert not card['ready']
            bag, = card['cash_bags']
            assert bag['id'] == bag_id and bag['count_id'] == receipt['count_id']
            assert bag['received_cash_halalas'] == 0 and bag['counted_at'] and bag['counted_by_name']
        for path in ('/api/cash-bags', '/api/shared-driver-closures'):
            response = actor('accountant').get(path)
            assert response.status_code == 200, response.text
