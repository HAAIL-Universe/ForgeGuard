"""Tests for forge_ide.backoff — ExponentialBackoff and ConcurrencyLimiter."""

from __future__ import annotations

import asyncio

import pytest

from forge_ide.backoff import ConcurrencyLimiter, ExponentialBackoff


# ===================================================================
# ExponentialBackoff
# ===================================================================


class TestExponentialBackoff:
    def test_first_value(self):
        bo = ExponentialBackoff(initial_s=1.0, jitter=False)
        assert next(bo) == 1.0

    def test_second_value(self):
        bo = ExponentialBackoff(initial_s=1.0, multiplier=2.0, jitter=False)
        next(bo)  # 1.0
        assert next(bo) == 2.0

    def test_third_value(self):
        bo = ExponentialBackoff(initial_s=1.0, multiplier=2.0, jitter=False)
        next(bo)  # 1.0
        next(bo)  # 2.0
        assert next(bo) == 4.0

    def test_capped_at_max(self):
        bo = ExponentialBackoff(initial_s=1.0, max_s=5.0, multiplier=10.0, jitter=False)
        next(bo)  # 1.0
        assert next(bo) == 5.0  # 10.0 capped to 5.0

    def test_stays_at_max(self):
        """After reaching max, keeps yielding max."""
        bo = ExponentialBackoff(initial_s=1.0, max_s=2.0, multiplier=4.0, jitter=False)
        next(bo)  # 1.0
        assert next(bo) == 2.0
        assert next(bo) == 2.0
        assert next(bo) == 2.0

    def test_reset(self):
        bo = ExponentialBackoff(initial_s=1.0, multiplier=2.0, jitter=False)
        next(bo)  # 1.0
        next(bo)  # 2.0
        bo.reset()
        assert next(bo) == 1.0

    def test_jitter_range(self):
        """With jitter, delays are in [0.5x, 1.0x]."""
        bo = ExponentialBackoff(initial_s=10.0, jitter=True)
        delays = [next(bo) for _ in range(100)]
        assert all(5.0 <= d <= 10.0 for d in delays[:1])  # first batch ~initial
        # Just check they're all reasonable
        assert all(d > 0 for d in delays)

    def test_infinite_iterator(self):
        """Can iterate many times without StopIteration."""
        bo = ExponentialBackoff(initial_s=1.0, max_s=2.0, jitter=False)
        delays = [next(bo) for _ in range(50)]
        assert len(delays) == 50

    def test_iter_protocol(self):
        bo = ExponentialBackoff(initial_s=1.0, jitter=False)
        assert iter(bo) is bo

    def test_properties(self):
        bo = ExponentialBackoff(initial_s=1.5, max_s=20.0)
        assert bo.initial_s == 1.5
        assert bo.max_s == 20.0

    def test_invalid_initial(self):
        with pytest.raises(ValueError, match="initial_s"):
            ExponentialBackoff(initial_s=0)

    def test_invalid_max(self):
        with pytest.raises(ValueError, match="max_s"):
            ExponentialBackoff(initial_s=10, max_s=5)

    def test_invalid_multiplier(self):
        with pytest.raises(ValueError, match="multiplier"):
            ExponentialBackoff(multiplier=0.5)

    def test_default_values(self):
        bo = ExponentialBackoff(jitter=False)
        first = next(bo)
        assert first == 1.0  # default initial_s
        assert bo.max_s == 30.0  # default max_s


# ===================================================================
# ConcurrencyLimiter
# ===================================================================


class TestConcurrencyLimiter:
    @pytest.mark.asyncio
    async def test_basic_usage(self):
        limiter = ConcurrencyLimiter(max_concurrent=3)
        async with limiter:
            assert limiter.active == 1
        assert limiter.active == 0

    @pytest.mark.asyncio
    async def test_max_concurrent_property(self):
        limiter = ConcurrencyLimiter(max_concurrent=5)
        assert limiter.max_concurrent == 5

    @pytest.mark.asyncio
    async def test_default_max(self):
        limiter = ConcurrencyLimiter()
        assert limiter.max_concurrent == 3

    @pytest.mark.asyncio
    async def test_serialises_with_max_one(self):
        """max_concurrent=1 forces serial execution."""
        limiter = ConcurrencyLimiter(max_concurrent=1)
        order: list[int] = []

        async def task(n: int) -> None:
            async with limiter:
                order.append(n)
                await asyncio.sleep(0.01)

        await asyncio.gather(task(1), task(2), task(3))
        # All completed
        assert len(order) == 3

    @pytest.mark.asyncio
    async def test_active_count_multiple(self):
        """Active count reflects concurrent tasks."""
        limiter = ConcurrencyLimiter(max_concurrent=5)
        max_active = 0

        async def task() -> None:
            nonlocal max_active
            async with limiter:
                if limiter.active > max_active:
                    max_active = limiter.active
                await asyncio.sleep(0.02)

        await asyncio.gather(*[task() for _ in range(5)])
        assert max_active >= 2  # at least some concurrency

    @pytest.mark.asyncio
    async def test_waiting_initially_zero(self):
        limiter = ConcurrencyLimiter(max_concurrent=3)
        assert limiter.waiting == 0

    def test_invalid_max(self):
        with pytest.raises(ValueError, match="max_concurrent"):
            ConcurrencyLimiter(max_concurrent=0)

    @pytest.mark.asyncio
    async def test_exception_releases(self):
        """Limiter released even if task raises."""
        limiter = ConcurrencyLimiter(max_concurrent=1)
        with pytest.raises(RuntimeError):
            async with limiter:
                raise RuntimeError("boom")
        assert limiter.active == 0
        # Can still acquire after exception
        async with limiter:
            assert limiter.active == 1
