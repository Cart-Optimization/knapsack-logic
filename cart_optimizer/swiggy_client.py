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
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL = "https://mcp.swiggy.com/food"


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
        if self._exit_stack:
            await self._exit_stack.aclose()

    async def call(self, tool_name: str, **kwargs: Any) -> Any:
        """Call a Swiggy MCP tool, return the parsed JSON result.

        Handles the shapes seen live: structured output (preferred), a JSON
        string in the first text block, and double-encoded JSON (a JSON string
        whose value is itself a JSON document)."""
        if self._session is None:
            raise SwiggyClientError("not inside an async with block")

        # Retry on Swiggy rate-limiting (429) with exponential backoff.
        result = None
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                result = await self._session.call_tool(tool_name, arguments=kwargs)
                break
            except Exception as exc:  # noqa: BLE001 — inspect message transport-agnostically
                msg = str(exc)
                if "429" in msg or "Too Many Requests" in msg:
                    last_exc = exc
                    await asyncio.sleep(1.5 * (2 ** attempt))  # 1.5s, 3s, 6s
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
