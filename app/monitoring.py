"""Sanitized runtime checks sharing recovery alerts and the canonical mail outbox.

This worker adds no service or subscription. A stopped service cannot monitor
itself: out-of-process downtime delivery remains an explicit external gate.
"""
from __future__ import annotations

import asyncio
import logging
import os
import resource
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends

from . import db, recovery

router = APIRouter()
logger = logging.getLogger('kps_order')
_request_lock = threading.Lock()
_requests = {'requests': 0, 'server_errors': 0}


def record_response(status_code):
    """Only totals are retained: no path, body, phone, address or access token."""
    with _request_lock:
        _requests['requests'] += 1
        _requests['server_errors'] += int(status_code >= 500)


def _integer(name, fallback, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(fallback)))))
    except ValueError:
        return fallback


def configuration():
    provider = recovery.configuration()
    return {**provider,
            'runtime_configured': bool(provider['provider_configured'] and os.getenv('DATABASE_URL')
                                       and os.getenv('MONITOR_ENABLED', 'true').lower() == 'true'),
            'runtime_interval_seconds': _integer('MONITOR_INTERVAL_SECONDS', 120, 60, 3600),
            'database_storage_limit_bytes': _integer('MONITOR_DATABASE_STORAGE_LIMIT_BYTES', 0, 0, 10**15),
            'database_latency_limit_ms': _integer('MONITOR_DATABASE_LATENCY_LIMIT_MS', 2000, 100, 30000),
            'mail_queue_age_limit_seconds': _integer('MONITOR_MAIL_QUEUE_AGE_LIMIT_SECONDS', 300, 60, 86400)}


def _runtime_resources():
    process = resource.getrusage(resource.RUSAGE_SELF)
    metrics = {'process_memory_peak_bytes': int(process.ru_maxrss) * 1024,
               'process_cpu_seconds': round(process.ru_utime + process.ru_stime, 3)}
    # cgroup v2 is measured from the process's real container limit, never an
    # assumed hosting plan. Missing resource counters stay explicitly unknown.
    try:
        current = int(Path('/sys/fs/cgroup/memory.current').read_text().strip())
        limit = Path('/sys/fs/cgroup/memory.max').read_text().strip()
        metrics['container_memory_bytes'] = current
        metrics['container_memory_limit_bytes'] = int(limit) if limit != 'max' else None
    except (OSError, ValueError):
        metrics['container_memory_bytes'] = metrics['container_memory_limit_bytes'] = None
    try:
        usage = shutil.disk_usage(db.ROOT)
        metrics.update(ephemeral_disk_total_bytes=usage.total, ephemeral_disk_used_bytes=usage.used)
    except OSError:
        metrics['ephemeral_disk_total_bytes'] = metrics['ephemeral_disk_used_bytes'] = None
    return metrics


def check_once():
    config = configuration()
    if not config['runtime_configured']:
        return {'configured': False, 'checked': False}
    started = time.monotonic()
    metrics = _runtime_resources()
    with _request_lock:
        responses = dict(_requests)
        _requests.update(requests=0, server_errors=0)
    metrics.update(responses)
    failures = []
    try:
        with db.tx() as conn:
            # Size and queue counters are within the private DB; provider plan
            # capacity is operator-configured and clearly labeled as such.
            metrics['database_size_bytes'] = conn.execute('SELECT pg_database_size(current_database()) n').fetchone()['n']
            metrics['database_latency_ms'] = round((time.monotonic() - started) * 1000, 3)
            queue = conn.execute("SELECT count(*) FILTER(WHERE status IN ('pending','sending'))::integer queued, "
                                 "count(*) FILTER(WHERE status='failed' AND updated_at>now()-interval '15 minutes')::integer failed, "
                                 "coalesce(max(extract(epoch FROM now()-created_at)) FILTER(WHERE status IN ('pending','sending')),0)::integer oldest_seconds "
                                 "FROM email_outbox WHERE brand_id=%s AND kind<>'operations_alert'", (config['brand_id'],)).fetchone()
            metrics.update(mail_queued=queue['queued'], mail_recently_failed=queue['failed'],
                           mail_oldest_queued_seconds=queue['oldest_seconds'])
            backup = conn.execute('SELECT last_success_at FROM recovery_state WHERE brand_id=%s AND database_id=%s',
                                  (config['brand_id'], config['database_id'])).fetchone()
            backup_time = backup['last_success_at'] if backup else None
            metrics['backup_observation_age_seconds'] = int((datetime.now(timezone.utc) - backup_time).total_seconds()) if backup_time else None
            metrics['database_storage_limit_bytes'] = config['database_storage_limit_bytes'] or None
            metrics['database_storage_capacity_source'] = 'private_operator_configuration' if config['database_storage_limit_bytes'] else 'unverified'
            if responses['server_errors'] >= 3:
                failures.append('runtime_server_errors')
            if metrics['database_latency_ms'] > config['database_latency_limit_ms']:
                failures.append('runtime_database_latency')
            if queue['failed']:
                failures.append('runtime_email_delivery_failed')
            if queue['queued'] and queue['oldest_seconds'] > config['mail_queue_age_limit_seconds']:
                failures.append('runtime_email_queue_stalled')
            if not backup_time or metrics['backup_observation_age_seconds'] > 36 * 3600:
                failures.append('runtime_backup_observation_stale')
            memory_limit = metrics.get('container_memory_limit_bytes')
            if memory_limit and metrics['container_memory_bytes'] / memory_limit >= .9:
                failures.append('runtime_memory_pressure')
            disk_total = metrics.get('ephemeral_disk_total_bytes')
            if disk_total and metrics['ephemeral_disk_used_bytes'] / disk_total >= .9:
                failures.append('runtime_ephemeral_storage_pressure')
            db_limit = config['database_storage_limit_bytes']
            if db_limit and metrics['database_size_bytes'] / db_limit >= .9:
                failures.append('runtime_database_storage_pressure')
            conn.execute('INSERT INTO recovery_monitor_state(brand_id,database_id,checked_at,metrics,failing_checks) '
                         'VALUES(%s,%s,now(),%s,%s) ON CONFLICT(brand_id,database_id) DO UPDATE '
                         'SET checked_at=now(),metrics=EXCLUDED.metrics,failing_checks=EXCLUDED.failing_checks',
                         (config['brand_id'], config['database_id'], db.json(metrics), db.json(failures)))
            for check in failures:
                # Existing shared queue groups failures for 24h. Avoid an
                # unbounded successful telemetry history or duplicate events.
                if conn.execute("SELECT 1 FROM recovery_alerts WHERE brand_id=%s AND database_id=%s AND event_type=%s "
                                "AND acknowledged_at IS NULL AND created_at>now()-interval '24 hours'",
                                (config['brand_id'], config['database_id'], check)).fetchone():
                    continue
                recovery._record(conn, config, db.new_id('runtime_'), check, 'failed', 'runtime_monitor',
                                 datetime.now(timezone.utc), db.digest({'check': check}), backup_state=False)
            queued = recovery.queue_alert_notifications(conn, config)
    except Exception:
        # Preserve only aggregate counters until the database reconnects. The
        # independent provider must detect a full outage; no false inbox proof.
        with _request_lock:
            _requests['requests'] += responses['requests']
            _requests['server_errors'] += responses['server_errors'] + 1
        logger.warning('runtime_monitor_database_unavailable')
        return {'configured': True, 'checked': False, 'database': 'unavailable',
                'out_of_process_alert_delivery_verified': False}
    return {'configured': True, 'checked': True, 'failing_checks': failures, 'alerts_queued': queued}


@router.get('/api/admin/monitoring')
def monitoring_status(user=Depends(recovery.owner)):
    config = configuration()
    recovery.owner_configuration(user)
    row, delivery = None, None
    if config['provider_configured']:
        with db.tx() as conn:
            row = conn.execute('SELECT checked_at,metrics,failing_checks FROM recovery_monitor_state WHERE brand_id=%s AND database_id=%s',
                               (user['brand_id'], config['database_id'])).fetchone()
            delivery = recovery.alert_delivery_status(conn, config)
    age = (datetime.now(timezone.utc) - row['checked_at']).total_seconds() if row else None
    stale = age is not None and age > config['runtime_interval_seconds'] * 3
    return {'configured': config['runtime_configured'],
            'status': 'unverified' if not row else 'stale' if stale else 'attention_required' if row['failing_checks'] else 'observed',
            'interval_seconds': config['runtime_interval_seconds'], 'last_checked_at': recovery._iso(row['checked_at']) if row else None,
            'metrics': row['metrics'] if row else {}, 'failing_checks': row['failing_checks'] if row else [],
            'external_alert_delivery': delivery or {'configured': False, 'statuses': {}, 'inbox_receipt_verified': False},
            'out_of_process_down_alert_verified': False,
            'downtime_alert_requirement': 'Independent provider notification must be configured and exercised.'}


@router.post('/api/admin/monitoring/refresh')
def refresh_monitoring(user=Depends(recovery.owner)):
    recovery.owner_configuration(user)
    config = configuration()
    with db.tx() as conn:
        if not db.rate_limit(conn, 'runtime-monitor-refresh:' + user['id'], 6, 60):
            raise db.error(429, 'MONITOR_RATE_LIMIT', 'انتظر قليلًا قبل إعادة الفحص.')
        db.audit(conn, user['id'], 'monitoring.refreshed', 'recovery', config.get('database_id'))
    check_once()
    return monitoring_status(user)


async def poll_loop():
    if not configuration()['runtime_configured']:
        return
    await asyncio.sleep(2)
    while configuration()['runtime_configured']:
        await asyncio.to_thread(check_once)
        await asyncio.sleep(configuration()['runtime_interval_seconds'])
