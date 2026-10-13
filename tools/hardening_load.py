#!/usr/bin/env python3
"""Isolated HTTP/PostgreSQL load gate; never contacts the customer service.

Run with TEST_DATABASE_URL=postgresql://...@127.0.0.1:5432/test_hardening
python tools/hardening_load.py --output test-artifacts/hardening-load.json

Every run creates its own UUID schema in an explicitly named local test DB.
It starts two real Uvicorn workers by default (matching Docker), preserves application authorization/rate
limits, and represents customers using distinct actual loopback source IPs.
The gate is a local engineering criterion, not a production capacity SLA.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

RELEASE_WORKERS = 2
RELEASE_KEEPALIVE_SECONDS = 15


def safe_url(value):
    """Validate independently of inherited runtime configuration."""
    if not value.startswith(('postgresql://', 'postgres://')):
        raise ValueError('Explicit TEST_DATABASE_URL must be a PostgreSQL URL.')
    parts = conninfo_to_dict(value)
    name = parts.get('dbname', '')
    if parts.get('host') not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('Load tests require a loopback PostgreSQL host.')
    if not (name.startswith('test_') or name.endswith('_test')) or parts.get('options'):
        raise ValueError('Database name must be test_* or *_test, without session options.')
    return value


def schema_url(base, schema):
    parsed = urlsplit(base)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(('options', '-csearch_path=' + schema))
    return urlunsplit(parsed._replace(query=urlencode(query)))


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (index - low), 3)


def distribution(values):
    return {name: percentile(values, p) for name, p in [('p50_ms', .5), ('p95_ms', .95), ('p99_ms', .99)]}


def _namespace_pid(proc_path):
    try:
        if os.readlink(proc_path / 'ns/pid') != os.readlink('/proc/self/ns/pid'):
            return None
        line = next(line for line in (proc_path / 'status').read_text().splitlines() if line.startswith('NSpid:'))
        return int(line.split()[-1])
    except (OSError, ValueError, StopIteration):
        return None


def _proc_path(pid):
    direct = Path(f'/proc/{pid}')
    if _namespace_pid(direct) == pid:
        return direct
    # Some isolated executors mount an ancestor's /proc. Resolve only entries
    # in our own PID namespace, keeping all public/DB/process-control IDs local.
    for path in Path('/proc').glob('[0-9]*'):
        if _namespace_pid(path) == pid:
            return path
    return None


def proc_metrics(pid):
    """Linux worker CPU/memory without adding a runtime dependency."""
    try:
        proc_path = _proc_path(pid)
        if proc_path is None:
            return None
        stat = (proc_path / 'stat').read_text().rsplit(')', 1)[1].split()
        rss = int((proc_path / 'statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
        seconds = (int(stat[11]) + int(stat[12])) / os.sysconf('SC_CLK_TCK')
        return seconds, rss
    except (OSError, ValueError, IndexError):
        return None


def process_start_ticks(pid):
    try:
        proc_path = _proc_path(pid)
        if proc_path is None:
            return None
        stat = (proc_path / 'stat').read_text().rsplit(')', 1)[1].split()
        return None if stat[0] == 'Z' else int(stat[19])
    except (OSError, ValueError, IndexError):
        return None


def owned_process_pids(root_pid):
    """Only the launched process and actual /proc descendants, never name matches."""
    found, pending = set(), [root_pid]
    while pending:
        pid = pending.pop()
        proc_path = _proc_path(pid)
        if pid in found or proc_path is None:
            continue
        found.add(pid)
        fallback_needed = False
        try:
            for task in (proc_path / 'task').iterdir():
                try:
                    for value in (task / 'children').read_text().split():
                        local_pid = _namespace_pid(Path('/proc') / value)
                        if local_pid is None:
                            # The children file can use reader-namespace IDs
                            # even when the mounted /proc paths use ancestor IDs.
                            local_path = _proc_path(int(value))
                            local_pid = _namespace_pid(local_path) if local_path is not None else None
                        if local_pid is not None:
                            pending.append(local_pid)
                except (OSError, ValueError):
                    fallback_needed = True
                    continue
        except OSError:
            fallback_needed = True
        if fallback_needed:
            # CONFIG_CHECKPOINT_RESTORE can omit task/children. Read PPid from
            # /proc status instead, still requiring our exact PID namespace.
            for candidate in Path('/proc').glob('[0-9]*'):
                local_pid = _namespace_pid(candidate)
                if local_pid is None:
                    continue
                try:
                    parent = next(line for line in (candidate / 'status').read_text().splitlines() if line.startswith('PPid:'))
                    if int(parent.split()[1]) == int(proc_path.name):
                        pending.append(local_pid)
                except (OSError, ValueError, StopIteration):
                    continue
    return found


def process_tree_metrics(root_pid):
    rows = {}
    for pid in owned_process_pids(root_pid):
        metric = proc_metrics(pid)
        if metric:
            rows[pid] = {'cpu_seconds': metric[0], 'rss_bytes': metric[1]}
    return {'cpu_seconds': sum(row['cpu_seconds'] for row in rows.values()),
            'summed_rss_bytes': sum(row['rss_bytes'] for row in rows.values()),
            'processes': rows}


def exact_owned_pool_names(root_pid, db_url):
    suffix = hashlib.sha256(db_url.encode()).hexdigest()[:12]
    return {pid: f'kps_order_pool_{pid}_{suffix}' for pid in owned_process_pids(root_pid)}


def startup_worker_pids(log_contents, root_pid):
    owned = owned_process_pids(root_pid)
    return sorted({int(value) for value in re.findall(r'Started server process \[(\d+)\]', log_contents)
                   if int(value) in owned})


ANSI_CSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def parse_docker_stat_frame(line):
    # Streaming Docker stats emits terminal clear/home controls even with a
    # JSON format. Remove only those protocol controls, never printable text
    # that could conceal a genuine Docker error or malformed measurement.
    frame = ANSI_CSI.sub('', line).strip()
    if not frame:
        return None
    raw = json.loads(frame)
    match = re.match(r'([\d.]+)([A-Za-z]+)', raw['MemUsage'])
    units = {'B': 1, 'KiB': 1024, 'MiB': 1024 ** 2, 'GiB': 1024 ** 3,
             'kB': 1000, 'MB': 1000 ** 2, 'GB': 1000 ** 3}
    if not match or match[2] not in units:
        raise ValueError('Docker memory measurement has an invalid format.')
    cpu = float(raw['CPUPerc'].rstrip('%'))
    memory = float(match[1]) * units[match[2]]
    if not math.isfinite(cpu) or not math.isfinite(memory) or cpu < 0 or memory < 0:
        raise ValueError('Docker resource measurement must be finite and nonnegative.')
    return {'cpu_percent_one_core': cpu, 'memory_bytes': memory}


def summarize_wait_observations(samples):
    totals = {}
    blockers = {}
    for sample in samples:
        for group in sample.get('database_activity_groups', []):
            label = ':'.join(str(group[key] or 'none') for key in ('state', 'wait_type', 'wait_event', 'query_class'))
            aggregate = totals.setdefault(label, {'observed_connection_samples': 0, 'peak_connections': 0,
                                                  'max_observed_query_age_ms': 0})
            aggregate['observed_connection_samples'] += group['connections']
            aggregate['peak_connections'] = max(aggregate['peak_connections'], group['connections'])
            aggregate['max_observed_query_age_ms'] = max(aggregate['max_observed_query_age_ms'], group['max_query_age_ms'])
        for group in sample.get('blocking_query_groups', []):
            label = group['waiting_query_class'] + ' -> ' + group['blocking_query_class']
            aggregate = blockers.setdefault(label, {'observed_blocking_pairs': 0, 'peak_blocking_pairs': 0})
            aggregate['observed_blocking_pairs'] += group['blocking_pairs']
            aggregate['peak_blocking_pairs'] = max(aggregate['peak_blocking_pairs'], group['blocking_pairs'])
    return {'activity_groups': totals, 'blocking_groups': blockers,
        'method': 'Read-only pg_stat_activity/pg_blocking_pids; fixed query classes, no SQL text or parameters stored.',
        'timing_limit': 'Query age includes lock waits; sampled ages are not per-statement execution timings.'}


# Classification happens inside PostgreSQL. Only fixed category names leave
# the database; customer values and SQL text are never written to artifacts.
ACTIVITY_CTE = """
WITH activity AS (
 SELECT pid,state,wait_event_type,wait_event,query_start,
        CASE WHEN query LIKE '%%pg_advisory_xact_lock%%' THEN 'idempotency_advisory'
             WHEN query LIKE '%%order_branch_counters%%' THEN 'receipt_counter'
             WHEN query LIKE 'INSERT INTO rate_limits%%' THEN 'rate_limit'
             WHEN query LIKE '%%idempotency_keys%%' THEN 'idempotency_store'
             WHEN query LIKE 'INSERT INTO orders%%' THEN 'order_insert'
             WHEN query LIKE 'INSERT INTO order_events%%' THEN 'order_event'
             WHEN query LIKE 'INSERT INTO audit_events%%' THEN 'audit_event'
             WHEN query LIKE '%%identity_channels%%' THEN 'channel_lookup'
             WHEN query LIKE '%%app_settings%%' THEN 'settings_lookup'
             WHEN query LIKE '%%performance_provinces%%' OR query LIKE '%%province_performance_policies%%' THEN 'performance_policy'
             WHEN query LIKE '%%FOR SHARE%%' THEN 'quote_or_duty_read_lock'
             WHEN query LIKE 'COMMIT%%' THEN 'commit'
             ELSE 'other' END query_class
 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()
)
""".replace('%%', '%')


class Sampler:
    def __init__(self, db_url, app_pid, container=None, worker_pids=None):
        self.db_url, self.app_pid, self.container = db_url, app_pid, container
        self.stop = threading.Event()
        self.samples = []
        self.errors = Counter()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.docker_process = None
        self.docker_thread = None
        self.docker_samples = []
        self.cleanup_actions = []
        self.expected_workers = {pid: process_start_ticks(pid) for pid in (worker_pids or [app_pid])}

    def workers_are_live(self, metrics):
        return all(start is not None and pid in metrics['processes'] and process_start_ticks(pid) == start
                   for pid, start in self.expected_workers.items())

    def read_docker(self):
        try:
            self.docker_process = subprocess.Popen(['docker', 'stats', '--format', '{{json .}}', self.container],
                                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            for line in self.docker_process.stdout:
                if self.stop.is_set():
                    break
                sample = parse_docker_stat_frame(line)
                if sample is not None:
                    self.docker_samples.append(sample)
        except Exception as exc:
            self.errors['docker:' + type(exc).__name__] += 1

    def run(self):
        previous = None
        try:
            with psycopg.connect(self.db_url, autocommit=True, row_factory=dict_row, connect_timeout=5) as conn:
                conn.execute("SET statement_timeout='3000ms'")
                while not self.stop.is_set():
                    now = time.perf_counter()
                    row = conn.execute("SELECT count(*) FILTER(WHERE pid<>pg_backend_pid())::int AS connections, "
                        "count(*) FILTER(WHERE wait_event_type='Lock')::int AS lock_waits, "
                        "count(*) FILTER(WHERE state='idle in transaction')::int AS idle_in_transaction "
                        "FROM pg_stat_activity WHERE datname=current_database()").fetchone()
                    row['database_activity_groups'] = conn.execute(ACTIVITY_CTE + """
                        SELECT state,wait_event_type AS wait_type,wait_event,query_class,count(*)::int connections,
                               round(max(greatest(0,extract(epoch FROM (clock_timestamp()-query_start))*1000))::numeric,3)::float8 max_query_age_ms
                        FROM activity WHERE state<>'idle'
                        GROUP BY state,wait_event_type,wait_event,query_class
                    """).fetchall()
                    row['blocking_query_groups'] = conn.execute(ACTIVITY_CTE + """
                        SELECT waiting.query_class waiting_query_class,blocking.query_class blocking_query_class,
                               count(*)::int blocking_pairs
                        FROM activity waiting
                        CROSS JOIN LATERAL unnest(pg_blocking_pids(waiting.pid)) AS blocker(pid)
                        JOIN activity blocking ON blocking.pid=blocker.pid
                        WHERE waiting.wait_event_type='Lock'
                        GROUP BY waiting.query_class,blocking.query_class
                    """).fetchall()
                    metrics = process_tree_metrics(self.app_pid)
                    if metrics['processes'] and self.workers_are_live(metrics):
                        row['app_rss_bytes'] = metrics['summed_rss_bytes']
                        row['app_cpu_seconds'] = metrics['cpu_seconds']
                        row['app_process_metrics'] = metrics['processes']
                        if previous:
                            if set(metrics['processes']) != previous[2]:
                                self.errors['application_process_tree:changed_during_stage'] += 1
                            elif metrics['cpu_seconds'] >= previous[1]:
                                row['app_cpu_percent_one_core'] = round(100 * (metrics['cpu_seconds'] - previous[1]) / (now - previous[0]), 2)
                            else:
                                self.errors['application_process_tree:cpu_counter_regressed'] += 1
                        previous = now, metrics['cpu_seconds'], set(metrics['processes'])
                    else:
                        self.errors['application_workers:missing_or_replaced'] += 1
                    row['monotonic_seconds'] = now
                    self.samples.append(row)
                    self.stop.wait(.25)
        except Exception as exc:
            self.errors[type(exc).__name__] += 1

    def begin(self):
        self.thread.start()
        if self.container:
            self.docker_thread = threading.Thread(target=self.read_docker, daemon=True)
            self.docker_thread.start()

    def finish(self):
        self.stop.set()
        if self.docker_process and self.docker_process.poll() is None:
            try:
                self.docker_process.terminate()
                try:
                    self.docker_process.wait(timeout=2)
                    self.cleanup_actions.append('docker_stats_terminated')
                except subprocess.TimeoutExpired:
                    # Docker's interactive stats command can ignore SIGTERM.
                    # Terminate only this observation process, never the DB.
                    self.docker_process.kill()
                    self.docker_process.wait(timeout=2)
                    self.cleanup_actions.append('docker_stats_killed_after_sigterm_timeout')
            except Exception as exc:
                self.errors['docker_cleanup:' + type(exc).__name__] += 1
        if self.docker_thread:
            self.docker_thread.join(timeout=3)
            if self.docker_thread.is_alive():
                self.errors['docker_cleanup:reader_thread_timeout'] += 1
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            self.errors['postgresql_sampler:thread_timeout'] += 1
        summary = {'sample_interval_seconds': .25, 'sample_count': len(self.samples),
                   'sample_errors': dict(self.errors), 'cleanup_actions': self.cleanup_actions}
        for key in ('connections', 'lock_waits', 'idle_in_transaction', 'app_rss_bytes', 'app_cpu_percent_one_core'):
            values = [row[key] for row in self.samples if key in row]
            summary['peak_' + key] = max(values) if values else None
        cpu_samples = [row for row in self.samples if 'app_cpu_seconds' in row]
        if cpu_samples:
            summary['app_cpu_seconds'] = round(cpu_samples[-1]['app_cpu_seconds'] - cpu_samples[0]['app_cpu_seconds'], 3)
        summary['samples'] = self.samples
        summary['process_measurement_scope'] = 'Launched Uvicorn process and owned descendants; CPU sum, summed RSS includes shared pages per process.'
        summary['expected_worker_start_ticks'] = self.expected_workers
        summary['wait_diagnostics'] = summarize_wait_observations(self.samples)
        summary['postgresql_container'] = {
            'provided': bool(self.container), 'sample_count': len(self.docker_samples),
            'peak_cpu_percent_one_core': max((s['cpu_percent_one_core'] for s in self.docker_samples), default=None),
            'peak_memory_bytes': max((s['memory_bytes'] for s in self.docker_samples if s['memory_bytes'] is not None), default=None),
            'samples': self.docker_samples}
        if self.container and not self.docker_samples:
            summary['sample_errors']['docker:no_samples'] = 1
        return summary


def configure_synthetic_data():
    from app import db
    db.migrate()
    hours = [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}]
    settings = {'active': True, 'ordering_verified': True, 'pickup_enabled': True, 'car_enabled': True,
                'delivery_enabled': True, 'opening_hours': hours, 'delivery_hours': hours,
                'payment_methods': ['cash', 'card'], 'location': {'lat': 28.4, 'lng': 45.9},
                'location_verified': True, 'coverage': {'type': 'radius', 'center': {'lat': 28.4, 'lng': 45.9},
                'radius_meters': 50000}, 'delivery_fee_halalas': 500, 'minimum_order_halalas': 0}
    with db.tx() as conn:
        conn.execute("UPDATE branches SET settings=settings||%s WHERE id IN ('kps-branch-1','kps-branch-2')", (db.json(settings),))
        conn.execute("UPDATE branches SET settings=settings||%s WHERE id='kps-branch-1'", (db.json({'product_availability': {'kps-33': False, 'kps-34': False}}),))
        conn.execute("UPDATE products SET settings=settings||%s WHERE id IN ('kps-33','kps-34')", (db.json({'available_branch_ids': ['kps-branch-2']}),))
        conn.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('load-manager','TEST synthetic duty','delivery_manager','kps',%s)",
                     (db.json(['kps-branch-1', 'kps-branch-2']),))
        conn.execute("INSERT INTO shifts(id,user_id,brand_id,kind,branch_ids,status) VALUES('load-manager-duty','load-manager','kps','manager',%s,'open')",
                     (db.json(['kps-branch-1', 'kps-branch-2']),))


class Workload:
    def __init__(self, origin, marker, concurrency, rounds, timeout):
        self.origin, self.marker, self.concurrency = origin, marker, concurrency
        self.rounds, self.timeout = rounds, timeout
        self.requests = []
        self.errors = []
        self.confirmed = []
        self.active, self.peak_active = 0, 0
        self.completed_users = 0
        self.transport_retries = []

    async def request(self, client, method, path, **kwargs):
        started = time.perf_counter()
        recorded = False
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        try:
            response = await client.request(method, path, **kwargs)
            data = response.json()
            self.requests.append({'endpoint': path.split('?')[0], 'method': method,
                                  'latency_ms': (time.perf_counter() - started) * 1000,
                                  'status': response.status_code})
            recorded = True
            if response.status_code != 200:
                detail = data.get('detail', {})
                raise RuntimeError(f'HTTP {response.status_code}: {detail.get("code", "unknown") if isinstance(detail, dict) else "unknown"}')
            return data
        except Exception as exc:
            if not recorded:
                self.requests.append({'endpoint': path.split('?')[0], 'method': method,
                                      'latency_ms': (time.perf_counter() - started) * 1000, 'status': 0})
            failure = {'endpoint': path.split('?')[0], 'type': type(exc).__name__,
                       'message': '' if isinstance(exc, httpx.TransportError) else str(exc)[:180]}
            if isinstance(exc, httpx.TransportError):
                causes, seen, cause = [], set(), exc
                while cause is not None and id(cause) not in seen and len(causes) < 8:
                    seen.add(id(cause))
                    entry = {'type': type(cause).__name__}
                    if isinstance(cause, OSError) and isinstance(cause.errno, int):
                        entry['errno'] = cause.errno
                    causes.append(entry)
                    cause = cause.__cause__ or cause.__context__
                failure['transport_causes'] = causes
            self.errors.append(failure)
            raise
        finally:
            self.active -= 1

    async def submit_order_pair(self, client, body, key):
        # Settle both transmissions before retrying or closing the client.
        # A lost response must not abort the other in-flight acknowledgement.
        results = await asyncio.gather(*[self.request(client, 'POST', '/api/orders', json=body,
            headers={'Idempotency-Key': key}) for _ in range(2)], return_exceptions=True)
        # A settled authorization/domain or invalid response is definitive:
        # fail the pair without sending another request for its sibling.
        for result in results:
            if isinstance(result, httpx.TransportError):
                continue
            if isinstance(result, BaseException):
                raise result
            result['order']
        for index, result in enumerate(results):
            if not isinstance(result, httpx.TransportError):
                continue
            recovery = {'initial_error_type': type(result).__name__, 'attempts': 1, 'recovered': False}
            self.transport_retries.append(recovery)
            try:
                # Exactly one retry per ambiguous transmission, with the same
                # browser principal/cookies, body and business-intent key.
                results[index] = await self.request(client, 'POST', '/api/orders', json=body,
                                                    headers={'Idempotency-Key': key})
                recovery['recovered'] = True
            except Exception as exc:
                results[index] = exc
                recovery['retry_error_type'] = type(exc).__name__
        for result in results:
            if isinstance(result, BaseException):
                raise result
        if results[0]['order'] != results[1]['order']:
            raise AssertionError('Same-key transmissions returned inconsistent business order responses.')
        return results

    async def user(self, index, barrier):
        # Actual socket source addresses exercise rate controls unchanged. No
        # spoofed forwarded headers or dependency overrides are used.
        source = f'127.20.{index // 250}.{index % 250 + 1}'
        transport = httpx.AsyncHTTPTransport(local_address=source, retries=0,
                    limits=httpx.Limits(max_connections=2, max_keepalive_connections=2))
        async with httpx.AsyncClient(base_url=self.origin, transport=transport, timeout=self.timeout,
                                     headers={'Origin': self.origin}) as client:
            await barrier.wait()
            try:
                guest = await self.request(client, 'POST', '/api/identity/guest', json={
                    'brand_id': 'kps', 'display_name': f'TEST load {self.marker} {index}',
                    'phone': f'050{self.concurrency:03}{index:04}'})
                client.headers['X-CSRF-Token'] = guest['csrf_token']
                uid = guest['user']['id']
                for cycle in range(self.rounds):
                    service = ('delivery', 'pickup', 'car')[index % 3]
                    khaliya = service == 'delivery' and index % 5 == 0
                    await self.request(client, 'GET', f'/api/catalog?branch_id=kps-branch-1&service={service}')
                    items = [{'product_id': 'kps-01', 'quantity': 1}]
                    if khaliya:
                        items.append({'product_id': 'kps-33', 'quantity': 1, 'units': [{'options': {'TYPE': 'classic'}}]})
                    notes = f'{self.marker}:{index}:{cycle}'
                    body = {'brand_id': 'kps', 'branch_id': 'kps-branch-1', 'service': service,
                            'payment_method': 'cash', 'items': items, 'notes': notes,
                            'customer': {'name': 'TEST synthetic customer', 'neighborhood': 'TEST حي',
                                         'address': 'TEST synthetic address', 'location': {'lat': 28.4, 'lng': 45.9},
                                         'car': {'plate': 'TEST', 'color': 'black'}}}
                    quote = (await self.request(client, 'POST', '/api/quote', json=body))['quote']
                    body['expected_total_halalas'] = quote['total_halalas']
                    key = uuid.uuid4().hex
                    # Two concurrent transmissions of one customer intent.
                    results = await self.submit_order_pair(client, body, key)
                    first, replay = [result['order'] for result in results]
                    if first['id'] != replay['id']:
                        raise AssertionError('Same-key retry created distinct business orders.')
                    for acknowledgement in (first, replay):
                        if acknowledgement['total_halalas'] != quote['total_halalas'] or acknowledgement['snapshot']['items'] != quote['items']:
                            raise AssertionError('Order differs from server-priced basket.')
                    read = (await self.request(client, 'GET', '/api/orders/' + first['id']))['order']
                    if read['id'] != first['id'] or read['total_halalas'] != first['total_halalas']:
                        raise AssertionError('Confirmed order could not be read consistently.')
                    self.confirmed.append({'id': first['id'], 'user': uid, 'key': key, 'notes': notes,
                                           'total': quote['total_halalas'], 'item_count': len(quote['items']),
                                           'execution_branch': 'kps-branch-2' if khaliya else 'kps-branch-1'})
                self.completed_users += 1
            except Exception as exc:
                if isinstance(exc, (AssertionError, KeyError)):
                    self.errors.append({'endpoint': 'business-validation', 'type': type(exc).__name__, 'message': str(exc)[:180]})

    async def run(self):
        barrier = asyncio.Event()
        tasks = [asyncio.create_task(self.user(index, barrier)) for index in range(self.concurrency)]
        # All client sessions have been constructed before simultaneous release.
        await asyncio.sleep(0)
        started = time.perf_counter()
        barrier.set()
        await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - started
        metrics = distribution([row['latency_ms'] for row in self.requests])
        metrics.update({'duration_seconds': round(elapsed, 3), 'requests': len(self.requests),
                        'http_throughput_per_second': round(len(self.requests) / elapsed, 3),
                        'business_orders_per_second': round(len(self.confirmed) / elapsed, 3),
                        'error_rate': len(self.errors) / max(1, len(self.requests)),
                        'error_count': len(self.errors), 'error_samples': self.errors[:20],
                        'completed_users': self.completed_users, 'peak_in_flight_http_requests': self.peak_active,
                        'statuses': dict(Counter(str(row['status']) for row in self.requests)),
                        'transport_retry_attempts': len(self.transport_retries),
                        'transport_retry_recovered': sum(row['recovered'] for row in self.transport_retries),
                        'transport_retry_failed': sum(not row['recovered'] for row in self.transport_retries),
                        'transport_retry_samples': self.transport_retries[:20]})
        metrics['by_endpoint'] = {}
        for key in sorted({(row['method'], row['endpoint']) for row in self.requests}):
            rows = [row for row in self.requests if (row['method'], row['endpoint']) == key]
            # Order-specific paths are consolidated to avoid hundreds of report fields.
            label = key[0] + ' ' + ('/api/orders/{id}' if key[1].startswith('/api/orders/') else key[1])
            destination = metrics['by_endpoint'].setdefault(label, [])
            destination.extend(row['latency_ms'] for row in rows)
        metrics['by_endpoint'] = {key: dict(distribution(values), requests=len(values))
                                  for key, values in metrics['by_endpoint'].items()}
        return metrics


def reconcile(db_url, workload):
    expected = workload.concurrency * workload.rounds
    failures = []
    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        rows = conn.execute("SELECT * FROM orders WHERE snapshot->>'notes' LIKE %s", (workload.marker + ':%',)).fetchall()
        ids = [row['id'] for row in rows]
        by_id = {row['id']: row for row in rows}
        duplicate_intents = conn.execute("SELECT count(*) n FROM (SELECT snapshot->>'notes' FROM orders "
            "WHERE snapshot->>'notes' LIKE %s GROUP BY snapshot->>'notes' HAVING count(*)<>1) bad", (workload.marker + ':%',)).fetchone()['n']
        events = conn.execute("SELECT order_id,count(*) n FROM order_events WHERE order_id=ANY(%s) "
                             "AND event_type='order.created' GROUP BY order_id", (ids,)).fetchall()
        audits = conn.execute("SELECT entity_id,count(*) n FROM audit_events WHERE entity_id=ANY(%s) "
                             "AND action='order.created' GROUP BY entity_id", (ids,)).fetchall()
        idem = conn.execute("SELECT count(*) n FROM idempotency_keys WHERE operation='orders.create' "
                            "AND response->'order'->>'id'=ANY(%s)", (ids,)).fetchone()['n']
        references = [row['snapshot'].get('order_reference') for row in rows]
        for acknowledged in workload.confirmed:
            row = by_id.get(acknowledged['id'])
            if not row:
                failures.append('Acknowledged order is absent from PostgreSQL.')
                continue
            if (row['customer_id'] != acknowledged['user'] or row['total_halalas'] != acknowledged['total'] or
                len(row['snapshot']['items']) != acknowledged['item_count'] or
                row['branch_id'] != acknowledged['execution_branch'] or
                row['total_halalas'] != row['subtotal_halalas'] + row['delivery_fee_halalas']):
                failures.append('Order identity, price, basket or executing branch differs in PostgreSQL.')
        orphan = conn.execute('SELECT count(*) n FROM orders o LEFT JOIN users u ON u.id=o.customer_id '
            'LEFT JOIN branches b ON b.id=o.branch_id WHERE o.id=ANY(%s) AND (u.id IS NULL OR b.id IS NULL)', (ids,)).fetchone()['n']
    if len(rows) != expected or len(workload.confirmed) != expected:
        failures.append('Business intent count differs from persisted/acknowledged order count.')
    if duplicate_intents or len(set(references)) != len(references) or None in references:
        failures.append('Duplicate business intent/receipt reference or missing receipt reference.')
    if len(events) != len(rows) or any(row['n'] != 1 for row in events):
        failures.append('Creation events missing or duplicated.')
    if len(audits) != len(rows) or any(row['n'] != 1 for row in audits):
        failures.append('Creation audit missing or duplicated.')
    if idem != len(rows) or orphan:
        failures.append('Replay persistence mismatch or orphaned order.')
    return {'expected_orders': expected, 'acknowledged_orders': len(workload.confirmed), 'persisted_orders': len(rows),
            'creation_events': len(events), 'creation_audits': len(audits), 'idempotency_rows': idem,
            'duplicate_business_intents': duplicate_intents, 'orphan_orders': orphan,
            'distinct_receipt_references': len(set(references)), 'failure_reasons': sorted(set(failures)),
            'passed': not failures}


def start_app(env, port, log_path, workers=RELEASE_WORKERS):
    log = log_path.open('a')
    offset = log_path.stat().st_size
    module = 'tools.profile_checkout:app' if env.get('KPS_LOAD_PROFILE_OUTPUT') else 'app.main:app'
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', module, '--host', '127.0.0.1',
                                '--port', str(port), '--workers', str(workers),
                                '--timeout-keep-alive', str(RELEASE_KEEPALIVE_SECONDS), '--no-access-log'], cwd=ROOT, env=env,
                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    origin = f'http://127.0.0.1:{port}'
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=2) as client:
            while time.perf_counter() - started < 45:
                if process.poll() is not None:
                    raise RuntimeError('Isolated application startup failed; inspect load-app.log.')
                try:
                    response = client.get(origin + '/ready')
                    if response.status_code == 200:
                        with log_path.open() as observed:
                            observed.seek(offset)
                            contents = observed.read()
                        worker_pids = startup_worker_pids(contents, process.pid)
                        if len(worker_pids) == workers and contents.count('Application startup complete.') >= workers:
                            return process, log, round(time.perf_counter() - started, 3), worker_pids
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
        raise RuntimeError('Isolated application readiness timed out.')
    except Exception:
        stop_app(process, log)
        raise


def stop_app(process, log):
    identities = {pid: process_start_ticks(pid) for pid in owned_process_pids(process.pid)}
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            # Popen created a dedicated session; the group belongs solely to
            # this isolated app, including both comparison workers/helpers.
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    # A supervisor may exit before a child completes shutdown. The group can
    # be signaled only while a captured, still-identical descendant remains
    # in the new session created for this invocation.
    def remaining_owned():
        result = []
        for pid, start in identities.items():
            if start is not None and process_start_ticks(pid) == start:
                try:
                    if os.getsid(pid) == process.pid:
                        result.append(pid)
                except ProcessLookupError:
                    pass
        return result
    if remaining_owned():
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 2
        while remaining_owned() and time.monotonic() < deadline:
            time.sleep(.05)
        if remaining_owned():
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    log.close()


def order_fingerprints(db_url):
    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        return {row['id']: row['fingerprint'] for row in conn.execute(
            'SELECT id,md5(row_to_json(o)::text) fingerprint FROM orders o ORDER BY id').fetchall()}


def inspect_test_container(container, base_url):
    """Mutations are restricted to the explicit, local synthetic PG service."""
    inspected = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
    env = dict(item.split('=', 1) for item in inspected['Config']['Env'] if '=' in item)
    info = conninfo_to_dict(base_url)
    if env.get('POSTGRES_DB') != info['dbname'] or not env['POSTGRES_DB'].startswith('test_'):
        raise ValueError('Docker service must advertise the exact named test PostgreSQL database.')
    bindings = inspected['NetworkSettings']['Ports'].get('5432/tcp') or []
    if not any(binding['HostPort'] == str(info.get('port', '5432')) for binding in bindings):
        raise ValueError('Docker PostgreSQL published port must match the explicit loopback test URL.')


def wait_ready(origin, timeout=45):
    start = time.perf_counter()
    with httpx.Client(timeout=2) as client:
        while time.perf_counter() - start < timeout:
            try:
                if client.get(origin + '/ready').status_code == 200:
                    return round(time.perf_counter() - start, 3)
            except httpx.HTTPError:
                pass
            time.sleep(.1)
    raise RuntimeError('Isolated application did not recover within the bounded readiness window.')


def database_fault_drills(db_url, base_url, origin, container, app_pid):
    inspect_test_container(container, base_url)
    report = {'container_safety_verified': True, 'outage': {}, 'connection_capacity': {}, 'passed': False}
    before = order_fingerprints(db_url)
    # Insert an actual business row inside an intentionally uncommitted
    # PostgreSQL transaction, then destroy its connection by stopping PG.
    identifier = 'load_uncommitted_' + uuid.uuid4().hex
    connection = psycopg.connect(db_url)
    stopped = False
    try:
        cursor = connection.execute('INSERT INTO orders(id,brand_id,branch_id,customer_id,service,delivery_status,payment_method,'
            'subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer,performance_snapshot,delivery_operational_date) '
            "SELECT %s,brand_id,branch_id,customer_id,service,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,"
            "total_halalas,jsonb_set(snapshot,'{order_reference}',%s),customer,performance_snapshot,delivery_operational_date "
            'FROM orders ORDER BY created_at LIMIT 1 RETURNING id', (identifier, psycopg.types.json.Jsonb(identifier)))
        if not cursor.fetchone():
            raise RuntimeError('No synthetic persisted business row available for rollback drill.')
        stopped = True
        subprocess.run(['docker', 'stop', '--time', '2', container], check=True, capture_output=True, timeout=30)
        # Allow the application's bounded five-second checkout to produce its
        # real 503 response, including response transport/scheduling overhead.
        with httpx.Client(base_url=origin, timeout=15) as client:
            report['outage']['readiness_http_status_during_outage'] = client.get('/ready').status_code
            report['outage']['liveness_http_status_during_outage'] = client.get('/health').status_code
        try:
            connection.commit()
            report['outage']['interrupted_commit_raised'] = False
        except psycopg.Error as exc:
            report['outage']['interrupted_commit_raised'] = True
            report['outage']['interrupted_commit_error_type'] = type(exc).__name__
    except Exception as exc:
        report['outage']['error_type'] = type(exc).__name__
    finally:
        connection.close()
        # Always restore this isolated service, including failed drill paths.
        try:
            if stopped:
                subprocess.run(['docker', 'start', container], check=True, capture_output=True, timeout=30)
            report['outage']['recovery_to_ready_seconds'] = wait_ready(origin)
        except Exception as exc:
            report['outage']['recovery_error_type'] = type(exc).__name__
    try:
        after = order_fingerprints(db_url)
        report['outage']['committed_order_count'] = len(before)
        report['outage']['committed_order_fingerprints_preserved'] = before == after
        report['outage']['uncommitted_order_rolled_back'] = identifier not in after
        report['outage']['passed'] = (report['outage'].get('readiness_http_status_during_outage') == 503 and
            report['outage'].get('liveness_http_status_during_outage') == 200 and
            report['outage'].get('interrupted_commit_raised') is True and before == after and identifier not in after and
            'recovery_error_type' not in report['outage'] and 'error_type' not in report['outage'])
    except Exception as exc:
        report['outage']['post_recovery_error_type'] = type(exc).__name__
        return report
    holders = []
    capacity = report['connection_capacity']
    try:
        control = psycopg.connect(db_url, autocommit=True)
        holders.append(control)
        maximum = int(control.execute('SHOW max_connections').fetchone()[0])
        users_before = control.execute('SELECT count(*) FROM users').fetchone()[0]
        capacity['configured_max_connections'] = maximum
        if maximum > 200:
            raise RuntimeError('Safety cap refuses to occupy more than 200 database connections.')
        owned_names = exact_owned_pool_names(app_pid, db_url)
        if not owned_names:
            raise RuntimeError('Isolated application process ownership could not be verified.')
        capacity['owned_application_process_pids'] = sorted(owned_names)
        capacity['terminated_isolated_app_pool_backends'] = 0
        # Idle pooled sockets can still execute at global connection capacity.
        # Fill every remaining slot, terminate only this isolated application's
        # named backends, then occupy freed slots before measuring failures.
        # Repeat boundedly if a pool worker wins a reconnect race.
        for attempt in range(10):
            for _ in range(maximum + 1):
                try:
                    holders.append(psycopg.connect(db_url, autocommit=True, connect_timeout=2))
                except psycopg.OperationalError:
                    capacity['exhaustion_reached'] = True
                    break
            app_backends = control.execute("SELECT pid FROM pg_stat_activity WHERE datname=current_database() "
                                           "AND application_name=ANY(%s)", (list(owned_names.values()),)).fetchall()
            if app_backends:
                for (pid,) in app_backends:
                    control.execute('SELECT pg_terminate_backend(%s)', (pid,))
                capacity['terminated_isolated_app_pool_backends'] += len(app_backends)
                continue
            capacity['remaining_isolated_app_pool_backends'] = 0
            capacity['client_backend_count_at_capacity'] = control.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend'").fetchone()[0]
            if capacity.get('exhaustion_reached'):
                break
        else:
            raise RuntimeError('Could not genuinely exhaust the isolated application pool within bounded attempts.')
        capacity['held_connections'] = len(holders)
        with httpx.Client(base_url=origin, headers={'Origin': origin}, timeout=15) as client:
            capacity['readiness_http_status_at_capacity'] = client.get('/ready').status_code
            capacity['liveness_http_status_at_capacity'] = client.get('/health').status_code
            started = time.perf_counter()
            response = client.post('/api/identity/guest', json={
                'brand_id': 'kps', 'display_name': 'TEST capacity failure', 'phone': '0509999999'})
            capacity['write_http_status_at_capacity'] = response.status_code
            capacity['write_error_code'] = response.json().get('detail', {}).get('code')
            capacity['write_failure_latency_ms'] = round((time.perf_counter() - started) * 1000, 3)
    except Exception as exc:
        capacity['error_type'] = type(exc).__name__
    finally:
        for holder in holders:
            holder.close()
        try:
            capacity['release_to_ready_seconds'] = wait_ready(origin)
            with psycopg.connect(db_url) as conn:
                users_after = conn.execute('SELECT count(*) FROM users').fetchone()[0]
            capacity['failed_write_left_no_partial_user'] = users_before == users_after
            capacity['committed_order_fingerprints_preserved'] = before == order_fingerprints(db_url)
        except Exception as exc:
            capacity['recovery_error_type'] = type(exc).__name__
    capacity['passed'] = (capacity.get('exhaustion_reached') is True and
        capacity.get('remaining_isolated_app_pool_backends') == 0 and
        capacity.get('client_backend_count_at_capacity') == capacity.get('configured_max_connections') and
        capacity.get('readiness_http_status_at_capacity') == 503 and capacity.get('liveness_http_status_at_capacity') == 200 and
        capacity.get('write_http_status_at_capacity') == 503 and capacity.get('failed_write_left_no_partial_user') is True and
        capacity.get('committed_order_fingerprints_preserved') is True and 'error_type' not in capacity and 'recovery_error_type' not in capacity)
    report['passed'] = report['outage'].get('passed') is True and capacity['passed']
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, default=ROOT / 'test-artifacts/hardening-load.json')
    parser.add_argument('--levels', default='50,100,250,500')
    parser.add_argument('--rounds', type=int, default=2)
    parser.add_argument('--request-timeout', type=float, default=60)
    parser.add_argument('--p95-ms', type=float, default=5000)
    parser.add_argument('--p99-ms', type=float, default=10000)
    parser.add_argument('--max-error-rate', type=float, default=.01)
    parser.add_argument('--database-container', help='Optional local Docker PG service ID for container CPU/RAM samples.')
    parser.add_argument('--profile-cpu', action='store_true', help='Separate diagnostic run, not release performance evidence.')
    parser.add_argument('--workers', type=int, choices=(1, 2), default=RELEASE_WORKERS,
                        help='Two matches Docker release configuration; one is an explicit diagnostic comparison.')
    args = parser.parse_args()
    if args.profile_cpu and args.workers != 1:
        parser.error('Per-thread CPU diagnosis requires one isolated worker.')
    levels = [int(item) for item in args.levels.split(',')]
    if not levels or min(levels) < 1 or max(levels) > 500 or not 1 <= args.rounds <= 5:
        parser.error('Levels must be 1..500 and rounds 1..5.')
    base = safe_url(os.environ.get('TEST_DATABASE_URL', ''))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tree_files = sorted([*ROOT.joinpath('app').glob('*.py'), *ROOT.joinpath('migrations').glob('*.sql'), Path(__file__)])
    tree_hash = hashlib.sha256()
    for path in tree_files:
        tree_hash.update(str(path.relative_to(ROOT)).encode())
        tree_hash.update(path.read_bytes())
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'head': subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), 'status': 'NOT_READY', 'stages': [],
        'tested_source_sha256': tree_hash.hexdigest(), 'tested_tree_dirty': bool(subprocess.check_output(
            ['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()),
        'gate': {'p95_ms_max': args.p95_ms, 'p99_ms_max': args.p99_ms, 'unexpected_error_rate_max': args.max_error_rate,
                 'data_invariant_failures_max': 0, 'deadlocks_max': 0, 'all_requested_levels_required': True},
        'methodology': {'workers': args.workers, 'rounds_per_user': args.rounds, 'requested_levels': levels,
            'real_guest_identity': True, 'real_HTTP': True, 'real_PostgreSQL': True,
            'concurrent_same_key_order_transmissions': 2, 'source_address_strategy': 'distinct actual 127.20.x.x socket IPs',
            'transport_recovery': 'Await both transmissions; at most one retry per TransportError, same session/body/key; original failures remain in error rate/latencies.',
            'traffic': 'Guest -> catalog -> server quote -> concurrent create/replay -> scoped order read; fresh key per cycle',
            'scope': 'closed-loop simultaneous bursts; no think time, not an open-loop sustained production soak',
            'safety': 'UUID test schema, synthetic customers, disabled providers/background delivery, localhost HTTP only'},
        'environment': {'python': platform.python_version(), 'platform': platform.platform(), 'logical_cpus': os.cpu_count(),
            'cpu_quota': Path('/sys/fs/cgroup/cpu.max').read_text().strip() if Path('/sys/fs/cgroup/cpu.max').exists() else 'unavailable',
            'memory_limit_bytes': Path('/sys/fs/cgroup/memory.max').read_text().strip() if Path('/sys/fs/cgroup/memory.max').exists() else 'unavailable'},
        'comparability_limits': [
            'Two workers match Docker CMD; one is an explicit diagnostic comparison. Local CPU/RAM and DB/network differ from deployed Render resources.',
            'Repository Blueprint specifies 0.5 CPU / 512 MiB; these measurements do not certify its production 500-user capacity.',
            'DB connection/lock metrics sample every 250 ms; transient peaks below the interval may be missed.',
            'App CPU/RSS aggregate the owned process tree; summed RSS counts shared pages in each process and is not unique physical memory.',
            'Load generator, app and PostgreSQL share the CI runner; this is not a remote-client benchmark or production hardware certification.',
            'No customer/production data, payments, physical printers or external notification providers are exercised.']}
    schema = 'kps_load_' + uuid.uuid4().hex
    isolated = schema_url(base, schema)
    process = log = sampler = None
    workloads = []
    previous_env = dict(os.environ)
    try:
        with psycopg.connect(base, autocommit=True, row_factory=dict_row) as control:
            actual = control.execute('SELECT current_database() name,version() version').fetchone()
            if not (actual['name'].startswith('test_') or actual['name'].endswith('_test')):
                raise ValueError('Connected PostgreSQL is not an explicitly named test database.')
            report['environment']['postgresql'] = actual['version']
            report['environment']['max_connections'] = control.execute('SHOW max_connections').fetchone()['max_connections']
            control.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        env = {key: value for key, value in previous_env.items() if not key.startswith((
            'TAWKED_', 'TWILIO_', 'RENDER_', 'RESEND_', 'SMTP_', 'RECOVERY_', 'MONITOR_', 'MONITORING_')) and
            key not in {'DATABASE_URL', 'MAIL_OUTBOX_KEY', 'EMAIL_FROM', 'KPS_LOAD_PROFILE_OUTPUT'}}
        if args.profile_cpu:
            env['KPS_LOAD_PROFILE_OUTPUT'] = str(args.output.parent.resolve() / 'checkout-cpu-profile.json')
            report['methodology']['diagnostic_profile_not_release_evidence'] = True
        env.update({'DATABASE_URL': isolated, 'APP_ENV': 'testing', 'PUBLIC_BASE_URL': f'http://127.0.0.1:{port}',
            'APP_ALLOWED_HOSTS': 'localhost,127.0.0.1', 'SESSION_COOKIE_SECURE': 'false',
            'OTP_PROVIDER': 'disabled', 'EMAIL_PROVIDER': 'disabled', 'EMAIL_WORKER_ENABLED': 'false',
            'REPORT_WORKER_ENABLED': 'false', 'INITIAL_CATALOG_IMPORT': 'true', 'RUNTIME_REVISION': report['head']})
        os.environ.clear()
        os.environ.update(env)
        configure_synthetic_data()
        from app import db
        db.close_pools(isolated)
        process, log, report['startup_seconds'], worker_pids = start_app(env, port, args.output.parent / 'load-app.log', args.workers)
        report['methodology']['verified_startup_worker_pids'] = worker_pids
        origin = f'http://127.0.0.1:{port}'
        for concurrency in levels:
            marker = f'load-{uuid.uuid4().hex}-{concurrency}'
            with psycopg.connect(isolated, row_factory=dict_row) as conn:
                before = conn.execute('SELECT deadlocks FROM pg_stat_database WHERE datname=current_database()').fetchone()['deadlocks']
            sampler = Sampler(isolated, process.pid, args.database_container, worker_pids)
            sampler.begin()
            workload = Workload(origin, marker, concurrency, args.rounds, args.request_timeout)
            workloads.append(workload)
            metrics = asyncio.run(workload.run())
            # Persist actual workload results before observation cleanup or
            # reconciliation can fail, so diagnostics are never masked.
            stage = {'concurrent_users': concurrency, 'metrics': metrics, 'passed': False,
                     'gate_failure_reasons': ['Observation/reconciliation not yet completed.']}
            report['stages'].append(stage)
            args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
            integrity = reconcile(isolated, workload)
            stage['database_integrity'] = integrity
            args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
            resources = sampler.finish()
            sampler = None
            with psycopg.connect(isolated, row_factory=dict_row) as conn:
                after = conn.execute('SELECT deadlocks FROM pg_stat_database WHERE datname=current_database()').fetchone()['deadlocks']
            violations = list(integrity['failure_reasons'])
            if metrics['p95_ms'] is None or metrics['p95_ms'] > args.p95_ms:
                violations.append('P95 latency gate failed.')
            if metrics['p99_ms'] is None or metrics['p99_ms'] > args.p99_ms:
                violations.append('P99 latency gate failed.')
            if metrics['error_rate'] > args.max_error_rate:
                violations.append('HTTP/business error rate gate failed.')
            if after != before:
                violations.append('PostgreSQL deadlocks increased.')
            if resources['sample_errors'] or not resources['sample_count']:
                violations.append('Resource/connection/lock monitoring failed.')
            stage.update({'resources': resources, 'deadlocks_delta': after - before,
                          'passed': not violations, 'gate_failure_reasons': violations})
            args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
            print(json.dumps({'concurrent_users': concurrency, 'passed': stage['passed'], 'p95_ms': metrics['p95_ms'],
                              'p99_ms': metrics['p99_ms'], 'persisted_orders': integrity['persisted_orders'],
                              'errors': metrics['error_count'], 'failure_reasons': violations}), flush=True)
            if violations:
                report['blocked_levels'] = levels[len(report['stages']):]
                report['reason'] = 'Stopped escalation after failed stage; investigate and rerun before attempting blocked levels.'
                break
        # Restart only this isolated process, then reconcile all acknowledged
        # orders after the application reruns its real additive migrations.
        if args.database_container:
            report['database_fault_drills'] = database_fault_drills(isolated, base, origin, args.database_container, process.pid)
        else:
            report['database_fault_drills'] = {'status': 'UNVERIFIED', 'reason': 'No explicitly verified local Docker PostgreSQL service was supplied.'}
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        stop_app(process, log)
        process = log = None
        process, log, recovery_seconds, restarted_worker_pids = start_app(env, port, args.output.parent / 'load-app.log', args.workers)
        recovery_checks = [reconcile(isolated, workload) for workload in workloads]
        report['recovery'] = {'process_restart_to_ready_seconds': recovery_seconds,
            'confirmed_orders_preserved': all(check['passed'] for check in recovery_checks),
            'verified_restarted_worker_pids': restarted_worker_pids,
            'worker_count_matches': len(restarted_worker_pids) == args.workers,
            'post_restart_reconciliation': recovery_checks}
        with httpx.Client(base_url=origin, timeout=10) as client:
            ready = client.get('/ready').json()
            report['recovery']['revision_matches'] = ready['revision'] == report['head']
            report['recovery']['migration_preservation_verified'] = ready['data_preservation']['verified']
        fault_drills_ok = not args.database_container or report['database_fault_drills'].get('passed') is True
        if all(stage['passed'] for stage in report['stages']) and len(report['stages']) == len(levels) and report['recovery']['revision_matches'] and report['recovery']['migration_preservation_verified'] and report['recovery']['confirmed_orders_preserved'] and report['recovery']['worker_count_matches'] and fault_drills_ok:
            report['status'] = 'PASS_LOCAL_ENGINEERING_GATE'
            report['reason'] = 'All requested load levels and PostgreSQL business invariants passed; production hardware/provider validation remains separate.'
    except Exception as exc:
        # No exception values/DSNs are printed, because startup errors may
        # contain connection configuration. Detailed app errors are sanitized.
        report['fatal_error_type'] = type(exc).__name__
        report['reason'] = 'Isolated load run did not complete.'
        print('Load gate failed: ' + type(exc).__name__, flush=True)
    finally:
        if sampler:
            # Cleanup is best effort but its failures remain a failed gate.
            # Workload metrics already written above survive this path.
            report['interrupted_observer_cleanup'] = sampler.finish()
        if process:
            stop_app(process, log)
        from app import db
        db.close_pools(isolated)
        os.environ.clear()
        os.environ.update(previous_env)
        try:
            with psycopg.connect(base, autocommit=True) as control:
                control.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
            report['cleanup'] = 'Only the run-owned UUID schema was removed.'
        except Exception as exc:
            report['cleanup_error_type'] = type(exc).__name__
            report['status'] = 'NOT_READY'
        report['finished_at'] = datetime.now(timezone.utc).isoformat()
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report['status'] == 'PASS_LOCAL_ENGINEERING_GATE' else 1


if __name__ == '__main__':
    raise SystemExit(main())
