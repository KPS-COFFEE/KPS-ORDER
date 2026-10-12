"""Regression for a real stats process that ignores termination signals."""
import subprocess
import sys
import threading
import time

from tools.hardening_load import Sampler


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
