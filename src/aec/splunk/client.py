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
        resp = requests.get(
            self._url("/services/server/info"),
            headers=self._headers,
            params={"output_mode": "json"},
            verify=self.verify_ssl,
            timeout=DEFAULT_TIMEOUT,
        )
        if resp.status_code == 401:
            raise SplunkAuthError("Authentication failed — check SPLUNK_TOKEN")
        resp.raise_for_status()
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
        if create_resp.status_code == 401:
            raise SplunkAuthError("Authentication failed — check SPLUNK_TOKEN")
        create_resp.raise_for_status()

        sid = create_resp.json()["sid"]
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            status_resp = requests.get(
                self._url(f"/services/search/jobs/{sid}"),
                headers=self._headers,
                params={"output_mode": "json"},
                verify=self.verify_ssl,
                timeout=DEFAULT_TIMEOUT,
            )
            status_resp.raise_for_status()
            entry = status_resp.json()["entry"][0]["content"]

            if entry.get("isDone"):
                break
            time.sleep(SEARCH_POLL_INTERVAL)
        else:
            raise SplunkSearchError(f"Search {sid} timed out after {timeout}s")

        results_resp = requests.get(
            self._url(f"/services/search/jobs/{sid}/results"),
            headers=self._headers,
            params={"output_mode": "json", "count": max_results},
            verify=self.verify_ssl,
            timeout=DEFAULT_TIMEOUT,
        )
        results_resp.raise_for_status()
        data = results_resp.json()

        return {
            "results": data.get("results", []),
            "event_count": int(entry.get("eventCount", 0)),
            "search_id": sid,
        }


def main() -> None:
    """CLI probe: python -m aec.splunk.client --probe"""
    import argparse

    parser = argparse.ArgumentParser(description="Splunk client probe")
    parser.add_argument("--probe", action="store_true", help="Test connectivity")
    args = parser.parse_args()

    if args.probe:
        try:
            client = SplunkClient()
            info = client.probe()
            server_name = info.get("entry", [{}])[0].get("content", {}).get("serverName", "unknown")
            print(f"OK — connected to {server_name} at {client.host}")
        except Exception as e:
            print(f"FAIL — {e}")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
