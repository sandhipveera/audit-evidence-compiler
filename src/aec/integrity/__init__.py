"""Merkle-chained evidence trail — tamper-evident audit artifacts."""

from aec.integrity.chain import (
    compute_snapshot_hash,
    chain_snapshots,
    verify_chain,
    canonical_json,
)
from aec.integrity.manifest import write_manifest_sheet, read_manifest, verify_report

__all__ = [
    "compute_snapshot_hash",
    "chain_snapshots",
    "verify_chain",
    "canonical_json",
    "write_manifest_sheet",
    "read_manifest",
    "verify_report",
]
