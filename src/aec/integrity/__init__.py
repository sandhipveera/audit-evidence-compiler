"""Merkle-chained evidence trail — tamper-evident audit artifacts."""

from aec.integrity.chain import (
    compute_snapshot_hash,
    chain_snapshots,
    verify_chain,
    canonical_json,
)
from aec.integrity.manifest import (
    compute_manifest_hash,
    compute_workbook_hash,
    read_manifest,
    verify_report,
    write_manifest_sheet,
)

__all__ = [
    "compute_snapshot_hash",
    "chain_snapshots",
    "verify_chain",
    "canonical_json",
    "compute_manifest_hash",
    "compute_workbook_hash",
    "write_manifest_sheet",
    "read_manifest",
    "verify_report",
]
