"""Tests for rate limiter."""

from app.api.rate_limit import RateLimiter


def test_allows_within_limit():
    """Requests within the limit should be allowed."""
    limiter = RateLimiter(max_requests=3, window_seconds=60)
    assert limiter.is_allowed("client1") is True
    assert limiter.is_allowed("client1") is True
    assert limiter.is_allowed("client1") is True


def test_blocks_over_limit():
    """Requests exceeding the limit should be blocked."""
    limiter = RateLimiter(max_requests=2, window_seconds=60)
    assert limiter.is_allowed("client1") is True
    assert limiter.is_allowed("client1") is True
    assert limiter.is_allowed("client1") is False


def test_separate_keys():
    """Different keys should have independent limits."""
    limiter = RateLimiter(max_requests=1, window_seconds=60)
    assert limiter.is_allowed("client1") is True
    assert limiter.is_allowed("client2") is True
    assert limiter.is_allowed("client1") is False


def test_window_expiry():
    """Requests should be allowed again after the window expires."""
    import time

    limiter = RateLimiter(max_requests=1, window_seconds=0.1)
    assert limiter.is_allowed("client1") is True
    assert limiter.is_allowed("client1") is False
    time.sleep(0.15)
    assert limiter.is_allowed("client1") is True
