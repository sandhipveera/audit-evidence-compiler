"""Splunk-native ML anomaly detection — runs Splunk's own AI/ML at query time.

This is the project's **Splunk AI capability**. Instead of only pulling raw rows
out of Splunk and reasoning over them with the external-vendor panel, the
pipeline asks Splunk's in-platform machine-learning engine to score the evidence
population for anomalies, and feeds those findings to the four-vendor debate as
corroborating monitoring evidence.

Engine selection happens automatically, at runtime:

* **Splunk Machine Learning Toolkit (MLTK)** — ``fit DensityFunction`` / ``apply``
  when the toolkit app is installed; or
* **Splunk built-in ``anomalydetection``** — the probabilistic outlier engine
  that ships in core Splunk and powers the *Splunk App for Anomaly Detection*.

Either way the model runs **inside Splunk** over the real BOTS v3 dataset; no
anomaly scoring happens in Python here. We only shape the result.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from aec.splunk.client import SplunkClient, SplunkSearchError

log = logging.getLogger(__name__)

# Population the Splunk ML engine scores for outliers: per registered-domain DNS
# behaviour from stream:dns. High unique-query cardinality against one domain is
# the classic DNS-tunnelling / exfiltration signal — the kind of "anomaly
# indicative of malicious acts" that monitoring controls (e.g. SOC 2 CC7.2)
# require an organisation to detect.
_DNS_POPULATION = (
    "search index=botsv3 sourcetype=stream:dns query=* "
    "| eval qlen=len(query) "
    '| rex field=query "(?<registered_domain>[a-zA-Z0-9-]+\\.[a-zA-Z]+)$" '
    "| stats count as query_count avg(qlen) as avg_qlen max(qlen) as max_qlen "
    "dc(query) as unique_queries by registered_domain "
    "| where query_count > 5 "
)

# Built-in engine: core `anomalydetection` (the Splunk App for Anomaly Detection
# engine). action=annotate adds log_event_prob / probable_cause to each row; we
# keep only the rows it actually flagged.
_BUILTIN_TAIL = (
    "| anomalydetection action=annotate unique_queries avg_qlen query_count "
    "| where isnotnull(probable_cause) "
    "| sort log_event_prob "
    "| head 10"
)

# MLTK engine: a DensityFunction is fit over the population and each domain
# scored; IsOutlier()=1 marks anomalies. Requires the Machine Learning Toolkit
# app (+ Python for Scientific Computing) to be installed in Splunk.
_MLTK_TAIL = (
    "| fit DensityFunction unique_queries threshold=0.01 "
    "into app:aec_dns_density show_density=true "
    "| where 'IsOutlier(unique_queries)'=1 "
    "| sort - unique_queries "
    "| head 10"
)

_ANOMALY_FIELDS = (
    "registered_domain",
    "query_count",
    "unique_queries",
    "avg_qlen",
    "max_qlen",
    "log_event_prob",
    "probable_cause",
    "ProbableCause",
    "density",
)


def _client(client: SplunkClient | None) -> SplunkClient:
    if client is not None:
        return client
    # Force basic auth: the env SPLUNK_TOKEN may be rotated/expired, and the
    # Dockerised Splunk uses a self-signed cert.
    return SplunkClient(token="", verify_ssl=False)


def _shape_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shaped = []
    for r in rows:
        row = {k: r[k] for k in _ANOMALY_FIELDS if k in r and r[k] != ""}
        # MLTK DensityFunction emits dynamically-named score fields, e.g.
        # `IsOutlier(unique_queries)` / `ProbabilityDensity(unique_queries)`.
        for k, v in r.items():
            if v == "":
                continue
            if k.startswith("IsOutlier("):
                row["is_outlier"] = v
            elif k.startswith("ProbabilityDensity("):
                row["probability_density"] = v
        shaped.append(row)
    return shaped


def run_ml_anomaly(
    control_id: str,
    framework: str = "",
    *,
    client: SplunkClient | None = None,
    earliest: str = "0",
    latest: str = "now",
) -> dict[str, Any]:
    """Run Splunk's ML anomaly engine over the evidence population at runtime.

    Returns a dict describing which Splunk ML engine ran, the SPL executed, and
    the anomalies Splunk flagged. On any failure (Splunk unreachable, command
    unavailable) returns ``{"available": False, "reason": ...}`` so the pipeline
    degrades gracefully rather than breaking.
    """
    engine_pref = os.environ.get("AEC_SPLUNK_ML_ENGINE", "auto").lower()  # auto|mltk|builtin

    try:
        cli = _client(client)
    except Exception as exc:  # noqa: BLE001 — env / credential issues
        return {"available": False, "reason": f"no Splunk client: {exc}"}

    attempts: list[tuple[str, str, str, str]] = []  # (engine, command, app, spl)
    if engine_pref in ("auto", "mltk"):
        attempts.append((
            "Splunk Machine Learning Toolkit (MLTK)",
            "fit DensityFunction / apply",
            "Splunk_ML_Toolkit",
            _DNS_POPULATION + _MLTK_TAIL,
        ))
    if engine_pref in ("auto", "builtin"):
        attempts.append((
            "Splunk built-in anomalydetection",
            "anomalydetection",
            "search (core) — Splunk App for Anomaly Detection engine",
            _DNS_POPULATION + _BUILTIN_TAIL,
        ))

    last_error: str | None = None
    for engine, command, app, spl in attempts:
        try:
            result = cli.search(spl, earliest=earliest, latest=latest, max_results=10)
        except SplunkSearchError as exc:
            last_error = str(exc)
            # MLTK not installed -> "Unknown search command 'fit'": fall through
            # to the built-in engine.
            log.info("Splunk ML engine %s unavailable: %s", engine, exc)
            continue
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            log.warning("Splunk ML search failed (%s): %s", engine, exc)
            continue

        anomalies = _shape_rows(result.get("results", []))
        return {
            "available": True,
            "source": "live",
            "engine": engine,
            "command": command,
            "splunk_app": app,
            "population": "stream:dns registered domains (BOTS v3)",
            "events_scanned": result.get("event_count", 0),
            "search": spl,
            "anomaly_count": len(anomalies),
            "anomalies": anomalies,
            "summary": _summary(engine, anomalies),
        }

    return {"available": False, "reason": last_error or "no ML engine ran"}


def _summary(engine: str, anomalies: list[dict[str, Any]]) -> str:
    if not anomalies:
        return f"{engine} scored the DNS population and flagged no anomalies."
    names = ", ".join(a.get("registered_domain", "?") for a in anomalies[:3])
    return (
        f"{engine} flagged {len(anomalies)} anomalous DNS domain(s) "
        f"({names}) — abnormal query cardinality consistent with tunnelling/exfiltration."
    )
