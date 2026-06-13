"""Tests for Splunk REST client — mocked at the HTTP boundary."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aec.splunk.client import SplunkAuthError, SplunkClient, SplunkSearchError


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> SplunkClient:
    monkeypatch.setenv("SPLUNK_HOST", "https://splunk.example.com:8089")
    monkeypatch.setenv("SPLUNK_TOKEN", "test-token-abc123")
    return SplunkClient()


class TestAuth:
    def test_auth_header_set(self, client: SplunkClient):
        assert client._headers["Authorization"] == "Bearer test-token-abc123"

    def test_missing_host_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SPLUNK_HOST", raising=False)
        monkeypatch.delenv("SPLUNK_TOKEN", raising=False)
        with pytest.raises(ValueError, match="SPLUNK_HOST"):
            SplunkClient()

    def test_missing_credentials_raises(self, monkeypatch: pytest.MonkeyPatch):
        # No bearer token AND no basic-auth password -> cannot authenticate.
        monkeypatch.setenv("SPLUNK_HOST", "https://localhost:8089")
        monkeypatch.delenv("SPLUNK_TOKEN", raising=False)
        monkeypatch.delenv("SPLUNK_PASSWORD", raising=False)
        with pytest.raises(ValueError, match="SPLUNK_TOKEN"):
            SplunkClient()

    def test_basic_auth_when_no_token(self, monkeypatch: pytest.MonkeyPatch):
        # Password but no token -> client falls back to HTTP basic auth.
        monkeypatch.setenv("SPLUNK_HOST", "https://localhost:8089")
        monkeypatch.delenv("SPLUNK_TOKEN", raising=False)
        monkeypatch.setenv("SPLUNK_USERNAME", "admin")
        monkeypatch.setenv("SPLUNK_PASSWORD", "secret")
        client = SplunkClient()
        assert client._auth == ("admin", "secret")
        assert "Authorization" not in client._headers

    def test_explicit_empty_token_forces_basic_auth(self, monkeypatch: pytest.MonkeyPatch):
        # token="" disables a stale env token and uses basic auth instead.
        monkeypatch.setenv("SPLUNK_HOST", "https://localhost:8089")
        monkeypatch.setenv("SPLUNK_TOKEN", "stale-rotated-token")
        monkeypatch.setenv("SPLUNK_PASSWORD", "secret")
        client = SplunkClient(token="")
        assert client.token == ""
        assert client._auth == ("admin", "secret")


class TestProbe:
    @patch("aec.splunk.client.requests.get")
    def test_probe_success(self, mock_get, client: SplunkClient):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"entry": [{"content": {"serverName": "test-splunk"}}]},
        )
        mock_get.return_value.raise_for_status = MagicMock()
        info = client.probe()
        assert info["entry"][0]["content"]["serverName"] == "test-splunk"
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        assert "Bearer test-token-abc123" in str(call_kwargs)

    @patch("aec.splunk.client.requests.get")
    def test_probe_401_raises_auth_error(self, mock_get, client: SplunkClient):
        mock_get.return_value = MagicMock(status_code=401)
        with pytest.raises(SplunkAuthError):
            client.probe()

    @patch("aec.splunk.client.requests.get")
    def test_probe_503_raises(self, mock_get, client: SplunkClient):
        mock_get.return_value = MagicMock(status_code=503)
        mock_get.return_value.raise_for_status = MagicMock(
            side_effect=Exception("503 Service Unavailable")
        )
        with pytest.raises(Exception, match="503"):
            client.probe()

    @patch("aec.splunk.client.requests.get")
    def test_probe_timeout_raises_search_error(self, mock_get, client: SplunkClient):
        import requests

        mock_get.side_effect = requests.Timeout("timed out")
        with pytest.raises(SplunkSearchError, match="timed out"):
            client.probe()


class TestSearch:
    @patch("aec.splunk.client.requests.get")
    @patch("aec.splunk.client.requests.post")
    def test_search_success(self, mock_post, mock_get, client: SplunkClient):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"sid": "search-123"},
        )
        mock_post.return_value.raise_for_status = MagicMock()

        mock_get.side_effect = [
            MagicMock(
                status_code=200,
                json=lambda: {"entry": [{"content": {"isDone": True, "eventCount": 42}}]},
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                status_code=200,
                json=lambda: {"results": [{"user": "alice", "count": "5"}]},
                raise_for_status=MagicMock(),
            ),
        ]

        result = client.search("index=auth | stats count by user")
        assert result["event_count"] == 42
        assert result["results"] == [{"user": "alice", "count": "5"}]
        assert result["search_id"] == "search-123"

    @patch("aec.splunk.client.requests.post")
    def test_search_401_raises(self, mock_post, client: SplunkClient):
        mock_post.return_value = MagicMock(status_code=401)
        with pytest.raises(SplunkAuthError):
            client.search("index=main")

    @patch("aec.splunk.client.requests.post")
    def test_search_parse_error_preserves_splunk_message(self, mock_post, client: SplunkClient):
        mock_post.return_value = MagicMock(
            status_code=400,
            json=lambda: {
                "messages": [
                    {"type": "ERROR", "text": "syntax error in line 3 of query"},
                ],
            },
        )
        with pytest.raises(SplunkSearchError, match="syntax error in line 3"):
            client.search("index=main | stats count")

    @patch("aec.splunk.client.requests.post")
    def test_search_request_timeout(self, mock_post, client: SplunkClient):
        import requests

        mock_post.side_effect = requests.Timeout("timed out")
        with pytest.raises(SplunkSearchError, match="timed out"):
            client.search("index=main")

    @patch("aec.splunk.client.requests.get")
    @patch("aec.splunk.client.requests.post")
    def test_search_timeout(self, mock_post, mock_get, client: SplunkClient):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"sid": "search-timeout"},
        )
        mock_post.return_value.raise_for_status = MagicMock()

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"entry": [{"content": {"isDone": False, "eventCount": 0}}]},
            raise_for_status=MagicMock(),
        )

        with pytest.raises(SplunkSearchError, match="timed out"):
            client.search("index=main", timeout=0)

    def test_query_encoding_prepends_search(self, client: SplunkClient):
        with patch("aec.splunk.client.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=401)
            try:
                client.search("index=main action=login\n| stats count")
            except SplunkAuthError:
                pass
            call_data = mock_post.call_args[1]["data"]
            assert call_data["search"].startswith("search ")
            assert "index=main" in call_data["search"]

    def test_query_starting_with_pipe_not_prepended(self, client: SplunkClient):
        with patch("aec.splunk.client.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=401)
            try:
                client.search("| makeresults | eval test=1")
            except SplunkAuthError:
                pass
            call_data = mock_post.call_args[1]["data"]
            assert call_data["search"].startswith("|")


class TestListIndexes:
    @patch("aec.splunk.client.requests.get")
    def test_list_indexes_filters_internal(self, mock_get, client: SplunkClient):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "entry": [
                    {"name": "botsv3"},
                    {"name": "main"},
                    {"name": "_internal"},
                    {"name": "_audit"},
                ]
            },
        )
        mock_get.return_value.raise_for_status = MagicMock()
        indexes = client.list_indexes()
        assert "botsv3" in indexes
        assert "main" in indexes
        assert "_internal" not in indexes
        assert "_audit" not in indexes


class TestListSourcetypes:
    @patch("aec.splunk.client.requests.get")
    @patch("aec.splunk.client.requests.post")
    def test_list_sourcetypes(self, mock_post, mock_get, client: SplunkClient):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"sid": "meta-123"},
        )
        mock_post.return_value.raise_for_status = MagicMock()

        mock_get.side_effect = [
            MagicMock(
                status_code=200,
                json=lambda: {"entry": [{"content": {"isDone": True, "eventCount": 5}}]},
                raise_for_status=MagicMock(),
            ),
            MagicMock(
                status_code=200,
                json=lambda: {"results": [
                    {"sourcetype": "wineventlog"},
                    {"sourcetype": "aws:cloudtrail"},
                    {"sourcetype": "o365:management:activity"},
                ]},
                raise_for_status=MagicMock(),
            ),
        ]

        sourcetypes = client.list_sourcetypes("botsv3")
        assert "wineventlog" in sourcetypes
        assert "o365:management:activity" in sourcetypes


@pytest.mark.integration
class TestSplunkIntegration:
    """Live integration tests — only run with SPLUNK_LIVE_TEST=1."""

    @pytest.fixture(autouse=True)
    def require_live(self):
        import os
        if not os.environ.get("SPLUNK_LIVE_TEST"):
            pytest.skip("SPLUNK_LIVE_TEST not set — skipping live Splunk tests")

    def test_probe_returns_ok(self):
        client = SplunkClient(verify_ssl=False)
        info = client.probe()
        assert "entry" in info
        content = info["entry"][0]["content"]
        assert "version" in content

    def test_list_indexes_includes_botsv3(self):
        client = SplunkClient(verify_ssl=False)
        indexes = client.list_indexes()
        assert "botsv3" in indexes, f"Expected botsv3 index, found: {indexes}"

    def test_botsv3_has_expected_sourcetypes(self):
        from aec.splunk.client import BOTS_V3_EXPECTED_SOURCETYPES

        client = SplunkClient(verify_ssl=False)
        sourcetypes = client.list_sourcetypes("botsv3")
        for expected in BOTS_V3_EXPECTED_SOURCETYPES:
            assert any(
                st.lower() == expected for st in sourcetypes
            ), f"Missing sourcetype '{expected}' in botsv3. Found: {sourcetypes}"

    def test_search_botsv3_returns_results(self):
        client = SplunkClient(verify_ssl=False)
        result = client.search(
            query="index=botsv3 | head 5",
            earliest="0",
            latest="now",
            max_results=5,
        )
        assert result["event_count"] > 0
        assert len(result["results"]) > 0


class TestBaseURL:
    def test_respects_https(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SPLUNK_HOST", "https://secure.splunk.io:8089")
        monkeypatch.setenv("SPLUNK_TOKEN", "tok")
        c = SplunkClient()
        assert c._url("/services/search/jobs").startswith("https://")

    def test_respects_http(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SPLUNK_HOST", "http://localhost:8089")
        monkeypatch.setenv("SPLUNK_TOKEN", "tok")
        c = SplunkClient()
        assert c._url("/services/search/jobs").startswith("http://")
