from copy import deepcopy
from datetime import datetime, timezone, timedelta
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from app import auth, first_order_gift as gift

NOW = datetime(2026,10,20,13,tzinfo=timezone.utc)


@pytest.mark.parametrize('offset,status',[(1,'scheduled'),(0,'eligible'),(-1,'eligible')])
def test_owner_start_time_exact_boundary(offset,status):
    p=gift.defaults()|{'enabled':True,'starts_at':(NOW+timedelta(seconds=offset)).isoformat()}
    assert gift.eligibility(p,verified=True,claimed=False,now=NOW)==status


def test_restarting_campaign_never_resets_a_previous_phone_claim():
    p=gift.defaults()|{'enabled':True,'starts_at':NOW.isoformat(),'version':1}
    assert gift.eligibility(p,verified=True,claimed=True,now=NOW)=='claimed'
    p.update(version=20,starts_at=(NOW-timedelta(days=1)).isoformat())
    assert gift.eligibility(p,verified=True,claimed=True,now=NOW)=='claimed'
    assert gift.eligibility(p,verified=False,claimed=False,now=NOW)=='verification_required'
    p['enabled']=False
    assert gift.eligibility(p,verified=True,claimed=False,now=NOW)=='inactive'


@pytest.mark.parametrize('offset,status', [(-1, 'expired'), (0, 'expired'), (1, 'eligible')])
def test_automatic_end_exact_boundary(offset, status):
    p = gift.defaults() | {'enabled': True, 'starts_at': (NOW-timedelta(days=7)).isoformat(),
                          'ends_at': (NOW+timedelta(seconds=offset)).isoformat()}
    assert gift.eligibility(p, verified=True, claimed=False, now=NOW) == status
    assert gift.eligibility(p, verified=False, claimed=False, now=NOW) == ('verification_required' if offset > 0 else 'expired')


@pytest.mark.parametrize('ends', ['2026-10-20T13:00:00', '2026-10-20T13:00:00+00:00', '2026-10-19T13:00:00+00:00'])
def test_end_requires_timezone_and_follows_start(ends):
    with pytest.raises(ValidationError):
        gift.GiftPolicy(starts_at=NOW, ends_at=ends)


@pytest.mark.parametrize('value',[
    {'enabled':True},
    {'enabled':True,'starts_at':'2026-10-20T16:00:00','items':[{'product_id':'coffee'},{'product_id':'cookie'}]},
    {'items':[{'product_id':'coffee'},{'product_id':'coffee'}]},
    {'enabled':'yes'},
    {'enabled':True,'starts_at':NOW.isoformat(),'items':[{'product_id':'coffee','quantity':50},{'product_id':'cookie'}]},
])
def test_policy_rejects_incomplete_or_caller_invented_gifts(value):
    with pytest.raises(ValidationError):gift.GiftPolicy.model_validate(value)


def test_gift_free_prices_do_not_mutate_catalog_or_options():
    p={'id':'coffee','brand_id':'kps','name':'قهوة اليوم','category':'قهوة','price_halalas':550,
       'settings':{'source_verified':True,'option_groups':[]}}
    before=deepcopy(p)
    line=gift._item(p,{'options':{}},None)
    assert line['quantity']==1 and line['line_total_halalas']==0 and line['unit_price_halalas']==0
    assert line['units'][0]['price_halalas']==0
    assert line['original_line_total_halalas']==550
    assert p==before


@pytest.mark.parametrize('role',['customer','driver','delivery_manager','delivery_staff','branch_manager','admin'])
def test_gift_configuration_is_exclusively_owner_controlled(role):
    app=FastAPI();app.include_router(gift.router)
    app.dependency_overrides[auth.current_user]=lambda:{'role':role,'brand_id':'kps'}
    client=TestClient(app)
    assert client.get('/api/gifts/policy').status_code==403
    assert client.put('/api/gifts/policy',json={'policy':{'enabled':False},'expected_version':0,'reason':'اختبار'}).status_code==403
