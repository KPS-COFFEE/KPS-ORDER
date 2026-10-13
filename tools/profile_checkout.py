"""Opt-in isolated CPU diagnosis; never loaded by the deployment entrypoint.

Run via tools.hardening_load with --profile-cpu. The normal performance gate
does not enable profiling. Reports contain function identifiers/timings only,
never request bodies, SQL, credentials or customer values.
"""
import atexit
import cProfile
import json
import os
from pathlib import Path
import pstats
import threading
import time

from tools.hardening_load import safe_url

if os.getenv('APP_ENV') != 'testing':
    raise RuntimeError('CPU diagnosis is restricted to an isolated test process.')
safe_url(os.environ['TEST_DATABASE_URL'])
if not os.environ.get('DATABASE_URL', '').startswith(os.environ['TEST_DATABASE_URL'].split('?')[0] + '?'):
    raise RuntimeError('CPU diagnosis requires the load harness isolated schema.')

import fastapi.dependencies.utils
import fastapi.routing
from starlette.concurrency import run_in_threadpool

_lock = threading.Lock()
_stats = pstats.Stats()
_calls = 0


async def measured_thread_call(func, /, *args, **kwargs):
    def measured():
        global _calls
        # Per-thread CPU excludes database waits and time running other threads.
        profile = cProfile.Profile(timer=time.thread_time)
        try:
            return profile.runcall(func, *args, **kwargs)
        finally:
            with _lock:
                _stats.add(profile)
                _calls += 1
    return await run_in_threadpool(measured)


fastapi.routing.run_in_threadpool = measured_thread_call
fastapi.dependencies.utils.run_in_threadpool = measured_thread_call


@atexit.register
def write_profile():
    rows = []
    with _lock:
        for (file, line, name), (primitive, calls, own, cumulative, callers) in _stats.stats.items():
            rows.append({'file': Path(file).name, 'line': line, 'function': name,
                         'calls': calls, 'cpu_self_seconds': round(own, 6),
                         'cpu_cumulative_seconds': round(cumulative, 6)})
    base = Path(os.environ['KPS_LOAD_PROFILE_OUTPUT'])
    output = base.with_name(f'{base.stem}-{os.getpid()}{base.suffix}')
    output.write_text(json.dumps({'mode': 'isolated_per_thread_cpu', 'thread_calls': _calls,
        'functions': sorted(rows, key=lambda row: row['cpu_self_seconds'], reverse=True)[:80]}, indent=2))


from app.main import app
