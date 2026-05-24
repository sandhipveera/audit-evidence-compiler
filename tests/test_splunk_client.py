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

    def test_missing_token_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SPLUNK_HOST", "https://localhost:8089")
        monkeypatch.delenv("SPLUNK_TOKEN", raising=False)
        with pytest.raises(ValueError, match="SPLUNK_TOKEN"):
            SplunkClient()


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
