"""Security policy at the ASGI boundary, without per-request relay tasks."""
from __future__ import annotations

import logging
import os
import uuid

from fastapi import HTTPException, Request
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

from . import auth, monitoring, storefront_scope

LOGGER = logging.getLogger('kps_order')
MAX_BODY_BYTES = 8 * 1024 * 1024


def _body_too_large():
    return HTTPException(413, {'code': 'BODY_TOO_LARGE',
        'message': 'الملف أو الطلب أكبر من الحد المسموح.'})


async def _bounded_request(receive):
    """Validate the complete body before downstream dependencies can write.

    Content-Length is only an early rejection hint, not a trusted byte count.
    Raising inside FastAPI's request parser can turn a size violation into 400;
    truncating a valid prefix can let an endpoint commit a partial request.
    Keep a single-chunk request intact and coalesce streamed input into one
    bounded buffer, without unbounded per-chunk metadata or relay tasks.
    """
    message = await receive()
    if message['type'] == 'http.disconnect':
        return None
    if message['type'] != 'http.request':
        raise RuntimeError('Invalid HTTP request event.')
    size = len(message.get('body', b''))
    if size > MAX_BODY_BYTES:
        raise _body_too_large()
    if not message.get('more_body', False):
        return message

    body = bytearray(message.get('body', b''))
    while message.get('more_body', False):
        message = await receive()
        if message['type'] == 'http.disconnect':
            return None
        if message['type'] != 'http.request':
            raise RuntimeError('Invalid HTTP request event.')
        chunk = message.get('body', b'')
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise _body_too_large()
        body.extend(chunk)
    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path, method = scope['path'], scope['method']
        trace = uuid.uuid4().hex
        response_started = False

        async def secured_send(message):
            nonlocal response_started
            if message['type'] == 'http.response.start':
                status = message['status']
                headers = MutableHeaders(scope=message)
                headers['X-Request-ID'] = trace
                headers['X-Content-Type-Options'] = 'nosniff'
                headers['Referrer-Policy'] = 'same-origin'
                headers['X-Frame-Options'] = 'DENY'
                headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=(self)'
                headers['Content-Security-Policy'] = (
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
                    "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
                # Only the explicitly enabled storefront can load the map SDK.
                # URL-restricted public Mapbox tokens require an origin referrer.
                from .customer_map import enabled as customer_map_enabled
                if path in ('/', '/index.html') and customer_map_enabled():
                    headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
                    headers['Content-Security-Policy'] = (
                        "default-src 'self'; script-src 'self' https://api.mapbox.com/mapbox-gl-js/v3.33.0/; "
                        "style-src 'self' 'unsafe-inline' https://api.mapbox.com/mapbox-gl-js/v3.33.0/; "
                        "img-src 'self' data: blob:; font-src 'self'; worker-src 'self' blob:; "
                        "connect-src 'self' https://api.mapbox.com/v4/ https://api.mapbox.com/styles/v1/mapbox/ "
                        "https://api.mapbox.com/fonts/v1/mapbox/ https://api.mapbox.com/map-sessions/v1 https://events.mapbox.com/; "
                        "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
                if os.getenv('APP_ENV', 'production') == 'production':
                    headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
                public_image = (path.startswith(('/api/product-assets/', '/api/banner-assets/'))
                                and method in ('GET', 'HEAD') and status == 200)
                if path.startswith(('/api/', '/ops')) and not public_image:
                    # Retain a route's explicit private classification while
                    # enforcing no-store and dropping cacheable directives.
                    directives = {item.strip().split('=', 1)[0].lower()
                                  for item in headers.get('Cache-Control', '').split(',')}
                    headers['Cache-Control'] = 'private, no-store' if 'private' in directives else 'no-store'
                if path.startswith('/assets/') and status in (200, 304):
                    headers['Cache-Control'] = 'public, max-age=86400'
                monitoring.record_response(status)
                response_started = True
            # Preserve streaming responses, duplicate Set-Cookie headers,
            # disconnect handling and server extensions without relay tasks.
            await send(message)

        brand_token = storefront_scope.current_brand.set(storefront_scope.bound_brand(request))
        try:
            if path.startswith('/api/') and method not in {'GET', 'HEAD', 'OPTIONS'}:
                auth.check_origin(request)
            length = request.headers.get('content-length')
            if length and (not length.isdecimal() or int(length) > MAX_BODY_BYTES):
                raise _body_too_large()
            buffered_request = await _bounded_request(receive)
            if buffered_request is None:
                return

            storefront_scope.validate_scope(request, buffered_request.get('body', b''))

            async def bounded_receive():
                nonlocal buffered_request
                if buffered_request is not None:
                    message, buffered_request = buffered_request, None
                    return message
                return await receive()

            await self.app(scope, bounded_receive, secured_send)
        except Exception as exc:
            if response_started:
                # Headers already went over the wire: a second success/error
                # response would corrupt HTTP framing. Let the server terminate
                # the incomplete response, not silently acknowledge it.
                raise
            if isinstance(exc, HTTPException):
                response = JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
            else:
                LOGGER.error('request_failed trace=%s method=%s path=%s error_type=%s',
                             trace, method, path, type(exc).__name__)
                response = JSONResponse({'detail': {'code': 'SERVICE_UNAVAILABLE',
                    'message': 'تعذر تنفيذ العملية الآن. حاول لاحقًا.'}}, status_code=503)
            await response(scope, receive, secured_send)

        finally:
            storefront_scope.current_brand.reset(brand_token)
