"""The request worker bound applies across tasks and restores the hosting loop."""
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import anyio
import pytest
from app import main, staff_geo


@pytest.fixture
def isolated_startup(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setattr(staff_geo, 'validate_configuration', lambda: None)
    monkeypatch.setattr(main.db, 'close_pools', Mock())
    return SimpleNamespace(state=SimpleNamespace())


def test_request_threads_are_bounded_across_tasks_and_restore_after_shutdown(isolated_startup):
    async def verify():
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        release = threading.Event()
        started = []
        lock = threading.Lock()
        def work():
            with lock:
                started.append(True)
            release.wait(5)
        try:
            async with main.lifespan(isolated_startup):
                async with anyio.create_task_group() as requests:
                    for _ in range(9):
                        requests.start_soon(anyio.to_thread.run_sync, work)
                    with anyio.fail_after(3):
                        while len(started) < 8:
                            await anyio.sleep(0.001)
                    assert limiter.borrowed_tokens == 8
                    assert len(started) == 8
                    release.set()
                assert len(started) == 9
        finally:
            release.set()
        assert limiter.total_tokens == previous
        main.db.close_pools.assert_called_once()
    anyio.run(verify)


def test_thread_capacity_restores_when_startup_fails(isolated_startup, monkeypatch):
    def fail():
        raise RuntimeError('Invalid required configuration')
    monkeypatch.setattr(staff_geo, 'validate_configuration', fail)
    async def verify():
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        with pytest.raises(RuntimeError, match='Invalid required configuration'):
            async with main.lifespan(isolated_startup):
                pytest.fail('Failed startup cannot serve requests')
        assert limiter.total_tokens == previous
        main.db.close_pools.assert_called_once()
    anyio.run(verify)


def test_thread_capacity_restores_even_if_pool_cleanup_fails(isolated_startup):
    main.db.close_pools.side_effect = RuntimeError('Pool cleanup failed')
    async def verify():
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        with pytest.raises(RuntimeError, match='Pool cleanup failed'):
            async with main.lifespan(isolated_startup):
                assert limiter.total_tokens == 8
        assert limiter.total_tokens == previous
    anyio.run(verify)
