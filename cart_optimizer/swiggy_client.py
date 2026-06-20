"""Thin async MCP client for the Swiggy Food server.

Uses the MCP Python SDK's streamable-HTTP transport. Callers get a
``SwiggyClient`` context manager; inside it, call any Swiggy tool by name.

Usage:
    async with SwiggyClient(access_token) as client:
        menu = await client.call("get_restaurant_menu", restaurantId="668678")
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL = "https://mcp.swiggy.com/food"

_log = logging.getLogger("cartoptimizer.swiggy")

# Retry/backoff for transient rate-limiting.
_MAX_RETRIES = 4
_BACKOFF_BASE = 1.2          # sleep grows 1.2, 2.4, 4.8, 9.6s
# Process-global pacing between Swiggy calls so we stay under the 429 ceiling.
_MIN_INTERVAL = float(os.getenv("SWIGGY_MIN_INTERVAL", "0.4"))


def _is_rate_limited(exc: BaseException | None) -> bool:
    """True if ``exc`` — or anything it wraps — signals HTTP 429 / rate limiting.

    Swiggy's 429 surfaces wrapped in anyio ``ExceptionGroup``s and
    ``__cause__``/``__context__`` chains, so a flat ``str(exc)`` substring check
    misses it. Walk the whole tree (with cycle protection)."""
    seen: set[int] = set()

    def walk(e: BaseException | None) -> bool:
        if e is None or id(e) in seen:
            return False
        seen.add(id(e))
        msg = str(e)
        if "429" in msg or "Too Many Requests" in msg:
            return True
        resp = getattr(e, "response", None)
        if getattr(resp, "status_code", None) == 429:
            return True
        for sub in getattr(e, "exceptions", ()) or ():   # ExceptionGroup members
            if walk(sub):
                return True
        return walk(getattr(e, "__cause__", None)) or walk(getattr(e, "__context__", None))

    return walk(exc)


class _RateLimiter:
    """Async min-interval gate. Holding the lock across the sleep serializes all
    acquirers, so concurrent callers are spaced ``min_interval`` apart too."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            wait = self._next_at - now
            if wait > 0:
                await asyncio.sleep(wait)
                now = loop.time()
            self._next_at = max(now, self._next_at) + self._min_interval


# One limiter for the whole process (all users share Swiggy's rate budget).
_LIMITER = _RateLimiter(_MIN_INTERVAL)


class SwiggyClientError(RuntimeError):
    """Raised when a Swiggy MCP tool call fails or returns an error."""


class SwiggyClient:
    """Async context manager wrapping a live Swiggy MCP session.

    async with SwiggyClient(token) as client:
        result = await client.call("get_restaurant_menu", restaurantId="668678")
    """

    def __init__(self, access_token: str) -> None:
        self._token = access_token
        self._session: ClientSession | None = None
        self._exit_stack = None

    async def __aenter__(self) -> "SwiggyClient":
        from contextlib import AsyncExitStack

        self._exit_stack = AsyncExitStack()
        headers = {"Authorization": f"Bearer {self._token}"}
        transport = await self._exit_stack.enter_async_context(
            streamablehttp_client(MCP_URL, headers=headers)
        )
        read, write, _ = transport
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc) -> None:
        if not self._exit_stack:
            return
        try:
            await self._exit_stack.aclose()
        except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
            raise
        except BaseException as e:  # noqa: BLE001
            # When a 429 (or other error) tears through the MCP transport's
            # task group, anyio teardown raises "exit cancel scope in a
            # different task" (or re-raises the wrapped error) here. The session
            # is already dead; swallow it so teardown never masks the real error
            # or turns a handled failure into a 500.
            _log.warning("SwiggyClient teardown ignored: %s: %s", type(e).__name__, e)

    async def call(self, tool_name: str, **kwargs: Any) -> Any:
        """Call a Swiggy MCP tool, return the parsed JSON result.

        Handles the shapes seen live: structured output (preferred), a JSON
        string in the first text block, and double-encoded JSON (a JSON string
        whose value is itself a JSON document)."""
        if self._session is None:
            raise SwiggyClientError("not inside an async with block")

        # Pace every call (global limiter) + retry on 429 with exponential
        # backoff. The 429 can arrive wrapped in an anyio ExceptionGroup, so we
        # detect it via _is_rate_limited rather than a flat string check.
        result = None
        last_exc: BaseException | None = None
        for attempt in range(_MAX_RETRIES):
            await _LIMITER.acquire()
            try:
                result = await self._session.call_tool(tool_name, arguments=kwargs)
                break
            except (KeyboardInterrupt, SystemExit, asyncio.CancelledError):
                raise
            except BaseException as exc:  # noqa: BLE001 — 429 may be a (Base)ExceptionGroup
                if _is_rate_limited(exc):
                    last_exc = exc
                    _log.warning("%s rate-limited (attempt %d/%d), backing off",
                                 tool_name, attempt + 1, _MAX_RETRIES)
                    await asyncio.sleep(_BACKOFF_BASE * (2 ** attempt))
                    continue
                raise
        if result is None:
            raise SwiggyClientError(f"{tool_name}: rate-limited after retries ({last_exc})")
        if result.isError:
            raise SwiggyClientError(f"{tool_name} returned error: {result.content}")

        # Prefer the SDK's structured output when the tool provides it.
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict) and structured:
            # Some servers wrap the payload as {"result": {...}}.
            if set(structured.keys()) == {"result"}:
                return structured["result"]
            return structured

        text = result.content[0].text if result.content else "{}"
        data: Any = text
        for _ in range(2):  # unwrap up to one level of double-encoding
            if not isinstance(data, str):
                break
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                break
        return data
