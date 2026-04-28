"""MCP client over Streamable HTTP with a per-(tenant, server) session pool.

MCP-02 made this module tenant-aware. The request shape is:

    call_tool(tool_name, arguments, *, tenant_id=None, server_label=None)
    list_tools(*, tenant_id=None, server_label=None)

``tenant_id`` is optional to keep internal paths that don't have tenant
context working (they fall back to ``settings.mcp_server_url``). When
both ``tenant_id`` and ``server_label`` are passed, the
``mcp_server_resolver`` looks the row up in ``tenant_mcp_servers`` and
returns a concrete URL + headers. An empty ``server_label`` resolves to
the tenant's ``is_default`` row or, if none exists, the env-var fallback
so pre-MCP-02 tenants keep working unchanged.

Pools and caches are keyed by ``(tenant_id or '__env__', pool_key)``
where ``pool_key`` is the server's row id (or ``'__env_fallback__'``
for the env path). This isolates tenants from each other at the
connection layer so a misbehaving server for tenant A can't pin a
session that tenant B would pick up.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from app.engine.mcp_server_resolver import (
    ResolvedMcpServer,
    resolve_mcp_server,
)

logger = logging.getLogger(__name__)

_TOOL_CACHE_TTL = 300  # seconds
# cache_key → (timestamp, tool_defs)
_tool_defs_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}


# ---------------------------------------------------------------------------
# Session pools, keyed by (tenant_id, pool_key)
# ---------------------------------------------------------------------------


class _MCPSessionPool:
    """Maintains a pool of warm MCP client sessions for a single target."""

    def __init__(self, target: ResolvedMcpServer, max_size: int = 4):
        self._target = target
        self._max_size = max_size
        self._available: asyncio.Queue | None = None
        self._created = 0
        self._lock = asyncio.Lock()

    async def _ensure_queue(self):
        if self._available is None:
            self._available = asyncio.Queue(maxsize=self._max_size)

    async def acquire(self):
        await self._ensure_queue()

        if not self._available.empty():
            return await self._available.get()

        async with self._lock:
            if self._created < self._max_size:
                self._created += 1
                return await self._create_session()

        return await self._available.get()

    async def release(self, session_tuple):
        if self._available is not None:
            try:
                self._available.put_nowait(session_tuple)
            except asyncio.QueueFull:
                pass

    async def _create_session(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        transport = streamablehttp_client(
            url=self._target.url,
            headers=self._target.headers or None,
        )
        read, write, _ = await transport.__aenter__()
        session = ClientSession(read, write)
        await session.__aenter__()
        await session.initialize()
        return (session, transport)

    async def shutdown(self):
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


# cache_key → pool
_pools: dict[tuple[str, str], _MCPSessionPool] = {}


def _pool_for(target: ResolvedMcpServer, tenant_id: str | None) -> _MCPSessionPool:
    """Return the warm-session pool for this ``(tenant, server)`` slot.

    ADMIN-01: pool size reads from ``tenant_policies.mcp_pool_size``
    when overridden, else the env default. Resolver is called lazily
    only when a new pool is constructed — existing pools keep their
    original size (a changed tenant policy takes effect the next time
    the orchestrator process rebuilds a pool, typically after a
    restart or shutdown_pool).
    """
    key = (tenant_id or "__env__", target.pool_key)
    pool = _pools.get(key)
    if pool is None:
        from app.engine.tenant_policy_resolver import get_effective_policy

        pool_size = get_effective_policy(tenant_id).mcp_pool_size
        pool = _MCPSessionPool(target, max_size=pool_size)
        _pools[key] = pool
    return pool


# ---------------------------------------------------------------------------
# Core async operations
# ---------------------------------------------------------------------------


async def _call_tool_async(
    tool_name: str,
    arguments: dict[str, Any],
    target: ResolvedMcpServer,
    tenant_id: str | None,
) -> Any:
    pool = _pool_for(target, tenant_id)
    try:
        session_tuple = await pool.acquire()
        session, transport = session_tuple
        try:
            result = await session.call_tool(tool_name, arguments=arguments)
        finally:
            await pool.release(session_tuple)
    except Exception:
        # Fallback to per-call session if pool fails.
        logger.debug("Pool acquire failed, falling back to per-call session")
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(
            url=target.url,
            headers=target.headers or None,
        ) as (read, write, _):
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


async def _list_tools_async(
    target: ResolvedMcpServer,
    tenant_id: str | None,
) -> list[dict[str, Any]]:
    pool = _pool_for(target, tenant_id)
    try:
        session_tuple = await pool.acquire()
        session, transport = session_tuple
        try:
            result = await session.list_tools()
        finally:
            await pool.release(session_tuple)
    except Exception:
        logger.debug("Pool acquire failed for list_tools, falling back")
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(
            url=target.url,
            headers=target.headers or None,
        ) as (read, write, _):
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
# Sync wrappers
#
# The MCP SDK binds its AnyIO task-group + streamable-HTTP transport to
# whichever event loop happens to be running when the session is
# created. The old implementation spun up a **new** loop (via
# ``asyncio.run``) on a throwaway worker thread for every sync call,
# which meant:
#
#   1. Concurrent sync callers raced to register a "current" loop.
#   2. Pooled sessions created on loop A couldn't be reused from loop B
#      — the task group was bound to A, which no longer existed.
#   3. ``list_tools`` sometimes returned empty because the session
#      initialization completed on a loop that was already being torn
#      down before the response arrived.
#
# Fix: a single module-level event loop running in a daemon background
# thread. ``asyncio.run_coroutine_threadsafe`` lets any number of sync
# callers (FastAPI handler threads, Celery workers, in-process DAG
# threads) submit work to it safely. Pools and transports stay pinned
# to one loop for the life of the process. Mirrors the pattern already
# in use for ``app.engine.embedding_provider``.
# ---------------------------------------------------------------------------


_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _get_or_create_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            t = threading.Thread(
                target=_loop.run_forever,
                name="mcp-event-loop",
                daemon=True,
            )
            t.start()
        return _loop


def _run_async(coro, timeout: float):
    """Submit *coro* to the dedicated background loop and block until it
    finishes (or *timeout* seconds elapse).

    Callers are always sync: FastAPI threadpool handlers, Celery
    workers, in-process DAG worker threads. ``run_coroutine_threadsafe``
    is the only supported way to cross the loop boundary from a
    non-loop thread; it also handles reentrancy cleanly if the loop
    ever runs in the calling thread's process.
    """
    loop = _get_or_create_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


def call_tool(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    tenant_id: str | None = None,
    server_label: str | None = None,
) -> Any:
    """Synchronous wrapper: call an MCP tool on the resolved server."""
    try:
        target = resolve_mcp_server(tenant_id, server_label)
        return _run_async(
            _call_tool_async(tool_name, arguments, target, tenant_id),
            timeout=60,
        )
    except Exception as exc:
        logger.error("MCP call_tool(%s) failed: %s", tool_name, exc)
        return {"error": str(exc)}


def list_tools(
    *,
    tenant_id: str | None = None,
    server_label: str | None = None,
) -> list[dict[str, Any]]:
    """Synchronous wrapper: list tools on the resolved server (TTL-cached)."""
    import time

    target = resolve_mcp_server(tenant_id, server_label)
    cache_key = (tenant_id or "__env__", target.pool_key)

    now = time.time()
    cached = _tool_defs_cache.get(cache_key)
    if cached is not None and now - cached[0] < _TOOL_CACHE_TTL:
        return cached[1]

    try:
        result = _run_async(
            _list_tools_async(target, tenant_id),
            timeout=30,
        )
        _tool_defs_cache[cache_key] = (now, result)
        logger.info(
            "Loaded %d tool definitions from MCP server (tenant=%s, label=%s)",
            len(result), tenant_id, target.label,
        )
        return result
    except Exception as exc:
        logger.error("MCP list_tools failed: %s", exc)
        return []


def invalidate_tool_cache(
    *,
    tenant_id: str | None = None,
    server_label: str | None = None,
) -> None:
    """Clear the tool-definition cache.

    With no args, clears every entry — used by the existing "invalidate
    cache" button. Pass ``tenant_id`` (and optionally ``server_label``)
    to invalidate only that slot, which the MCP-02 CRUD paths can use
    after a registry edit (future work — MCP-08 deals with this
    properly via ``notifications/tools/list_changed``).
    """
    if tenant_id is None and server_label is None:
        _tool_defs_cache.clear()
        logger.info("MCP tool cache invalidated (all slots)")
        return

    # Targeted invalidation: resolve first so we hit the same cache key
    # the fetch used.
    target = resolve_mcp_server(tenant_id, server_label)
    cache_key = (tenant_id or "__env__", target.pool_key)
    _tool_defs_cache.pop(cache_key, None)
    logger.info("MCP tool cache invalidated for %s", cache_key)


def shutdown_pool() -> None:
    """Shut down every session pool. Call on app shutdown."""
    async def _shutdown_all():
        for pool in list(_pools.values()):
            await pool.shutdown()
        _pools.clear()

    try:
        _run_async(_shutdown_all(), timeout=10)
        logger.info("MCP session pools shut down")
    except Exception as exc:
        logger.warning("MCP pool shutdown error: %s", exc)


def get_openai_style_tool_defs(
    tool_names: list[str],
    *,
    tenant_id: str | None = None,
    server_label: str | None = None,
) -> list[dict[str, Any]]:
    """Load tool definitions and return in OpenAI function-calling format.

    Used by the ReAct loop to feed tool schemas to LLM providers.
    """
    all_tools = list_tools(tenant_id=tenant_id, server_label=server_label)
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
