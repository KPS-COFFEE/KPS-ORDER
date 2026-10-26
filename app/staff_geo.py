"""Saudi-only work access at the verified Render/Cloudflare ingress.

Public Render web-service requests pass through Cloudflare, which replaces
CF-Connecting-IP. This source is an explicit deployment trust contract, never
enabled merely because a caller supplied a forwarding header. Other hosts must
use a separately reviewed source. Unknown/malformed ingress never means Saudi.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import threading
import time
from functools import lru_cache

from fastapi import HTTPException
from starlette.responses import HTMLResponse

LOGGER = logging.getLogger('kps_order')
_COUNTRY = re.compile(r'[A-Z]{2}\Z')
_RAY = re.compile(r'[a-fA-F0-9]{16,32}-[A-Z]{3}\Z')
_WORKER_IP = ipaddress.ip_address('2a06:98c0:3600::103')
_OBSERVATIONS = {}
_OBSERVATION_LOCK = threading.Lock()

# These shells are intentionally blocked before authentication. All staff
# principals are also checked in auth._find_session, including shared routes.
WORK_PAGES = frozenset({
    '/ops', '/ops.html', '/campaign-report', '/customer-crm', '/driver-hours',
    '/owner-phone', '/sms-login-test', '/docs/metrics',
})
WORK_ASSETS = frozenset({'/ops.js', '/report.js', '/owner-phone.js', '/sms-login-test.js'})
WORK_AUTH_PREFIXES = (
    '/api/identity/staff/', '/api/identity/owners/', '/api/identity/owner/',
    '/api/identity/owner-phone/', '/api/identity/owner-sms/',
)


def mode():
    return os.getenv('STAFF_GEO_MODE', 'off').strip().lower()


def enabled():
    return mode() == 'enforce'


def configured():
    return mode() in {'observe', 'enforce'}


def validate_configuration():
    if mode() not in {'off', 'observe', 'enforce'}:
        raise RuntimeError('STAFF_GEO_MODE must be off, observe or enforce')
    if configured():
        if os.getenv('STAFF_GEO_SOURCE') != 'render-cloudflare':
            raise RuntimeError('A verified staff geography ingress source is required')
        _proxy_networks(os.getenv('STAFF_GEO_TRUSTED_PROXY_CIDRS', ''))


@lru_cache(maxsize=8)
def _proxy_networks(value):
    try:
        networks = tuple(ipaddress.ip_network(item.strip(), strict=True)
                         for item in value.split(',') if item.strip())
        # Never accept a wildcard/public network as a trusted Render socket.
        if not networks or any(not net.is_private or net.prefixlen == 0 for net in networks):
            raise ValueError()
        return networks
    except ValueError:
        raise RuntimeError('Explicit private Render ingress proxy CIDRs are required') from None


def _single_header(request, name):
    values = [v.decode('latin1').strip() for k, v in request.scope.get('headers', ())
              if k.lower() == name]
    return values[0] if len(values) == 1 else None


def _public_ip(value):
    if not isinstance(value, str) or not value or len(value) > 45 or '%' in value:
        return None
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address if address.is_global and address != _WORKER_IP else None
    except ValueError:
        return None


def location(request):
    state = request.scope.setdefault('state', {})
    if '_staff_geo_location' in state:
        return state['_staff_geo_location']

    result = {'country_code': None, 'client_ip': None, 'source': 'unverified'}
    if os.getenv('STAFF_GEO_SOURCE') == 'render-cloudflare':
        try:
            peer = ipaddress.ip_address(request.scope.get('client', ('', 0))[0])
            trusted = any(peer in network for network in _proxy_networks(
                os.getenv('STAFF_GEO_TRUSTED_PROXY_CIDRS', '')) if peer.version == network.version)
        except (ValueError, TypeError, IndexError, RuntimeError):
            trusted = False
        ray = _single_header(request, b'cf-ray')
        ip = _public_ip(_single_header(request, b'cf-connecting-ip'))
        # Worker subrequests can have different IP semantics. They are never
        # an accepted source for interactive staff administration.
        worker = any(k.lower() == b'cf-worker' for k, _ in request.scope.get('headers', ()))
        if trusted and ip and ray and _RAY.fullmatch(ray) and not worker:
            country = _single_header(request, b'cf-ipcountry')
            result = {
                'country_code': country if country and _COUNTRY.fullmatch(country)
                    and country not in {'XX', 'ZZ'} else None,
                'client_ip': str(ip), 'source': 'render-cloudflare',
            }
        elif trusted:
            result['source'] = 'invalid-edge-metadata'
    state['_staff_geo_location'] = result
    return result


def is_work_entry(request):
    path = request.url.path
    return (path in WORK_PAGES or path in WORK_ASSETS
            or path in {'/api/auth/login', '/api/identity/step-up'}
            or path.startswith(WORK_AUTH_PREFIXES)
            or (path == '/api/session' and request.headers.get('x-kps-context') == 'work'))


def is_printer_bridge(request):
    return request.url.path.startswith('/api/printing/bridge/')


def deny():
    return HTTPException(403, {
        'code': 'STAFF_GEO_RESTRICTED',
        'message': 'دخول الإدارة متاح من داخل السعودية فقط. للدخول من خارجها يلزم إذن مؤقت من المالك. / Administration is available in Saudi Arabia only. Access from abroad requires a temporary owner authorization.',
    })


def require_domestic(request):
    if enabled() and location(request)['country_code'] != 'SA':
        raise deny()


def denied_page():
    # A neutral bilingual denial, never the admin application or login form.
    return HTMLResponse('''<!doctype html><html lang="ar" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>الدخول غير متاح | Access restricted</title>
<style>body{margin:0;background:#f5f5f5;color:#222;font:18px/1.9 system-ui,sans-serif;display:grid;min-height:100vh;place-items:center}main{max-width:520px;margin:24px;padding:32px;border:1px solid #ddd;border-radius:18px;background:white;text-align:center}h1{font-size:24px}p{margin:12px 0}.en{font-size:15px;color:#555}</style>
</head><body><main><h1>دخول الإدارة من داخل السعودية فقط</h1>
<p>للدخول من خارج السعودية، تواصل مع المالك للحصول على إذن مؤقت.</p>
<p class="en" lang="en" dir="ltr">Administration is available in Saudi Arabia only. Contact the owner for temporary access from abroad.</p>
</main></body></html>''', status_code=403,
        headers={'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer'})


def diagnostic_headers(request):
    if mode() != 'observe' or request.url.path not in {'/ops', '/ops.html', '/api/session'}:
        return {}
    info = location(request)
    digest = hashlib.sha256((info['client_ip'] or 'unknown').encode()).hexdigest()[:16]
    return {'X-Staff-Geo-Mode': 'observe', 'X-Staff-Geo-Country': info['country_code'] or 'UNKNOWN',
            'X-Staff-Geo-Source': info['source'], 'X-Staff-Geo-Client-Digest': digest}


def observe(request, trace):
    if mode() != 'observe':
        return
    info = location(request)
    category = 'printer' if is_printer_bridge(request) else 'work'
    key = (category, info['country_code'], info['source'])
    now = time.monotonic()
    with _OBSERVATION_LOCK:
        if now - _OBSERVATIONS.get(key, -1000) < 60:
            return
        if len(_OBSERVATIONS) >= 64:
            _OBSERVATIONS.clear()
        _OBSERVATIONS[key] = now
    LOGGER.warning('staff_geo_observation mode=%s category=%s country=%s source=%s trace=%s',
                   mode(), category, info['country_code'] or 'UNKNOWN', info['source'], trace)
