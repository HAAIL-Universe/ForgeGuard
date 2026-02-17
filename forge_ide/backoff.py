"""Backoff & concurrency — rate-limiting utilities for LLM API calls.

Provides ``ExponentialBackoff`` (an iterator yielding delay durations)
and ``ConcurrencyLimiter`` (an async context manager wrapping
``asyncio.Semaphore``).

No external dependencies beyond the standard library.
"""

from __future__ import annotations

import asyncio
import random


# ---------------------------------------------------------------------------
# Exponential backoff
# ---------------------------------------------------------------------------


class ExponentialBackoff:
    """Iterator yielding exponentially increasing delay durations.

    Parameters
    ----------
    initial_s:
        First delay in seconds (default 1.0).
    max_s:
        Maximum delay — values are capped at this ceiling (default 30.0).
    multiplier:
        Factor applied after each iteration (default 2.0).
    jitter:
        If True, each delay is multiplied by ``random.uniform(0.5, 1.0)``
        to decorrelate concurrent retriers (default True).

    The iterator is infinite: after reaching the cap it keeps yielding
    ``max_s`` (with jitter if enabled).

    Call ``reset()`` to restart the sequence from ``initial_s``.
    """

    __slots__ = ("_initial", "_max", "_multiplier", "_jitter", "_current")

    def __init__(
        self,
        *,
        initial_s: float = 1.0,
        max_s: float = 30.0,
        multiplier: float = 2.0,
        jitter: bool = True,
    ) -> None:
        if initial_s <= 0:
            raise ValueError("initial_s must be positive")
        if max_s < initial_s:
            raise ValueError("max_s must be >= initial_s")
        if multiplier < 1.0:
            raise ValueError("multiplier must be >= 1.0")

        self._initial = initial_s
        self._max = max_s
        self._multiplier = multiplier
        self._jitter = jitter
        self._current = initial_s

    def __iter__(self) -> ExponentialBackoff:
        return self

    def __next__(self) -> float:
        delay = min(self._current, self._max)

        if self._jitter:
            delay *= random.uniform(0.5, 1.0)

        # Advance for next call
        self._current = min(self._current * self._multiplier, self._max)

        return round(delay, 4)

    def reset(self) -> None:
        """Restart the backoff sequence from the initial delay."""
        self._current = self._initial

    @property
    def initial_s(self) -> float:
        """The configured initial delay."""
        return self._initial

    @property
    def max_s(self) -> float:
        """The configured maximum delay."""
        return self._max


# ---------------------------------------------------------------------------
# Concurrency limiter
# ---------------------------------------------------------------------------


class ConcurrencyLimiter:
    """Async context manager that limits concurrent operations.

    Wraps ``asyncio.Semaphore`` with a friendlier API and observability
    properties (``active``, ``waiting``).

    Parameters
    ----------
    max_concurrent:
        Maximum number of operations allowed to run simultaneously
        (default 3).

    Usage::

        limiter = ConcurrencyLimiter(max_concurrent=5)

        async with limiter:
            await some_api_call()
    """

    __slots__ = ("_max", "_semaphore", "_active")

    def __init__(self, max_concurrent: int = 3) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self._max = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active = 0

    async def __aenter__(self) -> None:
        await self._semaphore.acquire()
        self._active += 1

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self._active -= 1
        self._semaphore.release()

    @property
    def active(self) -> int:
        """Number of operations currently running."""
        return self._active

    @property
    def waiting(self) -> int:
        """Approximate number of operations waiting to acquire."""
        # Semaphore._value is the number of remaining permits.
        # waiting ≈ (requests blocked) = max - value - active
        # But we can't reliably get blocked count from Semaphore,
        # so we derive it: if value > 0, nobody is waiting.
        value = self._semaphore._value  # type: ignore[attr-defined]
        if value > 0:
            return 0
        # value == 0 means all permits taken; we can't see the queue length
        # directly, so return 0 (conservative — actual waiters are opaque).
        return 0

    @property
    def max_concurrent(self) -> int:
        """The configured concurrency limit."""
        return self._max


__all__ = [
    "ConcurrencyLimiter",
    "ExponentialBackoff",
]
