"""Delivery minimum feedback uses the existing authoritative product subtotal."""
from pathlib import Path
from datetime import datetime
import subprocess
import pytest
from fastapi import HTTPException
from app import catalog
from test_khaliya_routing_source import CatalogConnection, fixtures, quote_body


def failure(branches,products,body):
    with pytest.raises(HTTPException) as result:
        catalog.quote_order(CatalogConnection(branches,products),body)
    assert result.value.status_code==409
    assert result.value.detail['code']=='MINIMUM_ORDER_NOT_MET'
    return result.value.detail


@pytest.mark.parametrize('minimum',[1951,2500,3707])
def test_feedback_uses_configured_minimum_and_excludes_delivery_fee(minimum):
    branches,products=fixtures();selected=branches['kps-branch-1']
    selected['settings'].update(minimum_order_halalas=minimum,delivery_fee_halalas=5000)
    body=quote_body(items=[{'product_id':'kps-01','quantity':1}])
    detail=failure(branches,products,body)
    assert {key:detail[key] for key in ('minimum_order_halalas','subtotal_halalas','remaining_halalas','currency')}=={
        'minimum_order_halalas':minimum,'subtotal_halalas':1950,'remaining_halalas':minimum-1950,'currency':'SAR'}
    assert set(detail)=={'code','message','minimum_order_halalas','subtotal_halalas','remaining_halalas','currency'}


def test_priced_options_count_toward_minimum_and_exact_threshold_still_passes():
    branches,products=fixtures();selected=branches['kps-branch-1']
    selected['settings']['minimum_order_halalas']=2500
    products['kps-01']['settings']['option_groups']=[{
        'code':'SIZE','name':'الحجم','required':True,'values':[
            {'code':'large','name':'كبير','price_delta_halalas':275}]}]
    body=quote_body(items=[{'product_id':'kps-01','quantity':1,'units':[{'options':{'SIZE':'large'}}]}])
    detail=failure(branches,products,body)
    assert detail['subtotal_halalas']==2225 and detail['remaining_halalas']==275
    selected['settings']['minimum_order_halalas']=2225
    quote=catalog.quote_order(CatalogConnection(branches,products),body)
    assert quote['subtotal_halalas']==2225 and quote['total_halalas']==2425 and quote['discount_halalas']==0


def test_existing_internal_routing_minimum_is_not_replaced_by_display_logic():
    branches,products=fixtures();branches['kps-branch-1']['settings']['minimum_order_halalas']=6000
    branches['kps-branch-2']['settings']['minimum_order_halalas']=6500
    detail=failure(branches,products,quote_body())
    assert detail['minimum_order_halalas']==6500 and detail['subtotal_halalas']==5850 and detail['remaining_halalas']==650



def test_morning_feedback_keeps_selected_customer_contract_minimum(monkeypatch):
    from app import morning_delivery
    branches,products=fixtures()
    branches['kps-branch-1']['settings']['minimum_order_halalas']=6000
    branches['kps-branch-2']['settings']['minimum_order_halalas']=9000
    class BranchRows:
        def fetchall(self):return list(branches.values())
    class MorningConnection(CatalogConnection):
        def execute(self,sql,args=None):
            if 'FROM branches WHERE brand_id=' in sql:return BranchRows()
            return super().execute(sql,args)
    monkeypatch.setattr(catalog.service_hours,'now_riyadh',lambda:datetime(2026,10,22,8,tzinfo=catalog.RIYADH))
    monkeypatch.setattr(morning_delivery,'active',lambda *args:True)
    monkeypatch.setattr(morning_delivery,'target',lambda *args,**kwargs:branches['kps-branch-2'])
    with pytest.raises(HTTPException) as result:
        catalog.quote_order(MorningConnection(branches,products),quote_body())
    detail=result.value.detail
    assert detail['code']=='MINIMUM_ORDER_NOT_MET'
    assert detail['minimum_order_halalas']==6000 and detail['subtotal_halalas']==5850 and detail['remaining_halalas']==150


def test_pickup_and_complimentary_bypasses_remain_unchanged():
    branches,products=fixtures();branches['kps-branch-1']['settings']['minimum_order_halalas']=5000
    body=quote_body(items=[{'product_id':'kps-01','quantity':1}])
    assert catalog.quote_order(CatalogConnection(branches,products),body,complimentary=True)['subtotal_halalas']==1950
    body=quote_body(service='pickup',items=[{'product_id':'kps-01','quantity':1}])
    assert catalog.quote_order(CatalogConnection(branches,products),body)['total_halalas']==1950


def test_actual_customer_api_formats_minimum_in_both_languages():
    subprocess.run(['node','tests/test_minimum_order_feedback.cjs'],cwd=Path(__file__).resolve().parents[1],check=True)
