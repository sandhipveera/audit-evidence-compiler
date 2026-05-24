# Task 008 — Merkle-chained Evidence Trail + `aec verify`

**Goal:** Make the audit output tamper-evident. Every `EvidenceSnapshot` in `audit_trail.jsonl` is chained by SHA-256 hash; the final xlsx embeds the chain root in a `Manifest` sheet; `aec verify` recomputes the chain and proves nothing was edited post-collection.

## Why this matters

Auditors trust artifacts they can verify. SOC 2 trust services (CC7.2) and NIST 800-92 explicitly call for log integrity protections. Most compliance tools don't provide this — they just output xlsx and trust nobody changed it. This one cryptographically proves it.

For a hackathon demo, the visible beat is: open the xlsx, edit a cell, run `aec verify`, watch it scream `TAMPERED`. That's a 10-second proof-of-concept that lands.

## Spec

Each `EvidenceSnapshot` written to `audit_trail.jsonl` includes:
```json
{
  "snapshot_id": "uuid",
  "control_id": "CC6.1.c",
  "spl_executed": "...",
  "row_count": 247,
  "panel_verdict": "FAIL",
  "panel_transcript_hash": "sha256:...",
  "timestamp": "2026-06-01T14:23:00Z",
  "prev_hash": "sha256:<hash of previous snapshot's canonical-json>",
  "this_hash": "sha256:<hash of this snapshot's canonical-json minus this field>"
}
```

The first snapshot's `prev_hash` is `"sha256:GENESIS"`.

After the formatter writes `gap_report.xlsx`, append a `Manifest` sheet with:
- `chain_root` — `this_hash` of the last snapshot
- `chain_length` — total snapshot count
- `created_at` — UTC ISO timestamp
- `aec_version` — semver
- `verify_command` — literal string `aec verify gap_report.xlsx`

## `aec verify` command

```bash
$ aec verify gap_report.xlsx [--trail audit_trail.jsonl]

✓ Chain length: 12 snapshots
✓ Chain integrity: verified
✓ Manifest root matches trail tip: a8f3...4c91
✓ Report cells consistent with snapshots (12 findings ↔ 12 snapshots)

Report is verifiable. Nothing has been modified since 2026-06-01T14:23:00Z.
```

Failure case:
```bash
$ aec verify gap_report.xlsx
✗ Snapshot #7 (CC6.1.c) hash mismatch
  expected: a8f3...4c91
  actual:   d2e1...77ab
✗ TAMPERED — do not trust this report.
```

## Canonical JSON

Hashing requires deterministic serialization:
- UTF-8
- Sorted keys
- No whitespace
- `prev_hash` and `this_hash` excluded from each snapshot's own hash input
- `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`

## Acceptance

- `aec verify` returns exit code 0 on intact report, 1 on tampered
- Tampering any cell in the xlsx OR any line in audit_trail.jsonl causes verification to fail with a specific reason
- Verification takes <2 seconds for a 100-snapshot trail
- The Manifest sheet is human-readable (operator can read the root hash off the xlsx)

## Files to create

- `src/aec/integrity/__init__.py`
- `src/aec/integrity/chain.py` — `compute_snapshot_hash`, `chain_snapshots`, `verify_chain`
- `src/aec/integrity/manifest.py` — write `Manifest` sheet, read it back, cross-check with trail
- Wire into `src/aec/agent/graph.py` as a final post-formatter step
- Add `aec verify` subcommand in `src/aec/cli.py`
- `tests/test_integrity.py` — golden chain + multiple tamper scenarios

## Out of scope

- Real signatures (HMAC / Ed25519) — pure SHA-256 chain is enough for the hackathon hook. v2 can sign the root with a key.
- Timestamping authority / RFC 3161 — same reason.
- Verifying SPL was actually run against Splunk (would require Splunk-signed responses, which Splunk doesn't provide).
- Encryption of audit_trail.jsonl — orthogonal concern; not blocking integrity.

## Demo cue (10 seconds)

After showing `gap_report.xlsx`:
1. Open the xlsx in Excel/Numbers, change one cell value
2. Run `aec verify` → red `TAMPERED` output
3. Restore the cell → run again → green `verified`
4. Voice-over: *"The report cannot be silently edited. The chain of evidence is provable."*
