"""Simple in-memory rate limiter for webhook endpoints.

Uses a sliding-window counter keyed by client IP.
Not shared across workers -- sufficient for single-process MVP.
"""

import time


class RateLimiter:
    """Token-bucket rate limiter.

    Args:
        max_requests: Maximum requests allowed in the window.
        window_seconds: Length of the sliding window in seconds.
    """

    _call_count: int = 0
    _PRUNE_INTERVAL: int = 500  # prune idle keys every N calls

    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def _prune_idle_keys(self, now: float) -> None:
        """Remove keys whose timestamps have all expired."""
        cutoff = now - self._window
        dead = [k for k, ts in self._hits.items() if not ts or ts[-1] <= cutoff]
        for k in dead:
            del self._hits[k]

    def is_allowed(self, key: str) -> bool:
        """Check whether *key* is within the rate limit.

        Returns True if the request is allowed, False if it should be rejected.
        """
        now = time.monotonic()
        cutoff = now - self._window

        # Periodically prune idle keys to prevent unbounded growth
        RateLimiter._call_count += 1
        if RateLimiter._call_count % self._PRUNE_INTERVAL == 0:
            self._prune_idle_keys(now)

        # Lazy-init or prune expired entries
        timestamps = self._hits.get(key, [])
        timestamps = [t for t in timestamps if t > cutoff]

        if len(timestamps) >= self._max:
            self._hits[key] = timestamps
            return False

        timestamps.append(now)
        self._hits[key] = timestamps
        return True


# Module-level singleton -- 30 requests per 60 seconds for webhooks.
webhook_limiter = RateLimiter(max_requests=30, window_seconds=60)

# Build limiter -- 5 build starts per user per hour (prevents abuse).
build_limiter = RateLimiter(max_requests=5, window_seconds=3600)
