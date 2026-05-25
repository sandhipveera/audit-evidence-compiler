"""Transport for livehybrid/splunk-mcp (community).

Server repo: https://github.com/livehybrid/splunk-mcp

Tool surface differences from splunk-official:
  - search_splunk(search_query, app, earliest_time, latest_time) — different param names
  - list_indexes(app) — takes optional app context
  - list_sourcetypes_for_index(index) — different name, flatter return
  - get_server_info() — same name as official, different response shape
  - create_spl_query(description) — LLM-assisted query builder (we don't use this)

Return value normalization:
  - search results come back as {"results": [...], "messages": [...]}
  - indexes come back as a flat list of strings
  - sourcetypes come back as a flat list of strings
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from aec.splunk.time_window import normalize_earliest

from . import BaseMCPTransport, MCPTransportError

log = logging.getLogger(__name__)

TOOL_ALIASES = {
    "execute_spl": ["search_splunk", "run_search", "search", "execute_search"],
    "list_indexes": ["list_indexes", "get_indexes", "list_indices"],
    "get_sourcetypes": [
        "list_sourcetypes_for_index", "get_sourcetypes",
        "list_sourcetypes", "get_index_info",
    ],
    "get_metadata": ["get_server_info", "server_info", "get_info"],
}


class LivehybridTransport(BaseMCPTransport):
    """MCP client for livehybrid/splunk-mcp."""

    name = "livehybrid"

    def __init__(
        self,
        server_url: str | None = None,
        server_cmd: str | None = None,
    ) -> None:
        self._server_url = server_url or os.environ.get(
            "AEC_MCP_LIVEHYBRID_URL", "http://localhost:8766"
        )
        self._server_cmd = server_cmd or os.environ.get("AEC_MCP_LIVEHYBRID_CMD")
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._tool_map: dict[str, str] = {}
        self.version = "unknown"

    async def connect(self) -> None:
        self._exit_stack = AsyncExitStack()
        try:
            if self._server_cmd:
                await self._connect_stdio()
            else:
                await self._connect_http()
            await self._discover_tools()
            metadata = await self._safe_get_metadata()
            if metadata:
                self.version = metadata.get("version", "unknown")
        except Exception as exc:
            await self.close()
            raise MCPTransportError(
                f"Failed to connect to livehybrid MCP server: {exc}"
            ) from exc

    async def _connect_stdio(self) -> None:
        assert self._exit_stack is not None
        parts = self._server_cmd.split()  # type: ignore[union-attr]
        params = StdioServerParameters(command=parts[0], args=parts[1:])
        read_stream, write_stream = await self._exit_stack.enter_async_context(
            stdio_client(params)
        )
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self._session.initialize()

    async def _connect_http(self) -> None:
        assert self._exit_stack is not None
        url = self._server_url.rstrip("/")
        if not url.endswith("/mcp"):
            url = f"{url}/mcp"
        read_stream, write_stream, _ = await self._exit_stack.enter_async_context(
            streamablehttp_client(url)
        )
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        await self._session.initialize()

    async def _discover_tools(self) -> None:
        assert self._session is not None
        result = await self._session.list_tools()
        server_tools = {t.name for t in result.tools}
        log.debug("livehybrid tools: %s", server_tools)
        for our_fn, aliases in TOOL_ALIASES.items():
            for alias in aliases:
                if alias in server_tools:
                    self._tool_map[our_fn] = alias
                    break

    async def _call_tool(self, our_fn: str, arguments: dict[str, Any]) -> Any:
        assert self._session is not None
        tool_name = self._tool_map.get(our_fn)
        if not tool_name:
            raise MCPTransportError(
                f"livehybrid server has no tool for '{our_fn}'. "
                f"Available: {list(self._tool_map.values())}"
            )
        result = await self._session.call_tool(tool_name, arguments)
        if result.isError:
            text = result.content[0].text if result.content else "unknown error"
            raise MCPTransportError(f"MCP tool {tool_name} error: {text}")
        if not result.content:
            return {}
        raw = result.content[0].text
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {"raw": raw}

    async def close(self) -> None:
        if self._exit_stack:
            await self._exit_stack.aclose()
            self._exit_stack = None
        self._session = None

    async def probe(self) -> dict[str, Any]:
        if not self._session:
            raise MCPTransportError("Not connected — call connect() first")
        try:
            metadata = await self.get_metadata()
            return {"ok": True, "server": self.name, "version": self.version, **metadata}
        except Exception as exc:
            raise MCPTransportError(f"Probe failed: {exc}") from exc

    async def execute_spl(
        self,
        query: str,
        time_window: str = "-30d",
        latest: str = "now",
    ) -> dict[str, Any]:
        earliest = normalize_earliest(time_window)
        raw = await self._call_tool("execute_spl", {
            "search_query": query,
            "earliest_time": earliest,
            "latest_time": latest,
        })
        return _normalize_search_result(raw)

    async def list_indexes(self) -> list[str]:
        raw = await self._call_tool("list_indexes", {})
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            return raw.get("indexes", raw.get("results", []))
        return []

    async def get_sourcetypes(self, index: str) -> list[str]:
        raw = await self._call_tool("get_sourcetypes", {"index": index})
        if isinstance(raw, list):
            return [str(s) for s in raw]
        if isinstance(raw, dict):
            sourcetypes = raw.get("sourcetypes", raw.get("results", []))
            if isinstance(sourcetypes, list):
                return [str(s) for s in sourcetypes]
        return []

    async def get_metadata(self) -> dict[str, Any]:
        return await self._call_tool("get_metadata", {})

    async def _safe_get_metadata(self) -> dict[str, Any] | None:
        try:
            return await self.get_metadata()
        except MCPTransportError:
            return None


def _normalize_search_result(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        results = raw.get("results", raw.get("rows", []))
        event_count = raw.get("event_count", raw.get("eventCount", len(results)))
        search_id = raw.get("search_id", raw.get("sid", ""))
        return {
            "results": results if isinstance(results, list) else [],
            "event_count": int(event_count),
            "search_id": str(search_id),
        }
    return {"results": [], "event_count": 0, "search_id": ""}
