"""Tests for the Swiggy MCP client's resilience helpers.

These cover the rate-limit detection and pacing that keep a single 429 from
cascading into a 500 (see the live-run post-mortem): Swiggy's 429 surfaces
wrapped in anyio ExceptionGroups and __cause__/__context__ chains, so a flat
``str(exc)`` substring check misses it.
"""

import asyncio
import time

from cart_optimizer.swiggy_client import _RateLimiter, _is_rate_limited


# ── 429 detection through wrappers ──────────────────────────────────────────────

def test_plain_429_message_detected():
    assert _is_rate_limited(RuntimeError("Client error '429 Too Many Requests'"))


def test_too_many_requests_phrase_detected():
    assert _is_rate_limited(Exception("Too Many Requests"))


def test_429_inside_exception_group_detected():
    inner = RuntimeError("HTTP 429 Too Many Requests")
    group = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
    assert _is_rate_limited(group)


def test_429_inside_nested_group_detected():
    inner = RuntimeError("429 Too Many Requests")
    nested = ExceptionGroup("inner", [inner])
    outer = ExceptionGroup("outer", [nested])
    assert _is_rate_limited(outer)


def test_429_via_cause_chain_detected():
    try:
        try:
            raise RuntimeError("429 Too Many Requests")
        except RuntimeError as e:
            raise ValueError("wrapper") from e
    except ValueError as wrapped:
        assert _is_rate_limited(wrapped)


def test_429_via_response_status_code_detected():
    class FakeResp:
        status_code = 429

    class FakeHTTPError(Exception):
        response = FakeResp()

    assert _is_rate_limited(FakeHTTPError("anything"))


def test_non_429_errors_not_flagged():
    assert not _is_rate_limited(RuntimeError("connection reset"))
    assert not _is_rate_limited(ValueError("bad json"))
    assert not _is_rate_limited(ExceptionGroup("g", [RuntimeError("500 Server Error")]))


def test_handles_none_and_cycles():
    assert not _is_rate_limited(ValueError("nope"))
    # A self-referential cause must not infinite-loop.
    e = RuntimeError("x")
    e.__cause__ = e
    assert not _is_rate_limited(e)


# ── pacing ──────────────────────────────────────────────────────────────────────

def test_rate_limiter_spaces_calls():
    limiter = _RateLimiter(0.05)

    async def hammer():
        start = time.monotonic()
        for _ in range(3):
            await limiter.acquire()
        return time.monotonic() - start

    elapsed = asyncio.run(hammer())
    # 3 acquisitions => at least 2 inter-call gaps of 0.05s each.
    assert elapsed >= 0.09


def test_rate_limiter_serializes_concurrent_acquirers():
    limiter = _RateLimiter(0.05)

    async def many():
        start = time.monotonic()
        await asyncio.gather(*(limiter.acquire() for _ in range(4)))
        return time.monotonic() - start

    elapsed = asyncio.run(many())
    assert elapsed >= 0.14  # 4 concurrent acquirers still spaced ~0.05s apart
