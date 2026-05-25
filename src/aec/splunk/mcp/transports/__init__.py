"""MCP transport implementations for Splunk integration."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MCPTransportError(Exception):
    pass


class BaseMCPTransport(ABC):
    """Common interface for all Splunk MCP server transports."""

    name: str = "base"
    version: str = "unknown"

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the MCP server."""

    @abstractmethod
    async def close(self) -> None:
        """Close the MCP server connection."""

    @abstractmethod
    async def probe(self) -> dict[str, Any]:
        """Health check — returns server info or raises MCPTransportError."""

    @abstractmethod
    async def execute_spl(self, query: str, time_window: str = "-30d") -> dict[str, Any]:
        """Execute a SPL query and return normalized results.

        Returns dict with keys: results, event_count, search_id.
        """

    @abstractmethod
    async def list_indexes(self) -> list[str]:
        """Return names of available indexes."""

    @abstractmethod
    async def get_sourcetypes(self, index: str) -> list[str]:
        """Return sourcetypes present in the given index."""

    @abstractmethod
    async def get_metadata(self) -> dict[str, Any]:
        """Return Splunk instance metadata (version, license type, etc.)."""

    @property
    def label(self) -> str:
        return f"{self.name} (v{self.version})"
