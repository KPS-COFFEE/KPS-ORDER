"""Server-owned KPS storefront binding and contact presentation."""
import json
import os
import re
from contextvars import ContextVar
from urllib.parse import urlsplit

from fastapi import HTTPException

KPS_HOSTS = {'order.kps.sa', 'kps-order-emergency-v4.onrender.com'}
current_brand = ContextVar('storefront_brand', default=None)


def forced_work_context(request):
    from . import auth
    from starlette.requests import Request
    # An endpoint-owned work context is distinct from a caller-supplied header.
    neutral=Request({**request.scope,'headers':[(k,v) for k,v in request.scope['headers'] if k.lower()!=b'x-kps-context']})
    return auth.session_context(neutral)=='work'


def verify_work_override(request):
    from . import auth,db
    # Called in the thread pool before storefront binding, never in the event loop.
    with db.tx(pipeline=True) as c:
        principal=auth._find_session(c,request)
    return principal['brand_id'] if principal and principal['role']!='customer' else None


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
        work_brand = None
        if (request.url.path not in {'/','/index.html'} and auth.session_context(request)=='work'
                and (forced_work_context(request) or getattr(request.state,'verified_work_storefront',None))):
            if is_public_brand_route(request.url.path):
                # The fresh session probe already applies current operations
                # membership. A work header alone never unbinds a public API.
                verified = getattr(request.state,'verified_work_storefront',None)
                if isinstance(verified,str):
                    work_brand = verified
            else:
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
            return work_brand or mapping[request.url.hostname]
        if work_brand:
            return work_brand
        mapping = {'enbishan.com': 'enbishan', 'order.enbishan.com': 'enbishan', 'enbishan.test': 'enbishan', 'kps.test': 'kps'}
        if configured in mapping:
            return mapping[configured]
        if configured not in KPS_HOSTS and request.url.hostname in mapping:
            return mapping[request.url.hostname]
    return 'kps' if configured in KPS_HOSTS or request.url.hostname in KPS_HOSTS else None


# Only customer storefront reads/writes accept this binding. Staff sign-in
# identifies the home-brand account before operations-brand projection; it keeps
# its endpoint-owned work boundary and canonical proof checks. No query-string
# mutation is performed for private APIs, assets or unrelated application routes.
_PUBLIC_BRAND_ROUTES = frozenset({
    '/api/catalog', '/api/branches', '/api/banners', '/api/identity/channels',
    '/api/customer-map/config', '/api/customer/legal', '/api/experience/public',
    '/api/identity/staff/login-policy', '/privacy', '/terms', '/refunds',
    '/api/auth/otp/request', '/api/identity/guest', '/api/identity/email/request',
    '/api/quote', '/api/orders', '/api/branches/nearest',
})


def is_public_brand_route(path):
    return path in _PUBLIC_BRAND_ROUTES or path.startswith(('/api/banner-assets/', '/api/product-assets/')) or (
        path.startswith('/api/products/') and path.endswith('/availability'))


def default_brand():
    """Resolve an omitted body/query brand from the verified request context."""
    return current_brand.get() or 'kps'


def resolve_brand(brand=None):
    brand = default_brand() if brand is None else brand
    require_brand(brand)
    return brand


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
        def brand_fragment(match):
            if brand != 'enbishan':
                return ''
            fragment = match.group(0)
            # This is the approved KPS campaign renderer, not a shared brand
            # contact. Never send its executable creative to another brand.
            if '/assets/kps-gift-creative.' in fragment:
                return ''
            if 'og:site_name' in fragment:
                return '''<meta property="og:type" content="website">
  <meta property="og:site_name" content="Enbishan">
  <meta property="og:locale" content="ar_SA">
  <meta property="og:title" content="عنبيشن | ENBISHAN">
  <meta property="og:description" content="الطعم اللي يعلّق بالذاكرة، صار أقرب لك 💛">
  <meta property="og:url" content="https://order.enbishan.com">
  <meta property="og:image" content="https://order.enbishan.com/assets/enbishan-share-20261025.jpg">
  <meta property="og:image:secure_url" content="https://order.enbishan.com/assets/enbishan-share-20261025.jpg">
  <meta property="og:image:type" content="image/jpeg">
  <meta property="og:image:width" content="1536">
  <meta property="og:image:height" content="1024">
  <meta property="og:image:alt" content="بوكسات عنبيشن مع ورق العنب والفتة والكبة">'''
            for old, new in [('KPS COFFEE', 'Enbishan'), ('كبز قهوة مختصة', 'عنبيشن'), ('0550472662', '0509895855'), ('966550472662', '966509895855'), ('+966 55 047 2662', '+966 50 989 5855'), ('kps.coffe', 'Enbishan'), ('Kps.cofee', 'Enbishan'), ('data-kps-contact', 'data-enbishan-contact')]:
                fragment = fragment.replace(old, new)
            return fragment
        source = re.sub(r'<!-- KPS_CONTACT_START -->.*?<!-- KPS_CONTACT_END -->', brand_fragment, source, flags=re.S)
        if brand == 'enbishan':
            source = source.replace('<html lang="ar" dir="rtl">', '<html lang="ar" dir="rtl" data-storefront-brand="enbishan">')
            source = source.replace('</head>', '<link rel="stylesheet" href="/assets/enbishan.css?v=20261026-box-covers"></head>')
        return source
    return source
