"""Server-owned KPS storefront binding and contact presentation."""
import json
import os
import re
from contextvars import ContextVar
from urllib.parse import urlsplit

from fastapi import HTTPException

KPS_HOSTS = {'order.kps.sa', 'kps-order-emergency-v4.onrender.com'}
current_brand = ContextVar('storefront_brand', default=None)


def bound_brand(request):
    # The configured deployment binding survives a forged Host header.
    # Forwarded host headers never select a tenant.
    configured = urlsplit(os.getenv('PUBLIC_BASE_URL', '')).hostname
    from .multibrand import enabled, isolation_guard
    if enabled():
        isolation_guard()
        # Work credentials are role-bound by auth._find_session. A caller's
        # header cannot turn a customer cookie into a staff credential. This
        # permits explicit staff brand grants without relaxing customer scope.
        from . import auth
        if auth.session_context(request)=='work':
            return None
        if os.getenv('ENBISHAN_ISOLATED_ENABLED') != 'true':
            try:
                mapping=json.loads(os.environ['KPS_STOREFRONT_HOST_BRANDS'])
            except (KeyError,ValueError,TypeError):
                raise RuntimeError('Approved storefront host mapping is required.') from None
            if (not isinstance(mapping,dict) or not mapping
                    or any(not isinstance(host,str) or not re.fullmatch(r'[a-z0-9.-]+',host)
                           or brand not in {'kps','enbishan'} for host,brand in mapping.items())
                    or set(mapping.values()) != {'kps','enbishan'}
                    or configured not in mapping):
                raise RuntimeError('Storefront mapping must bind both brands and the configured public URL.')
            allowed=set(os.getenv('APP_ALLOWED_HOSTS','').split(','))
            if not set(mapping).issubset(allowed):
                raise RuntimeError('Each mapped storefront host must be explicitly allowed.')
            # Forwarded headers never choose the brand. Unknown virtual hosts
            # cannot acquire the default KPS storefront accidentally.
            if request.url.hostname not in mapping:
                raise HTTPException(404,{'code':'STOREFRONT_HOST_UNKNOWN'})
            return mapping[request.url.hostname]
        mapping = {'order.enbishan.com': 'enbishan', 'enbishan.test': 'enbishan', 'kps.test': 'kps'}
        if configured in mapping:
            return mapping[configured]
        if configured not in KPS_HOSTS and request.url.hostname in mapping:
            return mapping[request.url.hostname]
    return 'kps' if configured in KPS_HOSTS or request.url.hostname in KPS_HOSTS else None


def require_brand(brand):
    if current_brand.get() and brand != current_brand.get():
        raise HTTPException(403, {'code': 'STOREFRONT_BRAND_MISMATCH',
                                 'message': 'البراند غير متاح على هذا الموقع.'})


def validate_scope(request, body):
    if not current_brand.get():
        return
    values = request.query_params.getlist('brand_id')
    if len(values) > 1:
        raise HTTPException(400, {'code': 'AMBIGUOUS_BRAND'})
    for value in values:
        require_brand(value)
    media_type = request.headers.get('content-type', '').split(';', 1)[0].strip().lower()
    if body and (not media_type or media_type == 'application/json' or media_type.endswith('+json')):
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            return  # Preserve the endpoint's normal validation response.
        if isinstance(payload, dict) and 'brand_id' in payload:
            require_brand(payload['brand_id'])


def customer_html(source, request):
    brands = request.query_params.getlist('brand_id')
    brand = brands[0] if len(brands) == 1 else (current_brand.get() or 'kps' if not brands else None)
    if brand != 'kps':
        # Remove private-to-brand fragments before sending HTML, not just
        # visually hiding them after JavaScript executes.
        source = re.sub(r'<!-- KPS_CONTACT_START -->.*?<!-- KPS_CONTACT_END -->', '', source, flags=re.S)
        if brand == 'enbishan':
            source = source.replace('<html lang="ar" dir="rtl">', '<html lang="ar" dir="rtl" data-storefront-brand="enbishan">')
        return source
    return source
