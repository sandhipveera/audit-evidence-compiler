"""SHA-256 hash chain over EvidenceSnapshot records."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

GENESIS = "sha256:GENESIS"
HASH_EXCLUDED_KEYS = {"prev_hash", "this_hash"}


def canonical_json(snapshot: dict[str, Any]) -> bytes:
    filtered = {k: v for k, v in snapshot.items() if k not in HASH_EXCLUDED_KEYS}
    return json.dumps(filtered, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def compute_snapshot_hash(snapshot: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(snapshot)).hexdigest()
    return f"sha256:{digest}"


def chain_snapshots(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add prev_hash and this_hash to each snapshot in order. Mutates and returns the list."""
    prev = GENESIS
    for snap in snapshots:
        snap["prev_hash"] = prev
        snap["this_hash"] = compute_snapshot_hash(snap)
        prev = snap["this_hash"]
    return snapshots


def verify_chain(snapshots: list[dict[str, Any]]) -> list[str]:
    """Verify an already-chained list of snapshots. Returns a list of error strings (empty = OK)."""
    errors: list[str] = []
    if not snapshots:
        return errors

    prev = GENESIS
    for i, snap in enumerate(snapshots):
        if snap.get("prev_hash") != prev:
            errors.append(
                f"Snapshot #{i + 1} ({snap.get('control_id', '?')}) prev_hash mismatch: "
                f"expected {prev}, got {snap.get('prev_hash')}"
            )

        expected = compute_snapshot_hash(snap)
        actual = snap.get("this_hash", "")
        if actual != expected:
            errors.append(
                f"Snapshot #{i + 1} ({snap.get('control_id', '?')}) hash mismatch: "
                f"expected {expected}, actual {actual}"
            )

        prev = snap.get("this_hash", "")

    return errors


def read_trail(path: Path) -> list[dict[str, Any]]:
    """Read audit_trail.jsonl and return the list of snapshot dicts."""
    snapshots = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                snapshots.append(json.loads(line))
    return snapshots


def write_trail(path: Path, snapshots: list[dict[str, Any]]) -> None:
    """Write snapshots to audit_trail.jsonl (one JSON object per line)."""
    with open(path, "w", encoding="utf-8") as f:
        for snap in snapshots:
            f.write(json.dumps(snap, sort_keys=True, ensure_ascii=False) + "\n")
