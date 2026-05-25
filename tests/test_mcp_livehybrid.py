"""Tests for livehybrid MCP transport — mocked at the MCP session boundary."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aec.splunk.mcp.transports import MCPTransportError
from aec.splunk.mcp.transports.livehybrid import (
    LivehybridTransport,
    _normalize_search_result,
)


def _mock_tool(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


def _tool_result(data, is_error: bool = False) -> MagicMock:
    content_item = MagicMock()
    content_item.text = json.dumps(data)
    result = MagicMock()
    result.isError = is_error
    result.content = [content_item]
    return result


class TestConnect:
    async def test_connect_discovers_livehybrid_tools(self):
        transport = LivehybridTransport(server_url="http://localhost:8766")

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=MagicMock(
            tools=[
                _mock_tool("search_splunk"),
                _mock_tool("list_indexes"),
                _mock_tool("list_sourcetypes_for_index"),
                _mock_tool("get_server_info"),
            ]
        ))
        mock_session.call_tool = AsyncMock(return_value=_tool_result({"version": "1.4.0"}))

        with patch(
            "aec.splunk.mcp.transports.livehybrid.streamablehttp_client",
        ) as mock_http:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock(), None))
            mock_http.return_value = mock_ctx

            with patch(
                "aec.splunk.mcp.transports.livehybrid.ClientSession",
            ) as mock_cls:
                mock_session_ctx = AsyncMock()
                mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
                mock_cls.return_value = mock_session_ctx

                await transport.connect()

        assert transport._tool_map["execute_spl"] == "search_splunk"
        assert transport._tool_map["get_sourcetypes"] == "list_sourcetypes_for_index"
        assert transport.version == "1.4.0"
        await transport.close()


class TestExecuteSpl:
    async def test_execute_spl_uses_search_query_param(self):
        transport = LivehybridTransport()
        transport._session = AsyncMock()
        transport._tool_map = {"execute_spl": "search_splunk"}

        transport._session.call_tool = AsyncMock(
            return_value=_tool_result({
                "results": [{"src_ip": "10.0.0.1"}],
                "event_count": 7,
            })
        )

        result = await transport.execute_spl("index=botsv3 | head 5", "-30d")
        assert result["event_count"] == 7

        call_args = transport._session.call_tool.call_args
        assert call_args[0][0] == "search_splunk"
        arguments = call_args[0][1]
        assert "search_query" in arguments
        assert arguments["search_query"] == "index=botsv3 | head 5"

    async def test_execute_spl_accepts_absolute_latest_range(self):
        transport = LivehybridTransport()
        transport._session = AsyncMock()
        transport._tool_map = {"execute_spl": "search_splunk"}

        transport._session.call_tool = AsyncMock(
            return_value=_tool_result({"results": [], "event_count": 0})
        )

        await transport.execute_spl(
            "index=botsv3 | head 5",
            "2018-09-01",
            latest="2018-09-15",
        )

        arguments = transport._session.call_tool.call_args.args[1]
        assert arguments["earliest_time"] == "2018-09-01"
        assert arguments["latest_time"] == "2018-09-15"

    async def test_execute_spl_missing_tool(self):
        transport = LivehybridTransport()
        transport._session = AsyncMock()
        transport._tool_map = {}

        with pytest.raises(MCPTransportError, match="no tool for 'execute_spl'"):
            await transport.execute_spl("index=main")


class TestListIndexes:
    async def test_list_indexes_flat_list(self):
        transport = LivehybridTransport()
        transport._session = AsyncMock()
        transport._tool_map = {"list_indexes": "list_indexes"}
        transport._session.call_tool = AsyncMock(
            return_value=_tool_result(["botsv3", "main", "summary"])
        )

        indexes = await transport.list_indexes()
        assert "botsv3" in indexes
        assert len(indexes) == 3


class TestGetSourcetypes:
    async def test_get_sourcetypes_flat_list(self):
        transport = LivehybridTransport()
        transport._session = AsyncMock()
        transport._tool_map = {"get_sourcetypes": "list_sourcetypes_for_index"}
        transport._session.call_tool = AsyncMock(
            return_value=_tool_result(["wineventlog", "iis", "stream:dns"])
        )

        st = await transport.get_sourcetypes("botsv3")
        assert "wineventlog" in st
        assert len(st) == 3

        call_args = transport._session.call_tool.call_args
        assert call_args[0][1] == {"index": "botsv3"}

    async def test_get_sourcetypes_dict_wrapper(self):
        transport = LivehybridTransport()
        transport._session = AsyncMock()
        transport._tool_map = {"get_sourcetypes": "list_sourcetypes_for_index"}
        transport._session.call_tool = AsyncMock(
            return_value=_tool_result({"sourcetypes": ["wineventlog"]})
        )

        st = await transport.get_sourcetypes("botsv3")
        assert st == ["wineventlog"]


class TestProbe:
    async def test_probe_connected(self):
        transport = LivehybridTransport()
        transport._session = AsyncMock()
        transport._tool_map = {"get_metadata": "get_server_info"}
        transport.version = "1.4.0"
        transport._session.call_tool = AsyncMock(
            return_value=_tool_result({"version": "1.4.0", "serverName": "test"})
        )

        info = await transport.probe()
        assert info["ok"] is True
        assert info["server"] == "livehybrid"

    async def test_probe_not_connected_raises(self):
        transport = LivehybridTransport()
        with pytest.raises(MCPTransportError, match="Not connected"):
            await transport.probe()


class TestNormalize:
    def test_normalize_with_messages(self):
        raw = {"results": [{"a": 1}], "event_count": 3, "messages": []}
        out = _normalize_search_result(raw)
        assert out["event_count"] == 3

    def test_normalize_empty(self):
        out = _normalize_search_result({})
        assert out["results"] == []
        assert out["event_count"] == 0
