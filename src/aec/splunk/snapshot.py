"""Snapshot fetcher — pulls evidence from Splunk or falls back to sample files."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aec.splunk.client import SplunkClient
from aec.splunk.time_window import normalize_earliest

log = logging.getLogger(__name__)

CACHE_DIR = Path(".aec_cache")
SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "samples"

SPL_BY_CONTROL: dict[str, str] = {
    "CC6.1": (
        "index=botsv3 sourcetype=o365:management:activity action=Login "
        "| stats count by user, mfa_used, src_ip "
        '| where mfa_used="false"'
    ),
    "CC7.2": (
        "index=botsv3 sourcetype=wineventlog EventCode=4625 OR EventCode=4624 "
        "| stats count by severity, status, time_to_respond "
        '| eval response_sla=if(time_to_respond<=240,"met","breached")'
    ),
    "A.9.2.1": (
        "index=botsv3 sourcetype=wineventlog EventCode=4720 OR EventCode=4722 OR EventCode=4728 "
        "| stats count by action, approver, department "
        '| eval approved=if(isnotnull(approver),"yes","no")'
    ),
}

SAMPLE_NAME_BY_CONTROL: dict[str, str] = {
    "CC6.1": "soc2-cc61",
    "CC7.2": "soc2-cc72",
    "A.9.2.1": "iso27001-a921",
}


def _sha8(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def _cache_key(control_id: str, time_window: str, latest: str = "now") -> str:
    spl = SPL_BY_CONTROL.get(control_id, f"index=main control_id={control_id}")
    window = f"{time_window}_{latest}" if latest != "now" else time_window
    return f"{control_id}_{window}_{_sha8(spl)}".replace(".", "_").replace(" ", "_")


def _cache_path(control_id: str, time_window: str, latest: str = "now") -> Path:
    return CACHE_DIR / f"{_cache_key(control_id, time_window, latest)}.json"


def _read_cache(control_id: str, time_window: str, latest: str = "now") -> dict[str, Any] | None:
    path = _cache_path(control_id, time_window, latest)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "control_id" in data:
            return data
    except (json.JSONDecodeError, OSError):
        log.warning("Corrupted cache at %s — will refetch", path)
    return None


def _write_cache(
    control_id: str,
    time_window: str,
    data: dict[str, Any],
    latest: str = "now",
) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(control_id, time_window, latest)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_sample(control_id: str) -> dict[str, Any] | None:
    name = SAMPLE_NAME_BY_CONTROL.get(control_id)
    if not name:
        return None
    path = SAMPLES_DIR / f"{name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def fetch_snapshot(
    control_id: str,
    time_window: str = "30d",
    latest: str = "now",
    live: bool = True,
    client: SplunkClient | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Fetch an evidence snapshot for a control.

    When live=True: query Splunk via the REST API, cache results locally.
    When live=False: load from samples/<control_id>.json.
    """
    if not live:
        sample = _load_sample(control_id)
        if sample is not None:
            return sample
        raise FileNotFoundError(
            f"No sample file for control {control_id}. "
            f"Available: {list(SAMPLE_NAME_BY_CONTROL.keys())}"
        )

    if use_cache:
        cached = _read_cache(control_id, time_window, latest)
        if cached is not None:
            log.info("Cache hit for %s/%s/%s", control_id, time_window, latest)
            return cached

    if client is None:
        client = SplunkClient()

    spl = SPL_BY_CONTROL.get(control_id, f"index=main control_id={control_id}")
    earliest = normalize_earliest(time_window)

    result = client.search(query=spl, earliest=earliest, latest=latest, max_results=50)

    snapshot: dict[str, Any] = {
        "control_id": control_id,
        "framework": _infer_framework(control_id),
        "snapshot_name": f"{_infer_framework(control_id).lower()}-{control_id.lower().replace('.', '')}",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time_range": {"earliest": earliest, "latest": latest},
        "search": spl,
        "event_count": result["event_count"],
        "sample_events": result["results"][:10],
        "aggregations": derive_aggregations(result),
    }

    if use_cache:
        _write_cache(control_id, time_window, snapshot, latest)

    return snapshot


def _coerce_number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    if not isinstance(value, str):
        return None

    normalized = value.strip().replace(",", "")
    if not normalized:
        return None
    try:
        if any(ch in normalized.lower() for ch in (".", "e")):
            return float(normalized)
        return int(normalized)
    except ValueError:
        return None


def derive_aggregations(result: dict[str, Any]) -> dict[str, float | int]:
    """Derive stable numeric aggregation fields from a live Splunk result."""
    results = result.get("results", [])
    if not isinstance(results, list):
        results = []

    aggregations: dict[str, float | int] = {
        "event_count": int(result.get("event_count") or 0),
        "result_row_count": len(results),
    }

    numeric_sums: dict[str, float] = {}
    for row in results:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            number = _coerce_number(value)
            if number is None:
                continue
            numeric_sums[key] = numeric_sums.get(key, 0.0) + float(number)

    for key, value in sorted(numeric_sums.items()):
        if key.startswith("_"):
            continue
        metric_name = "result_count_sum" if key == "count" else f"{key}_sum"
        aggregations[metric_name] = int(value) if value.is_integer() else round(value, 4)

    return aggregations


def _infer_framework(control_id: str) -> str:
    if control_id.startswith("CC"):
        return "SOC2"
    if control_id.startswith("A."):
        return "ISO27001"
    if control_id.startswith("PR.") or control_id.startswith("DE.") or control_id.startswith("RS."):
        return "NIST_CSF"
    return "UNKNOWN"
