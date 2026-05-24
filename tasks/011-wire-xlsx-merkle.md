# Task 011 — Wire xlsx formatter + Merkle chain into aec_demo

**Goal:** Close the differentiator loop. Currently `aec_demo --sample` produces markdown only. Extend it to also produce a sealed `gap_report.xlsx` (from task 004 formatter) with Merkle chain manifest (from task 008 integrity module). After this lands, `aec verify` works on real demo output.

**Budget:** ~100 LOC + tests. ~2 hours of YC time.

## Acceptance

`aec_demo --sample soc2-cc61` produces all three artifacts in <30s:

```
out/
├── audit_memo_<ts>.md           # already produced
├── transcript_<ts>.md           # already produced
├── audit_trail_<ts>.jsonl       # NEW — chained EvidenceSnapshots
└── gap_report_<ts>.xlsx         # NEW — formatted, manifest-sealed
```

And:
```bash
$ aec verify out/gap_report_<ts>.xlsx
✓ Chain length: <N> snapshots
✓ Chain integrity: verified
✓ Manifest root matches trail tip: <hash>
✓ Report cells consistent with snapshots
Report is verifiable. Nothing has been modified since <ts>.
```

Tampering any cell in the xlsx OR any line in audit_trail.jsonl → red TAMPERED + exit 1.

## What to wire

1. **In `aec_demo` (cli/aec_demo.py from task 010):**
   After `result = await run_panel(...)` and the markdown writes:
   - Convert `result` + `splunk_snapshot` into a list of `EvidenceSnapshot` objects (one per persona verdict + one for the consensus = 4 snapshots minimum)
   - Pass through `aec.integrity.chain.chain_snapshots(snapshots)` → returns chained snapshots with `prev_hash` / `this_hash`
   - Write chained snapshots to `out/audit_trail_<ts>.jsonl` via existing `write_trail()`
   - Build `EvidenceRow` / `GapFinding` records from the consensus verdict + Adversary's surfaced gaps
   - Call `aec.formatter.audit_findings.write_findings(rows, out_path, template_path)` to produce the xlsx
   - Call `aec.integrity.manifest.write_manifest(xlsx_path, trail_root_hash, ...)` to embed the Manifest sheet
   - Print `Wrote out/gap_report_<ts>.xlsx (verifiable: aec verify out/gap_report_<ts>.xlsx)`

2. **EvidenceSnapshot ↔ PanelResult shape adapter:**
   Likely needed in a new file `src/aec/agent/snapshot_adapter.py` (~40 LOC):
   ```python
   def panel_result_to_snapshots(
       result: PanelResult,
       splunk_snapshot: dict,
       control_id: str,
   ) -> list[EvidenceSnapshot]:
       ...
   ```
   Each `Critique` in `result.critiques` becomes one snapshot (carries persona, verdict, transport, model, rationale). One final snapshot represents the consensus.

3. **GapFinding extraction:**
   When `result.final_verdict in ("FAIL", "PARTIAL", "INSUFFICIENT")`, build a `GapFinding`:
   - `severity` from verdict (FAIL/INSUFFICIENT → High, PARTIAL → Medium)
   - `root_cause` from Adversary's `rationale` (or consensus rationale if no adversary)
   - `remediation` from Adversary's `recommended_additional_searches[0]` if any, else placeholder
   - `evidence_reference` → path to the audit_trail.jsonl

## Files to create / modify

- `src/aec/agent/snapshot_adapter.py` — NEW (~40 LOC)
- `cli/aec_demo.py` — modify the main flow (~50 LOC added)
- `tests/test_snapshot_adapter.py` — NEW (~80 LOC)
- `tests/test_aec_demo_integration.py` — NEW (~60 LOC) — end-to-end test that runs aec_demo against a fixture sample and verifies all 4 files exist + `aec verify` passes

## Constraints

- No new dependencies.
- Don't refactor `run_panel()` signature.
- Don't touch existing `formatter/audit_findings.py` or `integrity/chain.py` internals — wire only.
- If `formatter.write_findings()` or `integrity.write_manifest()` have signatures incompatible with the new use case, *add overloads or kwargs*, don't rewrite.

## Definition of done

- `aec_demo --sample soc2-cc61` produces 4 files in <30s
- `aec verify out/gap_report_<ts>.xlsx` exits 0 (verified)
- Tampering test passes: `python -c "from openpyxl import load_workbook; wb=load_workbook('out/gap_report_*.xlsx'); wb.active['B9']='HACKED'; wb.save('out/gap_report_tampered.xlsx')"` then `aec verify` exits 1
- Two new test files pass

## Demo cue

After the existing `Done in 22s.` line, the screen now shows:

```
Wrote out/gap_report_2026-05-24T152422Z.xlsx (4 evidence snapshots, Merkle-sealed)

Verify integrity:
  $ aec verify out/gap_report_2026-05-24T152422Z.xlsx
  ✓ Chain length: 4 snapshots
  ✓ Chain integrity: verified
  ✓ Manifest root matches trail tip: a8f3...4c91
  Report is verifiable. Nothing has been modified since 2026-05-24T15:24:22Z.
```

That ten-second beat is the closing arc of the demo video.
