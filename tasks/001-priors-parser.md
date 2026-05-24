# Task 001 — Priors parser (catalog.json) — ✅ DONE

**Status:** Completed in kickoff session 2026-05-23.

**What was built:**
- `src/aec/priors/build_from_xlsx.py` — reads `Control Framework Mapping.xlsx` + `Control Mapping Matrix.xlsx`, emits `catalog.json` with 36 controls indexed across 5 frameworks (ISO 27001, NIST 800-53, NIST CSF, SOC 2, COBIT).
- Hand-curated SPL hint library at `SPL_HINTS` (7 control categories → splunk_indicators + spl_skeleton + evidence_question).
- Blank audit-findings template generator at `src/aec/formatter/templates/audit_findings_blank.xlsx` (real client template with all data rows wiped).

**Open follow-ups (if a future task picks this up):**
- Add SOC 2 Trust Service Criteria ID mapping (CC6.1, CC7.2, etc.) so the agent can resolve operator prompts that use TSC IDs rather than internal control IDs.
- Add NIST CSF subcategory mapping (PR.AC-1, DE.CM-1, etc.) — same reason.
- Add 5+ more SPL hint entries for currently-unmapped categories (Configuration Mgmt, Change Mgmt, BCP/DR).
