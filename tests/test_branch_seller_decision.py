"""A branch's ownership assignment cannot choose a new channel's legal seller."""
from copy import deepcopy
import pytest
from app import invoice_profiles


@pytest.mark.parametrize('channel', ['pickup', 'car'])
@pytest.mark.parametrize('branch', ['kps-branch-1', 'kps-branch-11', 'future-branch'])
def test_branch_services_never_infer_seller(channel, branch):
    config = {'invoice_profiles': invoice_profiles.defaults('kps')}
    original = deepcopy(config)
    profile = invoice_profiles.capture(None, 'kps', branch, brand_settings=config, channel=channel)
    assert profile == {'entity_id': None, 'issuance_status': 'seller_decision_pending',
                       'channel': channel, 'branch_id': branch, 'prices_include_vat': None}
    assert config == original


def test_delivery_retains_owner_approved_seller():
    profile = invoice_profiles.capture(None, 'kps', 'kps-branch-11', brand_settings={}, channel='kps_order')
    assert profile['entity_id'] == 'reem'
    assert profile['prices_include_vat'] is True
