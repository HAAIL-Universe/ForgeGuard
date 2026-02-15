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

    def __init__(self, max_requests: int = 60, window_seconds: int = 60) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def is_allowed(self, key: str) -> bool:
        """Check whether *key* is within the rate limit.

        Returns True if the request is allowed, False if it should be rejected.
        """
        now = time.monotonic()
        cutoff = now - self._window

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
