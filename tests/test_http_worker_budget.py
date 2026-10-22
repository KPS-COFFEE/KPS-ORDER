"""Bound CPU/thread contention without dropping work or retaining loop state."""
import asyncio
import threading
from types import SimpleNamespace

import anyio
import pytest
from anyio import from_thread, to_thread

from app import main


def test_burst_queues_every_sync_operation_and_restores_the_event_loop_budget(monkeypatch):
    monkeypatch.setattr(main.db, 'close_pools', lambda: None)

    async def exercise():
        limiter = to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        release = threading.Event()
        full = anyio.Event()
        lock = threading.Lock()
        active = peak = 0
        completed = []

        def work(identifier):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == main.HTTP_THREAD_TOKENS:
                    from_thread.run_sync(full.set)
            try:
                assert release.wait(5), 'Synthetic workers did not drain.'
                completed.append(identifier)
            finally:
                with lock:
                    active -= 1

        try:
            async with main.lifespan(SimpleNamespace(state=SimpleNamespace())):
                assert limiter.total_tokens == main.HTTP_THREAD_TOKENS
                async with anyio.create_task_group() as tasks:
                    for identifier in range(32):
                        tasks.start_soon(to_thread.run_sync, work, identifier)
                    try:
                        with anyio.fail_after(3):
                            await full.wait()
                        assert active == peak == main.HTTP_THREAD_TOKENS
                    finally:
                        release.set()
                assert sorted(completed) == list(range(32))
                assert active == 0 and peak == main.HTTP_THREAD_TOKENS
        finally:
            release.set()
        assert limiter.total_tokens == previous

    asyncio.run(exercise())


def test_failed_startup_restores_the_original_loop_budget(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused@localhost/test_never_connected')
    monkeypatch.setattr(main.db, 'close_pools', lambda: None)

    def fail_migration():
        raise RuntimeError('Synthetic startup failure')

    monkeypatch.setattr(main.db, 'migrate', fail_migration)

    async def exercise():
        limiter = to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        with pytest.raises(RuntimeError, match='Synthetic startup failure'):
            async with main.lifespan(SimpleNamespace(state=SimpleNamespace())):
                pytest.fail('Failed migration must block startup.')
        assert limiter.total_tokens == previous

    asyncio.run(exercise())
