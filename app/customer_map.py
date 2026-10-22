"""Customer map selection. Only explicitly selected, brand-specific browser credentials leave the server."""
import os
import re
from fastapi import APIRouter, Query
from . import db, storefront_scope

router = APIRouter(prefix='/api/customer-map', tags=['customer-map'])


def enabled():
    return (os.getenv('MAPBOX_ENABLED', '').lower() == 'true'
            and os.getenv('MAPBOX_CUSTOMER_ENABLED', '').lower() == 'true')


def public_config(brand_id):
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,59}', brand_id):
        return {'enabled': False}
    suffix = brand_id.upper().replace('-', '_')
    # Existing Mapbox remains the default. Preparing a Google key never activates it.
    selected = os.getenv('CUSTOMER_MAP_PROVIDER_' + suffix, 'mapbox').strip().lower()
    if selected == 'google' and os.getenv('GOOGLE_MAPS_CUSTOMER_ENABLED', '').lower() == 'true':
        browser_key = os.getenv('GOOGLE_MAPS_BROWSER_KEY_' + suffix, '')
        if re.fullmatch(r'AIza[A-Za-z0-9_-]{35}', browser_key):
            return {'enabled': True, 'provider': 'google', 'browser_key': browser_key}
    # Missing Google setup preserves the configured picker instead of breaking ordering.
    token = os.getenv('MAPBOX_PUBLIC_TOKEN_' + suffix, '')
    if not enabled() or not re.fullmatch(r'pk\.[A-Za-z0-9_.-]{20,2048}', token):
        return {'enabled': False}
    return {'enabled': True, 'provider': 'mapbox', 'public_token': token,
            'style': 'mapbox://styles/mapbox/streets-v12'}


@router.get('/config')
def config(brand_id: str = Query(default='kps', pattern=r'^[a-z][a-z0-9_-]{1,59}$')):
    storefront_scope.require_brand(brand_id)
    with db.tx() as conn:
        if not conn.execute('SELECT 1 FROM brands WHERE id=%s AND active', (brand_id,)).fetchone():
            raise db.error(404, 'BRAND_NOT_FOUND', 'البراند غير متاح.')
    return public_config(brand_id)
