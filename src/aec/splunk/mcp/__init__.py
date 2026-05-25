"""Splunk MCP integration — dual-server support (splunk-official + livehybrid).

Public API (all async):
    execute_spl(query, time_window) -> dict
    list_indexes() -> list[str]
    get_sourcetypes(index) -> list[str]
    get_metadata() -> dict
    probe() -> dict

Use get_router() for lifecycle control, or the module-level functions
for single-shot operations.
"""
from __future__ import annotations

from typing import Any

from .router import MCPRouter
from .transports import MCPTransportError

__all__ = [
    "MCPRouter",
    "MCPTransportError",
    "execute_spl",
    "get_metadata",
    "get_router",
    "get_sourcetypes",
    "list_indexes",
    "probe",
]


_router: MCPRouter | None = None


async def get_router(preferred: str | None = None) -> MCPRouter:
    """Get (or create) the singleton MCPRouter, connecting if needed."""
    global _router
    if _router is None or _router.active_transport is None:
        _router = MCPRouter(preferred=preferred)
        await _router.connect()
    return _router


async def execute_spl(query: str, time_window: str = "-30d") -> dict[str, Any]:
    router = await get_router()
    return await router.execute_spl(query, time_window=time_window)


async def list_indexes() -> list[str]:
    router = await get_router()
    return await router.list_indexes()


async def get_sourcetypes(index: str) -> list[str]:
    router = await get_router()
    return await router.get_sourcetypes(index)


async def get_metadata() -> dict[str, Any]:
    router = await get_router()
    return await router.get_metadata()


async def probe() -> dict[str, Any]:
    router = await get_router()
    return await router.probe()
