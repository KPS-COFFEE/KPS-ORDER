"""Failure-only numeric diagnostics; never SQL, parameters or exception messages."""
from __future__ import annotations

import json
import logging
import math
import re
import time

from . import db

LOGGER = logging.getLogger('kps_order')
_POOL_COUNTERS = frozenset({
    'pool_size', 'pool_available', 'pool_min', 'pool_max', 'requests_waiting',
    'requests_num', 'requests_queued', 'requests_errors', 'requests_wait_ms',
    'usage_ms', 'connections_num', 'connections_errors', 'connections_lost',
    'connections_ms',
})


def _class_name(exc):
    return re.sub(r'[^A-Za-z0-9_]', '', type(exc).__name__)[:80]


def record_failure(request, exc):
    """Observe an already failed request without opening a DB connection.

    OperationalError also covers deadlocks, query cancellation and connection
    checkout failures. Their SQLSTATE and cause distinguish those cases while
    keeping the existing public 503 response deliberately generic. No request
    is replayed here: even GET handlers may contain durable business effects.
    """
    state = request.scope.get('state', {})
    trace = state.get('request_trace', '')
    trace = trace if isinstance(trace, str) and re.fullmatch(r'[a-f0-9]{32}', trace) else None
    started = state.get('request_started')
    elapsed = (round(max(0, time.monotonic() - started) * 1000, 3)
               if isinstance(started, (int, float)) and not isinstance(started, bool)
               and math.isfinite(started) else None)
    route = getattr(request.scope.get('route'), 'path', None)
    # A route template comes from the registered router, never URL/path_params.
    route = route if isinstance(route, str) and len(route) <= 256 else '<unresolved>'
    sqlstate = getattr(exc, 'sqlstate', None)
    sqlstate = sqlstate if isinstance(sqlstate, str) and re.fullmatch(r'[A-Z0-9]{5}', sqlstate) else None
    cause = exc.__cause__
    if cause is None and not exc.__suppress_context__:
        cause = exc.__context__
    pools = {}
    try:
        # Pool names are omitted, even though the current pool uses opaque names.
        for stats in db.pool_statistics().values():
            for key, value in stats.items():
                if (key in _POOL_COUNTERS and isinstance(value, (int, float))
                        and not isinstance(value, bool) and math.isfinite(value)):
                    pools[key] = pools.get(key, 0) + value
    except Exception:
        # Diagnostics must not turn the original recoverable 503 into an error.
        pools = None
    method = request.method if request.method in {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'} else 'OTHER'
    LOGGER.error('database_request_failed %s', json.dumps({
        'trace': trace, 'method': method, 'route': route, 'elapsed_ms': elapsed,
        'error_type': _class_name(exc), 'cause_type': _class_name(cause) if cause else None,
        'sqlstate': sqlstate, 'pool_counters': pools,
    }, sort_keys=True, separators=(',', ':')))
