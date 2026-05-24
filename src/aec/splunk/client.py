"""Splunk REST client — token-based auth against Splunk Enterprise / Cloud."""
from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import urljoin

import requests

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
SEARCH_POLL_INTERVAL = 1.0


class SplunkAuthError(Exception):
    pass


class SplunkSearchError(Exception):
    pass


def _extract_error_message(resp: requests.Response) -> str:
    """Extract the useful Splunk error text from a REST response."""
    try:
        data = resp.json()
    except ValueError:
        text = getattr(resp, "text", "")
        return text if isinstance(text, str) and text else resp.reason

    messages = data.get("messages")
    if isinstance(messages, list) and messages:
        parts = []
        for message in messages:
            if isinstance(message, dict):
                parts.append(str(message.get("text") or message.get("message") or message))
            else:
                parts.append(str(message))
        return "; ".join(parts)
    return str(data)


def _raise_for_splunk_status(resp: requests.Response, context: str) -> None:
    if resp.status_code == 401:
        raise SplunkAuthError("Authentication failed — check SPLUNK_TOKEN")
    if resp.status_code >= 400:
        raise SplunkSearchError(
            f"{context} failed ({resp.status_code}): {_extract_error_message(resp)}"
        )
    resp.raise_for_status()


class SplunkClient:
    """Thin REST client for Splunk search via token auth.

    Env vars:
        SPLUNK_HOST — base URL (e.g. https://localhost:8089)
        SPLUNK_TOKEN — bearer token for authentication
    """

    def __init__(
        self,
        host: str | None = None,
        token: str | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self.host = (host or os.environ.get("SPLUNK_HOST", "")).rstrip("/")
        self.token = token or os.environ.get("SPLUNK_TOKEN", "")
        self.verify_ssl = verify_ssl

        if not self.host:
            raise ValueError("SPLUNK_HOST not set — provide host or set env var")
        if not self.token:
            raise ValueError("SPLUNK_TOKEN not set — provide token or set env var")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _url(self, path: str) -> str:
        return urljoin(self.host + "/", path.lstrip("/"))

    def probe(self) -> dict[str, Any]:
        """Probe connectivity — returns server info or raises."""
        try:
            resp = requests.get(
                self._url("/services/server/info"),
                headers=self._headers,
                params={"output_mode": "json"},
                verify=self.verify_ssl,
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.Timeout as exc:
            raise SplunkSearchError("Probe timed out") from exc
        _raise_for_splunk_status(resp, "Probe")
        return resp.json()

    def search(
        self,
        query: str,
        earliest: str = "-30d",
        latest: str = "now",
        max_results: int = 100,
        timeout: int = 120,
    ) -> dict[str, Any]:
        """Run a SPL search and return results.

        Returns dict with keys: results, event_count, search_id.
        """
        if not query.strip().startswith("search") and not query.strip().startswith("|"):
            query = f"search {query}"

        try:
            create_resp = requests.post(
                self._url("/services/search/jobs"),
                headers=self._headers,
                data={
                    "search": query,
                    "earliest_time": earliest,
                    "latest_time": latest,
                    "output_mode": "json",
                },
                verify=self.verify_ssl,
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.Timeout as exc:
            raise SplunkSearchError("Search creation timed out") from exc
        _raise_for_splunk_status(create_resp, "Search creation")

        sid = create_resp.json()["sid"]
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            try:
                status_resp = requests.get(
                    self._url(f"/services/search/jobs/{sid}"),
                    headers=self._headers,
                    params={"output_mode": "json"},
                    verify=self.verify_ssl,
                    timeout=DEFAULT_TIMEOUT,
                )
            except requests.Timeout as exc:
                raise SplunkSearchError(f"Search {sid} status poll timed out") from exc
            _raise_for_splunk_status(status_resp, "Search status poll")
            entry = status_resp.json()["entry"][0]["content"]

            if entry.get("isDone"):
                break
            time.sleep(SEARCH_POLL_INTERVAL)
        else:
            raise SplunkSearchError(f"Search {sid} timed out after {timeout}s")

        try:
            results_resp = requests.get(
                self._url(f"/services/search/jobs/{sid}/results"),
                headers=self._headers,
                params={"output_mode": "json", "count": max_results},
                verify=self.verify_ssl,
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.Timeout as exc:
            raise SplunkSearchError(f"Search {sid} results fetch timed out") from exc
        _raise_for_splunk_status(results_resp, "Search results fetch")
        data = results_resp.json()

        return {
            "results": data.get("results", []),
            "event_count": int(entry.get("eventCount", 0)),
            "search_id": sid,
        }


    def list_indexes(self) -> list[str]:
        """Return names of all non-internal indexes."""
        resp = requests.get(
            self._url("/services/data/indexes"),
            headers=self._headers,
            params={"output_mode": "json", "count": 0},
            verify=self.verify_ssl,
            timeout=DEFAULT_TIMEOUT,
        )
        _raise_for_splunk_status(resp, "List indexes")
        entries = resp.json().get("entry", [])
        return [
            e["name"]
            for e in entries
            if not e["name"].startswith("_")
        ]

    def list_sourcetypes(self, index: str) -> list[str]:
        """Return sourcetypes present in a given index."""
        result = self.search(
            query=f"| metadata type=sourcetypes index={index}",
            earliest="0",
            latest="now",
            max_results=200,
            timeout=60,
        )
        return [r.get("sourcetype", "") for r in result.get("results", [])]


BOTS_V3_EXPECTED_SOURCETYPES = [
    "wineventlog",
    "aws:cloudtrail",
    "o365:management:activity",
    "iis",
    "stream:dns",
]


def main() -> None:
    """CLI probe: python -m aec.splunk.client --probe"""
    import argparse
    import json as _json

    parser = argparse.ArgumentParser(description="Splunk client probe")
    parser.add_argument("--probe", action="store_true", help="Test connectivity")
    args = parser.parse_args()

    if args.probe:
        try:
            client = SplunkClient(verify_ssl=False)
            info = client.probe()
            entry = info.get("entry", [{}])[0].get("content", {})
            version = entry.get("version", "unknown")
            server_name = entry.get("serverName", "unknown")

            indexes = client.list_indexes()

            bots_sourcetypes: list[str] = []
            if "botsv3" in indexes:
                bots_sourcetypes = client.list_sourcetypes("botsv3")

            result = {
                "ok": True,
                "version": version,
                "server": server_name,
                "indexes": indexes,
                "botsv3_sourcetypes": bots_sourcetypes,
                "botsv3_ready": all(
                    any(st.lower() == expected for st in bots_sourcetypes)
                    for expected in BOTS_V3_EXPECTED_SOURCETYPES
                ) if bots_sourcetypes else False,
            }
            print(_json.dumps(result, indent=2))
        except Exception as e:
            print(_json.dumps({"ok": False, "error": str(e)}, indent=2))
            raise SystemExit(1)


if __name__ == "__main__":
    main()
