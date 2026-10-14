"""Async MCP client for connecting to the Great.cards server.

Provides helpers to list available tools and execute individual tool calls.
All public functions are synchronous wrappers around async internals so they
can be called from Flask's synchronous request handlers.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

log = logging.getLogger(__name__)

# Maximum seconds to wait for the MCP handshake + tool call.
_MCP_TIMEOUT = 30


def _parse_call_result(result) -> dict[str, Any]:
    """Convert an MCP CallToolResult into a plain dict."""
    texts: list[str] = []
    for item in result.content:
        if hasattr(item, "text"):
            texts.append(item.text)

    raw = "\n".join(texts)

    # Try to parse the text as JSON for structured access.
    data: Any = None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        data = raw  # Keep raw text if it isn't JSON.

    return {
        "data": data,
        "raw": raw,
        "is_error": getattr(result, "isError", False),
    }


# ---- async internals --------------------------------------------------------

async def _list_tools_async(url: str) -> list:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()
            return list(result.tools)


async def _call_tools_async(
    url: str,
    calls: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Execute a sequence of tool calls within a single MCP session.

    Parameters
    ----------
    url:
        The MCP server URL.
    calls:
        List of ``(tool_name, arguments)`` tuples, executed in order.

    Returns
    -------
    List of parsed result dicts, one per call.
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    results: list[dict[str, Any]] = []

    async with streamable_http_client(url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            for tool_name, arguments in calls:
                log.info("MCP call: %s(%s)", tool_name, arguments)
                raw_result = await session.call_tool(tool_name, arguments=arguments)
                parsed = _parse_call_result(raw_result)
                log.info(
                    "MCP result for %s: error=%s, length=%d",
                    tool_name, parsed["is_error"], len(parsed["raw"]),
                )
                results.append(parsed)

    return results


# ---- synchronous public API -------------------------------------------------

def list_tools_sync(url: str) -> list:
    """Connect to the MCP server and return the list of tool definitions."""
    return asyncio.run(_list_tools_async(url))


def call_tools_sync(
    url: str,
    calls: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Execute tool calls sequentially in one MCP session. Returns results."""
    return asyncio.run(_call_tools_async(url, calls))
