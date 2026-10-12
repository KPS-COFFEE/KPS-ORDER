"""Regression for a real stats process that ignores termination signals."""
import subprocess
import sys
import threading
import time
import json

import pytest

from tools.hardening_load import Sampler, parse_docker_stat_frame


def test_observer_cleanup_kills_unresponsive_child_and_keeps_measurements():
    process = subprocess.Popen([sys.executable, '-u', '-c',
        "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready'); signal.pause()"],
        stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'ready'
        observer = Sampler('unused-no-database-call', 0)
        observer.docker_process = process
        observer.thread = threading.Thread(target=lambda: None)
        observer.thread.start()
        observer.samples = [{'connections': 12, 'lock_waits': 3, 'app_rss_bytes': 12345,
                             'app_cpu_seconds': 1.0}]
        observer.docker_samples = [{'cpu_percent_one_core': 22.5, 'memory_bytes': 67890}]
        started = time.monotonic()
        result = observer.finish()
        assert time.monotonic() - started < 8
        assert process.poll() is not None
        assert result['cleanup_actions'] == ['docker_stats_killed_after_sigterm_timeout']
        assert result['sample_errors'] == {}
        assert result['peak_connections'] == 12 and result['peak_lock_waits'] == 3
        assert result['postgresql_container']['peak_memory_bytes'] == 67890
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def test_docker_stream_terminal_controls_keep_json_resource_measurements():
    # Clear-screen/home prefixes are Docker's streaming protocol; they can
    # occur as their own blank frame or before the formatted JSON object.
    lines = [
        '\x1b[2J\x1b[H\n',
        '\n',
        '\x1b[2J\x1b[H{"CPUPerc":"34.50%","MemUsage":"20.15MiB / 1.954GiB","PIDs":"18"}\n',
        '{"CPUPerc":"1.00%","MemUsage":"21MiB / 1.954GiB","PIDs":"18"}\n',
    ]
    measurements = [sample for line in lines if (sample := parse_docker_stat_frame(line)) is not None]
    assert measurements == [
        {'cpu_percent_one_core': 34.5, 'memory_bytes': 20.15 * 1024 ** 2},
        {'cpu_percent_one_core': 1.0, 'memory_bytes': 21 * 1024 ** 2},
    ]


@pytest.mark.parametrize('line,error', [
    ('\x1b[2J\x1b[Hdocker daemon error\n', json.JSONDecodeError),
    ('\x1b[2J\x1b[H{"CPUPerc":"NaN%","MemUsage":"20MiB / 1GiB"}\n', ValueError),
    ('{"CPUPerc":"2%","MemUsage":"invalid"}\n', ValueError),
])
def test_docker_stream_rejects_genuine_malformed_measurements(line, error):
    with pytest.raises(error):
        parse_docker_stat_frame(line)
