"""Verify the added post-recovery money/export check against real local HTTP/PG.

This small helper test is not a resource-budget or 500-user certificate.
"""
import asyncio
import os
import socket
import uuid

import pytest

from app import db
from tools import hardening_load as load


@pytest.mark.postgres
def test_reports_reconcile_real_unpaid_orders_without_changing_their_money(database, tmp_path, monkeypatch):
    monkeypatch.setenv('KPS_LOAD_RESOURCE_PROFILE', 'current')
    load.configure_synthetic_data()
    db.close_pools(database)
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    env = dict(os.environ, PUBLIC_BASE_URL=origin, APP_ALLOWED_HOSTS='localhost,127.0.0.1',
               RUNTIME_REVISION='hosting-report-helper-test')
    process, log, _, _ = load.start_app(env, port, tmp_path / 'app.log')
    try:
        workload = load.Workload(origin, 'helper-' + uuid.uuid4().hex, 3, 2, 60)
        metrics = asyncio.run(workload.run())
        assert metrics['error_count'] == 0, metrics['error_samples']
        report = load.hosting_report_checks(database, origin, [workload])
        assert report['passed'], report
        assert report['expected_orders'] == report['database_orders'] == 6
        assert report['export']['orders'] == 6
        assert report['expected_total_halalas'] > 0
        assert report['order_fingerprints_preserved']
    finally:
        load.stop_app(process, log)
