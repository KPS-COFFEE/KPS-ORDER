"""Optional staff map configuration; never changes customer map behavior."""
import os
import re
from fastapi import APIRouter, Depends
from . import auth

router = APIRouter(prefix='/api/operations', tags=['operations'])


def enabled():
    return os.getenv('MAPBOX_ENABLED', '').lower() == 'true'


@router.get('/map-config')
def map_config(user=Depends(auth.require_roles('owner', 'delivery_manager', 'delivery_staff'))):
    # Scope comes only from the authenticated principal, never a query string.
    brand = user['brand_id']
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,59}', brand):
        return {'enabled': False}
    token = os.getenv('MAPBOX_PUBLIC_TOKEN_' + brand.upper().replace('-', '_'), '')
    if not enabled() or not re.fullmatch(r'pk\.[A-Za-z0-9_.-]{20,2048}', token):
        return {'enabled': False}
    return {'enabled': True, 'provider': 'mapbox', 'public_token': token,
            'style': 'mapbox://styles/mapbox/streets-v12'}
