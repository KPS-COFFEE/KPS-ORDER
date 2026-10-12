"""Regression for a real stats process that ignores termination signals."""
import subprocess
import sys
import threading
import time
import json
import hashlib
import os
import signal

import pytest

from tools.hardening_load import (Sampler, exact_owned_pool_names, owned_process_pids,
                                 parse_docker_stat_frame, process_start_ticks,
                                 process_tree_metrics, startup_worker_pids, stop_app)


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


@pytest.fixture
def observed_process_tree(tmp_path):
    grandchild = "import time; memory=bytearray(2000000); start=time.process_time();\nwhile time.process_time()-start<.05: pass\ntime.sleep(30)"
    parent = ("import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-u','-c'," + repr(grandchild) + "]); "
              "start=time.process_time();\nwhile time.process_time()-start<.05: pass\nprint(child.pid,flush=True); time.sleep(30)")
    process = subprocess.Popen([sys.executable, '-u', '-c', parent], stdout=subprocess.PIPE, text=True,
                               start_new_session=True)
    sibling = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
    log = (tmp_path / 'owned-process.log').open('a')
    try:
        descendant = int(process.stdout.readline().strip())
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            metrics = process_tree_metrics(process.pid)
            if len(metrics['processes']) == 2 and all(row['rss_bytes'] > 0 for row in metrics['processes'].values()):
                break
            time.sleep(.01)
        yield process, descendant, sibling
    finally:
        stop_app(process, log)
        process.stdout.close()
        sibling.terminate()
        sibling.wait(timeout=5)


def test_process_tree_observation_includes_actual_child_cpu_memory_and_excludes_caller(observed_process_tree):
    process, descendant, sibling = observed_process_tree
    metrics = process_tree_metrics(process.pid)
    assert set(metrics['processes']) == owned_process_pids(process.pid) == {process.pid, descendant}
    assert os.getpid() not in metrics['processes']
    assert sibling.pid not in metrics['processes']
    assert metrics['cpu_seconds'] == pytest.approx(sum(row['cpu_seconds'] for row in metrics['processes'].values()))
    assert metrics['summed_rss_bytes'] == sum(row['rss_bytes'] for row in metrics['processes'].values())
    assert metrics['cpu_seconds'] > 0 and metrics['summed_rss_bytes'] > 0


def test_startup_proof_and_exact_pool_names_reject_unowned_pids(observed_process_tree):
    process, descendant, sibling = observed_process_tree
    logs = ''.join(f'INFO: Started server process [{pid}]\n' for pid in [process.pid, descendant, os.getpid(), sibling.pid])
    assert startup_worker_pids(logs, process.pid) == sorted([process.pid, descendant])
    url = 'postgresql://fixture@127.0.0.1/test_observer?options=-csearch_path%3Dfixture'
    names = exact_owned_pool_names(process.pid, url)
    suffix = hashlib.sha256(url.encode()).hexdigest()[:12]
    assert names == {pid: f'kps_order_pool_{pid}_{suffix}' for pid in [process.pid, descendant]}
    assert os.getpid() not in names
    assert sibling.pid not in names
    assert exact_owned_pool_names(process.pid, url + '&sslmode=require') != names


def test_missing_worker_fails_identity_proof_even_while_supervisor_remains(observed_process_tree):
    process, descendant, _ = observed_process_tree
    observer = Sampler('unused-no-database-call', process.pid, worker_pids=[descendant])
    assert observer.workers_are_live(process_tree_metrics(process.pid))
    os.kill(descendant, signal.SIGTERM)
    deadline = time.monotonic() + 5
    while process_start_ticks(descendant) is not None and time.monotonic() < deadline:
        time.sleep(.01)
    assert process_start_ticks(process.pid) is not None
    assert not observer.workers_are_live(process_tree_metrics(process.pid))
