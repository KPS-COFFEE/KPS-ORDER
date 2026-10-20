"""Optional customer Mapbox picker. Only public, brand-specific tokens leave the server."""
import os
import re
from fastapi import APIRouter, Query
from . import db, storefront_scope

router = APIRouter(prefix='/api/customer-map', tags=['customer-map'])


def enabled():
    return os.getenv('MAPBOX_ENABLED', '').lower() == 'true'


def public_config(brand_id):
    token = os.getenv('MAPBOX_PUBLIC_TOKEN_' + brand_id.upper().replace('-', '_'), '')
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
