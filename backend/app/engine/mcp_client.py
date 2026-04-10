"""MCP client using Streamable HTTP transport with connection pooling.

Connects to a configured MCP server via the standard MCP protocol over
Streamable HTTP.

V0.9: Session pool maintains up to N warm connections to reduce per-call
connection overhead.  Falls back to per-call sessions if pool is unavailable.

The MCP server must be running with ``--transport streamable-http``.
Default endpoint: ``http://localhost:8000/mcp``
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_TOOL_CACHE_TTL = 300  # seconds
_tool_defs_cache: tuple[float, list[dict[str, Any]]] | None = None


# ---------------------------------------------------------------------------
# Session pool (V0.9 — Component 3)
# ---------------------------------------------------------------------------

class _MCPSessionPool:
    """Maintains a pool of warm MCP client sessions."""

    def __init__(self, max_size: int = 4):
        self._max_size = max_size
        self._available: asyncio.Queue | None = None
        self._created = 0
        self._lock = asyncio.Lock()

    async def _ensure_queue(self):
        if self._available is None:
            self._available = asyncio.Queue(maxsize=self._max_size)

    async def acquire(self):
        """Get a (read, write) transport pair from the pool or create new."""
        await self._ensure_queue()

        # Try to get an existing session
        if not self._available.empty():
            return await self._available.get()

        # Create a new session if under limit
        async with self._lock:
            if self._created < self._max_size:
                self._created += 1
                return await self._create_session()

        # Pool exhausted — wait for one to be returned
        return await self._available.get()

    async def release(self, session_tuple):
        """Return a session to the pool."""
        if self._available is not None:
            try:
                self._available.put_nowait(session_tuple)
            except asyncio.QueueFull:
                # Pool is full, discard this session
                pass

    async def _create_session(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        transport = streamablehttp_client(url=settings.mcp_server_url)
        read, write, _ = await transport.__aenter__()
        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        return (session, transport)

    async def shutdown(self):
        """Close all pooled sessions."""
        if self._available is None:
            return
        while not self._available.empty():
            try:
                session, transport = self._available.get_nowait()
                try:
                    await session.__aexit__(None, None, None)
                    await transport.__aexit__(None, None, None)
                except Exception:
                    pass
            except asyncio.QueueEmpty:
                break
        self._created = 0


_pool = _MCPSessionPool(max_size=settings.mcp_pool_size)


# ---------------------------------------------------------------------------
# Core async operations
# ---------------------------------------------------------------------------

async def _call_tool_async(
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    """Call an MCP tool, using pooled session if available."""
    try:
        session_tuple = await _pool.acquire()
        session, transport = session_tuple
        try:
            result = await session.call_tool(tool_name, arguments=arguments)
        finally:
            await _pool.release(session_tuple)
    except Exception:
        # Fallback to per-call session if pool fails
        logger.debug("Pool acquire failed, falling back to per-call session")
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(url=settings.mcp_server_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments=arguments)

    content_parts = []
    for block in result.content:
        if hasattr(block, "text"):
            content_parts.append(block.text)

    raw_text = "\n".join(content_parts)
    try:
        return json.loads(raw_text)
    except (json.JSONDecodeError, ValueError):
        return {"result": raw_text}


async def _list_tools_async() -> list[dict[str, Any]]:
    """List all MCP tools, using pooled session if available."""
    try:
        session_tuple = await _pool.acquire()
        session, transport = session_tuple
        try:
            result = await session.list_tools()
        finally:
            await _pool.release(session_tuple)
    except Exception:
        logger.debug("Pool acquire failed for list_tools, falling back")
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(url=settings.mcp_server_url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()

    tools = []
    for tool in result.tools:
        tools.append({
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema if hasattr(tool, "inputSchema") else {"type": "object", "properties": {}},
        })
    return tools


# ---------------------------------------------------------------------------
# Sync wrappers (safe to call from Celery workers / FastAPI sync endpoints)
# ---------------------------------------------------------------------------

def _get_or_create_loop() -> asyncio.AbstractEventLoop:
    """Get the running event loop or create a new one for sync contexts."""
    try:
        loop = asyncio.get_running_loop()
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def call_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    """Synchronous wrapper: call an MCP tool via Streamable HTTP."""
    try:
        loop = _get_or_create_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _call_tool_async(tool_name, arguments))
                return future.result(timeout=120)
        else:
            return loop.run_until_complete(_call_tool_async(tool_name, arguments))
    except Exception as exc:
        logger.error("MCP call_tool(%s) failed: %s", tool_name, exc)
        return {"error": str(exc)}


def list_tools() -> list[dict[str, Any]]:
    """Synchronous wrapper: list available MCP tools (cached with TTL)."""
    import time
    global _tool_defs_cache
    now = time.time()
    if _tool_defs_cache is not None and now - _tool_defs_cache[0] < _TOOL_CACHE_TTL:
        return _tool_defs_cache[1]

    try:
        loop = _get_or_create_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _list_tools_async())
                result = future.result(timeout=30)
        else:
            result = loop.run_until_complete(_list_tools_async())

        _tool_defs_cache = (now, result)
        logger.info("Loaded %d tool definitions from MCP server", len(result))
        return result
    except Exception as exc:
        logger.error("MCP list_tools failed: %s", exc)
        return []


def invalidate_tool_cache() -> None:
    """Clear the tool definition cache so the next call re-fetches from MCP."""
    global _tool_defs_cache
    _tool_defs_cache = None
    logger.info("MCP tool cache invalidated")


def shutdown_pool() -> None:
    """Shut down the MCP session pool. Call on app shutdown."""
    try:
        loop = _get_or_create_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(asyncio.run, _pool.shutdown()).result(timeout=10)
        else:
            loop.run_until_complete(_pool.shutdown())
        logger.info("MCP session pool shut down")
    except Exception as exc:
        logger.warning("MCP pool shutdown error: %s", exc)


def get_openai_style_tool_defs(tool_names: list[str]) -> list[dict[str, Any]]:
    """Load tool definitions from MCP and return in OpenAI function-calling format.

    Used by the ReAct loop to feed tool schemas to LLM providers.
    """
    all_tools = list_tools()
    tool_map = {t["name"]: t for t in all_tools}

    result = []
    for name in tool_names:
        tool = tool_map.get(name)
        if not tool:
            logger.warning("Tool '%s' not found in MCP registry", name)
            continue
        result.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        })
    return result

