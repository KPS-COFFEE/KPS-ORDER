"""Default-off exact-host cutover; forwarding headers cannot authorize a host."""
import os
import re

from starlette.responses import JSONResponse

_HOST = re.compile(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z')


def _configured_hosts(name, default=''):
    hosts = frozenset(value.strip().lower() for value in os.getenv(name, default).split(',') if value.strip())
    if not hosts or any(not _HOST.fullmatch(host) for host in hosts):
        raise ValueError(name + ' must contain exact DNS hostnames without schemes, ports or wildcards')
    return hosts


class OfficialHostMiddleware:
    def __init__(self, app):
        self.app = app
        self.enabled = os.getenv('KPS_OFFICIAL_HOST_ENFORCEMENT_ENABLED') == 'true'
        self.hosts = _configured_hosts('KPS_OFFICIAL_HOSTS', 'order.kps.sa') if self.enabled else frozenset()
        raw_internal = os.getenv('KPS_INTERNAL_HEALTH_HOSTS', '')
        self.internal = _configured_hosts('KPS_INTERNAL_HEALTH_HOSTS') if self.enabled and raw_internal.strip() else frozenset()
        if any(host.endswith('.onrender.com') for host in self.hosts):
            raise ValueError('Render hosts belong only in KPS_INTERNAL_HEALTH_HOSTS')

    async def __call__(self, scope, receive, send):
        if not self.enabled or scope['type'] not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        values = [value.decode('latin1').lower() for key, value in scope.get('headers', []) if key.lower() == b'host']
        # Require one unambiguous Host. X-Forwarded-Host and Forwarded are not trusted.
        host = values[0] if len(values) == 1 else ''
        if host.endswith(':443') or host.endswith(':80'):
            host = host.rsplit(':', 1)[0]
        accepted = host in self.hosts
        internal_probe = (host in self.internal and scope['type'] == 'http'
                          and scope['method'] in {'GET', 'HEAD'}
                          and scope['path'] in {'/health', '/ready'})
        if accepted or internal_probe:
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket':
            return await send({'type': 'websocket.close', 'code': 1008})
        return await JSONResponse({'detail': {'code': 'OFFICIAL_HOST_REQUIRED',
            'message': 'استخدم الدومين الرسمي. / Use the official domain.'}}, status_code=421,
            headers={'Cache-Control': 'no-store'})(scope, receive, send)
