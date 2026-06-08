#!/usr/bin/env python3
"""Seed index=auditcompiler_posture with a realistic 30-day verdict history.

The Compliance Posture dashboard (splunk-app/auditcompiler) reads
index=auditcompiler_posture, which is normally populated over time by the 36
scheduled saved searches (every 4h). For a live demo we want it populated
immediately, so this backfills ~30 daily snapshots per control via Splunk HEC.

Schema per event matches the `mode=summary` output of the `| auditcompiler`
command and every field the dashboard queries:
  control_id, framework, consensus, event_count, root_cause, panel_mode,
  auditor_verdict, engineer_verdict, adversary_verdict, security_model_verdict

The 36 (control_id, name, framework) triples are parsed from the app's
savedsearches.conf so this stays in lockstep with the real app — no second
source of truth.

Env:
  HEC_TOKEN        (required) Splunk HEC token value
  HEC_URL          (default https://localhost:8088)
  POSTURE_INDEX    (default auditcompiler_posture)
  SEED_DAYS        (default 30)

Usage:
  HEC_TOKEN=... python3 scripts/seed_posture.py
  python3 scripts/seed_posture.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SAVEDSEARCHES = REPO / "splunk-app" / "auditcompiler" / "default" / "savedsearches.conf"

# Verdict severity order (matches consensus rule: lowest/worst wins).
ORDER = {"PASS": 0, "PARTIAL": 1, "FAIL": 2, "INSUFFICIENT": 3}

# Current (today's) posture by control family — aligned with the exec-report
# narrative: access control + vendor risk are weak; encryption/backup/change
# are strong; incident response has evidence gaps.
FAMILY_VERDICT = {
    "Access Control Policy": "FAIL",
    "User Access Review": "PARTIAL",
    "Vendor Risk Management": "FAIL",
    "Logging & Monitoring": "PARTIAL",
    "Encryption": "PASS",
    "Patch Management": "PARTIAL",
    "Asset Inventory": "PASS",
    "Incident Response": "INSUFFICIENT",
    "Change Management": "PASS",
    "Data Backup": "PASS",
}

# Families that improved over the window (worse 30d ago → current). Drives a
# visibly positive verdict trend ("risk trending down across KRIs").
IMPROVED = {"Logging & Monitoring", "User Access Review", "Patch Management"}

ROOT_CAUSE = {
    "PASS": "Evidence complete; controls operating effectively across the window.",
    "PARTIAL": "Control operating but coverage gaps remain; some events lack authorization.",
    "FAIL": "Multiple unremediated findings; control objective not met for sampled period.",
    "INSUFFICIENT": "Evidence too sparse to render a verdict; instrument additional logging.",
}

EVENTS_BY_VERDICT = {"PASS": 1809, "PARTIAL": 1247, "FAIL": 642, "INSUFFICIENT": 88}


def _one_worse(v: str) -> str:
    """Return the verdict one step worse (for the pre-improvement state)."""
    inv = {n: k for k, n in ORDER.items()}
    return inv[min(ORDER[v] + 1, 3)]


def parse_controls() -> list[dict]:
    """Extract (control_id, name, framework) triples from savedsearches.conf."""
    text = SAVEDSEARCHES.read_text(encoding="utf-8")
    pat = re.compile(
        r'control_id="(?P<cid>CTRL-\d+)",\s*ctrl_name="(?P<name>[^"]+)",\s*'
        r'framework="(?P<fw>[^"]+)"'
    )
    seen: dict[str, dict] = {}
    for m in pat.finditer(text):
        seen.setdefault(m["cid"], {"control_id": m["cid"], "name": m["name"], "framework": m["fw"]})
    return list(seen.values())


def vendor_verdicts(consensus: str) -> dict:
    """Four vendor verdicts whose worst equals the consensus (lowest-wins)."""
    # At least one vendor at the consensus level; the rest no worse.
    better = [k for k, n in ORDER.items() if n <= ORDER[consensus]]
    pick = lambda i: better[i % len(better)]  # noqa: E731
    return {
        "auditor_verdict": consensus,
        "engineer_verdict": pick(1),
        "adversary_verdict": consensus,
        "security_model_verdict": pick(2),
    }


def build_events(controls: list[dict], days: int) -> list[dict]:
    """Build daily snapshot events for every control across `days`."""
    now = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
    events: list[dict] = []
    for idx, c in enumerate(controls):
        current = FAMILY_VERDICT.get(c["name"], "PARTIAL")
        improved = c["name"] in IMPROVED
        start = _one_worse(current) if improved else current
        # Each improver flips on a control-specific day in the back half.
        flip_day = days - 8 - (idx % 5) if improved else 0
        for d in range(days):
            day_idx = days - 1 - d  # 0 == today, larger == older
            verdict = start if (improved and day_idx >= flip_day) else current
            ts = now - timedelta(days=day_idx)
            ev = {
                "control_id": c["control_id"],
                "framework": c["framework"],
                "consensus": verdict,
                "event_count": str(EVENTS_BY_VERDICT[verdict]),
                "root_cause": ROOT_CAUSE[verdict],
                "panel_mode": "four-vendor",
                **vendor_verdicts(verdict),
            }
            events.append({
                "time": ts.timestamp(),
                "index": os.environ.get("POSTURE_INDEX", "auditcompiler_posture"),
                "sourcetype": "aec:posture",
                "source": "aec:seed",
                "event": ev,
            })
    return events


def post_hec(events: list[dict], url: str, token: str) -> None:
    body = "\n".join(json.dumps(e) for e in events).encode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/services/collector/event",
        data=body,
        headers={"Authorization": f"Splunk {token}", "Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
        payload = json.loads(resp.read().decode())
    if payload.get("text") != "Success":
        raise SystemExit(f"HEC error: {payload}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Print summary, don't send")
    args = ap.parse_args()

    days = int(os.environ.get("SEED_DAYS", "30"))
    controls = parse_controls()
    if len(controls) != 36:
        print(f"[warn] expected 36 controls, parsed {len(controls)}", file=sys.stderr)
    events = build_events(controls, days)

    dist: dict[str, int] = {}
    for c in controls:
        dist[FAMILY_VERDICT.get(c["name"], "PARTIAL")] = dist.get(
            FAMILY_VERDICT.get(c["name"], "PARTIAL"), 0) + 1
    print(f"[seed] {len(controls)} controls × {days} days = {len(events)} events")
    print(f"[seed] current posture distribution: {dist}")

    if args.dry_run:
        print("[seed] dry-run — first event:")
        print(json.dumps(events[0], indent=2))
        return

    token = os.environ.get("HEC_TOKEN")
    if not token:
        raise SystemExit("HEC_TOKEN not set (source .env first)")
    url = os.environ.get("HEC_URL", "https://localhost:8088")
    # Send in batches to keep request bodies modest.
    batch = 200
    for i in range(0, len(events), batch):
        post_hec(events[i:i + batch], url, token)
    print(f"[seed] sent {len(events)} events to {url} → index="
          f"{os.environ.get('POSTURE_INDEX', 'auditcompiler_posture')}")


if __name__ == "__main__":
    main()
