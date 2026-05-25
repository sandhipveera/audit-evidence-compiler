"""Tests for MCP runtime router — transport selection, fallback, env var precedence."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aec.splunk.mcp.router import MCPRouter, TRANSPORT_CLASSES
from aec.splunk.mcp.transports import MCPTransportError


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AEC_SPLUNK_MCP_SERVER", raising=False)
    monkeypatch.delenv("AEC_MCP_OFFICIAL_URL", raising=False)
    monkeypatch.delenv("AEC_MCP_LIVEHYBRID_URL", raising=False)


def _mock_transport_cls(name, version="0.0.0", connect_exc=None):
    """Create a mock class that returns a mock transport instance."""
    instance = AsyncMock()
    instance.name = name
    instance.version = version
    instance.label = f"{name} (v{version})"
    if connect_exc:
        instance.connect = AsyncMock(side_effect=connect_exc)
    cls_mock = lambda: instance  # noqa: E731
    return cls_mock, instance


class TestEnvVarPrecedence:
    def test_default_is_splunk_official(self, clean_env):
        router = MCPRouter()
        assert router._preferred == "splunk-official"

    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv("AEC_SPLUNK_MCP_SERVER", "livehybrid")
        router = MCPRouter()
        assert router._preferred == "livehybrid"

    def test_explicit_arg_overrides_env(self, monkeypatch):
        monkeypatch.setenv("AEC_SPLUNK_MCP_SERVER", "livehybrid")
        router = MCPRouter(preferred="splunk-official")
        assert router._preferred == "splunk-official"


class TestConnect:
    async def test_connect_to_primary(self, clean_env):
        router = MCPRouter(preferred="splunk-official")
        official_cls, official_inst = _mock_transport_cls("splunk-official", "0.3.2")
        livehybrid_cls, _ = _mock_transport_cls(
            "livehybrid", "1.4.0",
            connect_exc=MCPTransportError("not running"),
        )

        with patch.dict(TRANSPORT_CLASSES, {
            "splunk-official": official_cls,
            "livehybrid": livehybrid_cls,
        }):
            await router.connect()

        assert router.active_transport is official_inst
        assert router.mcp_server_tag == "splunk-official-0.3.2"
        assert not router.fallback_used
        await router.close()

    async def test_fallback_when_primary_unreachable(self, clean_env):
        router = MCPRouter(preferred="splunk-official")
        official_cls, _ = _mock_transport_cls(
            "splunk-official", "0.3.2",
            connect_exc=MCPTransportError("connection refused"),
        )
        livehybrid_cls, livehybrid_inst = _mock_transport_cls("livehybrid", "1.4.0")

        with patch.dict(TRANSPORT_CLASSES, {
            "splunk-official": official_cls,
            "livehybrid": livehybrid_cls,
        }):
            await router.connect()

        assert router.active_transport is livehybrid_inst
        assert router.fallback_used
        assert router.mcp_server_tag == "livehybrid-1.4.0"
        await router.close()

    async def test_both_unreachable_raises(self, clean_env):
        router = MCPRouter(preferred="splunk-official")
        official_cls, _ = _mock_transport_cls(
            "splunk-official", connect_exc=MCPTransportError("refused"),
        )
        livehybrid_cls, _ = _mock_transport_cls(
            "livehybrid", connect_exc=MCPTransportError("refused"),
        )

        with patch.dict(TRANSPORT_CLASSES, {
            "splunk-official": official_cls,
            "livehybrid": livehybrid_cls,
        }):
            with pytest.raises(MCPTransportError, match="Both MCP servers unreachable"):
                await router.connect()

    async def test_unknown_server_name_raises(self, clean_env):
        router = MCPRouter(preferred="nonexistent-server")
        with pytest.raises(MCPTransportError, match="Unknown MCP server"):
            await router.connect()


class TestDelegate:
    async def test_delegate_execute_spl(self, clean_env):
        router = MCPRouter(preferred="splunk-official")
        official_cls, official_inst = _mock_transport_cls("splunk-official", "0.3.2")
        official_inst.execute_spl = AsyncMock(
            return_value={"results": [{"user": "alice"}], "event_count": 1, "search_id": "s1"}
        )
        livehybrid_cls, _ = _mock_transport_cls(
            "livehybrid", "1.4.0",
            connect_exc=MCPTransportError("not running"),
        )

        with patch.dict(TRANSPORT_CLASSES, {
            "splunk-official": official_cls,
            "livehybrid": livehybrid_cls,
        }):
            await router.connect()

        result = await router.execute_spl("index=botsv3 | head 5")
        assert result["event_count"] == 1
        official_inst.execute_spl.assert_awaited_once()
        await router.close()

    async def test_delegate_execute_spl_with_latest(self, clean_env):
        router = MCPRouter(preferred="splunk-official")
        official_cls, official_inst = _mock_transport_cls("splunk-official", "0.3.2")
        official_inst.execute_spl = AsyncMock(
            return_value={"results": [], "event_count": 0, "search_id": "s1"}
        )
        livehybrid_cls, _ = _mock_transport_cls(
            "livehybrid", "1.4.0",
            connect_exc=MCPTransportError("not running"),
        )

        with patch.dict(TRANSPORT_CLASSES, {
            "splunk-official": official_cls,
            "livehybrid": livehybrid_cls,
        }):
            await router.connect()

        await router.execute_spl(
            "index=botsv3 | head 5",
            time_window="2018-08-01",
            latest="2018-08-15",
        )

        official_inst.execute_spl.assert_awaited_once_with(
            query="index=botsv3 | head 5",
            time_window="2018-08-01",
            latest="2018-08-15",
        )
        await router.close()

    async def test_delegate_without_connect_raises(self, clean_env):
        router = MCPRouter()
        with pytest.raises(MCPTransportError, match="No MCP transport connected"):
            await router.execute_spl("index=main")


class TestLabels:
    def test_active_label_none(self, clean_env):
        router = MCPRouter()
        assert router.active_label == "none"

    def test_mcp_server_tag_none(self, clean_env):
        router = MCPRouter()
        assert router.mcp_server_tag is None
