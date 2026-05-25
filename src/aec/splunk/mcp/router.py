"""MCP runtime router — picks between splunk-official and livehybrid transports.

Reads AEC_SPLUNK_MCP_SERVER env var (default: splunk-official).
Probes the configured server first; falls back to the other if unreachable.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .transports import BaseMCPTransport, MCPTransportError
from .transports.livehybrid import LivehybridTransport
from .transports.splunk_official import SplunkOfficialTransport

log = logging.getLogger(__name__)

TRANSPORT_CLASSES: dict[str, type[BaseMCPTransport]] = {
    "splunk-official": SplunkOfficialTransport,
    "livehybrid": LivehybridTransport,
}

FALLBACK_ORDER = {
    "splunk-official": "livehybrid",
    "livehybrid": "splunk-official",
}


class MCPRouter:
    """Manages MCP transport lifecycle with automatic fallback."""

    def __init__(self, preferred: str | None = None) -> None:
        env_val = os.environ.get("AEC_SPLUNK_MCP_SERVER", "splunk-official")
        self._preferred = preferred or env_val
        self._active: BaseMCPTransport | None = None
        self._fallback_transport: BaseMCPTransport | None = None
        self.fallback_used = False

    @property
    def active_transport(self) -> BaseMCPTransport | None:
        return self._active

    @property
    def active_label(self) -> str:
        if self._active:
            return self._active.label
        return "none"

    @property
    def fallback_label(self) -> str | None:
        if self._fallback_transport:
            return self._fallback_transport.label
        return None

    @property
    def mcp_server_tag(self) -> str | None:
        """Short provenance tag for EvidenceSnapshot metadata."""
        if not self._active:
            return None
        return f"{self._active.name}-{self._active.version}"

    async def connect(self) -> BaseMCPTransport:
        """Connect to the preferred MCP server, falling back if it's unreachable."""
        primary_name = self._preferred
        fallback_name = FALLBACK_ORDER.get(primary_name)

        primary_cls = TRANSPORT_CLASSES.get(primary_name)
        if not primary_cls:
            raise MCPTransportError(
                f"Unknown MCP server '{primary_name}'. "
                f"Choose from: {list(TRANSPORT_CLASSES.keys())}"
            )

        primary = primary_cls()
        try:
            await primary.connect()
            self._active = primary
            log.info("MCP connected: %s", primary.label)
        except MCPTransportError as exc:
            log.warning("Primary MCP server %s unreachable: %s", primary_name, exc)
            if not fallback_name:
                raise
            fallback_cls = TRANSPORT_CLASSES[fallback_name]
            fallback = fallback_cls()
            try:
                await fallback.connect()
                self._active = fallback
                self.fallback_used = True
                log.info("Fell back to MCP server: %s", fallback.label)
            except MCPTransportError:
                raise MCPTransportError(
                    f"Both MCP servers unreachable — "
                    f"{primary_name} and {fallback_name}"
                ) from exc

        if fallback_name:
            await self._probe_fallback(fallback_name)

        return self._active

    async def _probe_fallback(self, fallback_name: str) -> None:
        if self._active and self._active.name == fallback_name:
            return
        fallback_cls = TRANSPORT_CLASSES[fallback_name]
        fallback = fallback_cls()
        try:
            await fallback.connect()
            self._fallback_transport = fallback
            log.info("Fallback available: %s", fallback.label)
        except MCPTransportError:
            log.debug("Fallback %s not reachable (non-fatal)", fallback_name)

    async def close(self) -> None:
        if self._active:
            await self._active.close()
            self._active = None
        if self._fallback_transport:
            await self._fallback_transport.close()
            self._fallback_transport = None

    async def execute_spl(self, query: str, time_window: str = "-30d") -> dict[str, Any]:
        return await self._delegate("execute_spl", query=query, time_window=time_window)

    async def list_indexes(self) -> list[str]:
        return await self._delegate("list_indexes")

    async def get_sourcetypes(self, index: str) -> list[str]:
        return await self._delegate("get_sourcetypes", index=index)

    async def get_metadata(self) -> dict[str, Any]:
        return await self._delegate("get_metadata")

    async def probe(self) -> dict[str, Any]:
        return await self._delegate("probe")

    async def _delegate(self, method: str, **kwargs: Any) -> Any:
        if not self._active:
            raise MCPTransportError("No MCP transport connected — call connect() first")
        return await getattr(self._active, method)(**kwargs)
