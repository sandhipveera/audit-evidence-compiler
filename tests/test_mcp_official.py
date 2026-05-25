"""Tests for splunk-official MCP transport — mocked at the MCP session boundary."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aec.splunk.mcp.transports import MCPTransportError
from aec.splunk.mcp.transports.splunk_official import (
    SplunkOfficialTransport,
    _normalize_search_result,
)


def _mock_tool(name: str) -> MagicMock:
    t = MagicMock()
    t.name = name
    return t


def _tool_result(data: dict, is_error: bool = False) -> MagicMock:
    content_item = MagicMock()
    content_item.text = json.dumps(data)
    result = MagicMock()
    result.isError = is_error
    result.content = [content_item]
    return result


class TestConnect:
    async def test_connect_http_discovers_tools(self):
        transport = SplunkOfficialTransport(server_url="http://localhost:8765")

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=MagicMock(
            tools=[_mock_tool("run_search"), _mock_tool("list_indexes"), _mock_tool("get_server_info")]
        ))
        mock_session.call_tool = AsyncMock(return_value=_tool_result({"version": "9.2.1"}))

        with patch(
            "aec.splunk.mcp.transports.splunk_official.streamablehttp_client",
        ) as mock_http:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock(), None))
            mock_http.return_value = mock_ctx

            with patch(
                "aec.splunk.mcp.transports.splunk_official.ClientSession",
            ) as mock_cls:
                mock_session_ctx = AsyncMock()
                mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
                mock_cls.return_value = mock_session_ctx

                await transport.connect()

        assert transport._tool_map["execute_spl"] == "run_search"
        assert transport._tool_map["list_indexes"] == "list_indexes"
        assert transport._tool_map["get_metadata"] == "get_server_info"
        assert transport.version == "9.2.1"
        await transport.close()


class TestExecuteSpl:
    async def test_execute_spl_normalizes_result(self):
        transport = SplunkOfficialTransport()
        transport._session = AsyncMock()
        transport._tool_map = {"execute_spl": "run_search"}

        transport._session.call_tool = AsyncMock(
            return_value=_tool_result({
                "results": [{"user": "alice", "count": "5"}],
                "eventCount": 42,
                "sid": "search-abc",
            })
        )

        result = await transport.execute_spl("index=botsv3 | head 5", "-30d")
        assert result["event_count"] == 42
        assert result["results"] == [{"user": "alice", "count": "5"}]
        assert result["search_id"] == "search-abc"

    async def test_execute_spl_missing_tool_raises(self):
        transport = SplunkOfficialTransport()
        transport._session = AsyncMock()
        transport._tool_map = {}

        with pytest.raises(MCPTransportError, match="no tool for 'execute_spl'"):
            await transport.execute_spl("index=main")

    async def test_execute_spl_error_result_raises(self):
        transport = SplunkOfficialTransport()
        transport._session = AsyncMock()
        transport._tool_map = {"execute_spl": "run_search"}
        transport._session.call_tool = AsyncMock(
            return_value=_tool_result({"error": "bad query"}, is_error=True)
        )

        with pytest.raises(MCPTransportError, match="error"):
            await transport.execute_spl("bad query")


class TestListIndexes:
    async def test_list_indexes_flat_list(self):
        transport = SplunkOfficialTransport()
        transport._session = AsyncMock()
        transport._tool_map = {"list_indexes": "list_indexes"}
        transport._session.call_tool = AsyncMock(
            return_value=_tool_result(["botsv3", "main"])
        )

        indexes = await transport.list_indexes()
        assert "botsv3" in indexes
        assert "main" in indexes

    async def test_list_indexes_dict_wrapper(self):
        transport = SplunkOfficialTransport()
        transport._session = AsyncMock()
        transport._tool_map = {"list_indexes": "list_indexes"}
        transport._session.call_tool = AsyncMock(
            return_value=_tool_result({"indexes": ["botsv3", "main"]})
        )

        indexes = await transport.list_indexes()
        assert indexes == ["botsv3", "main"]


class TestGetSourcetypes:
    async def test_get_sourcetypes_dict_results(self):
        transport = SplunkOfficialTransport()
        transport._session = AsyncMock()
        transport._tool_map = {"get_sourcetypes": "get_index_info"}
        transport._session.call_tool = AsyncMock(
            return_value=_tool_result({
                "sourcetypes": [
                    {"sourcetype": "wineventlog"},
                    {"sourcetype": "aws:cloudtrail"},
                ]
            })
        )

        st = await transport.get_sourcetypes("botsv3")
        assert "wineventlog" in st
        assert "aws:cloudtrail" in st


class TestProbe:
    async def test_probe_not_connected_raises(self):
        transport = SplunkOfficialTransport()
        with pytest.raises(MCPTransportError, match="Not connected"):
            await transport.probe()


class TestNormalize:
    def test_normalize_standard_shape(self):
        raw = {"results": [{"a": 1}], "event_count": 10, "search_id": "s1"}
        out = _normalize_search_result(raw)
        assert out["event_count"] == 10
        assert out["results"] == [{"a": 1}]

    def test_normalize_alternate_keys(self):
        raw = {"rows": [{"b": 2}], "eventCount": 5, "sid": "s2"}
        out = _normalize_search_result(raw)
        assert out["event_count"] == 5
        assert out["results"] == [{"b": 2}]

    def test_normalize_non_dict(self):
        out = _normalize_search_result("bad")
        assert out == {"results": [], "event_count": 0, "search_id": ""}
