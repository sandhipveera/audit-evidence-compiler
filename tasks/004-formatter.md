# Task 004 — Audit Findings xlsx formatter

**Goal:** Take a list of `EvidenceRow` / `GapFinding` and write them into a copy of `src/aec/formatter/templates/audit_findings_blank.xlsx`, starting at row 9 (data row).

## Acceptance

- `python -m aec.formatter.audit_findings --in evidence.json --out gap_report.xlsx` produces a valid xlsx that opens in Excel/Numbers.
- Each gap finding populates: Finding ID, Audit Type, Framework, Audit Reference, Finding Description, Category, Severity, Severity Score, Root Cause, Affected System, Remediation Action, Target Closure Date, Status, Evidence Reference.
- Severity score auto-derived from severity label (Low=1, Medium=2, High=3, Critical=4).
- Target Closure Date = today + 90 days for High/Critical, +180 for Medium, +365 for Low.

## Schema reference

The blank template's row 8 header (per parser run):
```
Finding ID | Audit Type | Framework | Audit Reference | Finding Description
| Finding Category | Severity | Severity Score | Root Cause | Affected System/Process
| Risk Owner | Remediation Action | Remediation Owner | Target Closure Date
| Current Status | Closure Date | Residual Risk Level | Evidence Reference
| Last Review Date | Comments
```

## Files to create

- `src/aec/formatter/__init__.py`
- `src/aec/formatter/audit_findings.py`
- `tests/test_formatter.py` (golden-file test: feed fixture EvidenceRows, diff against expected xlsx)

## Out of scope

- PDF output (xlsx is what auditors want)
- Multi-sheet output (one finding per row; everything on `Audit Remediation` sheet)
- Chart generation
