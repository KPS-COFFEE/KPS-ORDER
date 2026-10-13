"""Security policy at the ASGI boundary, without per-request relay tasks."""
from __future__ import annotations

import logging
import os
import uuid

from fastapi import HTTPException, Request
from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse

from . import auth, monitoring

LOGGER = logging.getLogger('kps_order')


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
                if os.getenv('APP_ENV', 'production') == 'production':
                    headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
                public_image = (path.startswith(('/api/product-assets/', '/api/banner-assets/'))
                                and method in ('GET', 'HEAD') and status == 200)
                if path.startswith(('/api/', '/ops')) and not public_image:
                    headers['Cache-Control'] = 'no-store'
                if path.startswith('/assets/') and status in (200, 304):
                    headers['Cache-Control'] = 'public, max-age=86400'
                monitoring.record_response(status)
                response_started = True
            # Preserve streaming bodies, duplicate Set-Cookie headers, disconnect
            # handling and server extensions. Never buffer or spawn relay tasks.
            await send(message)

        try:
            if path.startswith('/api/') and method not in {'GET', 'HEAD', 'OPTIONS'}:
                auth.check_origin(request)
            length = request.headers.get('content-length')
            if length and (not length.isdecimal() or int(length) > 8 * 1024 * 1024):
                response = JSONResponse({'detail': {'code': 'BODY_TOO_LARGE',
                    'message': 'الملف أو الطلب أكبر من الحد المسموح.'}}, status_code=413)
                await response(scope, receive, secured_send)
                return
            await self.app(scope, receive, secured_send)
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
