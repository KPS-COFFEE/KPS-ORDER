from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import auth, first_order_gift, morning_delivery, experience_policy, storefront_advertising as ads


def test_new_experience_and_promotions_are_disabled_by_default():
    assert ads.policy()['advertising_enabled'] is False
    assert first_order_gift.defaults()['enabled'] is False
    assert morning_delivery.defaults()['enabled'] is False
    assert experience_policy.defaults()['customer_entry_enabled'] is False
    assert experience_policy.defaults()['timing_enabled'] is False


def test_delegated_admin_cannot_enable_advertising():
    app=FastAPI();app.include_router(ads.router)
    app.dependency_overrides[auth.current_user]=lambda:{'id':'admin','brand_id':'kps','role':'admin','active':True,'effective_permissions':['banners_manage','appearance_manage']}
    response=TestClient(app).put('/api/advertising/policy',json={'advertising_enabled':True,'expected_version':0,'reason':'تفعيل إعلان'})
    assert response.status_code==403
